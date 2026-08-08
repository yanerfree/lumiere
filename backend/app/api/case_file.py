"""病历 + 用量 API"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.auth import get_current_user
from app.deps.db import get_db
from app.models.user import User
from app.models.case_file import CaseFileEvent, AIUsageLog

router = APIRouter(tags=["case-file"])


# ── 用例病历 ──

@router.get("/api/cases/{case_id}/file")
async def get_case_file(
    case_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await session.execute(
        select(CaseFileEvent)
        .where(CaseFileEvent.case_id == case_id)
        .order_by(CaseFileEvent.created_at.desc())
    )
    events = result.scalars().all()

    # 自动标签
    tags = []
    fail_streak = 0
    total_exec = 0
    total_pass = 0
    for e in reversed(list(events)):
        if e.event_type == "executed_fail":
            fail_streak += 1
            total_exec += 1
        elif e.event_type == "executed_pass":
            fail_streak = 0
            total_exec += 1
            total_pass += 1

    if fail_streak >= 3:
        tags.append("#不稳定")
    if total_exec > 0 and total_pass / total_exec < 0.5:
        tags.append("#需要关注")
    if total_exec == 0:
        tags.append("#待验证")

    return {
        "data": {
            "events": [
                {
                    "id": str(e.id),
                    "eventType": e.event_type,
                    "summary": e.summary,
                    "detail": e.detail,
                    "createdAt": e.created_at.isoformat() if e.created_at else None,
                }
                for e in events
            ],
            "tags": tags,
            "stats": {
                "totalEvents": len(events),
                "totalExecutions": total_exec,
                "passCount": total_pass,
                "passRate": round(total_pass / total_exec * 100, 1) if total_exec > 0 else None,
            },
        }
    }


# ── AI 用量统计 ──

@router.get("/api/projects/{project_id}/ai-usage")
async def get_ai_usage(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 按 Skill 分组统计
    result = await session.execute(
        select(
            AIUsageLog.skill_name,
            sa_func.count().label("count"),
            sa_func.sum(AIUsageLog.total_tokens).label("tokens"),
            sa_func.sum(AIUsageLog.duration_ms).label("duration"),
        )
        .where(AIUsageLog.project_id == project_id)
        .group_by(AIUsageLog.skill_name)
    )
    rows = result.all()

    total_tokens = sum(r.tokens or 0 for r in rows)
    total_calls = sum(r.count for r in rows)

    return {
        "data": {
            "totalTokens": total_tokens,
            "totalCalls": total_calls,
            "bySkill": [
                {
                    "skillName": r.skill_name,
                    "calls": r.count,
                    "tokens": r.tokens or 0,
                    "durationMs": r.duration or 0,
                }
                for r in rows
            ],
        }
    }

# ── 用例溯源：需求点 + 生成事件（把已有真数据接出来）────────────────
# 此前：需求溯源只渲染裸编号「R3」，生成档案是一句写死的占位文案，
# 而 requirement_points 有 107 条（含标题 + 原文引用 + 字符偏移锚点）、
# case_gen_events 有 49 条真事件。数据一直在，只是最后一公里没接。

@router.get("/api/cases/{case_id}/provenance")
async def get_case_provenance(
    case_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """这条用例从哪来：关联的需求点（带原文）+ 生成事件时间线。"""
    from sqlalchemy import func as sa_f

    from app.models.case import Case
    from app.models.scenario_gen import CaseGenEvent, RequirementPoint

    case = await session.get(Case, case_id)
    if not case:
        return {"data": {"requirementPoints": [], "events": [], "generationTaskId": None}}

    codes = [str(c) for c in (case.requirement_point_ids or [])]
    points = []
    if codes and case.generation_task_id:
        rows = (await session.execute(
            select(RequirementPoint).where(
                RequirementPoint.task_id == case.generation_task_id,
                RequirementPoint.code.in_(codes),
            ).order_by(RequirementPoint.sort_order, RequirementPoint.code)
        )).scalars().all()
        points = [{
            "code": r.code,
            "title": r.title,
            "quoteText": r.quote_text,
            "quoteOffset": r.quote_offset,
            "anchorStatus": r.anchor_status,
            "status": r.status,
            "docId": str(r.doc_id) if r.doc_id else None,
        } for r in rows]
        found = {p["code"] for p in points}
        # 编号在用例上、需求点却查不到 —— 说明需求文档改过或点被删了，
        # 这本身就是「用例可能过期」的信号，不能静默吞掉
        for miss in [c for c in codes if c not in found]:
            points.append({"code": miss, "title": None, "missing": True})

    events = (await session.execute(
        select(CaseGenEvent).where(CaseGenEvent.case_id == case_id)
        .order_by(CaseGenEvent.created_at)
    )).scalars().all()

    return {"data": {
        "generationTaskId": str(case.generation_task_id) if case.generation_task_id else None,
        "source": case.source,
        "createdAt": case.created_at.isoformat() if case.created_at else None,
        "requirementPoints": points,
        "events": [{
            "eventType": e.event_type,
            "actor": e.actor,
            "payload": e.payload,
            "createdAt": e.created_at.isoformat() if e.created_at else None,
        } for e in events],
    }}
