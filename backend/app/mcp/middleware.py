"""MCP 工具范围中间件 —— 按 API Key 限定该连接能看到/能调用的工具。

为什么需要：平台注册了 30+ 个工具，外部 Claude Code 面对全量列表容易挑错
（当年的典型：该做"活体验证后回推"的场景，却去调 tb_generate_api_test 凭文档造 ——
那个工具已下线，但挑错这件事本身不会随它消失）。
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

# key_hash -> (allowed_tools|None, user_id|None, 写入时间)。allowed=None 表示不限制。
# tools/list 每次连接都会调，加个短 TTL 缓存避免频繁打库。
#
# user_id 一起缓存：Key 上本来就有它，此前只取 allowed_tools 就把整行扔了，
# 于是所有人的回推 created_by 全记成同一个 admin —— 多人一起用时，
# 操作日志失去意义，「CC归因 vs 人确认」也没法按人分桶。**这段历史数据事后补不回来。**
_CACHE: dict[str, tuple[list[str] | None, str | None, float]] = {}
_TTL_SECONDS = 30


def pick_scope(
    project_id, project_scope: list | None, legacy_scope: list | None
) -> list | None:
    """一把 Key 到底按哪份范围跑。返回 None = 不限制。

    判据是**有没有归属项目**，不是"项目范围真不真"。
    写成 `project_scope or legacy_scope` 是这里最自然也最错的写法：项目明确
    设成不限制（NULL）时，那个写法会掉回 Key 上那份旧范围 —— 等于把人刚放开的
    权限又悄悄收回去，而页面上完全看不出为什么。

    抽成纯函数是为了能直接测这条判据，不用去正则匹配源码。
    """
    raw = project_scope if project_id else legacy_scope
    return [str(t) for t in raw] if raw else None


def invalidate_scope_cache(key_hash: str | None = None) -> None:
    """Key 的工具范围被改动后调用，让缓存立刻失效（不传则全清）。"""
    if key_hash is None:
        _CACHE.clear()
    else:
        _CACHE.pop(key_hash, None)


async def _lookup_key() -> tuple[list[str] | None, str | None]:
    """返回 (工具白名单, 调用方 user_id)。白名单 None = 不限制。

    没有 bearer（匿名放行 / 环境变量 key）→ (None, None)，那两条路子不是"某个 Key"，
    不做限制，与 MCPAuthMiddleware 的放行口径保持一致。
    """
    headers = get_http_headers(include={"authorization"})
    auth = headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None, None
    token = auth[7:].strip()
    if not token:
        return None, None

    key_hash = hashlib.sha256(token.encode()).hexdigest()
    hit = _CACHE.get(key_hash)
    if hit and (time.monotonic() - hit[2]) < _TTL_SECONDS:
        return hit[0], hit[1]

    allowed: list[str] | None = None
    user_id: str | None = None
    try:
        from sqlalchemy import select

        from app.deps.db import async_session_factory
        from app.models.mcp_api_key import McpApiKey
        from app.models.project import Project

        async with async_session_factory() as session:
            # LEFT JOIN：Key 归属项目 → 用项目的范围；没归属（存量 Key）→ 用 Key 自己那份。
            # 一次查询取完，别拆成两次 —— 这条在连接热路径上。
            result = await session.execute(
                select(
                    Project.mcp_allowed_tools,
                    McpApiKey.allowed_tools,
                    McpApiKey.user_id,
                    McpApiKey.project_id,
                )
                .select_from(McpApiKey)
                .join(Project, Project.id == McpApiKey.project_id, isouter=True)
                .where(
                    McpApiKey.key_hash == key_hash,
                    McpApiKey.is_active == True,  # noqa: E712
                )
            )
            row = result.first()
            # 查不到（环境变量 key 等）→ 不限制；查到但范围为 NULL → 不限制
            if row:
                project_scope, legacy_scope, uid, project_id = row
                allowed = pick_scope(project_id, project_scope, legacy_scope)
                if uid:
                    user_id = str(uid)
    except Exception:
        # 查库失败不能把 MCP 打死，退化为不限制
        return None, None

    _CACHE[key_hash] = (allowed, user_id, time.monotonic())
    return allowed, user_id


async def _lookup_allowed_tools() -> list[str] | None:
    return (await _lookup_key())[0]


async def current_caller_user_id() -> str | None:
    """当前 MCP 调用方的用户 id（由其 API Key 决定）。拿不到返回 None。

    工具落库时用它填 created_by / executed_by —— 记成别人比不记还糟。
    """
    try:
        return (await _lookup_key())[1]
    except Exception:  # noqa: BLE001
        return None


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
                f"工具 {context.message.name} 不在本项目的 MCP 工具范围内。"
                "如需使用，请在 testBench「MCP 工具中心 → 工具范围」调整 —— "
                "范围是项目级的，改一次本项目所有 Key 都生效，不用重新建 Key。"
            )
        return await call_next(context)
