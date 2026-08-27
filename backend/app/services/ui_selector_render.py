"""UI 脚本里的选择器占位：`${SEL:用例列表.新建按钮}`。

和文案占位（`${键|中文}`，见 ui_text_render）是同一套机制的另一半：
**外部取值一律走平台注入，脚本正文里不留字面量。**
数据（BASE_URL/凭据）早就硬拦了，文案后来补上了，选择器是最后一块 ——
而它恰恰是最容易过期的一块：前端改一个类名，这边 18 条脚本要逐条改，
改漏了当场不报错，等回归红了才发现。

替换发生在**执行前的源码文本**上（和文案、`os.getenv` 默认值同一处），
所以本地渲染出来的那一份文件跑的是同一个结果。

**先替选择器、再替文案** —— 登记表里的选择器值本身可以带文案占位
（`text=${services.action.more|更多}`），顺序反了那层就换不掉。

`${SEL:...}` 里的东西**不是文案键**：ui_text_render.text_key() 里对 `SEL:` 前缀
做了排除。不排的话 `SEL:用例列表.新建按钮` 会被当成命名空间为 SEL 的文案键，
被文案门禁硬拦成"这个键词典里没有"，而人根本找不到该去哪登记。
"""
from __future__ import annotations

import re

# `${SEL:键}`。键允许中文和点号（建议 `模块.元素` 两段式），不允许空白和右花括号。
SEL_RE = re.compile(r"\$\{SEL:([^}\s]+)\}")


def refs(content: str) -> list[str]:
    """脚本引用了哪些选择器键（去重、保持出现顺序）。"""
    out: list[str] = []
    for m in SEL_RE.finditer(content):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def _escape(text: str) -> str:
    """替换进源码字符串字面量里 —— 反斜杠和两种引号都得转。

    选择器里带引号是常态（`[data-testid="x"]`），不转的话替进双引号串直接语法错误。
    """
    return (text.replace("\\", "\\\\").replace('"', '\\"')
                .replace("'", "\\'").replace("\n", "\\n").replace("\r", ""))


def render(content: str, table: dict[str, dict]) -> tuple[str, dict]:
    """把 `${SEL:键}` 换成登记表里那条选择器。

    返回 (渲染后的脚本, {"resolved": [...], "gap": [...], "missing": [...]})
      · resolved  登记表命中且可用
      · gap       登记了、但还是 status='gap'（前端没给抓手，selector 是空的）
      · missing   登记表里压根没有

    gap 和 missing 都**原样留着那串 `${SEL:}`** —— 后面 unresolved() 会拦死它。
    故意不静默替成空串或键名：那样正例红在「找不到元素」上还看得见，
    而「不应出现」那类负例会**假绿**（匹配不到任何元素，"不该存在"当然成立）。
    这个坑文案那边已经真踩过一次（一趟里正例红了、两条负例全绿），不再踩第二遍。
    """
    stat: dict[str, list[str]] = {"resolved": [], "gap": [], "missing": []}

    def one(m: re.Match) -> str:
        key = m.group(1)
        row = table.get(key)
        if row is None:
            stat["missing"].append(key)
            return m.group(0)
        val = (row.get("selector") or "").strip()
        if not val or row.get("status") == "gap":
            stat["gap"].append(key)
            return m.group(0)
        stat["resolved"].append(key)
        return _escape(val)

    return SEL_RE.sub(one, content), stat


def unresolved(content: str) -> list[str]:
    """正文里还剩哪些没解析的选择器占位 —— 拦在执行之前用。

    理由和文案那边逐字相同：跑了也没意义，正例红在「找不到元素」上，
    负例假绿。顺带把"某条执行路径压根忘了渲染"一起拦住（这库栽过一次：
    词典只在一条路注入，另一条静默跑字面量）。
    """
    return refs(content)


def unresolved_hint(content: str, table: dict[str, dict] | None = None) -> str:
    """没解析出来该怎么修 —— 区分「没登记」和「登记了但还缺 testid」。

    这两件事的下一步完全不同：前者是你自己补一行登记；
    后者是**去被测前端补 testid 并提 MR**，在 MR 合进去之前这条 UI 用例写不了。
    混成一句"去登记一下"会把人引向在登记表里硬塞一个脆弱选择器 ——
    那正是这套机制要防的事。
    """
    table = table or {}
    left = refs(content)
    gaps = [k for k in left if (table.get(k) or {}).get("status") == "gap"]
    miss = [k for k in left if k not in gaps]
    parts = []
    if miss:
        parts.append(
            f"{len(miss)} 个键登记表里没有（{'、'.join(miss[:3])}…）——"
            f"用 lum_upsert_selectors(project_id, items=[{{key, selector, kind}}]) 补上。")
    if gaps:
        notes = "；".join(
            f"{k}: {(table.get(k) or {}).get('gap_note') or '未写缺口说明'}" for k in gaps[:2])
        parts.append(
            f"{len(gaps)} 个键**登记了但还是 gap**（{notes}）—— 这不是登记漏了，"
            f"是**被测前端还没给抓手**。正确做法是去前端仓补 data-testid 并提 MR，"
            f"合进去之后回来把 selector 填上、status 改 active，这条用例才写得了。"
            f"**别在登记表里硬塞一个样式类凑合** —— 那等于把脆弱性藏进了公共资产里，"
            f"下次它挂了没人知道当初是凑合的。")
    return " ".join(parts)


# ── 稳定性等级 ───────────────────────────────────────────────────────────────
#
# 排序就是推荐顺序。testid/id/role 语言无关、且是**给测试用的**，前端改它会被
# 当成契约变更；style 是最脆的一档 —— 样式类是给人看好看的，改版随手就变
# （antd v5→v6 类名整批换过），拿它当测试抓手就是在赌前端不重构。
_KIND_ORDER = ["testid", "id", "role", "semantic", "structure", "text", "style"]

_CLASS_TOKEN = re.compile(r"(?:^|[\s>~+(,])\.[A-Za-z_][\w-]*|[A-Za-z]+\.[A-Za-z_][\w-]*")


def infer_kind(selector: str) -> str:
    """从选择器字面量猜它属于哪一档 —— 登记时没给 kind 就用它。"""
    s = selector or ""
    if "data-testid" in s or "data-test-id" in s:
        return "testid"
    if re.search(r"(?:^|[\s>~+(,])#[A-Za-z_]", s):
        return "id"
    if "role=" in s or s.startswith("role"):
        return "role"
    if re.search(r"\[(?:aria-|data-)", s):
        return "semantic"
    if "text=" in s or ":has-text" in s or "getByText" in s:
        return "text"
    if _CLASS_TOKEN.search(s):
        return "style"
    return "structure"


# 定位 API 的字符串参数 —— 只扫这些，普通字符串和注释不算（脚本头的说明里
# 出现 `.card` 不该被报）。
_LOCATOR_CALL = re.compile(
    r"""(?:\.locator|\.query_selector_all|\.query_selector|\.wait_for_selector
        |locator|querySelector)\(\s*(['"])(?P<sel>.+?)\1""",
    re.X)


def fragile_literals(content: str) -> list[str]:
    """正文里写死的**脆弱**选择器（样式类），去重保序。

    判据：定位 API 的字符串参数里出现类选择器，且整串里没有 testid / #id /
    aria- / data- 这类稳定抓手。`.ant-*` 这种组件库内部类名尤其脆。
    """
    out: list[str] = []
    for m in _LOCATOR_CALL.finditer(content):
        sel = m.group("sel")
        if infer_kind(sel) != "style":
            continue
        if sel not in out:
            out.append(sel)
    return out


def testid_literals(content: str) -> list[str]:
    """正文里写死的 testid 选择器 —— 不脆，但仍该登记（改名时只改一处）。"""
    out: list[str] = []
    for m in _LOCATOR_CALL.finditer(content):
        sel = m.group("sel")
        if infer_kind(sel) == "testid" and sel not in out:
            out.append(sel)
    return out


async def load_table(session, project_id) -> dict[str, dict]:
    """项目的选择器登记表 → {键: {"selector":..., "kind":..., "status":..., "gap_note":...}}"""
    import uuid as _uuid

    from sqlalchemy import select

    from app.models.selector import ProjectSelector

    pid = project_id if isinstance(project_id, _uuid.UUID) else _uuid.UUID(str(project_id))
    rows = (await session.execute(
        select(ProjectSelector).where(ProjectSelector.project_id == pid)
    )).scalars().all()
    return {r.key: {"selector": r.selector, "kind": r.kind, "status": r.status,
                    "gap_note": r.gap_note, "module": r.module} for r in rows}


async def load_table_for_case(session, case_id) -> dict[str, dict]:
    """用例 → 分支 → 项目 → 登记表。取不到就返回空表（不抛：拿不到词典不该拖垮执行，
    真缺了后面 unresolved() 那道拦截会说清楚）。"""
    import uuid as _uuid

    from app.models.case import Case
    from app.models.project import Branch

    try:
        cid = case_id if isinstance(case_id, _uuid.UUID) else _uuid.UUID(str(case_id))
        case = await session.get(Case, cid)
        branch = await session.get(Branch, case.branch_id) if case else None
        if not branch:
            return {}
        return await load_table(session, branch.project_id)
    except Exception:  # noqa: BLE001
        return {}


async def render_for_case(session, case_id, content: str) -> tuple[str, dict, dict]:
    """按用例取登记表并替换 —— 各条执行路径统一走这一个入口。

    返回 (新正文, stat, 登记表)。登记表一并返回是给 unresolved_hint() 用的：
    「没登记」和「登记了但还缺 testid」下一步完全不同，不带表就分不出来。

    **每条执行路径都要调它。** 这库栽过一次同型的坑：文案词典只在一条路注入，
    另一条静默跑字面量 —— 而"没渲染"和"词典没这条"在结果上长得一模一样。
    """
    table = await load_table_for_case(session, case_id)
    content, stat = render(content, table)
    return content, stat, table
