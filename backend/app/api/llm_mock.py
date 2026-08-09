"""LLM Mock 管理 API"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.db import get_db
from app.schemas.llm_mock import (
    MockLogDetailResponse,
    MockLogResponse,
    MockRouteCreate,
    MockRouteResponse,
    MockRouteUpdate,
    MockServiceConfig,
    MockServiceStatus,
    ReorderRequest,
)
from app.services import llm_mock_service as svc
from app.services import custom_preset_service as preset_svc
from app.services.llm_mock_manager import mock_server
from app.services.llm_mock_presets import get_preset, list_presets

router = APIRouter(prefix="/api/llm-mock", tags=["llm-mock"])


# ───── 路由管理 ─────

@router.get("/routes", response_model=list[MockRouteResponse])
async def get_routes(session: AsyncSession = Depends(get_db)):
    routes = await svc.list_routes(session)
    return [MockRouteResponse.model_validate(r, from_attributes=True) for r in routes]


@router.post("/routes", response_model=MockRouteResponse, status_code=201)
async def create_route(body: MockRouteCreate, session: AsyncSession = Depends(get_db)):
    count = await svc.count_routes(session)
    if count > 0 and body.name == "新路由":
        body.name = f"路由 {count + 1}"
    route = await svc.create_route(session, body)
    return MockRouteResponse.model_validate(route, from_attributes=True)


@router.put("/routes/reorder")
async def reorder_routes(body: ReorderRequest, session: AsyncSession = Depends(get_db)):
    # 必须声明在 /routes/{route_id} 之前，否则 "reorder" 会被当作 route_id 解析
    await svc.reorder_routes(session, [item.model_dump() for item in body.items])
    return {"ok": True}


@router.patch("/routes/{route_id}/lock", response_model=MockRouteResponse)
async def lock_route(route_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    route = await svc.toggle_lock(session, route_id)
    if not route:
        return JSONResponse({"error": "Route not found"}, status_code=404)
    return MockRouteResponse.model_validate(route, from_attributes=True)


@router.put("/routes/{route_id}", response_model=MockRouteResponse)
async def update_route(route_id: uuid.UUID, body: MockRouteUpdate, session: AsyncSession = Depends(get_db)):
    existing = await svc.get_route(session, route_id)
    if not existing:
        return JSONResponse({"error": "Route not found"}, status_code=404)
    if existing.locked:
        return JSONResponse({"error": "路由已锁定，请先解锁后再编辑"}, status_code=423)
    route = await svc.update_route(session, route_id, body)
    return MockRouteResponse.model_validate(route, from_attributes=True)


@router.delete("/routes/{route_id}")
async def delete_route(route_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    route = await svc.get_route(session, route_id)
    if not route:
        return JSONResponse({"error": "Route not found"}, status_code=404)
    if route.locked:
        return JSONResponse({"error": "路由已锁定，请先解锁后再删除"}, status_code=423)
    routes = await svc.list_routes(session)
    if len(routes) <= 1:
        return JSONResponse({"error": "至少保留一条路由"}, status_code=400)
    earliest = min(routes, key=lambda r: r.created_at)
    if route.id == earliest.id:
        return JSONResponse({"error": "默认路由不允许删除"}, status_code=400)
    ok = await svc.delete_route(session, route_id)
    return {"ok": True}


@router.patch("/routes/{route_id}/toggle", response_model=MockRouteResponse)
async def toggle_route(route_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    existing = await svc.get_route(session, route_id)
    if not existing:
        return JSONResponse({"error": "Route not found"}, status_code=404)
    if existing.locked:
        return JSONResponse({"error": "路由已锁定，请先解锁后再操作"}, status_code=423)
    route = await svc.toggle_route(session, route_id)
    return MockRouteResponse.model_validate(route, from_attributes=True)


# ───── 预设模式 ─────

@router.get("/presets")
async def get_presets():
    return {"data": list_presets()}


@router.get("/presets/{key}")
async def get_preset_detail(key: str):
    p = get_preset(key)
    if not p:
        return JSONResponse({"error": "Preset not found"}, status_code=404)
    return {"data": p}


# ───── 自定义预设 ─────

@router.get("/custom-presets")
async def get_custom_presets(session: AsyncSession = Depends(get_db)):
    presets = await preset_svc.list_presets(session, "llm")
    return {"data": [{"id": str(p.id), "name": p.name, "config": p.config, "createdAt": p.created_at.isoformat()} for p in presets]}


@router.post("/custom-presets", status_code=201)
async def create_custom_preset(body: dict, session: AsyncSession = Depends(get_db)):
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "名称不能为空"}, status_code=400)
    config = body.get("config", {})
    preset = await preset_svc.create_preset(session, "llm", name, config)
    await session.commit()
    return {"data": {"id": str(preset.id), "name": preset.name}}


@router.delete("/custom-presets/{preset_id}")
async def delete_custom_preset(preset_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    ok = await preset_svc.delete_preset(session, preset_id)
    await session.commit()
    return {"ok": ok}


# ───── 服务控制 ─────

@router.get("/status", response_model=MockServiceStatus)
async def get_status(session: AsyncSession = Depends(get_db)):
    routes = await svc.list_routes(session)
    total_requests = await svc.count_logs(session)
    return MockServiceStatus(
        running=mock_server.running,
        port=mock_server.port,
        capture_enabled=mock_server.capture_enabled,
        routes_count=len(routes),
        routes_enabled=sum(1 for r in routes if r.enabled),
        total_requests=total_requests,
    )


@router.post("/start")
async def start_service():
    await mock_server.start()
    return {"ok": True, "port": mock_server.port}


@router.post("/stop")
async def stop_service():
    await mock_server.stop()
    return {"ok": True}


@router.put("/config")
async def update_config(body: MockServiceConfig):
    was_running = mock_server.running
    if was_running:
        await mock_server.stop()
    mock_server.port = body.port
    mock_server.host = body.listen_host
    mock_server.capture_enabled = body.capture_enabled
    mock_server.max_log_count = body.max_log_count
    if was_running:
        await mock_server.start()
    return {"ok": True}


# ───── 请求日志 ─────

@router.get("/logs")
async def get_logs(
    status: str | None = Query(None),
    route_id: uuid.UUID | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    logs, total = await svc.list_logs(session, status=status, route_id=route_id, search=search, limit=limit, offset=offset)
    return {
        "data": [MockLogResponse.model_validate(l, from_attributes=True) for l in logs],
        "total": total,
    }


# ⚠ /logs/export 必须声明在 /logs/{log_id} **前面**。
# FastAPI 按注册顺序匹配：先注册通配的话，"export" 会被当成 log_id 去解析 UUID，
# 页面上「导出日志」点下去只会开出一个 422 —— 实测就是这样，而隔壁 api_mock.py
# 的顺序恰好是对的，两个几乎一样的页面表现不同，所以一直没人发现。
@router.get("/logs/export")
async def export_logs(session: AsyncSession = Depends(get_db)):
    logs, _ = await svc.list_logs(session, limit=10000)
    data = [MockLogDetailResponse.model_validate(l, from_attributes=True).model_dump(mode="json") for l in logs]
    return JSONResponse(data, headers={"Content-Disposition": "attachment; filename=mock-logs.json"})


@router.get("/logs/{log_id}", response_model=MockLogDetailResponse)
async def get_log_detail(log_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    log = await svc.get_log(session, log_id)
    if not log:
        return JSONResponse({"error": "Log not found"}, status_code=404)
    return MockLogDetailResponse.model_validate(log, from_attributes=True)


@router.delete("/logs")
async def clear_logs(session: AsyncSession = Depends(get_db)):
    count = await svc.clear_logs(session)
    return {"ok": True, "deleted": count}


@router.post("/logs/{log_id}/replay")
async def replay_log(log_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    log = await svc.get_log(session, log_id)
    if not log:
        return JSONResponse({"error": "Log not found"}, status_code=404)
    if not mock_server.running:
        return JSONResponse({"error": "Mock service is not running"}, status_code=400)

    import httpx
    url = f"http://127.0.0.1:{mock_server.port}{log.path}"
    async with httpx.AsyncClient() as client:
        resp = await client.request(
            method=log.method,
            url=url,
            json=log.request_body,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
    return {"status_code": resp.status_code, "body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text}
