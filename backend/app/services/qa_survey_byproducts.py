"""页面枚举的两个副产品 —— 都是**复用现有出口**，不新增第二份数据（S6.5）。

1. 爬到的锚点 → 选择器登记表（`project_selectors` + `${SEL:键}` 那一整套：
   `lum_upsert_selectors` 登记、`lum_list_selectors` 看账、`status='gap'` 进
   `lum_next_duty` 的「待补 testid」队列）。**不新建"爬到的选择器"那张表。**
2. 模块体检的 `observed_actions` → 不传时从 survey 表读（今天靠 CC 手抄，上限 40 条）。

两件事共用一句前提：**「最近一趟能用的枚举」**。
`running` 那趟正在往里写，读它得到的是半份清单 —— 同一个模块连着体检两次会给出
不一样的缺口，而这份东西的全部说服力就在"两次一样"。`failed` 那趟没跑到终点。
所以只认 `done` / `partial` / `dirty`：
- `partial` 认，因为它**少**而不是**错**。少几条只是少几条提示。
- `dirty`（只读爬完但环境里的数变了）也认：那是「我们动了什么」的警报，
  可它**看见的控件仍然是真看见的**，把这一趟的观测整个扔掉换不来任何安全。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.qa_page_survey import QaPageSurvey, QaPageSurveyItem
from app.services.ui_selector_render import selector_of_item

USABLE_STATUSES = ("done", "partial", "dirty")

# 稳定性够高、可以直接登记成 `active` 的几档；其余（text / style / structure）
# 一律落 `gap`。**不稳的抓手进了公共资产比没有更坏** —— 它看起来是有的，
# 于是没人再去给前端提补 testid 的 MR，而它每次改版都会飘。
_STABLE_KINDS = ("testid", "id", "role", "semantic")

_KIND_CN = {"text": "文案", "style": "样式类", "structure": "结构"}


def _g(obj, name, default=""):
    """item 可能是爬取产出的 dict，也可能是从库里读出来的 ORM 行。"""
    if isinstance(obj, dict):
        v = obj.get(name)
    else:
        v = getattr(obj, name, None)
    return default if v is None else v


# ── 纯函数：爬到的锚点 → 登记表候选行 ──────────────────────────────────────


def candidate_key(page_path: str, anchor: str) -> str:
    """登记表的 key：`页面.锚点`。

    **取的是锚点原值，不是文案。** testid 锚的那些行，前端改文案 key 不动，
    脚本里那句 `${SEL:...}` 就一直有效。只能靠文案锚的行做不到这一点
    （它的原值就是文案），但那种行本来就是 `gap`、没有 selector 可以被引用，
    改文案让它换一行 key 不会让任何脚本失效。
    """
    page = (page_path or "").strip().strip("/").replace("/", ".") or "页面"
    return f"{page}.{(anchor or '').strip()}"[:200]


def candidates_from_items(items) -> list[dict]:
    """把一趟 survey 的 item 变成 `lum_upsert_selectors` 收得下的候选行。

    输出直接喂给 `upsert_selectors`（那边做校验），顺序按 key 排死 ——
    同一趟跑两次登记必须产生同样的东西。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for it in items or []:
        anchor = str(_g(it, "anchor")).strip()
        kind = str(_g(it, "anchor_kind")).strip()
        if not anchor:
            # 锚不住的控件根本不该进 survey（`collect_items` 只记数不出行），
            # 这里再挡一次：没有名字的行登记进去只会占一个谁也引用不了的 key。
            continue
        key = candidate_key(str(_g(it, "page_path")), anchor)
        if key in seen:
            continue
        seen.add(key)

        page = str(_g(it, "page_path"))
        label = str(_g(it, "label")) or anchor
        ctype = str(_g(it, "control_type")) or "控件"
        selector = selector_of_item(anchor=anchor, anchor_kind=kind)
        desc = f"页面枚举爬到的：{page} 上的「{label}」（{ctype}）"

        if kind in _STABLE_KINDS and selector:
            out.append({"key": key, "selector": selector, "kind": kind,
                        "module": page[:64] or None, "status": "active",
                        "description": desc})
        else:
            # **只能靠文案/样式定位的登记成 gap，而不是把凑合的选择器存下来。**
            # 存下来的话下一个人会直接拿去用，于是"去前端补 testid"永远不会发生。
            out.append({"key": key, "selector": None,
                        "kind": kind or "text",
                        "module": page[:64] or None, "status": "gap",
                        "description": desc,
                        "gap_note": (
                            f"页面枚举这一趟只能靠{_KIND_CN.get(kind, kind or '文案')}"
                            f"定位「{label}」（{page}）—— 换语种/改版就会飘。"
                            f"去被测前端补 data-testid 并提 MR，"
                            f"回来把 selector 填上、status 改 active。")})
    out.sort(key=lambda c: c["key"])
    return out


def disagreements(candidates, existing) -> list[dict]:
    """人工登记过的行里，爬到的跟登记的对不上的那些。**只报，不覆盖。**

    `existing` 形如 `{key: {"selector":…, "status":…, "kind":…, "source":…}}`。

    **只报"看得清"的那个方向。** 爬到的锚点比登记的弱（登记的是 testid、
    这一趟只找到文案），最可能的解释是这一趟没看清 —— 渲染时机、当前角色、
    列表空状态，而不是前端把 testid 拿掉了。报出来的话人会去查一个不存在的改动，
    查两次之后这份清单就没人信了。跟 `removed` 那条降级是同一条纪律：
    **信号弱的时候不许下结论。**
    """
    out: list[dict] = []
    for c in candidates or []:
        ex = (existing or {}).get(c["key"])
        if not ex or str(ex.get("source") or "manual") != "manual":
            continue
        ex_sel = (ex.get("selector") or "").strip() or None
        ex_status = str(ex.get("status") or "active")

        if ex_status == "gap" and c["status"] == "active":
            out.append({
                "key": c["key"], "登记的": "gap（当时还没有抓手）",
                "爬到的": c["selector"], "kind": c["kind"],
                "怎么回事": ("前端**可能已经补上抓手了** —— 回来把这行的 selector 填上、"
                             "status 改 active，被它卡住的用例会自己冒出来提醒写 UI。"),
            })
            continue

        if c["status"] != "active" or not ex_sel:
            # 爬到的更弱（或登记的本来就没有选择器可比）—— 见上面那段，不报。
            continue
        if c["kind"] not in _STABLE_KINDS:
            continue
        if ex_sel != c["selector"]:
            out.append({
                "key": c["key"], "登记的": ex_sel, "爬到的": c["selector"],
                "kind": c["kind"],
                "怎么回事": ("两边都是稳定抓手却对不上 —— 要么前端换了 testid、"
                             "要么这条登记的是另一个控件。**没有自动改**，"
                             "人看一眼再决定改哪边。"),
            })
    return out


# ── 纯函数：survey → 模块体检的 observed_actions ──────────────────────────


def module_pages(module: str, items) -> set[str]:
    """这个模块对应哪几个页面 —— **纯字面匹配，认不出就一页都不给。**

    survey 里没有域码也没有模块（爬取按角色分片，一趟横跨多个域），
    真正的映射链（页面控件 → 请求 → 路由组 → 域码）是 Epic 7 的事。
    这里只做一件保守的事：模块名和页面标题互为子串就算这一页属于它。

    **认不出时返回空集，而不是"整个产品的控件"。** 把别的模块的按钮塞进这个
    模块的缺口分析，产出的是「这个模块没测导出功能」这种查一次就发现根本不存在的
    假缺口 —— 正是这一版要治的病。给不出就退回今天的行为（模型只看用例标题），
    不多不少。
    """
    m = (module or "").strip()
    if not m:
        return set()
    pages: set[str] = set()
    for it in items or []:
        title = str(_g(it, "page_title")).strip()
        path = str(_g(it, "page_path")).strip()
        if title and (m in title or title in m):
            pages.add(path)
        elif path and m in path:
            pages.add(path)
    return pages


def observed_lines(items) -> list[str]:
    """一个可操作项一行，顺序排死 —— 同一趟 survey 体检两次必须一模一样。"""
    out: list[str] = []
    seen: set[str] = set()
    rows = sorted(items or [], key=lambda it: (str(_g(it, "page_path")),
                                               str(_g(it, "label")),
                                               str(_g(it, "key"))))
    for it in rows:
        where = str(_g(it, "page_title")) or str(_g(it, "page_path"))
        what = str(_g(it, "label")) or str(_g(it, "anchor"))
        ctype = str(_g(it, "control_type")) or "控件"
        line = f"{where} · {what}（{ctype}）"
        if line not in seen:
            seen.add(line)
            out.append(line)
    return out


# ── 读库 ──────────────────────────────────────────────────────────────────


async def latest_survey(session, project_id, survey_id=None) -> QaPageSurvey | None:
    """最近一趟**跑到终点**的枚举。指定了 survey_id 就取那一趟（仍要求它跑到终点）。"""
    pid = uuid.UUID(str(project_id))
    q = select(QaPageSurvey).where(QaPageSurvey.project_id == pid,
                                   QaPageSurvey.status.in_(USABLE_STATUSES))
    if survey_id:
        q = q.where(QaPageSurvey.id == uuid.UUID(str(survey_id)))
    q = q.order_by(QaPageSurvey.started_at.desc())
    return (await session.execute(q)).scalars().first()


async def _items_of(session, survey_id) -> list[QaPageSurveyItem]:
    return list((await session.execute(
        select(QaPageSurveyItem).where(QaPageSurveyItem.survey_id == survey_id)
        .order_by(QaPageSurveyItem.key))).scalars().all())


async def register_selectors(session, project_id, *, survey_id=None) -> dict:
    """把最近一趟爬到的锚点登记进选择器表。

    **走 `upsert_selectors`，不自己写行。** 那边攒着这张表的全部不变量
    （active 必须有 selector、gap 必须有 gap_note、gap 行故意不留凑合的选择器）；
    在这里另写一个写入器的话，两个写入器迟早会飘，而飘掉的那天没有任何报错 ——
    只是登记表里多出一批 status=active 却没有选择器的行，脚本替进去之后
    「不应出现」那类断言集体假绿。

    **人工登记过的绝不覆盖**：那条规则在 `upsert_selectors(source=...)` 里，
    对不上的地方由 `disagreements` 单独列出来给人看。
    """
    from app.mcp.tools.selectors import upsert_selectors
    from app.models.selector import ProjectSelector

    survey = await latest_survey(session, project_id, survey_id=survey_id)
    if survey is None:
        return {"status": "skipped",
                "reason": ("这个项目还没有跑到终点的页面枚举 —— "
                           f"只认 {'/'.join(USABLE_STATUSES)} 的那几趟。")}

    items = await _items_of(session, survey.id)
    candidates = candidates_from_items(items)
    if not candidates:
        return {"status": "skipped", "surveyId": str(survey.id),
                "reason": "这一趟一个能锚住的控件都没爬到。"}

    rows = (await session.execute(
        select(ProjectSelector).where(
            ProjectSelector.project_id == uuid.UUID(str(project_id))))).scalars().all()
    existing = {r.key: {"selector": r.selector, "status": r.status,
                        "kind": r.kind, "source": r.source} for r in rows}
    差 = disagreements(candidates, existing)

    res = await upsert_selectors(session, str(project_id), candidates, source="crawl")
    res["surveyId"] = str(survey.id)
    res["surveyStatus"] = survey.status
    if 差:
        res["爬到的与登记不符"] = 差
    return res


async def observed_actions_for_module(session, project_id, module: str) -> list[str]:
    """模块体检的 `observed_actions` 兜底：从最近一趟枚举里读这个模块的页面。

    对不上页面就返回空 —— 见 `module_pages` 那段，宁可没有也不要串台。
    """
    survey = await latest_survey(session, project_id)
    if survey is None:
        return []
    items = await _items_of(session, survey.id)
    pages = module_pages(module, items)
    if not pages:
        return []
    return observed_lines([it for it in items if it.page_path in pages])
