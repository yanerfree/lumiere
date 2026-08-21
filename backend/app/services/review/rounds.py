"""审核轮次的读写 —— 让「跟进到哪了」这件事有据可查。"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.review_round import CaseReviewRound


async def _next_round(session: AsyncSession, case_id: uuid.UUID) -> int:
    n = (await session.execute(
        select(func.max(CaseReviewRound.round)).where(CaseReviewRound.case_id == case_id)
    )).scalar_one_or_none()
    return (n or 0) + 1


async def record(session: AsyncSession, case_id, kind: str, **kw) -> CaseReviewRound:
    """记一轮。**不 commit** —— 让调用方跟它自己那笔改动在同一个事务里，
    否则会出现"审核记录写进去了、用例状态没改"这种半截状态。"""
    cid = case_id if isinstance(case_id, uuid.UUID) else uuid.UUID(str(case_id))
    row = CaseReviewRound(case_id=cid, round=await _next_round(session, cid), kind=kind,
                          **{k: v for k, v in kw.items() if v is not None})
    session.add(row)
    await session.flush()
    return row


async def list_rounds(session: AsyncSession, case_id) -> list[dict]:
    cid = case_id if isinstance(case_id, uuid.UUID) else uuid.UUID(str(case_id))
    rows = (await session.execute(
        select(CaseReviewRound).where(CaseReviewRound.case_id == cid)
        .order_by(CaseReviewRound.round.desc())
    )).scalars().all()
    return [{
        "round": r.round, "kind": r.kind, "verdict": r.verdict, "total": r.total,
        "dimensions": r.dimensions, "findings": r.findings or [],
        "coverageGaps": r.coverage_gaps or [], "summary": r.summary,
        "changed": r.changed, "actor": r.actor, "model": r.model,
        "at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


async def display_status(session: AsyncSession, case) -> str:
    """审核这一维当前显示成什么。**整改待复审是派生的** ——
    不塞进 review_status 的枚举里，免得动那个字段牵连门禁、筛选、批量操作一大片。
    """
    latest = (await session.execute(
        select(CaseReviewRound).where(CaseReviewRound.case_id == case.id)
        .order_by(CaseReviewRound.round.desc()).limit(1)
    )).scalars().first()
    if latest is not None and latest.kind == "cc_resubmit":
        return "resubmitted"          # 改完了，等再审
    return case.review_status or "not_submitted"
