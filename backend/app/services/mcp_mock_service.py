"""MCP Mock 的数据层 —— 服务 / 工具 / 日志的增删改查。"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp_mock import McpMockLog, McpMockServer, McpMockTool

MAX_LOGS = 1000

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}[a-z0-9]$|^[a-z0-9]$")

# slug 会直接拼进 URL，这几个是 MCP 服务自己要用的路径段，占了会打架。
RESERVED_SLUGS = {"api", "mcp", "sse", "health", "docs", "static", "_internal"}


def slug_error(slug: str) -> str | None:
    """slug 合法性。返回 None 表示没问题，否则是给人看的那句话。"""
    if not slug:
        return "服务代号不能为空"
    if not _SLUG_RE.match(slug):
        return "服务代号只能用小写字母、数字和中划线，且不能以中划线开头或结尾"
    if slug in RESERVED_SLUGS:
        return f"服务代号 {slug} 是保留字，换一个"
    return None


# ── 服务 ──

async def list_servers(session: AsyncSession) -> list[McpMockServer]:
    rows = await session.execute(
        select(McpMockServer).order_by(McpMockServer.sort_order, McpMockServer.created_at)
    )
    return list(rows.scalars().all())


async def get_server(session: AsyncSession, server_id: uuid.UUID) -> McpMockServer | None:
    return await session.get(McpMockServer, server_id)


async def get_server_by_slug(session: AsyncSession, slug: str) -> McpMockServer | None:
    return await session.scalar(select(McpMockServer).where(McpMockServer.slug == slug))


async def create_server(session: AsyncSession, data: dict) -> McpMockServer:
    max_order = await session.scalar(select(func.max(McpMockServer.sort_order))) or 0
    server = McpMockServer(
        slug=data["slug"],
        name=data.get("name") or data["slug"],
        description=data.get("description", ""),
        instructions=data.get("instructions", ""),
        transport=data.get("transport", "streamable-http"),
        auth_type=data.get("auth_type", "none"),
        auth_config=data.get("auth_config"),
        validate_mode=data.get("validate_mode", "strict"),
        enabled=data.get("enabled", True),
        builtin=False,
        locked=False,
        sort_order=max_order + 1,
    )
    session.add(server)
    await session.flush()
    return server


_SERVER_EDITABLE = (
    "name", "description", "instructions", "transport",
    "auth_type", "auth_config", "validate_mode", "enabled", "sort_order",
)


async def update_server(session: AsyncSession, server: McpMockServer, data: dict) -> McpMockServer:
    for key in _SERVER_EDITABLE:
        if key in data and data[key] is not None:
            setattr(server, key, data[key])
    # auth_config 传 None 是「清空」，上面那行会跳过，所以单独处理
    if "auth_config" in data and data["auth_config"] is None:
        server.auth_config = None
    await session.flush()
    return server


async def delete_server(session: AsyncSession, server: McpMockServer) -> None:
    await session.execute(delete(McpMockTool).where(McpMockTool.server_id == server.id))
    await session.execute(delete(McpMockLog).where(McpMockLog.server_id == server.id))
    await session.delete(server)
    await session.flush()


async def bump_call(session: AsyncSession, server_id: uuid.UUID) -> None:
    server = await session.get(McpMockServer, server_id)
    if server is not None:
        server.call_count = (server.call_count or 0) + 1
        server.last_call_at = datetime.now(timezone.utc)


# ── 工具 ──

async def list_tools(session: AsyncSession, server_id: uuid.UUID) -> list[McpMockTool]:
    rows = await session.execute(
        select(McpMockTool)
        .where(McpMockTool.server_id == server_id)
        .order_by(McpMockTool.sort_order, McpMockTool.created_at)
    )
    return list(rows.scalars().all())


async def get_tool(session: AsyncSession, tool_id: uuid.UUID) -> McpMockTool | None:
    return await session.get(McpMockTool, tool_id)


async def get_tool_by_name(session: AsyncSession, server_id: uuid.UUID, name: str) -> McpMockTool | None:
    return await session.scalar(
        select(McpMockTool).where(McpMockTool.server_id == server_id, McpMockTool.name == name)
    )


async def create_tool(session: AsyncSession, server_id: uuid.UUID, data: dict) -> McpMockTool:
    max_order = await session.scalar(
        select(func.max(McpMockTool.sort_order)).where(McpMockTool.server_id == server_id)
    ) or 0
    tool = McpMockTool(
        server_id=server_id,
        name=data["name"],
        description=data.get("description", ""),
        params=data.get("params") or [],
        mode=data.get("mode", "success"),
        success_data=data.get("success_data") if data.get("success_data") is not None else {"result": "ok"},
        custom_data=data.get("custom_data"),
        custom_is_error=bool(data.get("custom_is_error", False)),
        error_message=data.get("error_message") or "Mock error: tool call failed",
        delay_ms=int(data.get("delay_ms") or 0),
        enabled=data.get("enabled", True),
        locked=False,
        sort_order=max_order + 1,
    )
    session.add(tool)
    await session.flush()
    return tool


_TOOL_EDITABLE = (
    "name", "description", "params", "mode", "success_data",
    "custom_is_error", "error_message", "delay_ms", "enabled", "sort_order",
)


async def update_tool(session: AsyncSession, tool: McpMockTool, data: dict) -> McpMockTool:
    for key in _TOOL_EDITABLE:
        if key in data and data[key] is not None:
            setattr(tool, key, data[key])
    if "custom_data" in data:
        tool.custom_data = data["custom_data"]
    await session.flush()
    return tool


async def delete_tool(session: AsyncSession, tool: McpMockTool) -> None:
    await session.delete(tool)
    await session.flush()


# ── 日志 ──

async def create_log(session: AsyncSession, data: dict) -> McpMockLog:
    log = McpMockLog(**data)
    session.add(log)
    await session.flush()
    return log


async def trim_logs(session: AsyncSession, keep: int = MAX_LOGS) -> int:
    total = await session.scalar(select(func.count(McpMockLog.id))) or 0
    if total <= keep:
        return 0
    cutoff = await session.scalar(
        select(McpMockLog.timestamp).order_by(McpMockLog.timestamp.desc()).offset(keep).limit(1)
    )
    if cutoff is None:
        return 0
    res = await session.execute(delete(McpMockLog).where(McpMockLog.timestamp <= cutoff))
    return res.rowcount or 0


async def list_logs(
    session: AsyncSession,
    server_id: uuid.UUID | None = None,
    tool_name: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[McpMockLog], int]:
    stmt = select(McpMockLog)
    count_stmt = select(func.count(McpMockLog.id))
    if server_id is not None:
        stmt = stmt.where(McpMockLog.server_id == server_id)
        count_stmt = count_stmt.where(McpMockLog.server_id == server_id)
    if tool_name:
        stmt = stmt.where(McpMockLog.tool_name == tool_name)
        count_stmt = count_stmt.where(McpMockLog.tool_name == tool_name)
    if status == "ok":
        stmt = stmt.where(McpMockLog.is_error.is_(False))
        count_stmt = count_stmt.where(McpMockLog.is_error.is_(False))
    elif status == "error":
        stmt = stmt.where(McpMockLog.is_error.is_(True))
        count_stmt = count_stmt.where(McpMockLog.is_error.is_(True))
    total = await session.scalar(count_stmt) or 0
    rows = await session.execute(
        stmt.order_by(McpMockLog.timestamp.desc()).offset(offset).limit(limit)
    )
    return list(rows.scalars().all()), total


async def clear_logs(session: AsyncSession, server_id: uuid.UUID | None = None) -> int:
    stmt = delete(McpMockLog)
    if server_id is not None:
        stmt = stmt.where(McpMockLog.server_id == server_id)
    res = await session.execute(stmt)
    return res.rowcount or 0
