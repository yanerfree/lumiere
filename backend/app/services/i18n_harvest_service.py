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
    """采集单个脚本的文案（生成钩子用）。不 commit，交调用方。返回新增条数。"""
    return await _upsert_literals(session, project_id, extract_copy_literals(script_content))


async def harvest_project(session: AsyncSession, project_id) -> dict:
    """扫该项目所有 UI 脚本，批量采集文案入词典（前端「扫描脚本采集」用）。

    脚本归属链：Script.case_id → Case.branch_id → Branch.project_id。
    返回 {"added": 新增条数, "scanned": 扫描脚本数}。调用方负责 commit。
    """
    rows = await session.execute(
        select(Script.content)
        .join(Case, Script.case_id == Case.id)
        .join(Branch, Case.branch_id == Branch.id)
        .where(Branch.project_id == project_id, Script.script_type == "ui")
    )
    contents = [c for (c,) in rows.all() if c]
    # 汇总所有脚本的文案后一次性 upsert，避免逐脚本重复查库
    merged: dict[str, str] = {}
    for content in contents:
        for item in extract_copy_literals(content):
            merged.setdefault(item["text"], item["category"])
    literals = [{"text": t, "category": c} for t, c in merged.items()]
    added = await _upsert_literals(session, project_id, literals)
    return {"added": added, "scanned": len(contents)}


async def load_locale_table(session, project_id) -> dict[str, dict]:
    """取项目的 i18n 词典，形状 {中文文案: {语种: 译文}}，喂给沙箱的 t()。

    只取**有译文**的 —— 空 translations 的行注入进去只是让沙箱多背几百条噪音，
    t() 查到空还是得退回中文，结果一样。
    """
    from sqlalchemy import select as _select

    from app.models.i18n_message import ProjectI18nMessage

    rows = (await session.execute(
        _select(ProjectI18nMessage.key_text, ProjectI18nMessage.translations)
        .where(ProjectI18nMessage.project_id == project_id)
    )).all()
    return {k: v for k, v in rows if isinstance(v, dict) and any((x or "").strip() for x in v.values())}
