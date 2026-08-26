"""AI 生成接口 — 用例生成 / 脚本生成 / 文档生成（SSE 流式）"""
from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import AppError
from app.deps.auth import require_project_role
from app.deps.db import get_db
from app.models.user import User
from app.schemas.ai import (
    AIConfigResponse,
    ApplyCasesRequest,
    GenerateCasesRequest,
    GenerateScriptRequest,
)
from app.services.ai import llm_client
from app.services.ai_config_resolver import resolve_ai_config

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/projects/{project_id}/branches/{branch_id}/ai",
    tags=["ai"],
)

config_router = APIRouter(prefix="/api/ai", tags=["ai"])


async def _get_ai_config(project_id: uuid.UUID, session: AsyncSession, capability: str = "text"):
    config = await resolve_ai_config(project_id, session, capability=capability)
    if not config:
        raise AppError(
            code="AI_NOT_CONFIGURED",
            message="AI 服务未配置，请在项目设置或系统设置中配置 AI 服务",
            status_code=503,
        )
    return config


@config_router.get("/config")
async def get_ai_config(session: AsyncSession = Depends(get_db)) -> dict:
    config = await resolve_ai_config(None, session)
    if config:
        base_url = config.base_url
        masked = base_url[:15] + "..." + base_url[-5:] if len(base_url) > 20 else base_url
        return {
            "data": AIConfigResponse(
                enabled=True,
                provider=config.provider,
                model=config.model,
                base_url_masked=masked,
            ).model_dump(by_alias=True)
        }
    return {
        "data": AIConfigResponse(
            enabled=False,
            provider="",
            model="",
            base_url_masked="",
        ).model_dump(by_alias=True)
    }


@router.post("/generate-cases")
async def generate_cases(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: GenerateCasesRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    config = await _get_ai_config(project_id, session, capability="lum-case-generate")

    from app.services.ai.case_gen_service import build_case_gen_messages
    messages = build_case_gen_messages(
        interface_info=body.interface_info,
        business_rules=body.business_rules,
        module=body.target.module,
        submodule=body.target.submodule,
    )

    async def event_stream():
        full_content = ""
        try:
            async for chunk in llm_client.stream(
                messages,
                model=body.model,
                temperature=body.temperature,
                config=config,
            ):
                if chunk.delta:
                    full_content += chunk.delta
                    yield f"data: {json.dumps({'type': 'chunk', 'content': chunk.delta}, ensure_ascii=False)}\n\n"

            yield f"data: {json.dumps({'type': 'done', 'content': full_content}, ensure_ascii=False)}\n\n"
        except llm_client.LLMError as e:
            logger.error("AI generate-cases failed: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("Unexpected error in generate-cases")
            yield f"data: {json.dumps({'type': 'error', 'message': '生成失败，请稍后重试'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/generate-script")
async def generate_script(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: GenerateScriptRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    config = await _get_ai_config(project_id, session, capability="pytest-script")

    from app.services.ai.script_gen_service import build_script_gen_messages
    messages = await build_script_gen_messages(
        case_ids=body.case_ids,
        script_type=body.script_type,
        session=session,
    )

    async def event_stream():
        full_content = ""
        try:
            async for chunk in llm_client.stream(
                messages,
                model=body.model,
                temperature=body.temperature,
                config=config,
            ):
                if chunk.delta:
                    full_content += chunk.delta
                    yield f"data: {json.dumps({'type': 'chunk', 'content': chunk.delta}, ensure_ascii=False)}\n\n"

            yield f"data: {json.dumps({'type': 'done', 'content': full_content}, ensure_ascii=False)}\n\n"
        except llm_client.LLMError as e:
            logger.error("AI generate-script failed: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("Unexpected error in generate-script")
            yield f"data: {json.dumps({'type': 'error', 'message': '生成失败，请稍后重试'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/apply-cases")
async def apply_cases(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: ApplyCasesRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    from app.services.ai.case_gen_service import import_generated_cases
    result = await import_generated_cases(
        cases=body.cases,
        project_id=project_id,
        branch_id=branch_id,
        folder_id=uuid.UUID(body.folder_id) if body.folder_id else None,
        user_id=current_user.id,
        session=session,
    )
    return {"data": result}
