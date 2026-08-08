"""用例入库门禁（C3+C4）—— 批量生成会淹库，闸门必须在入库前。

两种淹法，第二种更难受：
- **明面的**：一次 300 条重复语义、断言宽松的垃圾。好发现。
- **暗面的**：每条单看都合理，合起来**覆盖度极度倾斜** —— 20 条都在测"创建服务"
  的参数组合，零条测删除的级联影响。总量涨了，风险覆盖没涨，而通过率、用例数
  这些指标全面美化。

这个项目踩过一次（P0 占比超标 / 模糊词 / module 为空），那次是**入库后**发现的；
批量化之后，入库后发现等于人肉从 300 条里捞。

全部作用在 MCP 回推入口 —— 那是唯一入口，这是架构上最大的便宜，一定要占。
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case, CaseFolder

# 单次批量里 P0 的占比上限。老毛病是"什么都 P0"，最后 P0 等于没分级。
P0_QUOTA = 0.15
# 语义去重两档。**误拦比漏拦更糟** —— CC 被挡住还不知道为什么，会开始想办法绕
# （把标题改得看不出关系），那比多一条重复用例有害得多。所以：
#   >= HARD  近乎一字不差 → 硬拒
#   >= WARN  相似但可能是不同测试点 → 只提醒，让人/CC 自己判
# ⚠ 字符串相似度**分不清**"同一测试点换个说法"和"不同测试点用词很像"。
# 实测反例：
#   「删除服务后列表不再显示」vs「删除服务成功后该服务从列表消失」= 40%（其实是同一条）
#   「创建服务时名称为空应报错」vs「创建服务时名称超长应报错」   = 73%（其实是两条）
# 后者只差两个字却是不同测试点 —— 没有任何阈值能同时抓住前者、放过后者。
#
# 所以按和断言强度同样的纪律办：**只有标题完全一样才硬拒**（那 100% 可判），
# 其余一律只提醒。误拦比漏拦更糟 —— CC 被挡住又不知道为什么，会开始把标题
# 改得看不出关系来绕过，那比多一条重复用例有害得多。
DUP_WARN = 0.60

# 模糊词 —— 写了等于没写，验不出对错
_VAGUE = re.compile(r"操作成功|显示正常|无报错|符合预期|功能正常|正确显示|正常展示|没有问题")

# 一批用例该覆盖的操作类型。全压在"创建"上是最常见的倾斜。
_OP_KINDS = {
    "创建": re.compile(r"新建|创建|添加|新增|上传|导入"),
    "查询": re.compile(r"查询|搜索|筛选|列表|查看|详情"),
    "修改": re.compile(r"修改|编辑|更新|重命名|调整"),
    "删除": re.compile(r"删除|移除|清空|卸载|解绑"),
    "异常": re.compile(r"失败|错误|异常|超时|非法|无效|越界|重复|为空"),
    "权限": re.compile(r"权限|越权|未授权|无权|角色|租户隔离"),
}


def _norm(t: str) -> str:
    return re.sub(r"[\s\-_/（）()【】\[\]，,。.、:：]", "", (t or "").lower())


def _similar(a: str, b: str) -> float:
    """标题相似度：字符 bigram 的**包含度**（交集 / 较短者）。

    不用 Jaccard：中文换个语序说同一件事，Jaccard 只有 40% 上下
    （"新建项目后可在列表中查到" vs "新建项目后在项目列表中可以查到" = 41%），
    阈值压到能抓住它就会开始误伤。包含度对"同一件事换个说法"敏感得多（64%），
    对"同一个宾语不同动词"仍然分得开（新建/删除项目 = 33%）。"""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ga = {na[i:i + 2] for i in range(len(na) - 1)} or {na}
    gb = {nb[i:i + 2] for i in range(len(nb) - 1)} or {nb}
    inter = len(ga & gb)
    return inter / min(len(ga), len(gb)) if ga and gb else 0.0


async def check_one(
    session: AsyncSession, branch_id: uuid.UUID, title: str, module: str, priority: str,
) -> tuple[list[str], list[str]]:
    """单条入库校验。返回 (硬错误, 软警告)。"""
    errors: list[str] = []
    warns: list[str] = []

    # 闸 1 语义去重 —— 同模块下相似标题
    rows = (await session.execute(
        select(Case.case_code, Case.title)
        .join(CaseFolder, CaseFolder.id == Case.folder_id, isouter=True)
        .where(Case.branch_id == branch_id, Case.deleted_at.is_(None),
               CaseFolder.name == module)
        .limit(500)
    )).all()
    nt = _norm(title)
    best = (0.0, None, None)
    exact = None
    for code, existing in rows:
        if _norm(existing) == nt:
            exact = (code, existing)
            break
        sim = _similar(title, existing)
        if sim > best[0]:
            best = (sim, code, existing)

    if exact:
        # **字面完全相同**才硬拒。注意不能用 `sim >= 1.0` 判：包含度对
        # 「A」vs「A（补充说明）」也给满分，而那是两个不同的测试点 —— 实测被误拒过。
        errors.append(f"和 {exact[0]}「{exact[1]}」标题完全一样 —— 重复入库了。")
        return errors, warns

    sim, code, existing = best
    if sim >= DUP_WARN:
        warns.append(
            f"⚠ 和 {code}「{existing}」很像（相似度 {sim:.0%}）。"
            f"如果确实是不同测试点，把标题写得能区分开 —— 看不出区别的两条用例，"
            f"跑起来也只会重复消耗。"
        )

    # 闸 2 的一半：标题里的模糊词
    m = _VAGUE.search(title or "")
    if m:
        errors.append(f"标题里有模糊词「{m.group(0)}」—— 验不出对错。写清楚具体预期什么。")

    return errors, warns


def check_batch(items: list[dict]) -> tuple[list[str], list[str]]:
    """整批校验：P0 配额 + 覆盖倾斜。items 至少含 title / priority。"""
    errors: list[str] = []
    warns: list[str] = []
    n = len(items)
    if n == 0:
        return errors, warns

    # 闸 3 P0 配额 —— 硬拒，整批打回重新分级
    p0 = sum(1 for it in items if (it.get("priority") or "").upper() == "P0")
    if n >= 5 and p0 / n > P0_QUOTA:
        errors.append(
            f"这批 {n} 条里有 {p0} 条 P0（{p0 / n:.0%} > {P0_QUOTA:.0%}）。"
            "P0 是挂了就得立刻停下来查的那一档，什么都 P0 等于没分级。重新分一遍再传。"
        )

    # 闸 4 覆盖倾斜 —— 只告警，不拦
    if n >= 8:
        hit = {k: 0 for k in _OP_KINDS}
        for it in items:
            text = f"{it.get('title', '')} {it.get('expected_result', '')}"
            for k, pat in _OP_KINDS.items():
                if pat.search(text):
                    hit[k] += 1
        top = max(hit, key=hit.get)
        if hit[top] / n > 0.7:
            warns.append(
                f"⚠ 覆盖倾斜：{hit[top]}/{n} 条都是「{top}」类操作。"
                f"总量涨了但风险覆盖没涨 —— 补几条别的类型再传。当前分布：{hit}"
            )
        for miss in ("删除", "权限"):
            if hit[miss] == 0:
                warns.append(
                    f"⚠ 这批一条「{miss}」相关的都没有。{miss}路径是最容易漏、"
                    f"出事又最贵的地方。"
                )
    return errors, warns


def check_p0_two_phase(priority: str, target_level: str, has_confirmed_expected: bool) -> list[str]:
    """闸：P0 不允许一次性三件套直出（C4）。

    三份产物同源生成会让它们**必然互相一致**，而一致会被误读成"验证过了"——
    一致性冒充正确性。典型失效：把"创建成功"理解成"返回 200"，于是步骤写
    "预期：创建成功"、接口断言 status==200、UI 断言 toast 含"成功"，三层全绿；
    但真实业务是异步创建，200 只代表受理。

    所以 P0 走两阶段：先只出步骤用例，人**只看「预期结果」这一列**确认
    （一屏二三十条，五分钟），再据此生成接口和 UI。人的介入点刻意选得极窄 ——
    让人审全文他会疲劳、会盖章。
    """
    if (priority or "").upper() != "P0":
        return []
    if target_level != "full":
        return []
    if has_confirmed_expected:
        return []
    return [
        "P0 用例不允许一次性出三件套：三份产物同源生成会必然互相一致，"
        "而一致会被当成已经验证过（一致性冒充正确性）。"
        "先只回推步骤用例（target_level=spec），让人确认「预期结果」这一列之后，"
        "再补接口和 UI。人选了 full 也不行 —— 门禁要能覆盖手滑。"
    ]
