"""MCP Mock 预置服务 —— 开箱即用的一批典型 MCP 服务端。

覆盖面是按「接入一个 MCP 服务时会踩到什么」排的，不是按功能排的：

    传输方式    Streamable HTTP / SSE
    认证方式    不认证 / Bearer Token / 自定义 API Key 请求头
    入参校验    严格 / 宽松 / 不校验
    异常场景    工具报错 / 响应很慢 / 返回空

⚠ 预置服务**不许删**（`builtin=True`，见 app/models/mcp_mock.py 的注释）：
   它每次启动都会按定义补回来，删掉的效果只是「重启前它不在、重启后又回来了」。
   不想用就停用。默认还带 `locked=True`，解锁之后照样能改 —— 锁是防误改，不是禁改。

⚠ 预置里的 token / API Key 是**写死的明文示例值**，故意的：它就是给人拿去
   连一下试试的。**别把它当凭证管理的样板** —— 真凭证走环境变量，不进这张表。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp_mock import McpMockServer, McpMockTool

logger = logging.getLogger("mcp_mock")

# 预置服务里写死的示例凭证 —— 页面上直接显示，供人复制去连。
DEMO_BEARER_TOKEN = "lumiere-mock-token-2026"
DEMO_API_KEY = "lumiere-mock-apikey-2026"
DEMO_API_KEY_HEADER = "X-API-Key"


def _p(name: str, ptype: str = "string", **kw: Any) -> dict:
    return {"name": name, "type": ptype, **kw}


PRESET_SERVERS: list[dict] = [
    {
        "slug": "basic",
        "name": "基础服务（HTTP·不认证）",
        "description": "最常见的一种接入：Streamable HTTP + 不要认证。先拿它确认客户端连得上、工具列得出来。",
        "instructions": "Lumiere MCP Mock —— 基础示例服务，无需认证，直接调用即可。",
        "transport": "streamable-http",
        "auth_type": "none",
        "validate_mode": "strict",
        "tools": [
            {
                "name": "echo",
                "description": "原样回显你传进来的一段文字，用来确认通道是通的",
                "params": [_p("message", "string", required=True, description="要回显的文字")],
                "success_data": {"echo": "hello from lumiere mock", "ok": True},
            },
            {
                "name": "get_server_time",
                "description": "返回一个固定的服务器时间（Mock，不会变）",
                "params": [_p("timezone", "string", description="时区名，如 Asia/Shanghai", default="Asia/Shanghai")],
                "success_data": {"time": "2026-09-29T10:00:00+08:00", "timezone": "Asia/Shanghai"},
            },
            {
                "name": "list_users",
                "description": "分页列出用户（Mock 数据）",
                "params": [
                    _p("page", "integer", description="页码，从 1 开始", default=1, minimum=1),
                    _p("page_size", "integer", description="每页条数", default=20, minimum=1, maximum=100),
                    _p("keyword", "string", description="按用户名模糊搜"),
                ],
                "success_data": {
                    "list": [
                        {"id": 1, "name": "张三", "role": "admin", "active": True},
                        {"id": 2, "name": "李四", "role": "member", "active": True},
                        {"id": 3, "name": "王五", "role": "member", "active": False},
                    ],
                    "total": 3, "page": 1, "pageSize": 20,
                },
            },
        ],
    },
    {
        "slug": "sse",
        "name": "SSE 服务（SSE·不认证）",
        "description": "老一代 MCP 传输方式（Server-Sent Events）。客户端连的还是同一个地址，但握手方式不一样，专门用来验客户端支不支持 SSE。",
        "instructions": "Lumiere MCP Mock —— SSE 传输示例服务。",
        "transport": "sse",
        "auth_type": "none",
        "validate_mode": "loose",
        "tools": [
            {
                "name": "ping",
                "description": "探活，固定返回 pong",
                "params": [],
                "success_data": {"pong": True, "transport": "sse"},
            },
            {
                "name": "search_docs",
                "description": "搜文档（Mock 数据）",
                "params": [
                    _p("query", "string", required=True, description="搜索词"),
                    _p("limit", "integer", description="最多返回几条", default=5, minimum=1, maximum=50),
                ],
                "success_data": {
                    "hits": [
                        {"title": "接入指南", "score": 0.94, "url": "https://example.com/doc/1"},
                        {"title": "常见问题", "score": 0.81, "url": "https://example.com/doc/2"},
                    ],
                    "total": 2,
                },
            },
        ],
    },
    {
        "slug": "bearer",
        "name": "Bearer Token 认证服务（HTTP）",
        "description": f"要求请求头带 Authorization: Bearer {DEMO_BEARER_TOKEN}。不带或带错一律 401，用来验客户端的认证配置对不对。",
        "instructions": "Lumiere MCP Mock —— 需要 Bearer Token 才能访问。",
        "transport": "streamable-http",
        "auth_type": "bearer",
        "auth_config": {"token": DEMO_BEARER_TOKEN},
        "validate_mode": "strict",
        "tools": [
            {
                "name": "whoami",
                "description": "返回当前调用方身份（Mock）",
                "params": [],
                "success_data": {"user": "mock-user", "tenant": "demo", "scopes": ["read", "write"]},
            },
            {
                "name": "create_ticket",
                "description": "建一张工单（Mock，不会真落库）",
                "params": [
                    _p("title", "string", required=True, description="工单标题", minLength=1, maxLength=100),
                    _p("priority", "string", description="优先级", default="P2", enum=["P0", "P1", "P2", "P3"]),
                    _p("labels", "array", description="标签列表", itemsType="string"),
                ],
                "success_data": {"id": "TK-10001", "title": "(mock) 新建工单", "priority": "P2", "status": "open"},
            },
        ],
    },
    {
        "slug": "apikey",
        "name": "API Key 认证服务（HTTP）",
        "description": f"要求自定义请求头 {DEMO_API_KEY_HEADER}: {DEMO_API_KEY}。和 Bearer 的区别是头的名字可以自己定，很多内网服务是这种。",
        "instructions": "Lumiere MCP Mock —— 需要自定义 API Key 请求头才能访问。",
        "transport": "streamable-http",
        "auth_type": "apikey",
        "auth_config": {"headerName": DEMO_API_KEY_HEADER, "apiKey": DEMO_API_KEY},
        "validate_mode": "strict",
        "tools": [
            {
                "name": "get_quota",
                "description": "查当前 Key 的配额（Mock）",
                "params": [],
                "success_data": {"used": 128, "limit": 1000, "resetAt": "2026-10-01T00:00:00Z"},
            },
            {
                "name": "query_metrics",
                "description": "查指标（Mock 数据）",
                "params": [
                    _p("metric", "string", required=True, description="指标名", enum=["qps", "latency", "error_rate"]),
                    _p("since", "string", description="起始时间 ISO8601"),
                    _p("group_by", "string", description="分组维度"),
                ],
                "success_data": {"metric": "qps", "points": [[1759104000, 120], [1759107600, 138]], "unit": "req/s"},
            },
        ],
    },
    {
        "slug": "strict",
        "name": "严格参数校验服务（HTTP）",
        "description": "六种参数类型全都有，必填/枚举/范围/长度都卡死，还不收多余参数。传错一个就报错，用来验客户端有没有按 schema 传参。",
        "instructions": "Lumiere MCP Mock —— 入参严格校验，不符合 schema 一律报错。",
        "transport": "streamable-http",
        "auth_type": "none",
        "validate_mode": "strict",
        "tools": [
            {
                "name": "submit_order",
                "description": "提交订单 —— 参数类型齐全的示例，六种类型都在这里",
                "params": [
                    _p("order_no", "string", required=True, description="订单号", minLength=4, maxLength=32),
                    _p("quantity", "integer", required=True, description="数量", minimum=1, maximum=999),
                    _p("amount", "number", required=True, description="金额", minimum=0),
                    _p("need_invoice", "boolean", description="要不要开票", default=False),
                    _p("sku_list", "array", description="商品编码列表", itemsType="string"),
                    _p("extra", "object", description="附加信息，任意 JSON"),
                    _p("channel", "string", description="下单渠道", default="web", enum=["web", "app", "api"]),
                ],
                "success_data": {"orderNo": "SO-20260929-0001", "status": "created", "amount": 199.0},
            },
            {
                "name": "cancel_order",
                "description": "取消订单，只认已存在的订单号",
                "params": [
                    _p("order_no", "string", required=True, description="订单号"),
                    _p("reason", "string", required=True, description="取消原因", enum=["用户取消", "超时未付", "缺货"]),
                ],
                "success_data": {"orderNo": "SO-20260929-0001", "status": "cancelled"},
            },
        ],
    },
    {
        "slug": "loose",
        "name": "宽松参数校验服务（HTTP）",
        "description": "参数表和严格那个一样，但松：数字传成字符串会自动转，必填漏了补默认值，多传的照收。用来验客户端传参不规范时对方还能不能用。",
        "instructions": "Lumiere MCP Mock —— 入参宽松校验，能转就转、能补就补。",
        "transport": "streamable-http",
        "auth_type": "none",
        "validate_mode": "loose",
        "tools": [
            {
                "name": "submit_order",
                "description": "提交订单 —— 同样的参数表，但校验很松",
                "params": [
                    _p("order_no", "string", required=True, description="订单号"),
                    _p("quantity", "integer", required=True, description="数量，传 \"3\" 也认", default=1),
                    _p("amount", "number", description="金额"),
                    _p("need_invoice", "boolean", description="要不要开票，传 \"true\" 也认", default=False),
                    _p("sku_list", "array", description="商品编码列表", itemsType="string"),
                ],
                "success_data": {"orderNo": "SO-20260929-0002", "status": "created", "note": "宽松模式：参数已自动收拾"},
            },
        ],
    },
    {
        "slug": "faulty",
        "name": "异常服务（HTTP·专门用来制造失败）",
        "description": "三个工具分别演：调用报错、响应很慢（3 秒）、返回空。客户端的重试、超时、空结果处理靠它验。",
        "instructions": "Lumiere MCP Mock —— 故意制造各种失败，用于客户端容错验证。",
        "transport": "streamable-http",
        "auth_type": "none",
        "validate_mode": "off",
        "tools": [
            {
                "name": "always_fail",
                "description": "必定报错，用来验客户端的错误处理",
                "params": [_p("anything", "string", description="随便传，反正会报错")],
                "mode": "error",
                "error_message": "Mock error: 上游服务不可用（这是故意的）",
            },
            {
                "name": "slow_response",
                "description": "等 3 秒才返回，用来验客户端超时设置",
                "params": [],
                "delay_ms": 3000,
                "success_data": {"ok": True, "waitedMs": 3000},
            },
            {
                "name": "empty_result",
                "description": "返回空对象，用来验客户端对空结果的处理",
                "params": [],
                "success_data": {},
            },
        ],
    },
]

# 预置服务上只刷这些「说明性」字段，行为字段（认证/校验/传输/启停）不动 ——
# 人改过认证方式之后，重启一次被悄悄改回去，表现是「昨天连得上今天 401」，还查不出原因。
_SERVER_META_FIELDS = ("name", "description", "instructions", "sort_order")
_TOOL_META_FIELDS = ("description",)


async def ensure_preset_servers(session: AsyncSession) -> dict[str, int]:
    """幂等落地预置服务。返回 {servers_created, tools_created, refreshed}。

    · 没这个 slug → 按定义整套建出来（服务 + 工具），builtin=True、locked=True
    · 已有        → 只刷名称/说明/排序，**行为和工具内容一个字不改**
    任何情况下都不删东西。
    """
    stats = {"servers_created": 0, "tools_created": 0, "refreshed": 0}
    existing = {
        s.slug: s for s in (await session.execute(select(McpMockServer))).scalars().all()
    }

    for idx, spec in enumerate(PRESET_SERVERS):
        slug = spec["slug"]
        row = existing.get(slug)
        if row is not None:
            for f in _SERVER_META_FIELDS:
                if f == "sort_order":
                    row.sort_order = idx
                elif f in spec:
                    setattr(row, f, spec[f])
            row.builtin = True
            stats["refreshed"] += 1
            continue

        server = McpMockServer(
            slug=slug,
            name=spec["name"],
            description=spec.get("description", ""),
            instructions=spec.get("instructions", ""),
            transport=spec.get("transport", "streamable-http"),
            auth_type=spec.get("auth_type", "none"),
            auth_config=spec.get("auth_config"),
            validate_mode=spec.get("validate_mode", "strict"),
            enabled=True,
            builtin=True,
            locked=True,
            sort_order=idx,
        )
        session.add(server)
        await session.flush()
        stats["servers_created"] += 1

        for order, t in enumerate(spec.get("tools", [])):
            session.add(McpMockTool(
                server_id=server.id,
                name=t["name"],
                description=t.get("description", ""),
                params=t.get("params") or [],
                mode=t.get("mode", "success"),
                success_data=t.get("success_data"),
                custom_data=t.get("custom_data"),
                custom_is_error=bool(t.get("custom_is_error", False)),
                error_message=t.get("error_message", "Mock error: tool call failed"),
                delay_ms=int(t.get("delay_ms", 0)),
                enabled=True,
                locked=True,
                sort_order=order,
            ))
            stats["tools_created"] += 1

    await session.flush()
    return stats


async def seed_preset_servers() -> dict[str, int] | None:
    """启动时调用的包装：自己开会话、自己提交，出错只记日志不拦启动。"""
    from app.deps.db import async_session_factory

    try:
        async with async_session_factory() as session:
            stats = await ensure_preset_servers(session)
            await _migrate_legacy_tools(session)
            await session.commit()
        if stats["servers_created"]:
            logger.info(
                "MCP Mock 预置服务：新建 %d 个服务、%d 个工具",
                stats["servers_created"], stats["tools_created"],
            )
        return stats
    except Exception as e:  # noqa: BLE001
        logger.warning("MCP Mock 预置服务落地失败（不影响服务启动）: %s", e)
        return None


LEGACY_SLUG = "legacy"
_LEGACY_FILE_NAME = "mcp_mock_tools.json"


async def _migrate_legacy_tools(session: AsyncSession) -> int:
    """把老的单服务 JSON 文件（.mock_state/mcp_mock_tools.json）搬进一个普通服务。

    老版本只有一个 MCP 服务，工具存在 JSON 文件里，人可能已经改过、加过。
    改成多服务之后那个文件不再被读 —— **不搬的话，人配过的东西会在某次升级后
    静悄悄地消失**，而页面上只会显示「预置服务好几个」，看不出少了什么。

    搬进来的服务是**普通服务**（builtin=False），可以改可以删 —— 那是人自己的东西，
    不是平台发的。搬完把文件改名成 `.migrated`，再启动就不重复搬了。
    """
    import json
    from pathlib import Path

    legacy_file = Path(__file__).resolve().parent.parent.parent / ".mock_state" / _LEGACY_FILE_NAME
    if not legacy_file.exists():
        return 0
    try:
        tools = json.loads(legacy_file.read_text())
    except Exception:  # noqa: BLE001
        legacy_file.rename(legacy_file.with_suffix(".json.broken"))
        return 0
    if not isinstance(tools, list) or not tools:
        legacy_file.rename(legacy_file.with_suffix(".json.migrated"))
        return 0

    exists = await session.scalar(select(McpMockServer).where(McpMockServer.slug == LEGACY_SLUG))
    if exists is not None:
        legacy_file.rename(legacy_file.with_suffix(".json.migrated"))
        return 0

    from app.services.mcp_mock_validate import normalize_params

    server = McpMockServer(
        slug=LEGACY_SLUG,
        name="原有工具（升级前配的）",
        description="改成多服务之前那一份 MCP Mock 的工具，原样搬过来了。这是你自己的服务，可以改也可以删。",
        instructions="Lumiere MCP Mock —— 升级前配置的工具集合。",
        transport="streamable-http",
        auth_type="none",
        validate_mode="off",
        enabled=True,
        builtin=False,
        locked=False,
        sort_order=100,
    )
    session.add(server)
    await session.flush()

    count = 0
    for order, t in enumerate(tools):
        if not isinstance(t, dict) or not t.get("name"):
            continue
        session.add(McpMockTool(
            server_id=server.id,
            name=str(t["name"])[:100],
            description=str(t.get("description") or ""),
            params=normalize_params(t.get("params")),
            mode=t.get("mode", "success"),
            success_data=t.get("successData"),
            custom_data=t.get("customData"),
            custom_is_error=bool(t.get("customIsError", False)),
            enabled=bool(t.get("enabled", True)),
            locked=bool(t.get("locked", False)),
            sort_order=order,
        ))
        count += 1
    await session.flush()
    legacy_file.rename(legacy_file.with_suffix(".json.migrated"))
    logger.info("MCP Mock：老的 %d 个工具已搬进「原有工具」服务", count)
    return count
