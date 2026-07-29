"""MCP API Key 管理 — 生成/列表/吊销"""
from __future__ import annotations

import hashlib
import logging
import secrets
import uuid

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.common import BaseSchema

from app.deps.db import get_db
from app.deps.auth import get_current_user
from app.models.user import User
from app.models.mcp_api_key import McpApiKey

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp-keys", tags=["mcp-keys"])


class CreateKeyRequest(BaseSchema):
    name: str = Field(default="default", max_length=100)
    # None / 不传 = 不限制（全部工具）；列表 = 只暴露这些工具
    allowed_tools: list[str] | None = None


class UpdateKeyRequest(BaseSchema):
    name: str | None = Field(default=None, max_length=100)
    allowed_tools: list[str] | None = None
    # 显式区分"不改 allowed_tools"和"改成不限制"——前者不传该字段，
    # 后者传 reset_tools=true（JSON 里 null 无法表达这个区别）
    reset_tools: bool = False


def _validate_tools(names: list[str] | None) -> list[str] | None:
    """过滤掉不存在的工具名，避免存进一堆拼错的名字导致 Key 形同虚设。"""
    if names is None:
        return None
    from app.mcp import TOOL_CATALOG

    known = {t["name"] for t in TOOL_CATALOG}
    return [n for n in names if n in known]


@router.get("/tools")
async def list_available_tools(_: User = Depends(get_current_user)):
    """MCP 工具目录（供工具中心展示 + Key 工具范围勾选）。

    直接来自 mcp 注册时登记的 TOOL_CATALOG，不再前端硬编码——
    此前前端写死 20 条而后端实际 32 条，回推工具全都没露出来。
    """
    from app.mcp import TOOL_CATALOG

    return {"data": TOOL_CATALOG}


@router.post("")
async def create_api_key(
    body: CreateKeyRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    raw_key = f"tb_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:8]

    api_key = McpApiKey(
        user_id=current_user.id,
        name=body.name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        allowed_tools=_validate_tools(body.allowed_tools),
    )
    session.add(api_key)
    await session.commit()

    return {"data": {
        "id": str(api_key.id),
        "name": api_key.name,
        "key": raw_key,
        "prefix": key_prefix,
        "allowedTools": api_key.allowed_tools,
        "createdAt": api_key.created_at.isoformat(),
    }}


@router.patch("/{key_id}")
async def update_api_key(
    key_id: uuid.UUID,
    body: UpdateKeyRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """改名 / 调整工具范围。"""
    key = await session.get(McpApiKey, key_id)
    if not key or key.user_id != current_user.id:
        return {"error": "Key not found"}

    if body.name is not None:
        key.name = body.name
    if body.reset_tools:
        key.allowed_tools = None
    elif body.allowed_tools is not None:
        key.allowed_tools = _validate_tools(body.allowed_tools)
    await session.commit()

    # 中间件按 key_hash 缓存了范围，改完要立刻失效，否则最长 30s 还是旧范围
    from app.mcp.middleware import invalidate_scope_cache

    invalidate_scope_cache(key.key_hash)

    return {"data": {
        "id": str(key.id),
        "name": key.name,
        "allowedTools": key.allowed_tools,
    }}


@router.get("")
async def list_api_keys(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await session.execute(
        select(McpApiKey)
        .where(McpApiKey.user_id == current_user.id, McpApiKey.is_active == True)
        .order_by(McpApiKey.created_at.desc())
    )
    keys = result.scalars().all()
    return {"data": [{
        "id": str(k.id),
        "name": k.name,
        "prefix": k.key_prefix,
        "allowedTools": k.allowed_tools,
        "createdAt": k.created_at.isoformat(),
        "lastUsedAt": k.last_used_at.isoformat() if k.last_used_at else None,
    } for k in keys]}


@router.delete("/{key_id}")
async def revoke_api_key(
    key_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    key = await session.get(McpApiKey, key_id)
    if not key or key.user_id != current_user.id:
        return {"error": "Key not found"}
    key.is_active = False
    await session.commit()

    from app.mcp.middleware import invalidate_scope_cache

    invalidate_scope_cache(key.key_hash)
    return {"data": {"revoked": True}}
