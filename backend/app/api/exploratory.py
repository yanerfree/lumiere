"""探索测试 API — 会话管理 + AI 章程生成 + 发现记录"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.common import BaseSchema
from app.core.exceptions import NotFoundError
from app.deps.auth import get_current_user, require_project_role
from app.deps.db import get_db
from app.models.user import User
from app.models.exploratory import ExploratorySession, ExploratoryFinding

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/projects/{project_id}/exploratory",
    tags=["exploratory"],
)


class CreateSessionRequest(BaseSchema):
    title: str = Field(..., min_length=1, max_length=200)
    target_module: str | None = None
    time_limit_minutes: int = 30


class AddFindingRequest(BaseSchema):
    finding_type: str = Field(..., pattern="^(bug|risk|suggestion)$")
    severity: str = Field(default="medium", pattern="^(critical|high|medium|low)$")
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    checkpoint: str | None = None


def _session_to_dict(s: ExploratorySession) -> dict:
    return {
        "id": str(s.id),
        "title": s.title,
        "targetModule": s.target_module,
        "timeLimitMinutes": s.time_limit_minutes,
        "status": s.status,
        "charter": s.charter,
        "checkpoints": s.checkpoints,
        "completedCheckpoints": s.completed_checkpoints,
        "totalCheckpoints": s.total_checkpoints,
        "summary": s.summary,
        "createdAt": s.created_at.isoformat() if s.created_at else None,
        "completedAt": s.completed_at.isoformat() if s.completed_at else None,
    }


# ── 会话 CRUD ──

@router.get("/sessions")
async def list_sessions(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await session.execute(
        select(ExploratorySession)
        .where(ExploratorySession.project_id == project_id)
        .order_by(ExploratorySession.created_at.desc())
    )
    sessions = result.scalars().all()
    return {"data": [_session_to_dict(s) for s in sessions]}


@router.post("/sessions")
async def create_session(
    project_id: uuid.UUID,
    body: CreateSessionRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    exp = ExploratorySession(
        project_id=project_id,
        title=body.title,
        target_module=body.target_module,
        time_limit_minutes=body.time_limit_minutes,
        created_by=current_user.id,
    )
    session.add(exp)
    await session.flush()
    await session.refresh(exp)
    return {"data": _session_to_dict(exp)}


@router.get("/sessions/{session_id}")
async def get_session(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    exp = await session.get(ExploratorySession, session_id)
    if not exp or exp.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="会话不存在")

    findings_result = await session.execute(
        select(ExploratoryFinding)
        .where(ExploratoryFinding.session_id == session_id)
        .order_by(ExploratoryFinding.created_at)
    )
    findings = findings_result.scalars().all()

    data = _session_to_dict(exp)
    data["findings"] = [
        {
            "id": str(f.id),
            "findingType": f.finding_type,
            "severity": f.severity,
            "title": f.title,
            "description": f.description,
            "checkpoint": f.checkpoint,
            "createdAt": f.created_at.isoformat() if f.created_at else None,
        }
        for f in findings
    ]
    return {"data": data}


# ── 发现记录 ──

@router.post("/sessions/{session_id}/findings")
async def add_finding(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    body: AddFindingRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    exp = await session.get(ExploratorySession, session_id)
    if not exp or exp.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="会话不存在")

    finding = ExploratoryFinding(
        session_id=session_id,
        finding_type=body.finding_type,
        severity=body.severity,
        title=body.title,
        description=body.description,
        checkpoint=body.checkpoint,
    )
    session.add(finding)
    await session.flush()
    await session.refresh(finding)
    return {"data": {"id": str(finding.id), "title": finding.title}}


# ── 完成检查点 ──

@router.post("/sessions/{session_id}/complete-checkpoint")
async def complete_checkpoint(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    exp = await session.get(ExploratorySession, session_id)
    if not exp:
        raise NotFoundError(code="NOT_FOUND", message="会话不存在")
    exp.completed_checkpoints = min(exp.completed_checkpoints + 1, exp.total_checkpoints)
    await session.commit()
    return {"data": {"completed": exp.completed_checkpoints, "total": exp.total_checkpoints}}


# ── 结束会话 ──

@router.post("/sessions/{session_id}/complete")
async def complete_session(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    exp = await session.get(ExploratorySession, session_id)
    if not exp:
        raise NotFoundError(code="NOT_FOUND", message="会话不存在")
    exp.status = "completed"
    exp.completed_at = datetime.now(timezone.utc)
    await session.commit()
    return {"data": _session_to_dict(exp)}


# ── AI 生成章程 ──

@router.post("/sessions/{session_id}/generate-charter")
async def generate_charter(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    from app.services.ai_config_resolver import resolve_ai_config
    from app.services.ai import llm_client
    from app.mcp.tools import test_cases, api_endpoints
    from app.core.exceptions import AppError

    exp = await session.get(ExploratorySession, session_id)
    if not exp:
        raise NotFoundError(code="NOT_FOUND", message="会话不存在")

    ai_config = await resolve_ai_config(project_id, session, capability="exploratory-charter")
    if not ai_config:
        raise AppError(code="AI_NOT_CONFIGURED", message="AI 服务未配置", status_code=503)

    api_tree = await api_endpoints.list_api_tree(session, str(project_id))
    endpoints = [n for n in api_tree if n.get("type") == "endpoint"]
    api_text = "\n".join(f"- {ep.get('method','GET')} {ep.get('url','')} ({ep.get('name','')})" for ep in endpoints[:20])

    messages = [
        {"role": "system", "content": """你是探索测试专家。根据项目信息生成结构化探索章程。
输出 JSON（```json包裹）：
{
  "objective": "测试目标",
  "timeBox": "建议时长",
  "checkpoints": [
    {"id": 1, "title": "检查点名称", "description": "具体要检查什么", "priority": "high|medium|low"}
  ],
  "riskAreas": ["高风险区域"],
  "explorationHints": ["探索建议"]
}"""},
        {"role": "user", "content": f"目标模块: {exp.target_module or exp.title}\n时间限制: {exp.time_limit_minutes}分钟\n\n项目API接口:\n{api_text or '（无录入）'}\n\n请生成探索测试章程。"},
    ]

    async def event_stream():
        full = ""
        try:
            async for chunk in llm_client.stream(messages, config=ai_config):
                if chunk.delta:
                    full += chunk.delta
                    yield f"data: {json.dumps({'type': 'chunk', 'content': chunk.delta}, ensure_ascii=False)}\n\n"

            import re
            match = re.search(r"```json\s*\n(.*?)(?:\n```|$)", full, re.DOTALL)
            charter_text = match.group(1) if match else full
            try:
                charter = json.loads(charter_text)
            except json.JSONDecodeError:
                last = charter_text.rfind("}")
                charter = json.loads(charter_text[:last+1]) if last > 0 else {}

            exp.charter = charter
            checkpoints = charter.get("checkpoints", [])
            exp.checkpoints = checkpoints
            exp.total_checkpoints = len(checkpoints)
            await session.commit()

            yield f"data: {json.dumps({'type': 'done', 'charter': charter}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:200]}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
