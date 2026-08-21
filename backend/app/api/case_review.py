"""AI 评审端点 —— 单条、批量。目标是替掉人工那道「待审」。

批量刻意做成**逐条评**（并发有限），不是"把一个模块塞进一次 prompt 让它整体评"：
后者出来的是"缺少安全测试场景"这类放到哪个项目都成立的话（上一版就是这么做的，
用户看完的评价是"不适用"）。逐条评贵一些，但每条结论都能指到具体步骤。
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.deps.auth import require_project_role
from app.deps.db import get_db
from app.models.case import Case
from app.models.user import User
from app.services.ai_config_resolver import resolve_ai_config
from app.services.review import reviewer

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/projects/{project_id}/branches/{branch_id}",
    tags=["review"],
)

MAX_BATCH = 30          # 一次最多评这么多条 —— 再多就该分模块评，报告也没人看得完


async def _config(project_id: uuid.UUID, session: AsyncSession):
    cfg = await resolve_ai_config(project_id, session, capability="tb-quality-review")
    if not cfg:
        raise AppError(code="AI_NOT_CONFIGURED", message="AI 服务未配置", status_code=503)
    return cfg


@router.post("/cases/{case_id}/ai-review")
async def review_one(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    run_first: bool = Query(default=False, alias="runFirst"),
    env_id: str | None = Query(default=None, alias="envId"),
    persist: bool = Query(default=True),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """评审一条用例。runFirst=true 会先真跑一遍接口场景再评（debug 模式，不进通过率）。"""
    cfg = await _config(project_id, session)
    out = await reviewer.review_case(session, case_id, ai_config=cfg,
                                    persist=persist, run_first=run_first, env_id=env_id)
    if out.get("error"):
        raise AppError(code="REVIEW_FAILED", message=out["error"], status_code=502)
    return {"data": out}


@router.post("/ai-review/batch")
async def review_batch(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_ids: list[uuid.UUID] | None = Body(default=None, embed=True, alias="caseIds"),
    folder_id: uuid.UUID | None = Body(default=None, embed=True, alias="folderId"),
    persist: bool = Body(default=True, embed=True),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """按勾选或按模块批量评审。逐条评，返回每条结论 + 汇总。"""
    cfg = await _config(project_id, session)

    if not case_ids:
        stmt = select(Case.id).where(Case.branch_id == branch_id, Case.deleted_at.is_(None))
        if folder_id:
            stmt = stmt.where(Case.folder_id == folder_id)
        case_ids = [r[0] for r in (await session.execute(stmt.limit(MAX_BATCH + 1))).all()]

    if not case_ids:
        raise AppError(code="NO_CASES", message="没有可评审的用例", status_code=400)
    truncated = len(case_ids) > MAX_BATCH
    case_ids = case_ids[:MAX_BATCH]

    results = []
    # 并发 3：评审是长请求，网关有 429（见 docs/ai-gateway-and-models.md），
    # 一次并发十几条会把限流打满、整批失败。
    sem = asyncio.Semaphore(3)

    async def one(cid):
        async with sem:
            try:
                return await reviewer.review_case(session, cid, ai_config=cfg, persist=persist)
            except Exception as e:  # noqa: BLE001
                logger.exception("评审失败 case=%s", cid)
                return {"caseId": str(cid), "error": str(e)[:200]}

    for cid in case_ids:            # 串行提交、并发受 sem 控制；session 不是线程安全的
        results.append(await one(cid))

    ok = [r for r in results if not r.get("error")]
    return {"data": {
        "total": len(results),
        "approved": len([r for r in ok if r.get("verdict") == "approved"]),
        "rejected": len([r for r in ok if r.get("verdict") == "rejected"]),
        "failed": len(results) - len(ok),
        "avgScore": round(sum(r.get("total", 0) for r in ok) / max(len(ok), 1)),
        "blockerCases": [r["caseCode"] for r in ok if r.get("blockerCount")],
        "truncated": truncated,
        "results": results,
    }}

@router.get("/cases/{case_id}/review-rounds")
async def review_rounds(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """这条用例的审核历史：每轮 AI 审的结论 / CC 的整改提交 / 人工覆盖。"""
    from app.services.review import rounds
    case = await session.get(Case, case_id)
    if case is None:
        raise AppError(code="NOT_FOUND", message="用例不存在", status_code=404)
    return {"data": {"status": await rounds.display_status(session, case),
                     "rounds": await rounds.list_rounds(session, case_id)}}


@router.post("/cases/{case_id}/review-override")
async def review_override(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    verdict: str = Query(..., pattern="^(approved|rejected)$"),
    reason: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """人工覆盖 AI 的结论。**记成一轮**，不悄悄改状态 ——
    人推翻了机器的判断，这件事本身就是要留痕的信息。"""
    from app.services.review import rounds
    case = await session.get(Case, case_id)
    if case is None:
        raise AppError(code="NOT_FOUND", message="用例不存在", status_code=404)
    case.review_status = verdict
    case.review_reason = {"category": "human_override", "text": reason or "人工判定",
                          "by": current_user.username}
    await rounds.record(session, case_id, "human_override", verdict=verdict,
                        summary=reason, actor=current_user.username)
    await session.commit()
    return {"data": {"verdict": verdict}}


@router.get("/review-report")
async def review_report(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """**模块审核报告**：按模块聚合 + 覆盖缺口去重合并。

    覆盖缺口原来每条用例各存一份，散在 review_reason 里没人看得见 ——
    而它是唯一指向"该补哪些用例"的东西。合并之后「越权被 3 条提到」
    就是一条明确的补测清单。
    """
    from app.models.case import CaseFolder
    from app.models.review_round import CaseReviewRound
    from app.services.review import rounds as rounds_svc

    cases = (await session.execute(
        select(Case).where(Case.branch_id == branch_id, Case.deleted_at.is_(None))
    )).scalars().all()
    folders = {f.id: f for f in (await session.execute(
        select(CaseFolder).where(CaseFolder.branch_id == branch_id))).scalars().all()}
    latest_kind = {}
    for r in (await session.execute(
            select(CaseReviewRound).order_by(CaseReviewRound.round.asc()))).scalars().all():
        latest_kind[r.case_id] = r.kind

    mods: dict[str, dict] = {}
    for c in cases:
        f = folders.get(c.folder_id)
        name = (f.path.split("/")[0] if f else None) or "（未归类）"
        m = mods.setdefault(name, {"module": name, "total": 0, "approved": 0, "rejected": 0,
                                   "resubmitted": 0, "pending": 0, "notReviewed": 0,
                                   "scores": [], "gaps": {}, "lastAt": None})
        m["total"] += 1
        if latest_kind.get(c.id) == "cc_resubmit":
            m["resubmitted"] += 1
        elif c.review_status == "approved":
            m["approved"] += 1
        elif c.review_status == "rejected":
            m["rejected"] += 1
        elif c.review_status == "pending":
            m["pending"] += 1
        else:
            m["notReviewed"] += 1
        q = c.quality_score or {}
        if q.get("total") is not None:
            m["scores"].append(q["total"])
            at = q.get("reviewedAt")
            if at and (m["lastAt"] is None or at > m["lastAt"]):
                m["lastAt"] = at
        for g in ((c.review_reason or {}).get("coverageGaps") or []):
            # 去重按"缺口的头 12 个字"归并 —— 同一件事各条的措辞不会完全一样
            key = str(g).replace("模块级缺口：", "").replace("模块级：", "")[:12]
            slot = m["gaps"].setdefault(key, {"gap": str(g)[:200], "count": 0, "cases": []})
            slot["count"] += 1
            slot["cases"].append(c.case_code)

    out = []
    for m in mods.values():
        gaps = sorted(m.pop("gaps").values(), key=lambda x: -x["count"])
        reviewed = m["approved"] + m["rejected"] + m["resubmitted"]
        m["avgScore"] = round(sum(m["scores"]) / len(m["scores"])) if m["scores"] else None
        m.pop("scores")
        m["status"] = ("未审" if reviewed == 0 else
                       "整改中" if (m["rejected"] or m["resubmitted"]) else "通过")
        m["gaps"] = gaps[:8]
        out.append(m)
    out.sort(key=lambda x: (x["status"] != "整改中", -x["total"]))
    return {"data": {"modules": out,
                     "usage": "覆盖缺口是各条审核时提的模块级缺口去重合并后的结果 —— "
                              "被提到次数多的，说明这个模块真的缺这一类用例。"}}

# ── 失败跟进单 ──────────────────────────────────────────────────

@router.get("/failure-tickets")
async def list_failure_tickets(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    report_id: uuid.UUID | None = Query(default=None, alias="reportId"),
    only_open: bool = Query(default=True, alias="onlyOpen"),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """失败跟进单。传 reportId 只看这次报告里红的那些。

    「已关闭」默认不列 —— 看板要显示的是**还没了结的**；要看历史传 onlyOpen=false。
    """
    from app.models.failure_ticket import OPEN_STATUSES, FailureTicket
    from app.models.report import TestReportScenario
    from app.models.script import ScriptRun
    from app.services import failure_ticket_service as svc

    stmt = (select(FailureTicket, Case.case_code, Case.title)
            .join(Case, Case.id == FailureTicket.case_id)
            .where(Case.branch_id == branch_id))
    if only_open:
        stmt = stmt.where(FailureTicket.status.in_(OPEN_STATUSES))
    if report_id:
        # 这次报告里的那些：跟进单的最近一次执行落在这份报告的场景上
        runs = select(ScriptRun.id).join(
            TestReportScenario, TestReportScenario.id == ScriptRun.report_scenario_id
        ).where(TestReportScenario.report_id == report_id)
        stmt = stmt.where(FailureTicket.last_run_id.in_(runs))
    rows = (await session.execute(
        stmt.order_by(FailureTicket.recurrence.desc(), FailureTicket.occurrences.desc())
        .limit(200))).all()
    return {"data": [{**svc.to_dict(t, code), "title": title} for t, code, title in rows]}


@router.post("/failure-tickets/{ticket_id}/close")
async def close_failure_ticket(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    ticket_id: uuid.UUID,
    reason: str = Body(..., embed=True, min_length=2),
    known_issue: bool = Body(default=False, embed=True, alias="knownIssue"),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """人工关单。**原因必填**（schema 上就卡住，不靠前端自觉）。

    `knownIssue=true` 走「已知问题」：知道它红、先不修，
    之后偶然绿一次也不会被自动关掉 —— 那种绿说明不了问题没了。
    """
    from app.services import failure_ticket_service as svc
    try:
        t = await svc.close_manually(session, ticket_id, reason=reason,
                                     actor=current_user.username, known_issue=known_issue)
    except ValueError as e:
        raise AppError(code="REASON_REQUIRED", message=str(e), status_code=400) from e
    if t is None:
        raise AppError(code="NOT_FOUND", message="跟进单不存在", status_code=404)
    await session.commit()
    return {"data": {"status": t.status, "closedReason": t.closed_reason}}
