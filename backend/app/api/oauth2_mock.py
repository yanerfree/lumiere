"""OAuth2 Mock API — Client 管理 + 服务控制 + 日志"""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.oauth2_mock_manager import oauth2_mock_server as mgr

router = APIRouter(prefix="/api/oauth2-mock", tags=["oauth2-mock"])


# ── Schemas ──

class ClientCreate(BaseModel):
    client_id: str = Field("", max_length=100)
    client_secret: str = ""
    name: str = ""
    scope: str = ""
    audience: str = "api"
    token_ttl: int = Field(3600, ge=5, le=86400)


class ClientUpdate(BaseModel):
    name: str | None = None
    client_secret: str | None = None
    scope: str | None = None
    audience: str | None = None
    token_ttl: int | None = Field(None, ge=60, le=86400)
    enabled: bool | None = None


class ServiceConfig(BaseModel):
    port: int | None = None


# ── Client 管理 ──

@router.get("/clients")
async def list_clients():
    return {"data": mgr.get_clients()}


@router.post("/clients", status_code=201)
async def create_client(body: ClientCreate):
    try:
        client = mgr.add_client(body.model_dump())
        return {"data": client}
    except ValueError as e:
        return {"error": str(e)}


@router.patch("/clients/{client_id}/lock")
async def lock_client(client_id: str):
    client = mgr.toggle_lock(client_id)
    if not client:
        return JSONResponse({"error": f"Client '{client_id}' 不存在"}, status_code=404)
    return {"data": {"client_id": client_id, "locked": client["locked"]}}


@router.put("/clients/{client_id}")
async def update_client(client_id: str, body: ClientUpdate):
    existing = mgr.get_client(client_id)
    if not existing:
        return {"error": f"Client '{client_id}' 不存在"}
    if existing.get("locked", False):
        return JSONResponse({"error": "Client 已锁定，请先解锁后再编辑"}, status_code=423)
    result = mgr.update_client(client_id, body.model_dump(exclude_unset=True))
    return {"data": result}


@router.delete("/clients/{client_id}")
async def delete_client(client_id: str):
    client = mgr.get_client(client_id)
    if not client:
        return {"error": f"Client '{client_id}' 不存在"}
    if client.get("locked", False):
        return JSONResponse({"error": "Client 已锁定，请先解锁后再删除"}, status_code=423)
    mgr.delete_client(client_id)
    return {"ok": True}


@router.patch("/clients/{client_id}/toggle")
async def toggle_client(client_id: str):
    client = mgr.get_client(client_id)
    if not client:
        return {"error": f"Client '{client_id}' 不存在"}
    if client.get("locked", False):
        return JSONResponse({"error": "Client 已锁定，请先解锁后再操作"}, status_code=423)
    mgr.update_client(client_id, {"enabled": not client.get("enabled", True)})
    return {"data": {"client_id": client_id, "enabled": not client.get("enabled", True)}}


# ── 服务控制 ──

@router.get("/status")
async def get_status():
    clients = mgr.get_clients()
    return {
        "data": {
            "running": mgr.running,
            "port": mgr.port,
            "clientsCount": len(clients),
            "clientsEnabled": sum(1 for c in clients if c.get("enabled", True)),
            "totalLogs": len(mgr._logs),
        }
    }


@router.post("/start")
async def start_service():
    try:
        await mgr.start()
        return {"ok": True, "port": mgr.port}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
    if was_running:
        await mgr.start()
    return {"data": {"port": mgr.port, "running": mgr.running}}


# ── JWKS 预览 ──

@router.get("/jwks")
async def get_jwks():
    return {"data": mgr.get_jwks()}


# ── 日志 ──

@router.get("/logs")
async def get_logs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    logs, total = mgr.get_logs(limit=limit, offset=offset)
    return {"data": logs, "total": total}


@router.delete("/logs")
async def clear_logs():
    count = mgr.clear_logs()
    return {"ok": True, "deleted": count}
