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
# 「验不出对错」的词。后半段是评审评测补的 —— 「各功能均正常」跟「功能正常」
# 是同一个毛病，但原来的词表匹配不上，于是那条垃圾用例三轮全部过审。
_VAGUE = re.compile(r"操作成功|显示正常|无报错|符合预期|功能正常|正确显示|正常展示|没有问题"
                    r"|均正常|都正常|一切正常|运行正常|表现正常|无异常|符合要求|正常使用")

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


# ── 模块名 ────────────────────────────────────────────────────────
# 实测两种毛病，都发生在"CC 随手起个名"这一下：
#   ① 「监控-请求日志」建成了**一级**模块 —— 它明显是两级，写成一级之后
#      同一个「监控」下的其他用例找不到家，导航栏一屏全是长名字的一级目录。
#   ② 同一个模块被拼成好几个：「LLM PROVIDERS」/「LLM Providers」/「llm_providers」
#      —— path 是大写归一的，所以后两个能撞上；但「LLM-PROVIDERS」就是新的一个了。
# 这两件都判得死，所以硬拒；判不死的（该不该挂到某个已有模块下）只提示。

_LEVEL_SEP = re.compile(r"[-/_:：·|>＞→]|\s+[-–—]\s+")


def _norm_module(name: str) -> str:
    """模块名归一：去掉分隔符和空格、统一大小写。用来判"是不是同一个模块"。"""
    return re.sub(r"[\s\-/_:：·|]", "", (name or "")).upper()


def check_module_name(name: str, existing: list[str], is_top_level: bool = True
                      ) -> tuple[list[str], list[str]]:
    """模块名规范。返回 (硬错误, 软警告)。existing 是同级已有的名字。"""
    errors: list[str] = []
    warns: list[str] = []
    n = (name or "").strip()
    if not n:
        return ["模块名不能为空"], warns

    # **重名写法先判**：`llm_providers` 既像两级、又是已有「LLM PROVIDERS」的
    # 另一种写法。这时候该说的是"用现成那个名字"，说"拆成 llm + providers"是把人带歪。
    same = [e for e in existing if _norm_module(e) == _norm_module(n) and e != n]
    if same:
        return [f"已经有「{same[0]}」了 —— 「{n}」只是写法不同（大小写/分隔符），"
                f"放行就会把同一个模块拆成两个。用现成的那个名字。"], warns

    m = _LEVEL_SEP.search(n)
    if m and is_top_level:
        left, _, right = n.partition(m.group(0))
        left, right = left.strip(), right.strip()
        if left and right:
            # **警告不硬拦**（判据规范 ①③）：合法写法存在且不少 ——
            # 「A/B 测试」「CI/CD」「OAuth2.0-登录」都是一个名字，不是两级。
            # 拼成一级顶多是导航难看，不影响任何正确性，不配硬拦。
            warns.append(
                f"⚠ 「{n}」看着像两级。如果确实是两级，传 "
                f"module=\"{left}\" + submodule=\"{right}\" —— "
                f"拼成一级之后「{left}」下的其他用例找不到家，导航栏也会被长名字撑满。"
                f"**如果它本来就是一个词**（A/B 测试、CI/CD 这种），忽略这条。"
            )

    # `n in existing` 要单独判：上面那条只拦「拼写不同的重名」（`e != n`），
    # 名字**一模一样**时它放行 —— 于是往已有模块里加用例，也会收到
    # 「这是新建的一级模块」，而同一句里的「现有一级模块」列表就含它自己。
    if is_top_level and existing and n not in existing:
        warns.append(
            f"⚠ 「{n}」是**新建的一级模块**。现有一级模块：{'、'.join(existing[:12])}。"
            f"确认它不该是其中某个的子模块 —— 一级模块是按被测系统的功能域分的，"
            f"不是按你这一轮在测什么分的。"
        )
    return errors, warns


# ── 标题形状 ────────────────────────────────────────────────────
# 现在的标题长这样：「消费方租户管理员自己申请跨租户订阅时一级节点自动跳过，
# 提供方直接批二级后订阅生效」—— 得读完整句才知道在测什么，而列表上只露标题。
# 规范成「**对象+动作**-预期」两段：前段十几个字一眼看完，后段是结论。
# 分隔符用**短横、不留空格** —— 列表里标题就那么点宽度，别浪费。
#   租户管理员跨租户订阅-一级自动跳过、二级批完即生效
# 好处不只是好读：**前段就是"测什么"，后段就是"预期什么"**，
# 评审判"这条在验什么、验到了没有"不用再从整句里猜。
_TITLE_SEP = re.compile(r"\s*[—–\-:：]\s*|，(?=.{6,})")
HEAD_MAX = 20


def check_title_shape(title: str) -> list[str]:
    """标题形状 —— **只提示**（判据规范 ③：合法写法存在，短标题本来就不需要分段）。"""
    t = (title or "").strip()
    if not t or len(t) <= HEAD_MAX:
        return []                       # 短标题本身就是一眼可读的，不用分段
    m = _TITLE_SEP.search(t)
    head = t[:m.start()] if m else t
    if m and len(head) <= HEAD_MAX:
        return []
    return [f"⚠ 标题 {len(t)} 字、前段没断开，列表上得读完整句才知道在测什么。"
            f"写成「对象+动作-预期」两段（短横、不留空格），前段 {HEAD_MAX} 字内："
            f"「租户管理员跨租户订阅-一级自动跳过、二级批完即生效」。"
            f"细节放预期结果里，标题只要一眼能认出是哪个功能。"]


async def check_one(
    session: AsyncSession, branch_id: uuid.UUID, title: str, module: str, priority: str,
    exclude_case_id: uuid.UUID | None = None,
) -> tuple[list[str], list[str]]:
    """单条入库校验。返回 (硬错误, 软警告)。

    `exclude_case_id` 给**改用例**用：不排除自己的话，原样保存一条已存在的用例
    会被判成"和自己标题完全一样，重复入库"，于是这条用例永远改不动 ——
    而改用例正是 CC 修正自己笔误的唯一途径。
    """
    errors: list[str] = []
    warns: list[str] = []

    # 闸 1 语义去重 —— 同模块下相似标题
    stmt = (
        select(Case.case_code, Case.title)
        .join(CaseFolder, CaseFolder.id == Case.folder_id, isouter=True)
        .where(Case.branch_id == branch_id, Case.deleted_at.is_(None),
               CaseFolder.name == module)
    )
    if exclude_case_id is not None:
        stmt = stmt.where(Case.id != exclude_case_id)
    rows = (await session.execute(stmt.limit(500))).all()
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

    warns.extend(check_title_shape(title))

    # 闸 2 的一半：标题里的模糊词
    m = _VAGUE.search(title or "")
    if m:
        # 模糊词是**唯一结论**时才硬拦。合法写法：模糊词只是限定语之一，
        # 旁边有量化条件 ——「批量导入 1000 条无报错且耗时 < 30s」里的「无报错」
        # 不是空话，它配着两个可验的数。一律硬拦会把这种标题也挡掉。
        rest = _VAGUE.sub("", title or "")
        concrete = bool(re.search(r"\d|[<>≤≥=]|返回|状态码|字段|提示|不可|禁止|失败|拒绝", rest))
        if concrete:
            warns.append(
                f"⚠ 标题里有「{m.group(0)}」这种模糊词。旁边有具体条件所以不拦，"
                f"但能替换成那个具体条件的话更好 —— 列表上只露标题。")
        else:
            errors.append(
                f"标题里有模糊词「{m.group(0)}」，而整句里没有任何可验的具体内容 —— "
                f"这种标题跑起来永远是绿的。写清楚具体预期什么（哪个文案/字段/状态码）。")

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

def p0_confirmation_hint(priority: str, target_level: str, confirmed_note: str | None) -> list[str]:
    """P0 一次性出三件套时的**提醒**，不是拦截。

    ## 为什么从"拦"改成"提醒"

    原来这里是硬拦：P0 不许一次性出三件套。CC 被拦住 → 人切到平台页面 → 找到那条
    用例 → 点「确认预期结果」→ CC 再回来挂。每条 P0 都走一趟，是实打实的税。

    支撑那道拦截的数（同源生成 80% 的断言退化成只看状态码）测的是**平台自己的
    AI 生成器**，不是 CC —— 拿它去拦 CC，这个推断跨得太快，当初没说清楚。

    而且拦截本身也没那么硬：人在页面上点一下按钮，同样验证不了他真读了预期结果。
    两边都是形式，那就选便宜的那个 —— 确认发生在人已经在的地方（CC 对话里），
    CC 把确认内容一起带上来，平台**只存不拦**。

    ## 那这条提醒还有什么用

    风险是真的（同源生成确实会把"创建成功"做成"返回 200"），所以信号留着：
    没带确认记录就回一句提醒，让 CC 知道该去问人。但它进 warnings 不进 errors，
    不挡任何东西。
    """
    if (priority or "").upper() != "P0":
        return []
    if target_level != "full":
        return []
    if (confirmed_note or "").strip():
        return []
    return [
        "⚠ 这是 P0 且一次性出三件套。三份产物同源生成容易互相一致而不正确 —— "
        "典型是把「创建成功」做成「返回 200」，三层全绿但没验到业务状态。"
        "建议先跟用户确认这个场景到底要验什么，再把确认内容用 "
        "expected_confirmed_by / expected_confirmed_note 带上来（平台只记录、不拦你）。"
    ]


# 原来这里还有第二道闸 check_p0_artifact：往已有 P0 用例上挂接口场景 / UI 脚本时，
# 没人确认过就拒收。一并去掉 —— 理由同上，确认挪到 CC 侧，平台只存不拦。
# 真要把拦截加回来就加在这里，判据是 case.expected_confirmed_note 是否为空。
