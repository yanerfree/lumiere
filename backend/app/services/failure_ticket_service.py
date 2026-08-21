"""失败跟进单的开、并、关。

**挂在执行记账的唯一写入口上**（script_run_service.record_run），
所以四条执行路径（单条调试/计划回归/批量/页面运行验证）自动都有，
不需要各自记得去调 —— 这个项目吃过"下次再加一条路径又漏记"的亏。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.failure_ticket import OPEN_STATUSES, FailureTicket


async def on_run(session: AsyncSession, run) -> FailureTicket | None:
    """一次执行记完账之后调。红了开单/累计，绿了关单。返回受影响的那张单。"""
    if run is None:
        return None
    passed = (run.status or "") == "passed"
    return (await _close_on_pass(session, run) if passed
            else await _open_or_bump(session, run))


async def _open_or_bump(session: AsyncSession, run) -> FailureTicket:
    ph = getattr(run, "failure_phenomenon", None) or "unknown"
    key = dict(case_id=run.case_id, script_type=run.script_type, phenomenon=ph)

    open_one = (await session.execute(
        select(FailureTicket).where(
            FailureTicket.case_id == run.case_id,
            FailureTicket.script_type == run.script_type,
            FailureTicket.phenomenon == ph,
            FailureTicket.status.in_(OPEN_STATUSES),
        ).order_by(FailureTicket.created_at.desc())
    )).scalars().first()
    if open_one is not None:
        # 没修好之前它本来就会一直红 —— 同一件事，只是又红了一次
        open_one.occurrences += 1
        open_one.last_run_id = run.id
        # 已经自称修好等复跑的，又红了 → 退回处置中（"改了但没修对"）
        if open_one.status == "verifying":
            open_one.status = "fixing"
        await session.flush()
        return open_one

    # 之前关过同样的单吗？关了又红 = **复发**，新开一张并挂上一张
    prev_closed = (await session.execute(
        select(FailureTicket).where(
            FailureTicket.case_id == run.case_id,
            FailureTicket.script_type == run.script_type,
            FailureTicket.phenomenon == ph,
            FailureTicket.status.in_(("closed", "known")),
        ).order_by(FailureTicket.closed_at.desc().nullslast(),
                   FailureTicket.created_at.desc())
    )).scalars().first()
    t = FailureTicket(
        **key, status="open", first_run_id=run.id, last_run_id=run.id, occurrences=1,
        reopened_from=prev_closed.id if prev_closed else None,
        recurrence=(prev_closed.recurrence + 1) if prev_closed else 0,
    )
    session.add(t)
    await session.flush()
    return t


async def _close_on_pass(session: AsyncSession, run) -> FailureTicket | None:
    """跑绿 → 关掉这条用例这一类的所有未关单，**记下凭哪一次跑绿关的**。

    「已知问题」(known) 不动 —— 那是人明确说过"知道它红、先不修"的，
    偶然绿一次不代表问题没了。
    """
    rows = (await session.execute(
        select(FailureTicket).where(
            FailureTicket.case_id == run.case_id,
            FailureTicket.script_type == run.script_type,
            FailureTicket.status.in_(OPEN_STATUSES),
        )
    )).scalars().all()
    last = None
    for t in rows:
        t.status = "closed"
        t.closed_by_run_id = run.id
        t.closed_reason = "复跑跑绿"
        t.closed_by = "platform"
        t.closed_at = datetime.now(timezone.utc)
        last = t
    if rows:
        await session.flush()
    return last


async def close_manually(session: AsyncSession, ticket_id, reason: str, actor: str,
                         known_issue: bool = False) -> FailureTicket | None:
    """人工关闭。**原因必填**（用户拍的）——
    没有原因的关闭等于把红的问题从看板上抹掉，下一轮又冒出来，谁都不知道上次为什么放过。
    `known_issue=True` 走「已知问题」：知道它红、先不修，之后偶然绿一次也不会被自动关掉。
    """
    if not (reason or "").strip():
        raise ValueError("手工关闭必须写原因")
    tid = ticket_id if isinstance(ticket_id, uuid.UUID) else uuid.UUID(str(ticket_id))
    t = await session.get(FailureTicket, tid)
    if t is None:
        return None
    t.status = "known" if known_issue else "closed"
    t.closed_reason = reason.strip()[:2000]
    t.closed_by = actor[:100]
    t.closed_at = datetime.now(timezone.utc)
    await session.flush()
    return t


def to_dict(t: FailureTicket, case_code: str | None = None) -> dict:
    return {
        "id": str(t.id), "caseId": str(t.case_id), "caseCode": case_code,
        "scriptType": t.script_type, "phenomenon": t.phenomenon, "status": t.status,
        "occurrences": t.occurrences, "recurrence": t.recurrence,
        "reopenedFrom": str(t.reopened_from) if t.reopened_from else None,
        "ccAnalysis": t.cc_analysis, "confirmedCause": t.confirmed_cause,
        "confirmedNote": t.confirmed_note, "confirmedBy": t.confirmed_by,
        "disposition": t.disposition,
        "closedReason": t.closed_reason, "closedBy": t.closed_by,
        "closedByRunId": str(t.closed_by_run_id) if t.closed_by_run_id else None,
        "firstAt": t.created_at.isoformat() if t.created_at else None,
        "lastAt": t.updated_at.isoformat() if t.updated_at else None,
    }
