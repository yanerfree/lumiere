"""MCP Mock API — 工具管理 + 服务控制 + 调用日志，对齐 LLM/API Mock 的端点结构"""
from __future__ import annotations

import time

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.mcp_mock_manager import mcp_mock_server as mgr

router = APIRouter(prefix="/api/mcp-mock", tags=["mcp-mock"])


# ── Schemas ──

class ToolCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = ""
    params: dict | None = None
    successData: dict | list | None = None


class ToolUpdate(BaseModel):
    description: str | None = None
    mode: str | None = Field(None, pattern="^(success|error|custom)$")
    enabled: bool | None = None
    custom_data: dict | list | None = None
    custom_is_error: bool | None = None
    success_data: dict | list | None = None
    params: dict | None = None


class ServiceConfig(BaseModel):
    port: int | None = None
    transport: str | None = None


# ── 工具管理 ──

@router.get("/tools")
async def list_tools():
    tools = mgr.get_tools()
    return {"data": [
        {
            "name": t["name"],
            "description": t.get("description", ""),
            "mode": t["mode"],
            "enabled": t.get("enabled", True),
            "locked": t.get("locked", False),
            "hasCustomData": t.get("customData") is not None,
            "params": t.get("params", {}),
        }
        for t in tools
    ]}


@router.post("/tools", status_code=201)
async def create_tool(body: ToolCreate):
    if mgr.get_tool(body.name):
        return {"error": f"工具 {body.name} 已存在"}
    tool = mgr.add_tool(body.model_dump())
    # 工具是建 app 时注册进 FastMCP 的，不重载的话对面 CC 的 tools/list 里根本没有
    rl = await mgr.reload()
    return {"data": {**tool, **rl}}


@router.patch("/tools/{tool_name}/lock")
async def lock_tool(tool_name: str):
    tool = mgr.toggle_lock(tool_name)
    if not tool:
        return JSONResponse({"error": f"工具 {tool_name} 不存在"}, status_code=404)
    return {"data": {"name": tool_name, "locked": tool["locked"]}}


@router.put("/tools/{tool_name}")
async def update_tool(tool_name: str, body: ToolUpdate):
    existing = mgr.get_tool(tool_name)
    if not existing:
        return {"error": f"工具 {tool_name} 不存在"}
    if existing.get("locked", False):
        return JSONResponse({"error": "工具已锁定，请先解锁后再编辑"}, status_code=423)
    update = {}
    if body.description is not None:
        update["description"] = body.description
    if body.mode is not None:
        update["mode"] = body.mode
    if body.enabled is not None:
        update["enabled"] = body.enabled
    if body.custom_data is not None:
        update["customData"] = body.custom_data
    if body.custom_is_error is not None:
        update["customIsError"] = body.custom_is_error
    if body.success_data is not None:
        update["successData"] = body.success_data
    if body.params is not None:
        update["params"] = body.params
    result = mgr.update_tool(tool_name, update)
    rl = await mgr.reload()
    return {"data": {"name": tool_name, "mode": result["mode"], **rl}}


@router.delete("/tools/{tool_name}")
async def delete_tool(tool_name: str):
    tools = mgr.get_tools()
    if len(tools) <= 1:
        return {"error": "至少保留一个工具"}
    tool = mgr.get_tool(tool_name)
    if not tool:
        return {"error": f"工具 {tool_name} 不存在"}
    if tool.get("locked", False):
        return JSONResponse({"error": "工具已锁定，请先解锁后再删除"}, status_code=423)
    mgr.delete_tool(tool_name)
    rl = await mgr.reload()
    return {"ok": True, **rl}


@router.patch("/tools/{tool_name}/toggle")
async def toggle_tool(tool_name: str):
    tool = mgr.get_tool(tool_name)
    if not tool:
        return {"error": f"工具 {tool_name} 不存在"}
    if tool.get("locked", False):
        return JSONResponse({"error": "工具已锁定，请先解锁后再操作"}, status_code=423)
    mgr.update_tool(tool_name, {"enabled": not tool.get("enabled", True)})
    rl = await mgr.reload()
    return {"data": {"name": tool_name, "enabled": not tool.get("enabled", True), **rl}}


# ── 服务控制 ──

@router.get("/status")
async def get_status():
    tools = mgr.get_tools()
    return {
        "data": {
            "running": mgr.running,
            "port": mgr.port,
            "transport": mgr.transport,
            "toolsCount": len(tools),
            "toolsEnabled": sum(1 for t in tools if t.get("enabled", True)),
            "totalLogs": len(mgr._call_logs),
        }
    }


@router.post("/start")
async def start_service():
    await mgr.start()
    return {"ok": True, "port": mgr.port}


@router.post("/stop")
async def stop_service():
    await mgr.stop()
    return {"ok": True}


@router.put("/config")
async def update_config(body: ServiceConfig):
    was_running = mgr.running
    if was_running:
        await mgr.stop()
    if body.port is not None:
        mgr.port = body.port
    if body.transport is not None and body.transport in ("streamable-http", "sse"):
        mgr.transport = body.transport
    if was_running:
        await mgr.start()
    return {"data": {"port": mgr.port, "transport": mgr.transport, "running": mgr.running}}


# ── 调用测试 ──

@router.post("/call")
async def call_tool(body: dict):
    tool_name = body.get("tool", "")
    arguments = body.get("arguments", {})
    t0 = time.perf_counter()

    tool = mgr.get_tool(tool_name)
    if not tool:
        return {"data": None, "error": f"工具 {tool_name} 不存在", "available": [t["name"] for t in mgr.get_tools()]}

    response = mgr.compute_response(tool_name)
    if response is None:
        response = {"result": "ok"}
    mode = tool["mode"]
    is_error = mode == "error" or (tool.get("customIsError") and mode == "custom")
    mgr.log_call(tool_name, arguments, response, "call", mode, is_error, t0)
    return {"data": response, "source": "mock", "mode": mode, "tool": tool_name}


@router.get("/preview/{tool_name}")
async def preview_tool(tool_name: str):
    tool = mgr.get_tool(tool_name)
    if not tool:
        return {"data": None, "error": f"工具 {tool_name} 不存在"}
    response = mgr.compute_response(tool_name)
    return {"data": response, "mode": tool["mode"]}


# ── 日志 ──

@router.get("/logs")
async def get_logs(
    tool: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    logs, total = mgr.get_logs(tool=tool, status=status, limit=limit, offset=offset)
    return {"data": logs, "total": total}


@router.delete("/logs")
async def clear_logs():
    count = mgr.clear_logs()
    return {"ok": True, "deleted": count}
