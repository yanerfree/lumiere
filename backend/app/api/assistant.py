"""AI 助手接口 —— 能力面 = 页面动作 ∩ 当前用户权限。

三个端点，三层收口（见 services/assistant/__init__.py）：
- GET  /api/assistant/capabilities  按持有权限过滤出可见工具（可见性层，模型只看得见能做的）
- POST /api/assistant/chat          SSE：把用户意图变成一个 proposal，**从不落库**
- POST /api/assistant/execute       唯一落库口：复检权限 → 守卫服务 → 审计(actor_type=assistant)
"""
from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.exceptions import AppError, ForbiddenError, ValidationError
from app.deps.auth import get_current_user
from app.deps.db import get_db
from app.deps.permissions import resolve_for_request
from app.models.user import User
from app.services.ai import llm_client
from app.services.ai_config_resolver import resolve_ai_config
from app.services.assistant import catalog, runner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

_SECRET_ARGS = {"value", "password", "token", "secret", "api_key"}


def _tool_dict(t: catalog.AssistantTool) -> dict:
    return {
        "key": t.key,
        "label": t.label,
        "description": t.description,
        "scope": t.scope,
        "mutates": t.mutates,
        "permission": t.permission,
        "args": [
            {"name": a.name, "type": a.type, "required": a.required, "description": a.description}
            for a in t.args
        ],
    }


def _mask_args(args: dict) -> dict:
    return {k: ("***" if k in _SECRET_ARGS else v) for k, v in (args or {}).items()}


# ── 能力面 ───────────────────────────────────────────────────────
@router.get("/capabilities")
async def capabilities(
    project_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """当前用户（在可选项目语境下）能让助手做的操作 —— 与 /api/me/permissions 同源过滤。"""
    held = await resolve_for_request(session, current_user, project_id)
    tools = catalog.visible_tools(held)
    return {
        "data": {
            "project_id": str(project_id) if project_id else None,
            "is_super_admin": current_user.role == "admin",
            "system_role": current_user.role,
            "capabilities": [_tool_dict(t) for t in tools],
        }
    }


# ── 对话（SSE，只提议不执行）─────────────────────────────────────
class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    project_id: uuid.UUID | None = None
    messages: list[ChatMessage] = []


@router.post("/chat")
async def chat(
    body: ChatRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    held = await resolve_for_request(session, current_user, body.project_id)
    tools = catalog.visible_tools(held)
    system_prompt = runner.build_system_prompt(tools, body.project_id)

    config = await resolve_ai_config(body.project_id, session, capability="assistant")

    llm_messages = [{"role": "system", "content": system_prompt}]
    for m in body.messages:
        if m.role in ("user", "assistant") and m.content:
            llm_messages.append({"role": m.role, "content": m.content})

    async def event_stream():
        if not config:
            yield f"data: {json.dumps({'type': 'error', 'message': 'AI 服务未配置，请在设置中配置后再用助手'}, ensure_ascii=False)}\n\n"
            return
        full = ""
        try:
            async for chunk in llm_client.stream(llm_messages, config=config):
                if chunk.delta:
                    full += chunk.delta
                    yield f"data: {json.dumps({'type': 'chunk', 'content': chunk.delta}, ensure_ascii=False)}\n\n"
            proposal = runner.parse_proposal(full, tools)
            out = None
            if proposal:
                tool = catalog.get_tool(proposal["tool"])
                out = {
                    "tool": tool.key,
                    "label": tool.label,
                    "mutates": tool.mutates,
                    "scope": tool.scope,
                    "args": proposal["args"],
                }
            yield f"data: {json.dumps({'type': 'done', 'content': full, 'proposal': out}, ensure_ascii=False)}\n\n"
        except llm_client.LLMError as e:
            logger.error("assistant chat LLM error: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        except Exception:
            logger.exception("assistant chat unexpected error")
            yield f"data: {json.dumps({'type': 'error', 'message': '助手出错了，请稍后重试'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ── 执行（唯一落库口，复检 + 审计）──────────────────────────────
class ExecuteRequest(BaseModel):
    project_id: uuid.UUID | None = None
    tool: str
    args: dict = {}


@router.post("/execute")
async def execute(
    body: ExecuteRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """执行一个助手操作。**权限复检在这里**：不信任 chat 那份已过滤的清单，重算一遍持有集。"""
    tool = catalog.get_tool(body.tool)
    if tool is None:
        raise ValidationError(code="UNKNOWN_TOOL", message=f"未知操作「{body.tool}」")

    # 项目级操作必须带项目语境
    if tool.scope == "project" and body.project_id is None:
        raise ValidationError(code="PROJECT_REQUIRED", message="该操作需要在具体项目下执行")

    # ① 复检：重新解析当前用户在该语境下的持有权限，工具权限 ∉ 持有集即拒
    held = await resolve_for_request(session, current_user, body.project_id)
    if not catalog.tool_allowed(tool, held):
        raise ForbiddenError(code="PERMISSION_DENIED", message="你没有执行该操作的权限")

    # ② 入参校验 → 守卫服务
    args = catalog.coerce_args(tool, body.args)
    ctx = catalog.ToolContext(
        session=session,
        user=current_user,
        project_id=body.project_id,
        args=args,
        background_tasks=background_tasks,
    )
    result = await tool.handler(ctx)

    # ③ 审计：actor_type=assistant，与人在页面点的（human）、外部 CC（mcp）区分开
    if tool.mutates:
        await write_audit_log(
            session,
            action=tool.key,
            target_type="assistant_action",
            target_name=tool.label,
            project_id=body.project_id,
            changes={"args": _mask_args(args), "result": result},
            actor_type="assistant",
            actor_label="AI 助手",
        )

    return {"data": {"tool": tool.key, "mutates": tool.mutates, "result": result}}
