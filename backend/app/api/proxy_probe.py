"""
代理观测工具 API。

页面靠 GET /records 轮询（1 秒一次），「清零」按钮调 POST /reset。

注意字段风格：本项目全局中间件会把响应 key 转成 camelCase（connectCount、rejectAll…），
所以这里的接口是 camelCase 的；独立版 tools/proxy_probe.py 没有这层中间件，是 snake_case。
两边字段含义一一对应，写断言脚本时注意别搞混。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import Field

from app.schemas.common import BaseSchema
from app.services.proxy_probe_manager import detect_lan_ip, proxy_probe

router = APIRouter(prefix="/api/proxy-probe", tags=["proxy-probe"])


class InjectionBody(BaseSchema):
    """故障注入，页面上实时切换，不用重启进程。"""
    reject_all: bool | None = None
    auth_required: str | None = None        # "user:pass"；空字符串 = 关掉
    delay: float | None = None


class ConfigBody(BaseSchema):
    port: int = Field(ge=1, le=65535)
    idle_timeout: float = Field(default=60.0, ge=1)


def _status() -> dict:
    return {
        "running": proxy_probe.running,
        # 必须 0.0.0.0：请求方是 Docker 容器，要从容器网络访问宿主机上的本工具
        "host": proxy_probe.host,
        "port": proxy_probe.port,
        # 页面要展示/复制的地址：必须是内网 IP，不能是 127.0.0.1（容器连不到）
        "lan_ip": detect_lan_ip(),
        "proxy_url": "http://%s:%d" % (detect_lan_ip() or "127.0.0.1", proxy_probe.port),
        "idle_timeout": proxy_probe.idle_timeout,
        "log_file": proxy_probe.log_file,
        "injection": proxy_probe.injection(),
    }


@router.get("/status")
async def get_status():
    return {**_status(), "stats": proxy_probe.stats()}


@router.post("/start")
async def start_service():
    await proxy_probe.start()
    return {"ok": True, **_status()}


@router.post("/stop")
async def stop_service():
    await proxy_probe.stop()
    return {"ok": True, **_status()}


@router.get("/stats")
async def get_stats():
    """纯计数，字段与独立版一致，可直接给自动化脚本断言。"""
    return proxy_probe.stats()


@router.post("/reset")
async def reset_stats():
    """页面「清零」按钮：计数归零 + 列表清空，用于在一次测试前打基线。"""
    proxy_probe.reset()
    return {"ok": True, "stats": proxy_probe.stats()}


@router.get("/records")
async def get_records(since: int = 0, limit: int = 200):
    """
    页面轮询用：默认回最新 limit 条，页面拿到后**整体替换**列表。

    不做增量追加 —— 轮询可能并发（定时器 + 切回标签页 + 操作后主动刷新），
    并发时按 since 增量会把同一批记录拼两遍。真要增量，去重键必须是记录 id。
    """
    return {
        "records": proxy_probe.records_since(since, limit),
        "stats": proxy_probe.stats(),
        **_status(),
    }


@router.get("/records/{rec_id}")
async def get_record_detail(rec_id: int):
    """
    单条明细：原始请求（客户端→代理）、转发请求（代理→上游）、上游响应。
    明细体积大，不塞进每秒轮询的列表里，点开才取。
    """
    rec = proxy_probe.record_detail(rec_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="记录不存在或已被清零")
    return rec


@router.post("/inject")
async def set_injection(body: InjectionBody):
    changed = proxy_probe.apply_injection(
        reject_all=body.reject_all,
        auth_required=body.auth_required,
        delay=body.delay,
    )
    return {"ok": True, "changed": changed, "injection": proxy_probe.injection()}


@router.put("/config")
async def update_config(body: ConfigBody):
    was_running = proxy_probe.running
    if was_running:
        await proxy_probe.stop()
    proxy_probe.port = body.port
    proxy_probe.idle_timeout = body.idle_timeout
    if was_running:
        await proxy_probe.start()
    return {"ok": True, **_status()}
