"""节点池测试 API —— 假节点起停/切状态 + 往网关发一批请求 + 看每条落到哪个节点。

页面：测试工具 → 节点池测试（/tools/node-pool）。说明见 services/node_pool_manager.py。
"""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.node_pool_manager import MAX_NODES, MODES, node_pool, start_run
from app.services.proxy_probe_manager import detect_lan_ip

router = APIRouter(prefix="/api/node-pool", tags=["node-pool"])


class NodeSpec(BaseModel):
    id: str = Field(min_length=1, max_length=20)
    name: str = Field(default="", max_length=30)
    mode: str = "normal"
    slowSeconds: float = 5
    port: int | None = None


class NodesBody(BaseModel):
    nodes: list[NodeSpec] = Field(max_length=MAX_NODES)


class HeaderItem(BaseModel):
    key: str = ""
    value: str = ""
    enabled: bool = True


class RunBody(BaseModel):
    targetUrl: str
    headers: list[HeaderItem] = []
    model: str = "nodepool-fake"
    count: int = Field(default=10, ge=1, le=1000)
    concurrency: int = Field(default=1, ge=1, le=50)
    timeoutSeconds: float = Field(default=60, ge=1, le=600)
    stream: bool = False
    sessionMode: str = "none"          # none / same / each
    sessionPlace: str = "uag_metadata"  # uag_metadata / body_metadata / header
    sessionField: str = "session_id"
    sessionValue: str = "会话-001"
    systemPrompt: str = ""
    userPrompt: str = "你好"
    autoNumber: bool = True


def _status() -> dict:
    host = detect_lan_ip() or "127.0.0.1"
    return {
        "host": host,
        "modes": [{"value": k, "label": v} for k, v in MODES.items()],
        "maxNodes": MAX_NODES,
        "nodes": [n.to_dict(host) for n in node_pool.nodes.values()],
        "runActive": bool(node_pool.run and node_pool.run.running),
    }


@router.get("/status")
async def get_status():
    return _status()


@router.put("/nodes")
async def put_nodes(body: NodesBody):
    """整份清单对齐：页面增删节点、切状态都走这一条。改状态即时生效，不重启节点。"""
    errors = await node_pool.apply([n.model_dump() for n in body.nodes])
    return {**_status(), "errors": errors}


@router.post("/stop-all")
async def stop_all():
    await node_pool.stop_all()
    return _status()


@router.post("/reset-counters")
async def reset_counters():
    node_pool.reset_counters()
    return _status()


@router.post("/runs")
async def create_run(body: RunBody):
    u = urlparse(body.targetUrl.strip())
    if u.scheme not in ("http", "https") or not u.netloc:
        raise HTTPException(400, "发送地址要以 http:// 或 https:// 开头，例如 http://网关地址/v1/chat/completions")
    cfg = body.model_dump()
    cfg["targetUrl"] = body.targetUrl.strip()
    try:
        run = await start_run(cfg)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return run.snapshot(with_details=False)


@router.get("/runs/current")
async def current_run(details: bool = False):
    run = node_pool.run
    if run is None:
        return None
    return run.snapshot(with_details=details)


@router.get("/runs/current/rows/{seq}")
async def current_run_row(seq: int):
    run = node_pool.run
    if run is None:
        raise HTTPException(404, "还没发过")
    for r in run.snapshot(with_details=True)["rows"]:
        if r["seq"] == seq:
            return r
    raise HTTPException(404, "这一条还没发完")


@router.post("/runs/current/stop")
async def stop_run():
    run = node_pool.run
    if run and run.running:
        run.cancelled = True
    return {"ok": True}
