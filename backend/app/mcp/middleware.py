"""MCP 工具范围中间件 —— 按 API Key 限定该连接能看到/能调用的工具。

为什么需要：平台注册了 30+ 个工具，外部 Claude Code 面对全量列表容易挑错
（典型：该做"活体验证后回推"的场景，却去调 tb_generate_api_test 凭文档造）。
instructions 里的引导是**软约束**，模型不一定听；这里做成**硬约束**——
范围外的工具在 tools/list 里根本不出现，直接 tools/call 也会被拒。

身份获取方式：不走 contextvar。FastMCP 的 streamable-http 用 session manager，
工具执行不一定在 HTTP 请求那个 task 里，contextvar 未必能传到。改用
`get_http_headers()` 读当前请求头——注意它**默认会剥掉 authorization**，
必须显式 include。
"""
from __future__ import annotations

import hashlib
import time

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware

# key_hash -> (allowed_tools|None, 写入时间)。None 表示不限制。
# tools/list 每次连接都会调，加个短 TTL 缓存避免频繁打库。
_CACHE: dict[str, tuple[list[str] | None, float]] = {}
_TTL_SECONDS = 30


def invalidate_scope_cache(key_hash: str | None = None) -> None:
    """Key 的工具范围被改动后调用，让缓存立刻失效（不传则全清）。"""
    if key_hash is None:
        _CACHE.clear()
    else:
        _CACHE.pop(key_hash, None)


async def _lookup_allowed_tools() -> list[str] | None:
    """返回当前调用方的工具白名单；None = 不限制。

    没有 bearer（匿名放行 / 环境变量 key）也返回 None——那两条路子不是"某个 Key"，
    不做限制，与 MCPAuthMiddleware 的放行口径保持一致。
    """
    headers = get_http_headers(include={"authorization"})
    auth = headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:].strip()
    if not token:
        return None

    key_hash = hashlib.sha256(token.encode()).hexdigest()
    hit = _CACHE.get(key_hash)
    if hit and (time.monotonic() - hit[1]) < _TTL_SECONDS:
        return hit[0]

    allowed: list[str] | None = None
    try:
        from sqlalchemy import select

        from app.deps.db import async_session_factory
        from app.models.mcp_api_key import McpApiKey

        async with async_session_factory() as session:
            result = await session.execute(
                select(McpApiKey.allowed_tools).where(
                    McpApiKey.key_hash == key_hash,
                    McpApiKey.is_active == True,  # noqa: E712
                )
            )
            row = result.scalar_one_or_none()
            # 查不到（环境变量 key 等）→ 不限制；查到但为 NULL → 不限制
            if row:
                allowed = [str(t) for t in row]
    except Exception:
        # 查库失败不能把 MCP 打死，退化为不限制
        return None

    _CACHE[key_hash] = (allowed, time.monotonic())
    return allowed


class ToolScopeMiddleware(Middleware):
    """按 Key 过滤工具列表 + 拦截越权调用。"""

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        allowed = await _lookup_allowed_tools()
        if allowed is None:
            return tools
        allowed_set = set(allowed)
        return [t for t in tools if t.name in allowed_set]

    async def on_call_tool(self, context, call_next):
        # 必须单独拦一道：从 tools/list 里藏起来 ≠ 不能直接调
        allowed = await _lookup_allowed_tools()
        if allowed is not None and context.message.name not in set(allowed):
            raise ToolError(
                f"工具 {context.message.name} 不在当前 API Key 的授权范围内。"
                "如需使用，请在 testBench「MCP 工具中心」调整该 Key 的工具范围。"
            )
        return await call_next(context)
