"""项目级 i18n 词典采集器 —— 从生成的 Playwright 脚本里抽被测系统 UI 文案。

脚本里的选择器/断言硬编码了被测系统的中文文案（既是断言也是选择器），此模块用正则
把这些含中文的字面量抽出来，去重后写进 project_i18n_messages 词典（translations 留空，
英文以后在英文环境跑时再补）。为二期脚本 t() 运行时切换语种打数据底座。

不改 AI 生成逻辑；纯字面量匹配 + upsert，无外部依赖。
"""
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case
from app.models.i18n_message import ProjectI18nMessage
from app.models.project import Branch
from app.models.script import Script

logger = logging.getLogger(__name__)

_CJK = re.compile(r"[一-鿿]")

# 方法名两种写法都要认：JS/TS 是 getByRole，Python 是 get_by_role。
#
# 这里原来只写了驼峰。而平台上**所有**脚本都是 Python（回推通道走 pytest +
# playwright.sync_api，平台侧生成早已封存），所以这个采集器从上线起就一条也没抽到过
# ——实测扫 4 个脚本、added=0，页面上「扫描脚本采集」按了等于没按。
def _m(camel: str, snake: str) -> str:
    return f"(?:{camel}|{snake})"


# 各定位方式的字面量抽取规则：(正则, 分类, 文案捕获组序号)
# 说明：get_by_role 里 role 与 name 都要，name 才是文案，role 作分类。
# name 的写法 JS 是 `name: '导入'`、Python 是 `name="导入"`，所以分隔符收 [:=]。
_PATTERNS: list[tuple[re.Pattern, str | None, int]] = [
    # get_by_role("button", name="导入") / getByRole('button', { name: '导入' })
    (re.compile(
        _m("getByRole", "get_by_role")
        + r"""\(\s*(['"])([^'"]+)\1[^)]*?\bname\s*[:=]\s*(['"])(.+?)\3""", re.DOTALL), None, 4),
    # get_by_placeholder("请输入 用户名")
    (re.compile(_m("getByPlaceholder", "get_by_placeholder") + r"""\(\s*(['"])(.+?)\1"""), "placeholder", 2),
    # get_by_label("服务名称")
    (re.compile(_m("getByLabel", "get_by_label") + r"""\(\s*(['"])(.+?)\1"""), "label", 2),
    # get_by_title("删除")
    (re.compile(_m("getByTitle", "get_by_title") + r"""\(\s*(['"])(.+?)\1"""), "title", 2),
    # get_by_text("登录", exact=True)
    (re.compile(_m("getByText", "get_by_text") + r"""\(\s*(['"])(.+?)\1"""), "text", 2),
]

# getByRole 的 role → 分类映射（其余 role 归 text）
_ROLE_CATEGORY = {
    "button": "button", "tab": "tab", "link": "link",
    "menuitem": "menu", "option": "option", "heading": "text",
}


def _unescape(s: str) -> str:
    """还原字面量里被转义的引号/反斜杠（UI 文案极少用到，做个兜底）。"""
    return s.replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")


def extract_copy_literals(script: str) -> list[dict]:
    """从脚本文本抽出含中文的 UI 文案字面量。

    返回去重后的 [{"text": 中文原文, "category": 分类}]，同一文案首个分类胜出。
    """
    found: dict[str, str] = {}
    if not script:
        return []
    for pattern, fixed_cat, text_group in _PATTERNS:
        for m in pattern.finditer(script):
            text = _unescape(m.group(text_group)).strip()
            if not text or not _CJK.search(text):
                continue
            if fixed_cat is None:  # getByRole：分类由 role 推断
                role = (m.group(2) or "").lower()
                category = _ROLE_CATEGORY.get(role, "text")
            else:
                category = fixed_cat
            # 首次出现的分类胜出（getByRole 规则排在最前，优先级最高）
            found.setdefault(text, category)
    return [{"text": t, "category": c} for t, c in found.items()]


async def _upsert_literals(
    session: AsyncSession, project_id, literals: list[dict]
) -> int:
    """按 (project_id, key_text) 去重写入词典。新增则插入，已存在且分类为空则补齐分类。

    不 commit（交调用方），以便批量场景一次提交。返回新增条数。
    """
    if not literals:
        return 0
    rows = await session.execute(
        select(ProjectI18nMessage).where(ProjectI18nMessage.project_id == project_id)
    )
    existing = {r.key_text: r for r in rows.scalars().all()}
    added = 0
    for item in literals:
        text, category = item["text"], item.get("category")
        row = existing.get(text)
        if row is None:
            row = ProjectI18nMessage(
                project_id=project_id,
                key_text=text,
                translations={},
                category=category,
                source="harvested",
            )
            session.add(row)
            existing[text] = row  # 同批次去重
            added += 1
        elif category and not row.category:
            row.category = category  # 补齐已有条目缺失的分类
    await session.flush()
    return added


async def harvest_from_script(session: AsyncSession, project_id, script_content: str) -> int:
    """**已弃用**：以前用它把中文原文当键插词典，见 harvest_project 的说明。
    保留是因为平台侧生成钩子还在调它 —— 现在什么都不做，返回 0。
    """
    return 0


async def harvest_project(session: AsyncSession, project_id) -> dict:
    """扫该项目所有 UI 脚本，**报告哪些硬编码文案该换成哪个键** —— 不再造词条。

    **原来它拿中文原文当键往词典里插。那是错的**：中文既是键又是值，不对称；
    中文文案一改（「服务名已存在」→「服务名称已存在」），键就失效、静默退回原文，
    红都不红。而且插进去的 `translations` 是空的 —— t() 查不到译文就返回键，
    返回的正好是中文，**和没这条一模一样**，凭空多了一套不一致的键约定。

    现在它做一件真有用的事：把脚本里的中文反查成语言中立的键。
      · 对上了 → 这处该改成 t("<key>")，照着改就换语种无痛
      · 对不上 → **那正是脚本在英文环境会挂的地方**：要么被测系统自己硬编码了中文
        没走 i18n，要么脚本里的文案过期/是拼接出来的
    实测 33 条里 26 条能对上、7 条对不上。

    脚本归属链：Script.case_id → Case.branch_id → Branch.project_id。
    只读，不写库。
    """
    rows = await session.execute(
        select(Case.case_code, Script.content)
        .join(Case, Script.case_id == Case.id)
        .join(Branch, Case.branch_id == Branch.id)
        .where(Branch.project_id == project_id, Script.script_type == "ui")
    )
    per_script = [(code, c) for code, c in rows.all() if c]

    # 中文译文 → 键。同一句中文可能挂在多个键上，取第一个（字典序稳定）。
    zh2key: dict[str, str] = {}
    for row in (await session.execute(
        select(ProjectI18nMessage.key_text, ProjectI18nMessage.translations)
        .where(ProjectI18nMessage.project_id == project_id)
        .order_by(ProjectI18nMessage.key_text)
    )).all():
        zh = (row[1] or {}).get("zh-CN")
        if zh:
            zh2key.setdefault(zh, row[0])

    mapped: dict[str, dict] = {}
    unmapped: dict[str, dict] = {}
    for code, content in per_script:
        for item in extract_copy_literals(content):
            txt = item["text"]
            bucket = mapped if txt in zh2key else unmapped
            hit = bucket.setdefault(txt, {"text": txt, "category": item.get("category"),
                                          "cases": []})
            if txt in zh2key:
                hit["key"] = zh2key[txt]
            if code not in hit["cases"]:
                hit["cases"].append(code)

    return {
        "scanned": len(per_script),
        "mapped": sorted(mapped.values(), key=lambda x: x["text"]),
        "unmapped": sorted(unmapped.values(), key=lambda x: x["text"]),
        "hint": (f"{len(mapped)} 处能换成语言中立的键（照 key 改成 t(\"…\") 即可）；"
                 f"{len(unmapped)} 处在被测系统 locale 里找不到 —— "
                 f"那是英文环境下会挂的地方：要么被测系统硬编码了中文，"
                 f"要么脚本里的文案过期了。"),
    }


async def load_locale_table(session, project_id) -> dict[str, dict]:
    """取项目的 i18n 词典，形状 {中文文案: {语种: 译文}}，喂给沙箱的 t()。

    只取**有译文**的 —— 空 translations 的行注入进去只是让沙箱多背几百条噪音，
    t() 查到空还是得退回中文，结果一样。

    返回里**两种命名空间拼法都在**（见 ui_text_render.with_aliases）。
    """
    from sqlalchemy import select as _select

    from app.models.i18n_message import ProjectI18nMessage

    rows = (await session.execute(
        _select(ProjectI18nMessage.key_text, ProjectI18nMessage.translations)
        .where(ProjectI18nMessage.project_id == project_id)
    )).all()
    table = {k: v for k, v in rows
             if isinstance(v, dict) and any((x or "").strip() for x in v.values())}
    # 两种拼法互认（`ns:a.b` ↔ `ns.a.b`）—— 词典里是点号、脚本里按被测系统写冒号，
    # 不认的话查不到就静默退回中文：英文环境下测的其实是中文，一点红都没有。
    from app.services.ui_text_render import with_aliases
    return with_aliases(table)


async def load_locale_table_for_case(session, case_id) -> dict[str, dict]:
    """按用例取项目词典 —— **两条执行路径共用这一个**。

    原来页面那条（api/scripts.py）自己查一遍，MCP 那条（mcp/tools/ui_scripts.py）
    压根没查 —— 而 CC 走的正是 MCP 这条。后果：脚本里的 `t("services.list.xxx")`
    查不到表，**原样返回那串键**，选择器拿键去匹配必然找不到元素，
    整条链红在「element not found」上，谁都看不出是词典没注入。
    实测就是这么撞上的（语种演示那条用例第一次跑）。

    取不到就返回空表 —— t() 会原样返回 ref，中文当 ref 时正好还是对的。
    """
    try:
        from app.models.case import Case
        from app.models.project import Branch

        case = await session.get(Case, case_id)
        branch = await session.get(Branch, case.branch_id) if case else None
        return await load_locale_table(session, branch.project_id) if branch else {}
    except Exception:  # noqa: BLE001
        return {}
