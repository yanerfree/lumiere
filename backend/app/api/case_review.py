"""AI 评审端点 —— 单条、批量。目标是替掉人工那道「待审」。

批量刻意做成**逐条评**（并发有限），不是"把一个模块塞进一次 prompt 让它整体评"：
后者出来的是"缺少安全测试场景"这类放到哪个项目都成立的话（上一版就是这么做的，
用户看完的评价是"不适用"）。逐条评贵一些，但每条结论都能指到具体步骤。
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy import func, select
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


# ── 批量审核：入队 + 轮询，不再是一次长 POST ──────────────────────

async def _resolve_env(session, project_id, env_id):
    """挑这次在哪个环境上跑。**环境是结论的一部分**（§5）——
    测试环境过了不等于预发环境也过，所以它要跟着批次一起落库。

    没配环境的项目**直接拦住**，而不是跑起来然后 16 条全标「无法审核」：
    后者白烧一遍 AI 调用，还让人以为是用例坏了。
    """
    from app.models.environment import Environment, EnvironmentVariable

    if env_id:
        e = await session.get(Environment, uuid.UUID(str(env_id)))
        if e is None or str(e.project_id) != str(project_id):
            raise AppError(code="ENV_NOT_FOUND", message="环境不存在或不属于这个项目",
                           status_code=400)
        return e

    # 单条/默认：不每次都问人。挑「有 BASE_URL 且 sort_order 最小」的那个 ——
    # 没有 BASE_URL 的环境跑出来是空地址的垃圾运行，会被报成「这条挂了」。
    e = (await session.execute(
        select(Environment)
        .join(EnvironmentVariable, EnvironmentVariable.environment_id == Environment.id)
        .where(Environment.project_id == project_id,
               EnvironmentVariable.key == "BASE_URL", EnvironmentVariable.value != "")
        .order_by(Environment.sort_order, Environment.created_at).limit(1))).scalars().first()
    if e is None:
        has_any = (await session.execute(
            select(func.count(Environment.id))
            .where(Environment.project_id == project_id))).scalar_one()
        raise AppError(
            code="NO_ENVIRONMENT",
            message=("这个项目还没配环境，先去「项目设置 → 环境」加一个再来审核。"
                     if not has_any else
                     "现有环境都没有 BASE_URL —— 没有地址跑不了，先把 BASE_URL 填上。"),
            status_code=400)
    return e


@router.post("/ai-review/batch")
async def review_batch(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_ids: list[uuid.UUID] | None = Body(default=None, embed=True, alias="caseIds"),
    folder_id: uuid.UUID | None = Body(default=None, embed=True, alias="folderId"),
    env_id: uuid.UUID | None = Body(default=None, embed=True, alias="envId"),
    kind: str | None = Body(default=None, embed=True),
    scope: str = Body(default="all", embed=True),
    with_checkup: bool = Body(default=True, embed=True, alias="withCheckup"),
    actor_kind: str = Body(default="human", embed=True, alias="actorKind"),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """发起一次审核。**立刻返回**，真正的活在队列里跑（review-spec §5）。

    以前这里是一次同步长 POST：30 条实测跑满 5 分钟，这五分钟里人不能碰浏览器 ——
    刷新一下就再也找不回这一批在跑什么。现在建一条 `review_batches` 记录就返回，
    进度和结论都在库里，关掉页面照样跑完。

    类型（§4）由「你从哪儿发起的」推出来，不让人额外选：勾了 N 条 = 抽审；
    选中模块一条没勾 = 模块全量/增量；详情页发起 = 单条。
    **只有「模块全量」能代表这个模块的情况** —— 否则挑三条好的一审，
    就能宣布模块没问题。
    """
    from app.models.case import CaseFolder
    from app.services.review import queue

    # ── 体检：不跑用例，只看模块覆盖。不占队列、不用环境 ──
    if kind == "checkup":
        if not folder_id:
            raise AppError(code="NO_FOLDER", message="体检要指定模块", status_code=400)
        folder = await session.get(CaseFolder, folder_id)
        batch, _m = await queue.enqueue(
            session, project_id=project_id, branch_id=branch_id, kind="checkup",
            case_ids=[], folder_id=folder_id,
            scope_label=(folder.name if folder else None),
            actor=current_user.username, actor_kind=actor_kind, with_checkup=True)
        return {"data": {"batchId": str(batch.id), "kind": "checkup", "total": 0}}

    if not case_ids:
        stmt = select(Case.id).where(Case.branch_id == branch_id, Case.deleted_at.is_(None))
        if folder_id:
            stmt = stmt.where(Case.folder_id == folder_id)
        if scope == "incremental":
            # 「只审没审过的和被打回的」。**无法审核的也算没审过** ——
            # 它上次就是因为环境不行才没得出结论，正是这次该补的那批。
            stmt = stmt.where(
                (Case.review_status.is_(None))
                | (Case.review_status.in_(("pending", "rejected", "inconclusive"))))
        case_ids = [r[0] for r in (await session.execute(stmt.limit(MAX_BATCH + 1))).all()]
        inferred = "module_incremental" if scope == "incremental" else "module_full"
    else:
        inferred = "single" if len(case_ids) == 1 else "sample"

    if not case_ids:
        raise AppError(code="NO_CASES", message="没有可评审的用例", status_code=400)
    truncated = len(case_ids) > MAX_BATCH
    case_ids = case_ids[:MAX_BATCH]

    env = await _resolve_env(session, project_id, env_id)

    folder = await session.get(CaseFolder, folder_id) if folder_id else None
    label = (f"{folder.name} {len(case_ids)} 条" if folder else f"{len(case_ids)} 条")
    if inferred == "single":
        one = await session.get(Case, case_ids[0])
        label = one.case_code if one else label

    batch, merged = await queue.enqueue(
        session, project_id=project_id, branch_id=branch_id, kind=(kind or inferred),
        case_ids=case_ids, folder_id=folder_id, scope_label=label,
        environment_id=env.id, environment_name=env.name,
        actor=current_user.username, actor_kind=actor_kind,
        with_checkup=with_checkup and inferred.startswith("module"))

    return {"data": {
        "batchId": str(batch.id), "kind": batch.kind, "total": batch.total,
        "scopeLabel": batch.scope_label, "environment": env.name,
        # 被合并掉的要说出来 —— 静默少审几条，页面上和"审完了"长得一样
        "merged": merged, "truncated": truncated,
        "note": (f"其中 {len(merged)} 条已经在别的批次里排着了，这次不重复跑"
                 if merged else None),
    }}


def _batch_dict(b, items=None) -> dict:
    out = {
        "batchId": str(b.id), "kind": b.kind, "scopeLabel": b.scope_label,
        "environment": b.environment_name,
        "environmentId": str(b.environment_id) if b.environment_id else None,
        "actor": b.actor, "actorKind": b.actor_kind, "status": b.status,
        "total": b.total, "done": b.done, "approved": b.approved,
        "rejected": b.rejected, "inconclusive": b.inconclusive, "failed": b.failed,
        "current": b.current_case_code, "note": b.note, "report": b.report,
        "withCheckup": b.with_checkup,
        "createdAt": b.created_at.isoformat() if b.created_at else None,
        "startedAt": b.started_at.isoformat() if b.started_at else None,
        "finishedAt": b.finished_at.isoformat() if b.finished_at else None,
        # 轮询侧靠它停下来。不能让客户端拿 done==total 去猜 ——
        # total 为 0、或者中途熔断时那个猜法不成立。
        "finished": b.status in ("done", "partial", "cancelled"),
    }
    if items is not None:
        out["items"] = [{
            "caseId": str(i.case_id), "caseCode": i.case_code, "status": i.status,
            "verdict": i.verdict, "runState": i.run_state, "error": i.error,
        } for i in items]
    return out


@router.get("/ai-review/batch/{batch_id}")
async def review_batch_progress(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """这一批审到第几条了。**查库** —— 以前进度只在发起它的那个进程的内存里，
    刷新页面就丢、重启就消失。现在关掉页面回来照样看得到。
    """
    from app.models.review_batch import ReviewBatch
    b = await session.get(ReviewBatch, batch_id)
    if b is None:
        return {"data": {"known": False, "note": "没有这一批"}}
    return {"data": {"known": True, **_batch_dict(b)}}


@router.get("/ai-review/batches")
async def list_review_batches(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    mine: bool = Query(default=True),
    limit: int = Query(default=50, le=200),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """审核报告页：**一行一次审核**（§6）。

    以前这页是"一行一个模块"，塞不进类型，点一行只是跳到筛好的用例列表 ——
    那是筛选器，不叫报告。

    `mine=true`（默认）只看人发起的：CC 每次回推都会自审，一天几十条，
    全混在一起就找不到自己点的那次了。
    """
    from app.models.review_batch import ReviewBatch
    from app.services.review import queue

    stmt = select(ReviewBatch).where(ReviewBatch.branch_id == branch_id)
    if mine:
        stmt = stmt.where(ReviewBatch.actor_kind == "human")
    rows = (await session.execute(
        stmt.order_by(ReviewBatch.created_at.desc()).limit(limit))).scalars().all()
    return {"data": {"batches": [_batch_dict(b) for b in rows],
                     "queue": await queue.queue_view(session, branch_id)}}


@router.get("/ai-review/batches/{batch_id}")
async def review_batch_detail(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """模块报告（§7）：结论 + 共性问题 + 覆盖缺口 + 逐条结果。

    **价值在共性问题和覆盖缺口**：一条一条看只知道"这条不行"；
    看模块才知道"这一整片都犯同一个错"和"这个模块压根没测到的地方"。
    """
    from app.models.review_batch import ReviewBatch, ReviewBatchItem
    b = await session.get(ReviewBatch, batch_id)
    if b is None:
        raise AppError(code="NOT_FOUND", message="没有这一批", status_code=404)
    items = (await session.execute(
        select(ReviewBatchItem).where(ReviewBatchItem.batch_id == b.id)
        .order_by(ReviewBatchItem.case_code))).scalars().all()
    d = _batch_dict(b, items)
    # 只有「模块全量」能代表这个模块的情况（§4）。这句话要跟着报告走 ——
    # 不写的话，抽审三条的报告过两周会被当成"这个模块审过了"。
    d["representative"] = b.kind == "module_full"
    d["scopeNote"] = ("这次审了这个模块的全部用例，可以代表模块情况"
                      if b.kind == "module_full" else
                      "这次只审了列出来的这几条，**不能代表整个模块**")
    return {"data": d}


@router.post("/ai-review/batches/{batch_id}/cancel")
async def cancel_review_batch(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """取消。正在跑的那条会做完再停 —— 跑到一半掐断会留半截数据，
    下一批撞上它又是一轮假打回。"""
    from app.services.review import queue
    if not await queue.cancel(session, batch_id):
        raise AppError(code="NOT_CANCELLABLE", message="这批已经结束了，取消不了",
                       status_code=400)
    return {"data": {"cancelled": True}}


@router.post("/ai-review/batches/{batch_id}/resume")
async def resume_review_batch(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """熔断暂停之后，人确认环境好了，接着跑剩下的（不用重新发起、不重跑已审的）。"""
    from app.services.review import queue
    if not await queue.resume(session, batch_id):
        raise AppError(code="NOT_PAUSED", message="这批不是暂停状态", status_code=400)
    return {"data": {"resumed": True}}

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
