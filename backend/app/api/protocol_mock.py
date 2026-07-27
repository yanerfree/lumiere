"""Protocol Mock API — WebSocket / TCP / UDP / gRPC mock management"""
from __future__ import annotations

import asyncio
import socket
import time
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.db import get_db
from app.schemas.protocol_mock import (
    WsEndpointCreate, WsEndpointUpdate, WsEndpointResponse, WsLogResponse,
    TcpHandlerCreate, TcpHandlerUpdate, TcpHandlerResponse, TcpLogResponse,
    UdpHandlerCreate, UdpHandlerUpdate, UdpHandlerResponse, UdpLogResponse,
    GrpcServiceCreate, GrpcServiceUpdate, GrpcServiceResponse, GrpcLogResponse,
    ProtocolServiceStatus, ProtocolServiceConfig, ProtocolReorderRequest,
)
from app.services import ws_mock_service as ws_svc
from app.services import tcp_mock_service as tcp_svc
from app.services import udp_mock_service as udp_svc
from app.services import grpc_mock_service as grpc_svc
from app.services.ws_mock_manager import ws_mock_server
from app.services.tcp_mock_manager import tcp_mock_server
from app.services.udp_mock_manager import udp_mock_server
from app.services.grpc_mock_manager import grpc_mock_server
from app.services.protocol_mock_presets import (
    list_ws_presets, list_tcp_presets, list_udp_presets, list_grpc_presets,
    get_ws_preset, get_tcp_preset, get_udp_preset, get_grpc_preset,
)

router = APIRouter(prefix="/api/protocol-mock", tags=["protocol-mock"])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  WebSocket
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ───── Endpoint management ─────

@router.get("/ws/endpoints", response_model=list[WsEndpointResponse])
async def ws_list_endpoints(session: AsyncSession = Depends(get_db)):
    rows = await ws_svc.list_endpoints(session)
    return [WsEndpointResponse.model_validate(r, from_attributes=True) for r in rows]


@router.post("/ws/endpoints", response_model=WsEndpointResponse, status_code=201)
async def ws_create_endpoint(body: WsEndpointCreate, session: AsyncSession = Depends(get_db)):
    row = await ws_svc.create_endpoint(session, body)
    return WsEndpointResponse.model_validate(row, from_attributes=True)


@router.put("/ws/endpoints/reorder")
async def ws_reorder_endpoints(body: ProtocolReorderRequest, session: AsyncSession = Depends(get_db)):
    # 必须声明在 /ws/endpoints/{endpoint_id} 之前，否则 "reorder" 会被当作 endpoint_id 解析
    await ws_svc.reorder_endpoints(session, [item.model_dump() for item in body.items])
    return {"ok": True}


@router.patch("/ws/endpoints/{endpoint_id}/lock", response_model=WsEndpointResponse)
async def ws_lock_endpoint(endpoint_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    row = await ws_svc.toggle_lock_endpoint(session, endpoint_id)
    if not row:
        return JSONResponse({"error": "Endpoint not found"}, status_code=404)
    return WsEndpointResponse.model_validate(row, from_attributes=True)


@router.put("/ws/endpoints/{endpoint_id}", response_model=WsEndpointResponse)
async def ws_update_endpoint(endpoint_id: uuid.UUID, body: WsEndpointUpdate, session: AsyncSession = Depends(get_db)):
    existing = await ws_svc.get_endpoint(session, endpoint_id)
    if not existing:
        return JSONResponse({"error": "Endpoint not found"}, status_code=404)
    if existing.locked:
        return JSONResponse({"error": "端点已锁定，请先解锁后再编辑"}, status_code=423)
    row = await ws_svc.update_endpoint(session, endpoint_id, body)
    return WsEndpointResponse.model_validate(row, from_attributes=True)


@router.delete("/ws/endpoints/{endpoint_id}")
async def ws_delete_endpoint(endpoint_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    row = await ws_svc.get_endpoint(session, endpoint_id)
    if not row:
        return JSONResponse({"error": "Endpoint not found"}, status_code=404)
    if row.locked:
        return JSONResponse({"error": "端点已锁定，请先解锁后再删除"}, status_code=423)
    await ws_svc.delete_endpoint(session, endpoint_id)
    return {"ok": True}


@router.patch("/ws/endpoints/{endpoint_id}/toggle", response_model=WsEndpointResponse)
async def ws_toggle_endpoint(endpoint_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    existing = await ws_svc.get_endpoint(session, endpoint_id)
    if not existing:
        return JSONResponse({"error": "Endpoint not found"}, status_code=404)
    if existing.locked:
        return JSONResponse({"error": "端点已锁定，请先解锁后再操作"}, status_code=423)
    row = await ws_svc.toggle_endpoint(session, endpoint_id)
    return WsEndpointResponse.model_validate(row, from_attributes=True)


# ───── Service control ─────

@router.get("/ws/status", response_model=ProtocolServiceStatus)
async def ws_get_status(session: AsyncSession = Depends(get_db)):
    endpoints = await ws_svc.list_endpoints(session)
    total_logs = await ws_svc.count_logs(session)
    return ProtocolServiceStatus(
        running=ws_mock_server.running,
        port=ws_mock_server.port,
        endpoints_count=len(endpoints),
        endpoints_enabled=sum(1 for e in endpoints if e.enabled),
        total_logs=total_logs,
    )


@router.post("/ws/start")
async def ws_start():
    await ws_mock_server.start()
    return {"ok": True, "port": ws_mock_server.port}


@router.post("/ws/stop")
async def ws_stop():
    await ws_mock_server.stop()
    return {"ok": True}


@router.put("/ws/config")
async def ws_update_config(body: ProtocolServiceConfig):
    was_running = ws_mock_server.running
    if was_running:
        await ws_mock_server.stop()
    if body.port is not None:
        ws_mock_server.port = body.port
    if was_running:
        await ws_mock_server.start()
    return {"ok": True}


# ───── Logs ─────

@router.get("/ws/logs")
async def ws_get_logs(
    endpoint_id: uuid.UUID | None = Query(None),
    event_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    logs, total = await ws_svc.list_logs(
        session, endpoint_id=endpoint_id, event_type=event_type, limit=limit, offset=offset,
    )
    return {
        "data": [WsLogResponse.model_validate(l, from_attributes=True) for l in logs],
        "total": total,
    }


@router.delete("/ws/logs")
async def ws_clear_logs(session: AsyncSession = Depends(get_db)):
    count = await ws_svc.clear_logs(session)
    return {"ok": True, "deleted": count}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  TCP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ───── Handler management ─────

@router.get("/tcp/handlers", response_model=list[TcpHandlerResponse])
async def tcp_list_handlers(session: AsyncSession = Depends(get_db)):
    rows = await tcp_svc.list_handlers(session)
    return [TcpHandlerResponse.model_validate(r, from_attributes=True) for r in rows]


@router.post("/tcp/handlers", response_model=TcpHandlerResponse, status_code=201)
async def tcp_create_handler(body: TcpHandlerCreate, session: AsyncSession = Depends(get_db)):
    row = await tcp_svc.create_handler(session, body)
    return TcpHandlerResponse.model_validate(row, from_attributes=True)


@router.put("/tcp/handlers/reorder")
async def tcp_reorder_handlers(body: ProtocolReorderRequest, session: AsyncSession = Depends(get_db)):
    # 必须声明在 /tcp/handlers/{handler_id} 之前
    await tcp_svc.reorder_handlers(session, [item.model_dump() for item in body.items])
    return {"ok": True}


@router.patch("/tcp/handlers/{handler_id}/lock", response_model=TcpHandlerResponse)
async def tcp_lock_handler(handler_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    row = await tcp_svc.toggle_lock_handler(session, handler_id)
    if not row:
        return JSONResponse({"error": "Handler not found"}, status_code=404)
    return TcpHandlerResponse.model_validate(row, from_attributes=True)


@router.put("/tcp/handlers/{handler_id}", response_model=TcpHandlerResponse)
async def tcp_update_handler(handler_id: uuid.UUID, body: TcpHandlerUpdate, session: AsyncSession = Depends(get_db)):
    existing = await tcp_svc.get_handler(session, handler_id)
    if not existing:
        return JSONResponse({"error": "Handler not found"}, status_code=404)
    if existing.locked:
        return JSONResponse({"error": "处理器已锁定，请先解锁后再编辑"}, status_code=423)
    row = await tcp_svc.update_handler(session, handler_id, body)
    return TcpHandlerResponse.model_validate(row, from_attributes=True)


@router.delete("/tcp/handlers/{handler_id}")
async def tcp_delete_handler(handler_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    row = await tcp_svc.get_handler(session, handler_id)
    if not row:
        return JSONResponse({"error": "Handler not found"}, status_code=404)
    if row.locked:
        return JSONResponse({"error": "处理器已锁定，请先解锁后再删除"}, status_code=423)
    await tcp_svc.delete_handler(session, handler_id)
    return {"ok": True}


@router.patch("/tcp/handlers/{handler_id}/toggle", response_model=TcpHandlerResponse)
async def tcp_toggle_handler(handler_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    existing = await tcp_svc.get_handler(session, handler_id)
    if not existing:
        return JSONResponse({"error": "Handler not found"}, status_code=404)
    if existing.locked:
        return JSONResponse({"error": "处理器已锁定，请先解锁后再操作"}, status_code=423)
    row = await tcp_svc.toggle_handler(session, handler_id)
    return TcpHandlerResponse.model_validate(row, from_attributes=True)


# ───── Service control ─────

@router.get("/tcp/status", response_model=ProtocolServiceStatus)
async def tcp_get_status(session: AsyncSession = Depends(get_db)):
    handlers = await tcp_svc.list_handlers(session)
    total_logs = await tcp_svc.count_logs(session)
    return ProtocolServiceStatus(
        running=tcp_mock_server.running,
        port=tcp_mock_server.port,
        endpoints_count=len(handlers),
        endpoints_enabled=sum(1 for h in handlers if h.enabled),
        total_logs=total_logs,
    )


@router.post("/tcp/start")
async def tcp_start():
    await tcp_mock_server.start()
    return {"ok": True, "port": tcp_mock_server.port}


@router.post("/tcp/stop")
async def tcp_stop():
    await tcp_mock_server.stop()
    return {"ok": True}


@router.put("/tcp/config")
async def tcp_update_config(body: ProtocolServiceConfig):
    was_running = tcp_mock_server.running
    if was_running:
        await tcp_mock_server.stop()
    if body.port is not None:
        tcp_mock_server.port = body.port
    if was_running:
        await tcp_mock_server.start()
    return {"ok": True}


# ───── Logs ─────

@router.get("/tcp/logs")
async def tcp_get_logs(
    handler_id: uuid.UUID | None = Query(None),
    event_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    logs, total = await tcp_svc.list_logs(
        session, handler_id=handler_id, event_type=event_type, limit=limit, offset=offset,
    )
    return {
        "data": [TcpLogResponse.model_validate(l, from_attributes=True) for l in logs],
        "total": total,
    }


@router.delete("/tcp/logs")
async def tcp_clear_logs(session: AsyncSession = Depends(get_db)):
    count = await tcp_svc.clear_logs(session)
    return {"ok": True, "deleted": count}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  UDP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ───── Handler management ─────

@router.get("/udp/handlers", response_model=list[UdpHandlerResponse])
async def udp_list_handlers(session: AsyncSession = Depends(get_db)):
    rows = await udp_svc.list_handlers(session)
    return [UdpHandlerResponse.model_validate(r, from_attributes=True) for r in rows]


@router.post("/udp/handlers", response_model=UdpHandlerResponse, status_code=201)
async def udp_create_handler(body: UdpHandlerCreate, session: AsyncSession = Depends(get_db)):
    row = await udp_svc.create_handler(session, body)
    return UdpHandlerResponse.model_validate(row, from_attributes=True)


@router.put("/udp/handlers/reorder")
async def udp_reorder_handlers(body: ProtocolReorderRequest, session: AsyncSession = Depends(get_db)):
    # 必须声明在 /udp/handlers/{handler_id} 之前
    await udp_svc.reorder_handlers(session, [item.model_dump() for item in body.items])
    return {"ok": True}


@router.patch("/udp/handlers/{handler_id}/lock", response_model=UdpHandlerResponse)
async def udp_lock_handler(handler_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    row = await udp_svc.toggle_lock_handler(session, handler_id)
    if not row:
        return JSONResponse({"error": "Handler not found"}, status_code=404)
    return UdpHandlerResponse.model_validate(row, from_attributes=True)


@router.put("/udp/handlers/{handler_id}", response_model=UdpHandlerResponse)
async def udp_update_handler(handler_id: uuid.UUID, body: UdpHandlerUpdate, session: AsyncSession = Depends(get_db)):
    existing = await udp_svc.get_handler(session, handler_id)
    if not existing:
        return JSONResponse({"error": "Handler not found"}, status_code=404)
    if existing.locked:
        return JSONResponse({"error": "处理器已锁定，请先解锁后再编辑"}, status_code=423)
    row = await udp_svc.update_handler(session, handler_id, body)
    return UdpHandlerResponse.model_validate(row, from_attributes=True)


@router.delete("/udp/handlers/{handler_id}")
async def udp_delete_handler(handler_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    row = await udp_svc.get_handler(session, handler_id)
    if not row:
        return JSONResponse({"error": "Handler not found"}, status_code=404)
    if row.locked:
        return JSONResponse({"error": "处理器已锁定，请先解锁后再删除"}, status_code=423)
    await udp_svc.delete_handler(session, handler_id)
    return {"ok": True}


@router.patch("/udp/handlers/{handler_id}/toggle", response_model=UdpHandlerResponse)
async def udp_toggle_handler(handler_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    existing = await udp_svc.get_handler(session, handler_id)
    if not existing:
        return JSONResponse({"error": "Handler not found"}, status_code=404)
    if existing.locked:
        return JSONResponse({"error": "处理器已锁定，请先解锁后再操作"}, status_code=423)
    row = await udp_svc.toggle_handler(session, handler_id)
    return UdpHandlerResponse.model_validate(row, from_attributes=True)


# ───── Service control ─────

@router.get("/udp/status", response_model=ProtocolServiceStatus)
async def udp_get_status(session: AsyncSession = Depends(get_db)):
    handlers = await udp_svc.list_handlers(session)
    total_logs = await udp_svc.count_logs(session)
    return ProtocolServiceStatus(
        running=udp_mock_server.running,
        port=udp_mock_server.port,
        endpoints_count=len(handlers),
        endpoints_enabled=sum(1 for h in handlers if h.enabled),
        total_logs=total_logs,
    )


@router.post("/udp/start")
async def udp_start():
    await udp_mock_server.start()
    return {"ok": True, "port": udp_mock_server.port}


@router.post("/udp/stop")
async def udp_stop():
    await udp_mock_server.stop()
    return {"ok": True}


@router.put("/udp/config")
async def udp_update_config(body: ProtocolServiceConfig):
    was_running = udp_mock_server.running
    if was_running:
        await udp_mock_server.stop()
    if body.port is not None:
        udp_mock_server.port = body.port
    if was_running:
        await udp_mock_server.start()
    return {"ok": True}


# ───── Logs ─────

@router.get("/udp/logs")
async def udp_get_logs(
    handler_id: uuid.UUID | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    logs, total = await udp_svc.list_logs(
        session, handler_id=handler_id, limit=limit, offset=offset,
    )
    return {
        "data": [UdpLogResponse.model_validate(l, from_attributes=True) for l in logs],
        "total": total,
    }


@router.delete("/udp/logs")
async def udp_clear_logs(session: AsyncSession = Depends(get_db)):
    count = await udp_svc.clear_logs(session)
    return {"ok": True, "deleted": count}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  gRPC
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ───── Service management ─────

@router.get("/grpc/services", response_model=list[GrpcServiceResponse])
async def grpc_list_services(session: AsyncSession = Depends(get_db)):
    rows = await grpc_svc.list_services(session)
    return [GrpcServiceResponse.model_validate(r, from_attributes=True) for r in rows]


@router.post("/grpc/services", response_model=GrpcServiceResponse, status_code=201)
async def grpc_create_service(body: GrpcServiceCreate, session: AsyncSession = Depends(get_db)):
    row = await grpc_svc.create_service(session, body)
    await grpc_mock_server.refresh_reflection()
    return GrpcServiceResponse.model_validate(row, from_attributes=True)


@router.put("/grpc/services/reorder")
async def grpc_reorder_services(body: ProtocolReorderRequest, session: AsyncSession = Depends(get_db)):
    # 必须声明在 /grpc/services/{service_id} 之前
    await grpc_svc.reorder_services(session, [item.model_dump() for item in body.items])
    return {"ok": True}


@router.patch("/grpc/services/{service_id}/lock", response_model=GrpcServiceResponse)
async def grpc_lock_service(service_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    row = await grpc_svc.toggle_lock_service(session, service_id)
    if not row:
        return JSONResponse({"error": "Service not found"}, status_code=404)
    return GrpcServiceResponse.model_validate(row, from_attributes=True)


@router.put("/grpc/services/{service_id}", response_model=GrpcServiceResponse)
async def grpc_update_service(service_id: uuid.UUID, body: GrpcServiceUpdate, session: AsyncSession = Depends(get_db)):
    existing = await grpc_svc.get_service(session, service_id)
    if not existing:
        return JSONResponse({"error": "Service not found"}, status_code=404)
    if existing.locked:
        return JSONResponse({"error": "服务已锁定，请先解锁后再编辑"}, status_code=423)
    row = await grpc_svc.update_service(session, service_id, body)
    await grpc_mock_server.refresh_reflection()
    return GrpcServiceResponse.model_validate(row, from_attributes=True)


@router.delete("/grpc/services/{service_id}")
async def grpc_delete_service(service_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    row = await grpc_svc.get_service(session, service_id)
    if not row:
        return JSONResponse({"error": "Service not found"}, status_code=404)
    if row.locked:
        return JSONResponse({"error": "服务已锁定，请先解锁后再删除"}, status_code=423)
    await grpc_svc.delete_service(session, service_id)
    await grpc_mock_server.refresh_reflection()
    return {"ok": True}


@router.patch("/grpc/services/{service_id}/toggle", response_model=GrpcServiceResponse)
async def grpc_toggle_service(service_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    existing = await grpc_svc.get_service(session, service_id)
    if not existing:
        return JSONResponse({"error": "Service not found"}, status_code=404)
    if existing.locked:
        return JSONResponse({"error": "服务已锁定，请先解锁后再操作"}, status_code=423)
    row = await grpc_svc.toggle_service(session, service_id)
    await grpc_mock_server.refresh_reflection()
    return GrpcServiceResponse.model_validate(row, from_attributes=True)


# ───── Service control ─────

@router.get("/grpc/status", response_model=ProtocolServiceStatus)
async def grpc_get_status(session: AsyncSession = Depends(get_db)):
    services = await grpc_svc.list_services(session)
    total_logs = await grpc_svc.count_logs(session)
    return ProtocolServiceStatus(
        running=grpc_mock_server.running,
        port=grpc_mock_server.port,
        endpoints_count=len(services),
        endpoints_enabled=sum(1 for s in services if s.enabled),
        total_logs=total_logs,
        reflection_version=grpc_mock_server.reflection_version,
    )


@router.post("/grpc/start")
async def grpc_start():
    await grpc_mock_server.start()
    return {"ok": True, "port": grpc_mock_server.port}


@router.post("/grpc/stop")
async def grpc_stop():
    await grpc_mock_server.stop()
    return {"ok": True}


@router.put("/grpc/config")
async def grpc_update_config(body: ProtocolServiceConfig):
    was_running = grpc_mock_server.running
    if was_running:
        await grpc_mock_server.stop()
    if body.port is not None:
        grpc_mock_server.port = body.port
    if body.reflection_version is not None and body.reflection_version in ("both", "v1", "v1alpha"):
        grpc_mock_server.reflection_version = body.reflection_version
        grpc_mock_server._save_state(grpc_mock_server.running)
    if was_running:
        await grpc_mock_server.start()
    return {"ok": True, "reflectionVersion": grpc_mock_server.reflection_version}


# ───── Logs ─────

@router.get("/grpc/logs")
async def grpc_get_logs(
    service_id: uuid.UUID | None = Query(None),
    service_name: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    logs, total = await grpc_svc.list_logs(
        session, service_id=service_id, service_name=service_name, limit=limit, offset=offset,
    )
    return {
        "data": [GrpcLogResponse.model_validate(l, from_attributes=True) for l in logs],
        "total": total,
    }


@router.delete("/grpc/logs")
async def grpc_clear_logs(session: AsyncSession = Depends(get_db)):
    count = await grpc_svc.clear_logs(session)
    return {"ok": True, "deleted": count}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  预设模板
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/ws/presets")
async def ws_get_presets():
    return {"data": list_ws_presets()}

@router.get("/tcp/presets")
async def tcp_get_presets():
    return {"data": list_tcp_presets()}

@router.get("/udp/presets")
async def udp_get_presets():
    return {"data": list_udp_presets()}

@router.get("/grpc/presets")
async def grpc_get_presets():
    return {"data": list_grpc_presets()}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  测试面板
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class WsTestRequest(BaseModel):
    path: str = "/"
    message: str = "Hello"


class TcpTestRequest(BaseModel):
    message: str = "Hello"
    hex_mode: bool = False


class UdpTestRequest(BaseModel):
    message: str = "Hello"
    hex_mode: bool = False


class GrpcTestRequest(BaseModel):
    service_name: str = "helloworld.Greeter"
    method_name: str = "SayHello"
    body: str = '{"name": "world"}'


@router.post("/ws/test")
async def ws_test(body: WsTestRequest):
    if not ws_mock_server.running:
        return JSONResponse({"error": "WebSocket Mock 服务未启动"}, status_code=400)

    import websockets

    port = ws_mock_server.port
    url = f"ws://127.0.0.1:{port}{body.path if body.path.startswith('/') else '/' + body.path}"
    t0 = time.perf_counter()
    try:
        async with websockets.connect(url, open_timeout=5, close_timeout=5) as ws:
            await ws.send(body.message)
            try:
                response = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                response = None
            duration_ms = round((time.perf_counter() - t0) * 1000, 1)
            return {
                "ok": True,
                "url": url.replace("127.0.0.1", "localhost"),
                "sent": body.message,
                "received": response,
                "duration_ms": duration_ms,
            }
    except Exception as e:
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        return JSONResponse({
            "error": str(e),
            "url": url.replace("127.0.0.1", "localhost"),
            "duration_ms": duration_ms,
        }, status_code=502)


@router.post("/tcp/test")
async def tcp_test(body: TcpTestRequest):
    if not tcp_mock_server.running:
        return JSONResponse({"error": "TCP Mock 服务未启动"}, status_code=400)

    port = tcp_mock_server.port
    target = f"127.0.0.1:{port}"
    t0 = time.perf_counter()
    try:
        if body.hex_mode:
            data = bytes.fromhex(body.message.replace(" ", ""))
        else:
            data = body.message.encode("utf-8")

        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=5,
        )
        writer.write(data)
        await writer.drain()
        try:
            resp_data = await asyncio.wait_for(reader.read(4096), timeout=5)
        except asyncio.TimeoutError:
            resp_data = b""
        writer.close()
        await writer.wait_closed()
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)

        resp_text = resp_data.decode("utf-8", errors="replace") if resp_data else None
        resp_hex = resp_data.hex(" ") if resp_data else None
        return {
            "ok": True,
            "target": target.replace("127.0.0.1", "localhost"),
            "sent": body.message,
            "sent_hex": data.hex(" "),
            "received": resp_text,
            "received_hex": resp_hex,
            "received_bytes": len(resp_data) if resp_data else 0,
            "duration_ms": duration_ms,
        }
    except Exception as e:
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        return JSONResponse({
            "error": str(e),
            "target": target.replace("127.0.0.1", "localhost"),
            "duration_ms": duration_ms,
        }, status_code=502)


@router.post("/udp/test")
async def udp_test(body: UdpTestRequest):
    if not udp_mock_server.running:
        return JSONResponse({"error": "UDP Mock 服务未启动"}, status_code=400)

    port = udp_mock_server.port
    target = f"127.0.0.1:{port}"
    t0 = time.perf_counter()
    try:
        if body.hex_mode:
            data = bytes.fromhex(body.message.replace(" ", ""))
        else:
            data = body.message.encode("utf-8")

        def _send():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(5.0)
            sock.sendto(data, ("127.0.0.1", port))
            try:
                resp_data, _ = sock.recvfrom(4096)
            except socket.timeout:
                resp_data = b""
            finally:
                sock.close()
            return resp_data

        resp_data = await asyncio.to_thread(_send)
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)

        resp_text = resp_data.decode("utf-8", errors="replace") if resp_data else None
        resp_hex = resp_data.hex(" ") if resp_data else None
        return {
            "ok": True,
            "target": target.replace("127.0.0.1", "localhost"),
            "sent": body.message,
            "sent_hex": data.hex(" "),
            "received": resp_text,
            "received_hex": resp_hex,
            "received_bytes": len(resp_data) if resp_data else 0,
            "duration_ms": duration_ms,
        }
    except Exception as e:
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        return JSONResponse({
            "error": str(e),
            "target": target.replace("127.0.0.1", "localhost"),
            "duration_ms": duration_ms,
        }, status_code=502)


@router.post("/grpc/test")
async def grpc_test(body: GrpcTestRequest):
    if not grpc_mock_server.running:
        return JSONResponse({"error": "gRPC Mock 服务未启动"}, status_code=400)

    import grpc as grpc_lib

    port = grpc_mock_server.port
    target = f"127.0.0.1:{port}"
    method = f"/{body.service_name}/{body.method_name}"
    t0 = time.perf_counter()

    def _call():
        channel = grpc_lib.insecure_channel(target)
        try:
            grpc_lib.channel_ready_future(channel).result(timeout=5)
            resp_bytes = channel.unary_unary(method)(
                body.body.encode("utf-8"),
                timeout=10,
            )
            return resp_bytes, None
        except grpc_lib.RpcError as e:
            return None, e
        finally:
            channel.close()

    try:
        resp_bytes, rpc_err = await asyncio.to_thread(_call)
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        if rpc_err:
            return {
                "ok": False,
                "target": target.replace("127.0.0.1", "localhost"),
                "method": method,
                "sent": body.body,
                "received": rpc_err.details() if hasattr(rpc_err, "details") else str(rpc_err),
                "status_code": rpc_err.code().value[0] if hasattr(rpc_err, "code") else -1,
                "status_message": rpc_err.code().name if hasattr(rpc_err, "code") else str(rpc_err),
                "duration_ms": duration_ms,
            }
        resp_text = resp_bytes.decode("utf-8", errors="replace")
        # Try decoding as protobuf Struct (gRPC Mock encodes responses this way)
        try:
            from google.protobuf import struct_pb2
            from google.protobuf.json_format import MessageToJson
            s = struct_pb2.Struct()
            s.ParseFromString(resp_bytes)
            resp_text = MessageToJson(s, preserving_proto_field_name=True)
        except Exception:
            pass
        return {
            "ok": True,
            "target": target.replace("127.0.0.1", "localhost"),
            "method": method,
            "sent": body.body,
            "received": resp_text,
            "status_code": 0,
            "status_message": "OK",
            "duration_ms": duration_ms,
        }
    except Exception as e:
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        return JSONResponse({
            "error": str(e),
            "target": target.replace("127.0.0.1", "localhost"),
            "method": method,
            "duration_ms": duration_ms,
        }, status_code=502)

