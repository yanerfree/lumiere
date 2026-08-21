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

# 批量审核的进度台账。**为什么需要它**：批量是一次长 POST（30 条 × 逐条读断言和脚本，
# 实测跑满 5 分钟），弹窗只能显示"逐条评审中…"—— 没有 n/N、也没有完成态。
# 而每条都是**审完就落库**的，人从详情页看得到轮次已经出来了，弹窗还在转：
# 看起来像卡死，实际早就在干活。客户端自己带 batchId 进来，边跑边轮询这个台账。
# 放内存里够了：进度是过程量，重启丢了无所谓（结论在库里）。
_BATCH_PROGRESS: dict[str, dict] = {}
_BATCH_KEEP = 20        # 只留最近这么多批，别让字典无界长


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
    batch_id: str | None = Body(default=None, embed=True, alias="batchId"),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """按勾选或按模块批量评审。逐条评，返回每条结论 + 汇总。

    传 `batchId`（客户端自己生成）就能边跑边用 GET /ai-review/batch/{batchId} 看进度 ——
    这一跑要几分钟，不给进度的话弹窗和卡死长得一模一样。
    """
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

    prog = None
    if batch_id:
        while len(_BATCH_PROGRESS) >= _BATCH_KEEP:
            _BATCH_PROGRESS.pop(next(iter(_BATCH_PROGRESS)), None)
        prog = _BATCH_PROGRESS.setdefault(str(batch_id), {})
        prog.update({"total": len(case_ids), "done": 0, "approved": 0, "rejected": 0,
                     "failed": 0, "current": None, "finished": False})

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
        r = await one(cid)
        results.append(r)
        if prog is not None:
            prog["done"] = len(results)
            prog["current"] = r.get("caseCode") or str(cid)
            if r.get("error"):
                prog["failed"] += 1
            elif r.get("verdict") == "approved":
                prog["approved"] += 1
            else:
                prog["rejected"] += 1

    ok = [r for r in results if not r.get("error")]
    if prog is not None:
        # 完成态要**立刻**写上：轮询侧靠它停下来，不然只能靠"done==total"猜，
        # 而 total 为 0 或中途出错时那个猜法不成立。
        prog["finished"] = True
        prog["current"] = None
    return {"data": {
        "total": len(results),
        "batchId": str(batch_id) if batch_id else None,
        "approved": len([r for r in ok if r.get("verdict") == "approved"]),
        "rejected": len([r for r in ok if r.get("verdict") == "rejected"]),
        "failed": len(results) - len(ok),
        "avgScore": round(sum(r.get("total", 0) for r in ok) / max(len(ok), 1)),
        "blockerCases": [r["caseCode"] for r in ok if r.get("blockerCount")],
        "truncated": truncated,
        "results": results,
    }}


@router.get("/ai-review/batch/{batch_id}")
async def review_batch_progress(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    batch_id: str,
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """这一批审到第几条了。**不查库** —— 进度是过程量，只在发起它的那个进程里。

    查不到分两种，必须分清：批次还没登记（POST 刚发出、还没进到循环）
    和批次已经跑完被清掉了。前者继续等，后者别再等了。
    """
    p = _BATCH_PROGRESS.get(str(batch_id))
    if p is None:
        return {"data": {"known": False,
                         "note": "这一批没在这个进程里（刚发起还没登记，或已经跑完被清掉了）"}}
    return {"data": {"known": True, **p}}

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


@router.get("/deprecate-pending")
async def list_deprecate_pending(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """这个分支上挂着「待废审」等人拍板的。列表页的徽标和详情页的提示条都读它。

    **一条一条点，不做批量。** 误废一条用例，那块功能就再没人测了，
    而且**永远不报错** —— 没有任何信号会说"这里本来该有覆盖"。
    批量确认按钮的存在本身就是在鼓励不看证据就点过去。
    """
    rows = (await session.execute(
        select(Case).where(Case.branch_id == branch_id, Case.deleted_at.is_(None),
                           Case.deprecate_status == "requested")
        .order_by(Case.case_code)
    )).scalars().all()
    return {"data": [{
        "id": str(c.id), "caseCode": c.case_code, "title": c.title,
        "targetLevel": c.target_level,
        "reason": (c.deprecate_reason or {}).get("reason"),
        "evidence": (c.deprecate_reason or {}).get("evidence"),
        "requestedBy": (c.deprecate_reason or {}).get("requestedBy"),
        "requestedAt": (c.deprecate_reason or {}).get("requestedAt"),
        "note": (c.deprecate_reason or {}).get("note"),
        "platformProbe": (c.deprecate_reason or {}).get("platformProbe"),
    } for c in rows]}


@router.post("/cases/{case_id}/deprecate-decide")
async def deprecate_decide(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    approve: bool = Query(...),
    note: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """人确认或驳回一条废弃请求。批准才落 lifecycle_status=deprecated。

    驳回的语义是「这是要改，不是要废」—— 用例回到要改堆，不是被否掉。
    """
    from app.services import branch_diff_review

    out = await branch_diff_review.decide_deprecate(
        session, case_id, approve=approve, note=note,
        user_id=current_user.id, actor=current_user.username,
    )
    if out.get("error"):
        raise AppError(code="DEPRECATE_DECIDE_FAILED", message=out["error"], status_code=400)
    await session.commit()
    return {"data": out}


@router.post("/cases/{case_id}/deprecate-undo")
async def deprecate_undo(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """撤销废弃，回草稿。**废弃可逆是 AI 敢直接批准的前提之一**，所以这条必须有。"""
    from app.services import branch_diff_review

    out = await branch_diff_review.undo_deprecate(session, case_id)
    if out.get("error"):
        raise AppError(code="DEPRECATE_UNDO_FAILED", message=out["error"], status_code=400)
    await session.commit()
    return {"data": out}


@router.get("/branch-diff")
async def branch_diff_summary(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """这个分支对过账没有、分了几堆。分支复制窗口和用例列表的提示条读它。"""
    from app.models.endpoint_diff import EndpointDiffBatch, EndpointDiffHit

    batches = (await session.execute(
        select(EndpointDiffBatch).where(EndpointDiffBatch.branch_id == branch_id)
        .order_by(EndpointDiffBatch.created_at)
    )).scalars().all()
    if not batches:
        return {"data": {"reconciled": False}}

    hit_ids = set((await session.execute(
        select(EndpointDiffHit.case_id)
        .where(EndpointDiffHit.batch_id.in_([b.id for b in batches]))
    )).scalars().all())
    total = (await session.execute(
        select(Case).where(Case.branch_id == branch_id, Case.deleted_at.is_(None))
    )).scalars().all()
    pending_new: list = []
    for b in batches:
        pending_new.extend(b.pending_new or [])
    return {"data": {
        "reconciled": True,
        "batches": len(batches),
        "lastAt": batches[-1].created_at.isoformat() if batches[-1].created_at else None,
        "fromRef": batches[-1].from_ref, "toRef": batches[-1].to_ref,
        "revise": len([c for c in total if c.id in hit_ids]),
        "reuse": len([c for c in total if c.id not in hit_ids
                      and c.lifecycle_status != "deprecated"]),
        "pendingNew": len(pending_new),
        "deprecated": len([c for c in total if c.lifecycle_status == "deprecated"]),
        "pendingDeprecate": len([c for c in total if c.deprecate_status == "requested"]),
    }}


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
                                   "scores": [], "gaps": [], "lastAt": None})
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
            # 归并按**话题**，不按字面。原来的键是"头 12 个字"，而 LLM 每轮措辞都不一样 ——
            # 同一件事（越权）被拆成三条各 1×，而这一列存在的理由就是那个 count。
            # 见 review/gap_merge.py。
            m["gaps"].append((str(g), c.case_code))

    from app.services.review.gap_merge import merge as _merge_gaps
    out = []
    for m in mods.values():
        gaps, gaps_total = _merge_gaps(m.pop("gaps"), top=8)
        reviewed = m["approved"] + m["rejected"] + m["resubmitted"]
        m["avgScore"] = round(sum(m["scores"]) / len(m["scores"])) if m["scores"] else None
        m.pop("scores")
        m["status"] = ("未审" if reviewed == 0 else
                       "整改中" if (m["rejected"] or m["resubmitted"]) else "通过")
        m["gaps"] = gaps
        # 砍没砍过要说出来 —— 静默截断在页面上和"就这几类"长得一样。
        m["gapsTotal"] = gaps_total
        out.append(m)
    out.sort(key=lambda x: (x["status"] != "整改中", -x["total"]))
    return {"data": {"modules": out,
                     "usage": "覆盖缺口按**话题**归并（越权/并发/边界/…），不按字面 —— "
                              "被提到次数多的，说明这个模块真的缺这一类用例。"
                              "每桶的 phrasings 是各条的原话，用来核对这几条是不是真的一件事；"
                              "gapsTotal > 桶数说明列表被截到了前 8 个。"}}

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
