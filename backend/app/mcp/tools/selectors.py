"""项目级选择器登记表 —— 登记 / 查看 / 盯住缺 testid 的口子。

**思想一句话：选择器是项目的公共资产，不是每条用例自己的私货。**
前端改一个名字，改登记表一行，全项目的脚本跟着好；写在正文里就得逐条改 N 遍，
而改漏了当场不报错 —— 等某次回归红了才发现，那时已经分不清是产品坏了还是脚本过期。

第二条、也是更硬的一条：**产品缺 testid 时该改的是产品，不是退而写脆弱选择器。**
所以登记表有 `status='gap'` 这一档，专门给"前端还没给抓手"留痕，
并把卡在上面的用例记进 blocked_cases，进 `lum_next_duty` 的待办。
不留痕的话「没抓手」就只是一句口头的"以后再说"，然后永远没有以后 ——
因为它不在任何队列、不出现在任何数字上，**不写这条用例是零成本的**。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.selector import ProjectSelector
from app.services.ui_selector_render import infer_kind

_STATUS = ("active", "gap")

# 这一行是**谁登记的**。`manual` 是人（或 CC 代人）一条条手改的，`crawl` 是
# 页面枚举自动扫出来的（S6.5）。区分它俩只为一条规则：**自动的不许压过手改的。**
# 爬取每次都跑，人是一次一次改的 —— 让爬取覆盖掉手改，等于那次手改从没发生过，
# 而且不留任何痕迹（下一趟又会把它改回去，查都查不出是谁改的）。
_SOURCES = ("manual", "crawl")

# 稳定性从高到低。登记时按这个给建议，不硬拦 —— 有些页面确实只有结构可抓，
# 硬拦会逼人去登记表里塞假 testid，比脆弱选择器更坏（它看起来是稳的）。
_KIND_ADVICE = {
    "testid": None,
    "id": None,
    "role": None,
    "semantic": None,
    "structure": "结构定位（父子/nth）—— 布局一改就飘。能让前端补个 testid 就补。",
    "text": "文案定位 —— 换语种必挂。选择器值里请写成 ${键|中文} 占位，"
            "平台按 TEST_LANGUAGE 替换（先替选择器、再替文案，这个顺序是对的）。",
    "style": "**样式类是最脆的一档** —— 它是给人看好看的，改版随手就变"
             "（antd v5→v6 类名整批换过）。这是权宜之计，不是终点："
             "去被测前端补 data-testid 并提 MR，补完回来把这行换掉。",
}


async def _project_of_branch(session: AsyncSession, branch_id) -> uuid.UUID | None:
    from app.models.project import Branch
    b = await session.get(Branch, branch_id)
    return b.project_id if b else None


async def upsert_selectors(session: AsyncSession, project_id: str,
                           items: list, *, source: str = "manual") -> dict:
    """登记/更新项目的选择器（按 key upsert）。

    items 每条：{key(必填), selector, kind, module, description, status, gap_note, blocked_cases}

    `source="crawl"` 是页面枚举在调（S6.5）：这时**人工登记过的行一条都不动**，
    对不上的地方由 `qa_survey_byproducts.disagreements` 列成「爬到的与登记不符」
    给人看。MCP 那个入口永远是 `manual`，参数不外露。
    """
    if source not in _SOURCES:
        return {"error": f"source 只能是 {_SOURCES}，收到 {source}"}
    try:
        pid = uuid.UUID(project_id)
    except (ValueError, AttributeError, TypeError):
        return {"error": f"project_id 不是合法 UUID: {project_id}"}
    if not items:
        return {"error": "items 是空的 —— 要登记的是选择器条目列表。"}

    existing = {r.key: r for r in (await session.execute(
        select(ProjectSelector).where(ProjectSelector.project_id == pid)
    )).scalars().all()}

    saved, problems, advice, skipped = [], [], [], []
    for i, raw in enumerate(items):
        it = raw if isinstance(raw, dict) else {}
        key = str(it.get("key") or "").strip()
        if not key:
            problems.append(f"第 {i + 1} 条没有 key")
            continue
        if len(key) > 200:
            problems.append(f"{key[:30]}… 的 key 超过 200 字")
            continue
        status = str(it.get("status") or "active").strip()
        if status not in _STATUS:
            problems.append(f"{key}: status 只能是 active / gap，收到 {status}")
            continue
        selector = (it.get("selector") or "").strip() or None
        gap_note = (it.get("gap_note") or "").strip() or None

        if status == "active" and not selector:
            problems.append(
                f"{key}: status=active 却没给 selector —— 空的选择器替进脚本，"
                f"「不应出现」那类断言会**假绿**。真的还没抓手就写 status='gap'。")
            continue
        if status == "gap":
            if not gap_note:
                problems.append(
                    f"{key}: status=gap 必须写 gap_note —— 缺什么、在哪个面板、"
                    f"MR 提了没有。不写的话过两周没人知道它当初卡在哪，"
                    f"这条缺口就等于没记。")
                continue
            # gap 行**故意不保留凑合的选择器**：留着它，下一个人会直接拿去用，
            # 于是"等前端补 testid"永远不会发生。
            selector = None

        kind = (it.get("kind") or "").strip() or (infer_kind(selector) if selector else "style")
        row = existing.get(key)
        if row is not None and source != "manual" and row.source == "manual":
            # **人工登记过的绝不覆盖。** 人手改过这一行，说明爬到的那个值不对
            # （或者指的根本是另一个控件）；让每次都跑的爬取把它改回去，
            # 那次手改就等于没发生，而且下一趟还会再来一遍。
            skipped.append(key)
            continue
        if row is None:
            row = ProjectSelector(project_id=pid, key=key)
            session.add(row)
            existing[key] = row
        row.selector, row.kind, row.status, row.gap_note = selector, kind, status, gap_note
        if it.get("module") is not None:
            row.module = str(it["module"])[:64] or None
        if it.get("description") is not None:
            row.description = str(it["description"]) or None
        bc = it.get("blocked_cases")
        if bc is not None:
            row.blocked_cases = [str(x) for x in bc if str(x).strip()]
        row.source = source

        saved.append({"key": key, "kind": kind, "status": status})
        tip = _KIND_ADVICE.get(kind)
        if tip and status == "active":
            advice.append(f"{key}（{kind}）：{tip}")

    if problems and not saved and not skipped:
        return {"error": "一条都没登记成，先改掉这些：", "problems": problems}
    await session.commit()

    gaps = [s for s in saved if s["status"] == "gap"]
    out = {
        "status": "ok",
        "saved": len(saved),
        "items": saved,
        "引用写法": '在 UI 脚本里写 page.locator("${SEL:' + (saved[0]["key"] if saved else "模块.元素") + '}")'
                    " —— 平台执行前替换成登记表里那条；本地跑用 lum_render_ui_script 渲一份。",
    }
    if problems:
        out["未登记"] = problems
    if skipped:
        out["跳过（人工登记过的不覆盖）"] = skipped
    if advice:
        out["建议"] = advice
    if gaps:
        out["缺口已留痕"] = (
            f"{len(gaps)} 条 status=gap —— 它们会出现在 lum_next_duty 的「待补 testid」队列里。"
            f"**下一步是去被测前端仓补 data-testid 并提 MR**，不是在这儿凑合一个样式类。"
            f"MR 合了回来把 selector 填上、status 改 active，"
            f"blocked_cases 里那几条用例会自动变成「回来写 UI」的待办。")
    return out


async def list_selectors(session: AsyncSession, project_id: str,
                         module: str | None = None,
                         status: str | None = None) -> dict:
    """看项目的选择器登记表 + 还欠着的两笔账（缺 testid 的口子、正文里的字面选择器）。"""
    try:
        pid = uuid.UUID(project_id)
    except (ValueError, AttributeError, TypeError):
        return {"error": f"project_id 不是合法 UUID: {project_id}"}

    q = select(ProjectSelector).where(ProjectSelector.project_id == pid)
    if module:
        q = q.where(ProjectSelector.module == module)
    if status:
        q = q.where(ProjectSelector.status == status)
    rows = (await session.execute(q.order_by(ProjectSelector.key))).scalars().all()

    by_kind: dict[str, int] = {}
    for r in rows:
        if r.status == "active":
            by_kind[r.kind] = by_kind.get(r.kind, 0) + 1

    listed = [{"key": r.key, "selector": r.selector, "kind": r.kind,
               "status": r.status, "module": r.module,
               **({"gap_note": r.gap_note} if r.gap_note else {}),
               **({"blockedCases": r.blocked_cases} if r.blocked_cases else {})}
              for r in rows]

    out = {
        "project_id": project_id,
        "total": len(rows),
        "按稳定性": by_kind or None,
        "selectors": listed,
        "引用写法": 'page.locator("${SEL:模块.元素}")  —— 平台执行前替换；'
                    "本地跑先 lum_render_ui_script 渲一份",
    }
    if by_kind.get("style"):
        out["注意"] = (
            f"{by_kind['style']} 条登记的是**样式类**（最脆的一档）—— 能用，但改版随手就变。"
            f"把它们逐条换成 data-testid 是有回报的活：去被测前端补、提 MR、回来改这一行，"
            f"全项目的脚本跟着好。")

    out["待整改"] = await _pending_migration(session, pid)
    差 = await _crawl_disagreements(session, pid)
    if 差:
        out["待整改·爬到的与登记不符"] = 差
    return out


async def _crawl_disagreements(session: AsyncSession, pid: uuid.UUID) -> dict | None:
    """人工登记的那几行，跟最近一趟页面枚举爬到的对不上（S6.5）。

    **每次现算，不落库。** 落一张"冲突表"就有了第二份数据，而它会过期：
    人把登记改对了、或者下一趟爬取跟登记一致了，那张表还留着一条谁也不敢删的记录。
    现算的话两边任意一边修好，这一条自己从清单上消失 —— 跟 `_pending_migration`
    是同一个自清套路。
    """
    from app.services.qa_survey_byproducts import (
        candidates_from_items,
        disagreements,
        latest_survey,
    )

    survey = await latest_survey(session, pid)
    if survey is None:
        return None
    from app.models.qa_page_survey import QaPageSurveyItem
    items = (await session.execute(
        select(QaPageSurveyItem).where(QaPageSurveyItem.survey_id == survey.id)
    )).scalars().all()
    rows = (await session.execute(
        select(ProjectSelector).where(ProjectSelector.project_id == pid))).scalars().all()
    existing = {r.key: {"selector": r.selector, "status": r.status,
                        "kind": r.kind, "source": r.source} for r in rows}
    差 = disagreements(candidates_from_items(items), existing)
    if not 差:
        return None
    return {
        "条数": len(差), "surveyId": str(survey.id), "明细": 差[:20],
        "怎么改": ("**平台没有自动改任何一行** —— 人工登记过的行爬取一条都不动。"
                   "一条条看：爬到的对就把登记改过来，登记的对就说明爬取那条指的是"
                   "另一个控件（换个 key）。"),
    }


async def _pending_migration(session: AsyncSession, pid: uuid.UUID) -> dict | None:
    """项目里还有哪些 UI 脚本在正文里写死脆弱选择器 —— 这就是"整改清单"。

    自清：脚本改成 `${SEL:...}` 回推之后，它自己从这份清单上消失。
    """
    from app.models.case import Case
    from app.models.project import Branch
    from app.models.script import Script
    from app.services.ui_selector_render import fragile_literals

    scripts = (await session.execute(
        select(Script, Case.case_code, Case.title)
        .join(Case, Case.id == Script.case_id)
        .join(Branch, Branch.id == Case.branch_id)
        .where(Branch.project_id == pid, Script.script_type == "ui",
               Script.status == "active", Case.deleted_at.is_(None))
    )).all()

    dirty, total_hits = [], 0
    for sc, code, title in scripts:
        hits = fragile_literals(sc.content or "")
        if hits:
            total_hits += len(hits)
            dirty.append({"caseCode": code, "caseId": str(sc.case_id),
                          "title": (title or "")[:40],
                          "字面选择器": hits[:6], "共": len(hits)})
    if not dirty:
        return None
    dirty.sort(key=lambda d: -d["共"])
    return {
        "脚本数": len(dirty), "总处数": total_hits, "扫过的脚本": len(scripts),
        "明细": dirty[:20],
        "怎么改": ("① 这些选择器逐个登记进来（lum_upsert_selectors）；"
                   "② 脚本正文换成 ${SEL:键} 再 lum_sync_ui_script 回推；"
                   "③ 换不掉的（前端压根没抓手）登记成 status='gap' 并写 blocked_cases —— "
                   "**别硬塞样式类当 active**，那是把脆弱性藏进公共资产里。"),
    }


async def selector_gaps_for_branch(session: AsyncSession, branch_id) -> tuple[list, list]:
    """给 lum_next_duty 用：(还缺 testid 的口子, testid 补齐了、该回来写 UI 的用例)。

    第二个队列是这套机制的关键 —— uag-qa 那条纪律的完整样子是
    「缺抓手 → 记账 → 自己去前端补并提 MR → **回来写用例**」，
    绝大多数人（和 AI）会漏掉最后一步：MR 合了就当这件事结了。
    所以补齐之后被卡的用例要自己冒出来，一直冒到真的推了 UI 脚本为止。
    """
    from app.models.case import Case
    from app.models.script import Script

    pid = await _project_of_branch(session, branch_id)
    if not pid:
        return [], []
    rows = (await session.execute(
        select(ProjectSelector).where(ProjectSelector.project_id == pid)
    )).scalars().all()
    if not rows:
        return [], []

    still_gap = [{"选择器键": r.key, "模块": r.module, "缺什么": r.gap_note,
                  "卡住的用例": r.blocked_cases or [], "来源": r.source,
                  "下一步": ("去被测前端仓补 data-testid 并提 MR。**别在登记表里塞样式类凑合** —— "
                             "MR 合了回来 lum_upsert_selectors 把 selector 填上、status 改 active。")}
                 for r in rows if r.status == "gap"]
    # **有人卡在上面的排前面，页面枚举自动扫出来的排后面。**
    # S6.5 之后这个队列里绝大多数是爬取扫出来的文案锚点 —— 图标按钮和纯文案按钮
    # 到处都是，一次爬取能扫出几百条。它们该留痕（这就是 gap 这一档的用处），
    # 但**不能把真正有人等着的那几条挤出展示层的前 N 条**：
    # `lum_next_duty` 只列 `[:limit]`，挤出去就等于没记。
    # 计数照旧是全量（`test_队列数字不许被limit截小`），只动顺序。
    still_gap.sort(key=lambda g: (not g["卡住的用例"], g["来源"] != "manual",
                                  g["选择器键"]))

    # 补齐了、但当初被它卡住的用例还没有 UI 脚本 → 「回来写 UI」
    waiting_codes: dict[str, str] = {}
    for r in rows:
        if r.status == "active":
            for code in (r.blocked_cases or []):
                waiting_codes.setdefault(str(code), r.key)
    resume = []
    if waiting_codes:
        cases = (await session.execute(
            select(Case).where(Case.branch_id == branch_id,
                               Case.case_code.in_(list(waiting_codes)),
                               Case.deleted_at.is_(None),
                               Case.lifecycle_status != "deprecated")
        )).scalars().all()
        for c in cases:
            has_ui = (await session.execute(
                select(Script.id).where(Script.case_id == c.id,
                                        Script.script_type == "ui",
                                        Script.status == "active").limit(1)
            )).scalar_one_or_none()
            if has_ui:
                continue
            resume.append({
                "caseCode": c.case_code, "caseId": str(c.id), "title": (c.title or "")[:40],
                "当初卡在": waiting_codes[c.case_code],
                "下一步": ("testid 已经补齐了 —— 回来把这条的 UI 脚本写掉再 lum_sync_ui_script。"
                           "推上来它就自己从这个队列消失。"),
            })
    # **不在这儿截断。** 截了的话 next_duty 的 summary 数字会跟着变小 ——
    # 11 个缺口显示成 "待补 testid: 3"，看着像快做完了。
    # 展示层的 [:limit] 只该管列多少条，不该管"到底欠多少"。
    return still_gap, resume
