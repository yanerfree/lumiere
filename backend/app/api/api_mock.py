"""API Mock 管理 API"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.db import get_db
from app.schemas.api_mock import (
    ApiMockLogDetailResponse,
    ApiMockLogResponse,
    ApiMockRouteCreate,
    ApiMockRouteResponse,
    ApiMockRouteUpdate,
    ApiMockServiceConfig,
    ApiMockServiceStatus,
    ApiMockReorderRequest,
)
from app.services import api_mock_service as svc
from app.services import custom_preset_service as preset_svc
from app.services.api_mock_manager import api_mock_server
from app.services.api_mock_presets import get_preset, list_presets

router = APIRouter(prefix="/api/api-mock", tags=["api-mock"])


# ───── 路由管理 ─────

@router.get("/routes", response_model=list[ApiMockRouteResponse])
async def get_routes(session: AsyncSession = Depends(get_db)):
    routes = await svc.list_routes(session)
    return [ApiMockRouteResponse.model_validate(r, from_attributes=True) for r in routes]


@router.post("/routes", response_model=ApiMockRouteResponse, status_code=201)
async def create_route(body: ApiMockRouteCreate, session: AsyncSession = Depends(get_db)):
    count = await svc.count_routes(session)
    if count > 0 and body.name == "新路由":
        body.name = f"路由 {count + 1}"
    route = await svc.create_route(session, body)
    return ApiMockRouteResponse.model_validate(route, from_attributes=True)


@router.put("/routes/reorder")
async def reorder_routes(body: ApiMockReorderRequest, session: AsyncSession = Depends(get_db)):
    # 必须声明在 /routes/{route_id} 之前，否则 "reorder" 会被当作 route_id 解析
    await svc.reorder_routes(session, [item.model_dump() for item in body.items])
    return {"ok": True}


@router.patch("/routes/{route_id}/lock", response_model=ApiMockRouteResponse)
async def lock_route(route_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    route = await svc.toggle_lock(session, route_id)
    if not route:
        return JSONResponse({"error": "Route not found"}, status_code=404)
    return ApiMockRouteResponse.model_validate(route, from_attributes=True)


@router.put("/routes/{route_id}", response_model=ApiMockRouteResponse)
async def update_route(route_id: uuid.UUID, body: ApiMockRouteUpdate, session: AsyncSession = Depends(get_db)):
    existing = await svc.get_route(session, route_id)
    if not existing:
        return JSONResponse({"error": "Route not found"}, status_code=404)
    if existing.locked:
        return JSONResponse({"error": "路由已锁定，请先解锁后再编辑"}, status_code=423)
    route = await svc.update_route(session, route_id, body)
    return ApiMockRouteResponse.model_validate(route, from_attributes=True)


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
    await svc.delete_route(session, route_id)
    return {"ok": True}


@router.patch("/routes/{route_id}/toggle", response_model=ApiMockRouteResponse)
async def toggle_route(route_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    existing = await svc.get_route(session, route_id)
    if not existing:
        return JSONResponse({"error": "Route not found"}, status_code=404)
    if existing.locked:
        return JSONResponse({"error": "路由已锁定，请先解锁后再操作"}, status_code=423)
    route = await svc.toggle_route(session, route_id)
    return ApiMockRouteResponse.model_validate(route, from_attributes=True)


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
    presets = await preset_svc.list_presets(session, "api")
    return {"data": [{"id": str(p.id), "name": p.name, "config": p.config, "createdAt": p.created_at.isoformat()} for p in presets]}


@router.post("/custom-presets", status_code=201)
async def create_custom_preset(body: dict, session: AsyncSession = Depends(get_db)):
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "名称不能为空"}, status_code=400)
    config = body.get("config", {})
    preset = await preset_svc.create_preset(session, "api", name, config)
    await session.commit()
    return {"data": {"id": str(preset.id), "name": preset.name}}


@router.delete("/custom-presets/{preset_id}")
async def delete_custom_preset(preset_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    ok = await preset_svc.delete_preset(session, preset_id)
    await session.commit()
    return {"ok": ok}


# ───── 服务控制 ─────

@router.get("/status", response_model=ApiMockServiceStatus)
async def get_status(session: AsyncSession = Depends(get_db)):
    routes = await svc.list_routes(session)
    total_requests = await svc.count_logs(session)
    return ApiMockServiceStatus(
        running=api_mock_server.running,
        port=api_mock_server.port,
        capture_enabled=api_mock_server.capture_enabled,
        routes_count=len(routes),
        routes_enabled=sum(1 for r in routes if r.enabled),
        total_requests=total_requests,
    )


@router.post("/start")
async def start_service():
    await api_mock_server.start()
    return {"ok": True, "port": api_mock_server.port}


@router.post("/stop")
async def stop_service():
    await api_mock_server.stop()
    return {"ok": True}


@router.put("/config")
async def update_config(body: ApiMockServiceConfig):
    was_running = api_mock_server.running
    if was_running:
        await api_mock_server.stop()
    api_mock_server.port = body.port
    api_mock_server.host = body.listen_host
    api_mock_server.capture_enabled = body.capture_enabled
    api_mock_server.max_log_count = body.max_log_count
    if was_running:
        await api_mock_server.start()
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
        "data": [ApiMockLogResponse.model_validate(l, from_attributes=True) for l in logs],
        "total": total,
    }


@router.get("/logs/export")
async def export_logs(route_id: uuid.UUID | None = Query(None), session: AsyncSession = Depends(get_db)):
    logs, _ = await svc.list_logs(session, route_id=route_id, limit=10000)
    data = [ApiMockLogDetailResponse.model_validate(l, from_attributes=True).model_dump(mode="json") for l in logs]
    return JSONResponse(data, headers={"Content-Disposition": "attachment; filename=api-mock-logs.json"})


@router.get("/logs/{log_id}", response_model=ApiMockLogDetailResponse)
async def get_log_detail(log_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    log = await svc.get_log(session, log_id)
    if not log:
        return JSONResponse({"error": "Log not found"}, status_code=404)
    return ApiMockLogDetailResponse.model_validate(log, from_attributes=True)


@router.delete("/logs")
async def clear_logs(route_id: uuid.UUID | None = Query(None), session: AsyncSession = Depends(get_db)):
    # route_id 传了就只清这一条路由的日志（对齐页面上「本条」的查看范围）
    count = await svc.clear_logs(session, route_id=route_id)
    return {"ok": True, "deleted": count}


@router.post("/logs/{log_id}/replay")
async def replay_log(log_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    log = await svc.get_log(session, log_id)
    if not log:
        return JSONResponse({"error": "Log not found"}, status_code=404)
    if not api_mock_server.running:
        return JSONResponse({"error": "API Mock service is not running"}, status_code=400)

    import httpx
    url = f"http://127.0.0.1:{api_mock_server.port}{log.path}"
    headers = {"Content-Type": "application/json"} if log.request_body else {}
    async with httpx.AsyncClient() as client:
        resp = await client.request(
            method=log.method,
            url=url,
            content=log.request_body.encode() if log.request_body else None,
            headers=headers,
            timeout=30,
        )
    ct = resp.headers.get("content-type", "")
    body = resp.json() if "application/json" in ct else resp.text
    return {"status_code": resp.status_code, "body": body}


@router.post("/test")
async def test_request(body: dict):
    if not api_mock_server.running:
        return JSONResponse({"error": "HTTP Mock 服务未启动"}, status_code=400)

    import httpx
    import time

    method = body.get("method", "GET").upper()
    path = body.get("path", "/")
    if not path.startswith("/"):
        path = "/" + path
    req_headers = body.get("headers", {})
    req_body = body.get("body", "")

    port = api_mock_server.port
    url = f"http://127.0.0.1:{port}{path}"
    t0 = time.perf_counter()
    try:
        headers = {}
        if isinstance(req_headers, dict):
            headers = {**req_headers}
        content = None
        if req_body and method not in ("GET", "HEAD", "OPTIONS"):
            content = req_body.encode("utf-8") if isinstance(req_body, str) else req_body
            if "Content-Type" not in headers and "content-type" not in headers:
                headers["Content-Type"] = "application/json"

        async with httpx.AsyncClient() as client:
            resp = await client.request(
                method=method, url=url, content=content,
                headers=headers, timeout=30,
            )
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        ct = resp.headers.get("content-type", "")
        try:
            resp_body = resp.json() if "application/json" in ct else resp.text
        except Exception:
            resp_body = resp.text
        display_url = url.replace("127.0.0.1", "localhost")
        return {
            "ok": True,
            "request": {
                "url": display_url,
                "method": method,
                "headers": dict(resp.request.headers),
                "body": req_body or None,
            },
            "response": {
                "status_code": resp.status_code,
                "headers": dict(resp.headers),
                "body": resp_body,
            },
            "duration_ms": duration_ms,
        }
    except Exception as e:
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        return JSONResponse({
            "error": str(e),
            "url": url.replace("127.0.0.1", "localhost"),
            "duration_ms": duration_ms,
        }, status_code=502)
