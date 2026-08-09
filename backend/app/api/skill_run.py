"""Skill 执行 SSE 端点 — 前端触发 Skill，实时接收进度"""
from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import Field

from app.schemas.common import BaseSchema
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.auth import require_project_role
from app.deps.db import get_db
from app.models.user import User
from app.services.ai_config_resolver import resolve_ai_config
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/projects/{project_id}/branches/{branch_id}/skills",
    tags=["skills"],
)


class RunCaseGenerateRequest(BaseSchema):
    interface_info: str = Field(..., min_length=1)
    business_rules: list[str] = Field(default_factory=list)
    module: str = Field(..., min_length=1)
    submodule: str | None = None


@router.post("/tb-case-generate")
async def run_case_generate(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: RunCaseGenerateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    ai_config = await resolve_ai_config(project_id, session, capability="tb-case-generate")
    if not ai_config:
        raise AppError(
            code="AI_NOT_CONFIGURED",
            message="AI 服务未配置，请在项目设置中配置 AI 服务",
            status_code=503,
        )

    from app.services.ai.skill_executor import execute_case_generate

    async def event_stream():
        try:
            async for event in execute_case_generate(
                project_id=project_id,
                branch_id=branch_id,
                interface_info=body.interface_info,
                business_rules=body.business_rules,
                module=body.module,
                submodule=body.submodule,
                ai_config=ai_config,
                session=session,
            ):
                yield f"data: {json.dumps({'type': event.type, **event.data}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("Skill execution failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:200]}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


class RunQualityReviewRequest(BaseSchema):
    folder_id: str | None = None
    module: str | None = None


@router.post("/tb-quality-review")
async def run_quality_review(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: RunQualityReviewRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    ai_config = await resolve_ai_config(project_id, session, capability="tb-quality-review")
    if not ai_config:
        raise AppError(code="AI_NOT_CONFIGURED", message="AI 服务未配置", status_code=503)

    from app.services.ai.skill_executor import execute_quality_review

    async def event_stream():
        try:
            async for event in execute_quality_review(
                project_id=project_id,
                branch_id=branch_id,
                folder_id=body.folder_id,
                module=body.module,
                ai_config=ai_config,
                session=session,
            ):
                yield f"data: {json.dumps({'type': event.type, **event.data}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("Quality review failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:200]}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ── 平台侧「AI 失败诊断」已下线（2026-08-08）────────────────────────
# 原 POST /tb-diagnose 在此摘掉。三个理由：
#   1. 前端从来没有调用方 —— 能力总览页写着"测试报告 → 失败用例旁「AI 诊断」按钮"，
#      那个按钮不存在，是一条只在文案里活着的能力。
#   2. 和已定的三层失败判断直接冲突：现象由平台按规则算，归因由外部 Claude Code
#      提（tb_submit_analysis），结论由人确认。平台自己再诊断一份就是第四个声音。
#   3. 留着路由 = 留一条"平台也能归因"的暗路，和 docs/cc-platform-loop-spec.md
#      红线 3 是同一条纪律。
# skill_executor.execute_diagnose 暂留（已无调用方），要复活先改红线再说。
