"""
服务与端口总览 —— 一个只读接口，把这套环境里所有监听端口的东西汇总成一张表。

为什么要有它：端口散落在四处（各 *_manager.py 里硬编码的 281xx~289xx、.env 的 MCP_PORT /
PLAYWRIGHT_MCP_URL、deploy/start-ai-services.sh 的 38210/38931、vite.config.js 的 5173→8756），
以前想知道"谁在跑"只能挨个点进各自页面看徽标，或者回终端 ss -ltnp。

探测分两种，别混：
  · inproc —— 8 个 Mock + 代理观测都跑在**本进程内**，直接读 manager 的 .running/.port。
    不走网络探测是故意的：零成本，而且比 TCP 探测准 —— TCP 通只能说明端口被占，
    占它的可能是别的程序（这台机器上就有个 stoa 占着 28000/28443）。
  · tcp/http —— 只有进程外的 6 项（独立 MCP 端口、PG、Redis、claude-proxy、
    playwright-mcp、AI 网关）需要真连一下。全部并发 + 0.8s 超时，整个接口 P99 < 1s。

本接口不返回任何 token/密钥（AI 网关只给 host:port，不给 AUTH_TOKEN）。
"""
from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit

from fastapi import APIRouter, Request

from app.config import settings
from app.services.proxy_probe_manager import split_hostport

router = APIRouter(prefix="/api/system", tags=["system"])

PROBE_TIMEOUT = 0.8

# 状态取值
UP = "up"
DOWN = "down"
NOT_CONFIGURED = "notConfigured"

# 分类标签（前端按这个上色）
KIND_BUILTIN = "内建"        # 跑在后端进程内，平台自己管启停
KIND_EXTERNAL = "外部长驻"    # 独立进程，要跑 deploy/start-ai-services.sh
KIND_INFRA = "基础依赖"       # PG / Redis / 公司网关，平台管不了


async def _tcp_alive(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> bool:
    """能建起 TCP 连接就算活着。异常一律当挂了，不往外抛。"""
    if not host or not port:
        return False
    writer = None
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        return True
    except Exception:
        return False
    finally:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass


async def _http_alive(url: str, timeout: float = PROBE_TIMEOUT) -> bool:
    """HTTP 探活（claude-proxy 有 /health）。连不上或非 2xx 都算挂。"""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            return resp.status_code < 400
    except Exception:
        return False


def _parse_endpoint(url: str, default_port: int) -> tuple[str, int] | None:
    """
    从各种连接串里抠出 (host, port)。

    吃得下这几类：
      postgresql+asyncpg://user:pw@localhost:5432/testbench
      redis://localhost:6379/0
      http://192.168.51.10:8080/v1
      https://api.deepseek.com/v1   （不带端口 → 按 scheme 补 443，不是 80）
      localhost:38210               （没 scheme 的裸 host:port）
    解析不出来返回 None（上层显示"未配置"）。
    """
    url = (url or "").strip()
    if not url:
        return None
    try:
        parts = urlsplit(url) if "//" in url else None
        netloc = parts.netloc if parts else url.split("/")[0]
        if "@" in netloc:                       # 去掉 user:pw@
            netloc = netloc.rsplit("@", 1)[1]
        if not netloc:
            return None
        # 串里没写端口时，scheme 比调用方给的 default 更可信
        # （AI_BASE_URL 可能是 https://api.deepseek.com/v1，补 80 会永远探测失败）
        scheme = (parts.scheme if parts else "").lower()
        fallback = {"https": 443, "http": 80, "redis": 6379, "rediss": 6379}.get(scheme, default_port)
        if scheme.startswith("postgres"):
            fallback = 5432
        host, port = split_hostport(netloc, fallback)
        return (host or "127.0.0.1"), port
    except Exception:
        return None


def _item(key, name, host, port, status, probe, kind, desc,
          manage_url=None, start_hint=None, scheme="http"):
    addr = "[%s]" % host if host and ":" in host else host
    return {
        "key": key,
        "name": name,
        "host": host,
        "port": port,
        "url": ("%s://%s:%s" % (scheme, addr, port)) if host and port else None,
        "status": status,
        "probe": probe,
        "kind": kind,
        "desc": desc,
        "manageUrl": manage_url,
        "startHint": start_hint,
    }


def _inproc(key, name, mgr, desc, manage_url, page_name, scheme="http"):
    """进程内服务：直接读 manager 的 .running / .port，不做网络探测。"""
    try:
        running, port = bool(mgr.running), int(mgr.port)
    except Exception:
        running, port = False, None
    return _item(
        key, name, "0.0.0.0", port,
        UP if running else DOWN, "inproc", KIND_BUILTIN, desc,
        manage_url=manage_url,
        start_hint=None if running else "到【%s】页面点「启动服务」" % page_name,
        scheme=scheme,
    )


def _self_port(request: Request) -> int:
    """
    后端自己的端口。优先用请求的 Host 头（前端经 vite 代理过来是 127.0.0.1:8756），
    拿不到再退 uvicorn 惯例端口 —— 注意 CLAUDE.md 的硬规则：本项目后端必须跑 8756。
    """
    host_header = request.headers.get("host", "")
    if ":" in host_header:
        try:
            return int(host_header.rsplit(":", 1)[1])
        except ValueError:
            pass
    return int(os.environ.get("PORT", "8756"))


@router.get("/services")
async def list_services(request: Request):
    from app.services.api_mock_manager import api_mock_server
    from app.services.grpc_mock_manager import grpc_mock_server
    from app.services.llm_mock_manager import mock_server as llm_mock_server
    from app.services.mcp_mock_manager import mcp_mock_server
    from app.services.oauth2_mock_manager import oauth2_mock_server
    from app.services.proxy_probe_manager import proxy_probe
    from app.services.tcp_mock_manager import tcp_mock_server
    from app.services.udp_mock_manager import udp_mock_server
    from app.services.ws_mock_manager import ws_mock_server

    # ---- 需要网络探测的目标先解析地址，再一把并发探 ----
    mcp_port = int(os.environ.get("MCP_PORT", "18800"))
    pg = _parse_endpoint(settings.database_url, 5432)
    redis = _parse_endpoint(settings.redis_url, 6379)
    # claude-proxy：ai_proxy_base_url 是专门的兜底通道配置，没配则回退 ai_ui_base_url（.env 现状）
    proxy_ep = _parse_endpoint(settings.ai_proxy_base_url or settings.ai_ui_base_url, 38210)
    pw = _parse_endpoint(settings.playwright_mcp_url, 38931)
    gateway = _parse_endpoint(settings.ai_base_url, 80)

    probes = await asyncio.gather(
        _tcp_alive("127.0.0.1", mcp_port),
        _tcp_alive(*pg) if pg else _noop(),
        _tcp_alive(*redis) if redis else _noop(),
        _http_alive("http://%s:%d/health" % proxy_ep) if proxy_ep else _noop(),
        _tcp_alive(*pw) if pw else _noop(),
        _tcp_alive(*gateway) if gateway else _noop(),
    )
    mcp_up, pg_up, redis_up, proxy_up, pw_up, gw_up = probes

    def net_status(ep, alive):
        if ep is None:
            return NOT_CONFIGURED
        return UP if alive else DOWN

    core = [
        _item("backend", "后端 API", "0.0.0.0", _self_port(request), UP, "self", KIND_BUILTIN,
              "平台主服务，前端所有 /api 请求都打这里",
              start_hint="uvicorn app.main:app --port 8756（必须 8756，跑错端口前端会全 502）"),
        _item("mcp", "MCP Server", "127.0.0.1", mcp_port,
              UP if mcp_up else DOWN, "tcp", KIND_BUILTIN,
              "给 Claude Code 连的独立 MCP 端口（与主服务分开，避免混用）",
              start_hint=None if mcp_up else "随后端一起启动；没起说明端口被占，查 MCP_PORT"),
        _item("postgres", "PostgreSQL", pg[0] if pg else None, pg[1] if pg else None,
              net_status(pg, pg_up), "tcp", KIND_INFRA,
              "主数据库，挂了整站不可用",
              start_hint=None if pg_up else "docker compose up -d db",
              scheme="postgresql"),
        _item("redis", "Redis", redis[0] if redis else None, redis[1] if redis else None,
              net_status(redis, redis_up), "tcp", KIND_INFRA,
              "缓存 / 任务队列",
              start_hint=None if redis_up else "docker compose up -d redis",
              scheme="redis"),
    ]

    mocks = [
        _inproc("api-mock", "API Mock (HTTP)", api_mock_server,
                "HTTP 接口挡板，被测系统的依赖用它顶", "/tools/api-mock", "协议 Mock → HTTP"),
        _inproc("ws-mock", "WebSocket Mock", ws_mock_server,
                "WebSocket 挡板", "/tools/api-mock", "协议 Mock → WebSocket", scheme="ws"),
        _inproc("tcp-mock", "TCP Mock", tcp_mock_server,
                "裸 TCP 挡板", "/tools/api-mock", "协议 Mock → TCP", scheme="tcp"),
        _inproc("udp-mock", "UDP Mock", udp_mock_server,
                "UDP 挡板", "/tools/api-mock", "协议 Mock → UDP", scheme="udp"),
        _inproc("grpc-mock", "gRPC Mock", grpc_mock_server,
                "gRPC 挡板", "/tools/api-mock", "协议 Mock → gRPC", scheme="grpc"),
        _inproc("llm-mock", "LLM Mock", llm_mock_server,
                "OpenAI 兼容的假模型，省网关额度 / 造异常响应", "/tools/llm-mock", "LLM Mock"),
        _inproc("mcp-mock", "MCP Mock", mcp_mock_server,
                "假 MCP Server，测 Agent 侧的工具调用", "/tools/mcp-mock", "MCP Mock"),
        _inproc("oauth2-mock", "OAuth2 Mock", oauth2_mock_server,
                "假授权服务器，测登录 / token 流程", "/tools/oauth2-mock", "OAuth2 Mock"),
    ]

    tools = [
        _inproc("proxy-probe", "代理观测", proxy_probe,
                "验证「配了出站代理，请求是否真的走了代理」", "/tools/proxy-probe", "代理观测"),
    ]

    ai_hint = "bash deploy/start-ai-services.sh"
    ai = [
        _item("claude-proxy", "claude-proxy", proxy_ep[0] if proxy_ep else None,
              proxy_ep[1] if proxy_ep else None,
              net_status(proxy_ep, proxy_up), "http", KIND_EXTERNAL,
              "429 降级通道：把真 claude CLI 包成 OpenAI 接口，绕开网关对 SDK 的限流。挂了则限流只能靠重试",
              start_hint=None if proxy_up else ai_hint),
        _item("playwright-mcp", "playwright-mcp", pw[0] if pw else None, pw[1] if pw else None,
              net_status(pw, pw_up), "tcp", KIND_EXTERNAL,
              "UI 脚本生成的浏览器通道（SSE）。挂了 UI 脚本生成会静默失败。host 只认 localhost",
              start_hint=None if pw_up else ai_hint),
        _item("ai-gateway", "AI 网关", gateway[0] if gateway else None,
              gateway[1] if gateway else None,
              net_status(gateway, gw_up), "tcp", KIND_INFRA,
              "公司统一 AI 网关，所有 LLM 调用的出口",
              start_hint=None if gw_up else "网关在公司内网，本地起不了；确认 VPN / AI_BASE_URL"),
    ]

    groups = [
        {"key": "core", "name": "平台核心", "items": core},
        {"key": "mock", "name": "Mock 服务", "items": mocks},
        {"key": "tool", "name": "测试工具", "items": tools},
        {"key": "ai", "name": "AI 依赖", "items": ai},
    ]

    all_items = [i for g in groups for i in g["items"]]
    summary = {
        "total": len(all_items),
        "up": sum(1 for i in all_items if i["status"] == UP),
        "down": sum(1 for i in all_items if i["status"] == DOWN),
        "notConfigured": sum(1 for i in all_items if i["status"] == NOT_CONFIGURED),
    }
    return {"summary": summary, "groups": groups}


async def _noop() -> bool:
    """地址没配时占位，让 gather 的返回值个数对齐。"""
    return False
