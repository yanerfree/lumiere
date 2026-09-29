"""MCP Mock API —— 多服务版：服务 CRUD + 服务下的工具 CRUD + 每服务的调用日志。

地址形状 `http://<host>:28300/<slug>/mcp`，所有服务共用一个端口，靠路径分开。

⚠ 凡是**改变了「有哪些服务 / 有哪些工具 / 工具参数长什么样」**的操作，都得重载一次
  Mock 服务 —— 工具是建 app 时一次性注册进 FastMCP 的。返回里的 `reloaded` /
  `reloadError` 就是这件事的结果，**故意不叫 `error`**：改动其实已经存下来了，
  混用会让「删成功了但没重载」在页面上报成「删除失败」。
  改返回值、改延迟这类**不**用重载（运行时现查库），所以那些接口不带这两个字段。
"""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.db import get_db
from app.models.mcp_mock import McpMockServer, McpMockTool
from app.services import mcp_mock_service as svc
from app.services.mcp_mock_manager import mcp_mock_server as mgr
from app.services.mcp_mock_validate import (
    PARAM_TYPES,
    VALIDATE_MODES,
    build_schema,
    normalize_params,
    validate_arguments,
)

router = APIRouter(prefix="/api/mcp-mock", tags=["mcp-mock"])

TRANSPORTS = ("streamable-http", "sse")
AUTH_TYPES = ("none", "bearer", "apikey")


# ── Schemas ──

class ServerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=50)
    description: str = ""
    instructions: str = ""
    transport: str = "streamable-http"
    authType: str = "none"
    authConfig: dict | None = None
    validateMode: str = "strict"


class ServerUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    transport: str | None = None
    authType: str | None = None
    authConfig: dict | None = None
    validateMode: str | None = None
    enabled: bool | None = None


class ToolCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = ""
    params: list | dict | None = None
    successData: dict | list | None = None
    mode: str = Field("success", pattern="^(success|error|custom)$")
    errorMessage: str | None = None
    delayMs: int | None = None


class ToolUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    params: list | dict | None = None
    mode: str | None = Field(None, pattern="^(success|error|custom)$")
    successData: dict | list | None = None
    customData: dict | list | None = None
    customIsError: bool | None = None
    errorMessage: str | None = None
    delayMs: int | None = None
    enabled: bool | None = None


class CallBody(BaseModel):
    tool: str
    arguments: dict | None = None


# ── 序列化 ──

def _host_of(request: Request) -> str:
    """Mock 地址用哪个主机名 —— 照用户访问平台用的那个。

    写死 localhost 的话，别人从自己机器打开页面复制走的地址指向**他自己的电脑**，
    连不上，而且报错长得像 Mock 服务没起来。
    """
    return (request.url.hostname or "localhost")


def _server_json(s: McpMockServer, request: Request, tool_count: int | None = None) -> dict:
    return {
        "id": str(s.id),
        "name": s.name,
        "slug": s.slug,
        "description": s.description or "",
        "instructions": s.instructions or "",
        "transport": s.transport,
        "authType": s.auth_type,
        "authConfig": s.auth_config or {},
        "validateMode": s.validate_mode,
        "enabled": s.enabled,
        "builtin": s.builtin,
        "locked": s.locked,
        "sortOrder": s.sort_order,
        "callCount": s.call_count or 0,
        "lastCallAt": s.last_call_at.isoformat() if s.last_call_at else None,
        "url": mgr.url_for(s.slug, _host_of(request)),
        "toolCount": tool_count,
    }


def _tool_json(t: McpMockTool, validate_mode: str = "strict") -> dict:
    params = normalize_params(t.params)
    return {
        "id": str(t.id),
        "serverId": str(t.server_id),
        "name": t.name,
        "description": t.description or "",
        "params": params,
        "inputSchema": build_schema(params, validate_mode),
        "mode": t.mode,
        "successData": t.success_data,
        "customData": t.custom_data,
        "hasCustomData": t.custom_data is not None,
        "customIsError": t.custom_is_error,
        "errorMessage": t.error_message,
        "delayMs": t.delay_ms or 0,
        "enabled": t.enabled,
        "locked": t.locked,
        "sortOrder": t.sort_order,
    }


def _log_json(log) -> dict:
    return {
        "id": str(log.id),
        "serverId": str(log.server_id) if log.server_id else None,
        "timestamp": log.timestamp.isoformat() if log.timestamp else None,
        "tool": log.tool_name,
        "arguments": log.arguments or {},
        "response": log.response,
        "source": log.source,
        "mode": log.mode,
        "isError": log.is_error,
        "rejectKind": log.reject_kind,
        "rejectDetail": log.reject_detail,
        "elapsedMs": log.elapsed_ms,
    }


def _bad(msg: str, code: int = 400):
    return JSONResponse({"error": msg}, status_code=code)


def _check_server_writable(s: McpMockServer, action: str):
    """预置 / 锁定的判据只在这一处，别在各个路由里各写一遍。"""
    if s.locked:
        return _bad(f"服务已锁定，请先解锁后再{action}", 423)
    return None


# ── 元信息（页面上的下拉都从这里拿，别在前端各写一份）──

@router.get("/meta")
async def get_meta():
    return {"data": {
        "transports": [
            {"value": "streamable-http", "label": "Streamable HTTP", "supported": True,
             "hint": "现在的 MCP 主流传输，Claude Code / Cursor 都用它"},
            {"value": "sse", "label": "SSE", "supported": True,
             "hint": "老版传输，还有存量客户端在用，测兼容性用"},
            {"value": "stdio", "label": "stdio（暂不支持）", "supported": False,
             "hint": "stdio 是本地进程管道，不走网络 —— 平台这边起不了也给不出地址。"
                     "要测 stdio 得在客户端本机跑一个命令行进程。"},
        ],
        "authTypes": [
            {"value": "none", "label": "不认证"},
            {"value": "bearer", "label": "Bearer Token"},
            {"value": "apikey", "label": "自定义 API Key 请求头"},
        ],
        "validateModes": [
            {"value": "strict", "label": "严格",
             "hint": "缺必填、类型不符、不在枚举、越界、多传没声明的参数，一律打回"},
            {"value": "loose", "label": "宽松",
             "hint": "类型能转就转（\"3\"→3），缺必填补零值，多传的留着 —— 测客户端乱传时对方兜不兜得住"},
            {"value": "off", "label": "不校验",
             "hint": "参数原样收下，永远不报错 —— 测客户端对「服务端来者不拒」的处理"},
        ],
        "paramTypes": list(PARAM_TYPES),
    }}


# ── 服务 ──

@router.get("/servers")
async def list_servers(request: Request, session: AsyncSession = Depends(get_db)):
    servers = await svc.list_servers(session)
    out = []
    for s in servers:
        tools = await svc.list_tools(session, s.id)
        out.append(_server_json(s, request, tool_count=len(tools)))
    return {"data": out}


@router.post("/servers", status_code=201)
async def create_server(body: ServerCreate, request: Request, session: AsyncSession = Depends(get_db)):
    slug = (body.slug or "").strip().lower()
    err = svc.slug_error(slug)
    if err:
        return _bad(err)
    if await svc.get_server_by_slug(session, slug):
        return _bad(f"服务代号 {slug} 已经被占用了，换一个（它会出现在地址里，不能重复）", 409)
    if body.transport not in TRANSPORTS:
        return _bad("传输方式只能是 Streamable HTTP 或 SSE（stdio 暂不支持）")
    if body.authType not in AUTH_TYPES:
        return _bad("认证方式不对")
    if body.validateMode not in VALIDATE_MODES:
        return _bad("参数校验松紧不对")
    server = await svc.create_server(session, {
        "slug": slug,
        "name": body.name,
        "description": body.description,
        "instructions": body.instructions,
        "transport": body.transport,
        "auth_type": body.authType,
        "auth_config": body.authConfig,
        "validate_mode": body.validateMode,
    })
    await session.commit()
    rl = await mgr.reload()
    return {"data": {**_server_json(server, request, tool_count=0), **rl}}


@router.get("/servers/{server_id}")
async def get_server(server_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_db)):
    server = await svc.get_server(session, server_id)
    if not server:
        return _bad("该服务不存在（可能已被删除），刷新一下页面", 404)
    tools = await svc.list_tools(session, server.id)
    return {"data": {
        **_server_json(server, request, tool_count=len(tools)),
        "tools": [_tool_json(t, server.validate_mode) for t in tools],
    }}


@router.put("/servers/{server_id}")
async def update_server(
    server_id: uuid.UUID, body: ServerUpdate, request: Request,
    session: AsyncSession = Depends(get_db),
):
    server = await svc.get_server(session, server_id)
    if not server:
        return _bad("该服务不存在（可能已被删除），刷新一下页面", 404)
    blocked = _check_server_writable(server, "编辑")
    if blocked:
        return blocked
    if body.transport is not None and body.transport not in TRANSPORTS:
        return _bad("传输方式只能是 Streamable HTTP 或 SSE（stdio 暂不支持）")
    if body.authType is not None and body.authType not in AUTH_TYPES:
        return _bad("认证方式不对")
    if body.validateMode is not None and body.validateMode not in VALIDATE_MODES:
        return _bad("参数校验松紧不对")
    if body.authType == "bearer" and not (body.authConfig or {}).get("token"):
        return _bad("选了 Bearer Token 就得填一个 token，否则对面永远连不上")
    if body.authType == "apikey" and not (body.authConfig or {}).get("apiKey"):
        return _bad("选了 API Key 就得填一个 key，否则对面永远连不上")
    data = {
        "name": body.name,
        "description": body.description,
        "instructions": body.instructions,
        "transport": body.transport,
        "auth_type": body.authType,
        "validate_mode": body.validateMode,
        "enabled": body.enabled,
    }
    if "authConfig" in body.model_fields_set:
        data["auth_config"] = body.authConfig
    await svc.update_server(session, server, data)
    await session.commit()
    rl = await mgr.reload()
    tools = await svc.list_tools(session, server.id)
    return {"data": {**_server_json(server, request, tool_count=len(tools)), **rl}}


@router.patch("/servers/{server_id}/lock")
async def lock_server(server_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_db)):
    server = await svc.get_server(session, server_id)
    if not server:
        return _bad("该服务不存在（可能已被删除），刷新一下页面", 404)
    server.locked = not server.locked
    await session.commit()
    return {"data": {"id": str(server.id), "locked": server.locked}}


@router.patch("/servers/{server_id}/toggle")
async def toggle_server(server_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_db)):
    server = await svc.get_server(session, server_id)
    if not server:
        return _bad("该服务不存在（可能已被删除），刷新一下页面", 404)
    blocked = _check_server_writable(server, "停用/启用")
    if blocked:
        return blocked
    server.enabled = not server.enabled
    await session.commit()
    rl = await mgr.reload()
    return {"data": {"id": str(server.id), "enabled": server.enabled, **rl}}


@router.delete("/servers/{server_id}")
async def delete_server(server_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    server = await svc.get_server(session, server_id)
    if not server:
        return _bad("该服务不存在（可能已被删除），刷新一下页面", 404)
    if server.builtin:
        # 预置的一律不许删 —— **解锁也不行**。每次启动都会按定义补回来，
        # 删掉的效果只是「重启前它不在、重启后又回来了」，看着像平台自己乱加东西。
        # 不想用就停用，那是个能留住的状态。
        return _bad("预置服务不允许删除（解锁也不行）。不想用它就点「解锁」再「停用」")
    if server.locked:
        return _bad("服务已锁定，请先解锁后再删除", 423)
    await svc.delete_server(session, server)
    await session.commit()
    rl = await mgr.reload()
    return {"ok": True, **rl}


# ── 工具（挂在服务下）──

@router.get("/servers/{server_id}/tools")
async def list_tools(server_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    server = await svc.get_server(session, server_id)
    if not server:
        return _bad("该服务不存在（可能已被删除），刷新一下页面", 404)
    tools = await svc.list_tools(session, server_id)
    return {"data": [_tool_json(t, server.validate_mode) for t in tools]}


@router.post("/servers/{server_id}/tools", status_code=201)
async def create_tool(server_id: uuid.UUID, body: ToolCreate, session: AsyncSession = Depends(get_db)):
    server = await svc.get_server(session, server_id)
    if not server:
        return _bad("该服务不存在（可能已被删除），刷新一下页面", 404)
    blocked = _check_server_writable(server, "加工具")
    if blocked:
        return blocked
    name = (body.name or "").strip()
    if await svc.get_tool_by_name(session, server_id, name):
        return _bad(f"这个服务下已经有叫 {name} 的工具了", 409)
    tool = await svc.create_tool(session, server_id, {
        "name": name,
        "description": body.description,
        "params": normalize_params(body.params),
        "mode": body.mode,
        "success_data": body.successData,
        "error_message": body.errorMessage,
        "delay_ms": body.delayMs,
    })
    await session.commit()
    rl = await mgr.reload()
    return {"data": {**_tool_json(tool, server.validate_mode), **rl}}


@router.put("/servers/{server_id}/tools/{tool_id}")
async def update_tool(
    server_id: uuid.UUID, tool_id: uuid.UUID, body: ToolUpdate,
    session: AsyncSession = Depends(get_db),
):
    server = await svc.get_server(session, server_id)
    tool = await svc.get_tool(session, tool_id)
    if not server or not tool or tool.server_id != server_id:
        return _bad("该工具不存在（可能已被删除），刷新一下页面", 404)
    if tool.locked:
        return _bad("工具已锁定，请先解锁后再编辑", 423)
    if server.locked:
        return _bad("服务已锁定，请先解锁后再编辑它的工具", 423)
    if body.name is not None and body.name.strip() != tool.name:
        dup = await svc.get_tool_by_name(session, server_id, body.name.strip())
        if dup:
            return _bad(f"这个服务下已经有叫 {body.name.strip()} 的工具了", 409)

    data: dict = {
        "name": body.name.strip() if body.name else None,
        "description": body.description,
        "mode": body.mode,
        "success_data": body.successData,
        "custom_is_error": body.customIsError,
        "error_message": body.errorMessage,
        "delay_ms": body.delayMs,
        "enabled": body.enabled,
    }
    if "params" in body.model_fields_set:
        data["params"] = normalize_params(body.params)
    if "customData" in body.model_fields_set:
        data["custom_data"] = body.customData
    # 改了「有哪些工具 / 参数长什么样 / 叫什么」才要重载；
    # 只改返回值、延迟、模式的话运行时现查库就够了，不必踢断已连着的客户端。
    need_reload = any(k in body.model_fields_set for k in ("name", "params", "description", "enabled"))
    await svc.update_tool(session, tool, data)
    await session.commit()
    rl = await mgr.reload() if need_reload else {}
    return {"data": {**_tool_json(tool, server.validate_mode), **rl}}


@router.patch("/servers/{server_id}/tools/{tool_id}/lock")
async def lock_tool(server_id: uuid.UUID, tool_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    tool = await svc.get_tool(session, tool_id)
    if not tool or tool.server_id != server_id:
        return _bad("该工具不存在（可能已被删除），刷新一下页面", 404)
    tool.locked = not tool.locked
    await session.commit()
    return {"data": {"id": str(tool.id), "locked": tool.locked}}


@router.patch("/servers/{server_id}/tools/{tool_id}/toggle")
async def toggle_tool(server_id: uuid.UUID, tool_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    tool = await svc.get_tool(session, tool_id)
    if not tool or tool.server_id != server_id:
        return _bad("该工具不存在（可能已被删除），刷新一下页面", 404)
    if tool.locked:
        return _bad("工具已锁定，请先解锁后再操作", 423)
    tool.enabled = not tool.enabled
    await session.commit()
    rl = await mgr.reload()
    return {"data": {"id": str(tool.id), "enabled": tool.enabled, **rl}}


@router.delete("/servers/{server_id}/tools/{tool_id}")
async def delete_tool(server_id: uuid.UUID, tool_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    server = await svc.get_server(session, server_id)
    tool = await svc.get_tool(session, tool_id)
    if not server or not tool or tool.server_id != server_id:
        return _bad("该工具不存在（可能已被删除），刷新一下页面", 404)
    if tool.locked:
        return _bad("工具已锁定，请先解锁后再删除", 423)
    if server.locked:
        return _bad("服务已锁定，请先解锁后再删它的工具", 423)
    await svc.delete_tool(session, tool)
    await session.commit()
    rl = await mgr.reload()
    return {"ok": True, **rl}


# ── 服务控制 ──

@router.get("/status")
async def get_status(request: Request, session: AsyncSession = Depends(get_db)):
    servers = await svc.list_servers(session)
    _, total_logs = await svc.list_logs(session, limit=1)
    return {"data": {
        "running": mgr.running,
        "port": mgr.port,
        "baseUrl": mgr.base_url(_host_of(request)),
        "serversCount": len(servers),
        "serversEnabled": sum(1 for s in servers if s.enabled),
        "totalLogs": total_logs,
        # 上次起服务时真挂上去的那几个 —— 和数据库对不上就说明改完没重载
        "mounted": mgr.mounted,
    }}


@router.post("/start")
async def start_service():
    try:
        await mgr.start()
    except RuntimeError as e:
        return _bad(str(e))
    return {"ok": True, "port": mgr.port, "mounted": mgr.mounted}


@router.post("/stop")
async def stop_service():
    await mgr.stop()
    return {"ok": True}


@router.post("/reload")
async def reload_service():
    rl = await mgr.reload()
    return {"ok": True, **rl, "mounted": mgr.mounted}


# ── 调用测试（页面上点的那个，不经过 MCP 协议）──

@router.post("/servers/{server_id}/call")
async def call_tool(server_id: uuid.UUID, body: CallBody, session: AsyncSession = Depends(get_db)):
    """页面上的「调用测试」。

    ⚠ 它**走同一套参数校验**（严格/宽松/不校验），否则页面上试着能过、
      对面客户端打过来却被打回，人只会觉得这个 Mock 时灵时不灵。
      认证那一层不走 —— 页面是平台内部调用，本来就没走那个网关。
    """
    from app.services.mcp_mock_manager import _compute

    server = await svc.get_server(session, server_id)
    if not server:
        return _bad("该服务不存在（可能已被删除），刷新一下页面", 404)
    tool = await svc.get_tool_by_name(session, server_id, body.tool)
    if not tool:
        tools = await svc.list_tools(session, server_id)
        return {"data": None, "error": f"工具 {body.tool} 不存在",
                "available": [t.name for t in tools]}

    t0 = time.perf_counter()
    args = body.arguments or {}
    errors, cleaned = validate_arguments(normalize_params(tool.params), args, server.validate_mode)
    if errors:
        detail = "；".join(errors)
        await mgr.record_call(
            server_id=server.id, tool_name=tool.name, arguments=args,
            response={"error": detail}, source="call", mode="validate",
            is_error=True, t0=t0, reject_kind="validate", reject_detail=detail,
        )
        return {"data": None, "error": f"参数校验不通过：{detail}",
                "tool": tool.name, "mode": "validate", "rejectKind": "validate"}

    payload, is_error, err_msg = _compute({
        "mode": tool.mode,
        "success_data": tool.success_data,
        "custom_data": tool.custom_data,
        "custom_is_error": tool.custom_is_error,
        "error_message": tool.error_message,
    })
    await mgr.record_call(
        server_id=server.id, tool_name=tool.name, arguments=cleaned,
        response=payload if not is_error else {"error": err_msg},
        source="call", mode=tool.mode, is_error=is_error, t0=t0,
    )
    if is_error:
        return {"data": None, "error": err_msg, "tool": tool.name, "mode": tool.mode}
    return {"data": payload, "source": "mock", "mode": tool.mode, "tool": tool.name,
            "arguments": cleaned}


@router.get("/servers/{server_id}/tools/{tool_id}/preview")
async def preview_tool(server_id: uuid.UUID, tool_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    from app.services.mcp_mock_manager import _compute

    tool = await svc.get_tool(session, tool_id)
    if not tool or tool.server_id != server_id:
        return _bad("该工具不存在（可能已被删除），刷新一下页面", 404)
    payload, is_error, err_msg = _compute({
        "mode": tool.mode,
        "success_data": tool.success_data,
        "custom_data": tool.custom_data,
        "custom_is_error": tool.custom_is_error,
        "error_message": tool.error_message,
    })
    return {"data": payload if not is_error else {"error": err_msg}, "mode": tool.mode,
            "isError": is_error}


# ── 日志 ──

@router.get("/logs")
async def get_logs(
    serverId: uuid.UUID | None = Query(None),
    tool: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    logs, total = await svc.list_logs(
        session, server_id=serverId, tool_name=tool, status=status, limit=limit, offset=offset
    )
    return {"data": [_log_json(l) for l in logs], "total": total}


@router.delete("/logs")
async def clear_logs(
    serverId: uuid.UUID | None = Query(None),
    session: AsyncSession = Depends(get_db),
):
    count = await svc.clear_logs(session, server_id=serverId)
    await session.commit()
    return {"ok": True, "deleted": count}
