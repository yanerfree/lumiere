"""MCP Mock 服务管理器 —— **一个端口（28300）上挂 N 个 MCP 服务**。

地址形状：`http://<host>:28300/<slug>/mcp`

为什么不是一服务一端口：28xxx 那一段（28100~28900）在 `app/main.py` 的 lifespan 里
已经全部绑掉了，再要端口就得往外扩段，而扩段是部署那边的事（防火墙、容器端口映射）。
路径前缀不占新端口，加服务不用动任何部署配置。

跟老版本（单服务 + JSON 文件）比，三件事变了：

1. **工具参数是真有类型的。** 老实现把每个参数 `exec` 成 `str = ""`，
   于是页面上标的 integer/array **纯属装饰** —— 客户端 `tools/list` 拿到的
   永远是一串可选字符串，测不出「传错类型对方认不认」。
   现在用自定义 `Tool` 子类，`parameters` 直接就是我们自己拼的 JSON Schema。
2. **认证在服务级。** 不认证 / Bearer Token / 自定义 API Key 请求头，
   由一层 ASGI 网关在进 MCP 之前拦。
3. **入参校验有三档松紧**（严格/宽松/不校验），我们自己校验 ——
   fastmcp 只对「从函数签名推出来的」工具自动校验，我们这种它不管，
   这正好，否则「宽松」「不校验」两档根本做不出来。

⚠ 工具清单是**建 app 时**一次性注册进 FastMCP 的，改了数据库不会影响已经跑着的实例，
   所以增删工具/改服务设置都要 `reload()`（停+起）。但**响应内容是每次调用现查库的**，
   改返回值不用重载 —— 那是最常改的东西，每改一次断一次连太贵。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("mcp_mock")

_STATE_FILE = Path(__file__).resolve().parent.parent.parent / ".mock_state" / "mcp_mock.json"

DEFAULT_ERROR_MESSAGE = "Mock error: tool call failed"


def _transport_arg(transport: str) -> str:
    """数据库里存的传输名 → fastmcp `http_app(transport=)` 认的值。"""
    return "sse" if transport == "sse" else "http"


class _AuthGate:
    """服务级认证网关 —— 在请求进 MCP 之前拦一道。

    ⚠ 认证失败**必须**是 401 而不是 403：MCP 客户端（以及 OAuth 那一套）看的是 401
      才会去补认证头，403 会被当成「认证对了但没权限」，于是客户端不会重试，
      人看到的现象是「配了 token 也连不上」。
    """

    def __init__(self, app, snapshot: dict):
        self.app = app
        self.snap = snapshot  # {"auth_type", "auth_config", "name"}

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        why = self._check(scope)
        if why is None:
            return await self.app(scope, receive, send)
        body = json.dumps({"error": "unauthorized", "message": why}, ensure_ascii=False).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", b'Bearer realm="lumiere-mcp-mock"'),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    def _check(self, scope) -> str | None:
        auth_type = self.snap.get("auth_type") or "none"
        if auth_type == "none":
            return None
        cfg = self.snap.get("auth_config") or {}
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        if auth_type == "bearer":
            want = str(cfg.get("token") or "")
            got = headers.get("authorization", "")
            if not got:
                return "缺少 Authorization 请求头，这个服务要求 Bearer Token"
            prefix, _, value = got.partition(" ")
            if prefix.lower() != "bearer":
                return "Authorization 请求头要写成 `Bearer <token>`"
            if not want or value.strip() != want:
                return "Bearer Token 不对"
            return None
        if auth_type == "apikey":
            header_name = str(cfg.get("headerName") or "X-API-Key")
            want = str(cfg.get("apiKey") or "")
            got = headers.get(header_name.lower())
            if not got:
                return f"缺少 {header_name} 请求头，这个服务要求 API Key"
            if not want or got.strip() != want:
                return f"{header_name} 的值不对"
            return None
        return None


def _build_tool_class():
    """延迟建类 —— fastmcp 的 import 有点重，模块导入时别拖着。"""
    from fastmcp.exceptions import ToolError
    from fastmcp.tools.tool import Tool, ToolResult

    class MockTool(Tool):
        """一个 Mock 工具。参数 schema 是我们自己拼的，执行时现查库。"""

        server_id: Any = None
        tool_id: Any = None
        server_slug: str = ""
        validate_mode: str = "strict"
        param_defs: Any = None

        async def run(self, arguments: dict) -> ToolResult:
            from app.services.mcp_mock_validate import validate_arguments

            t0 = time.perf_counter()
            errors, cleaned = validate_arguments(self.param_defs, arguments, self.validate_mode)
            if errors:
                detail = "；".join(errors)
                await mcp_mock_server.record_call(
                    server_id=self.server_id, tool_name=self.name, arguments=arguments,
                    response={"error": detail}, source="mock-server", mode="validate",
                    is_error=True, t0=t0, reject_kind="validate", reject_detail=detail,
                )
                raise ToolError(f"参数校验不通过：{detail}")

            cfg = await mcp_mock_server.fetch_tool_runtime(self.tool_id)
            if cfg is None:
                raise ToolError(f"工具 {self.name} 已经被删掉了，请重新加载工具列表")

            if cfg["delay_ms"] > 0:
                await asyncio.sleep(min(cfg["delay_ms"], 60000) / 1000)

            payload, is_error, err_msg = _compute(cfg)
            await mcp_mock_server.record_call(
                server_id=self.server_id, tool_name=self.name, arguments=cleaned,
                response=payload if not is_error else {"error": err_msg},
                source="mock-server", mode=cfg["mode"], is_error=is_error, t0=t0,
            )
            if is_error:
                raise ToolError(err_msg)
            structured = payload if isinstance(payload, dict) else {"result": payload}
            return ToolResult(
                content=json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                structured_content=structured,
            )

    return MockTool


def _compute(cfg: dict) -> tuple[Any, bool, str]:
    """按 mode 算这次该返回什么。返回 (数据, 是不是错误, 错误文案)。"""
    mode = cfg.get("mode") or "success"
    if mode == "error":
        return None, True, cfg.get("error_message") or DEFAULT_ERROR_MESSAGE
    if mode == "custom":
        data = cfg.get("custom_data")
        if data is None:
            data = cfg.get("success_data")
        if data is None:
            data = {"result": "ok"}
        if cfg.get("custom_is_error"):
            msg = data.get("error", json.dumps(data, ensure_ascii=False)) if isinstance(data, dict) else str(data)
            return None, True, str(msg)
        return data, False, ""
    data = cfg.get("success_data")
    return ({"result": "ok"} if data is None else data), False, ""


class McpMockServerManager:
    def __init__(self):
        self.port: int = 28300
        self.host: str = "0.0.0.0"
        self._server = None
        self._task: asyncio.Task | None = None
        # 上次起服务时的服务快照，页面上的地址列表照它显示
        self._mounted: list[dict] = []

    # ── 状态 ──

    def _save_state(self, running: bool):
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(json.dumps({"running": running, "port": self.port}))
        except Exception:  # noqa: BLE001
            pass

    def _load_state(self) -> bool:
        try:
            data = json.loads(_STATE_FILE.read_text())
            self.port = data.get("port", self.port)
            return data.get("running", False)
        except Exception:  # noqa: BLE001
            return False

    @property
    def running(self) -> bool:
        if self._server is None:
            return False
        if self._task is not None and self._task.done():
            logger.warning("MCP Mock 服务 task 已意外退出，清理状态")
            self._server = None
            self._task = None
            return False
        return getattr(self._server, "started", False)

    @property
    def mounted(self) -> list[dict]:
        return self._mounted

    def base_url(self, host: str | None = None) -> str:
        return f"http://{host or 'localhost'}:{self.port}"

    def url_for(self, slug: str, host: str | None = None) -> str:
        return f"{self.base_url(host)}/{slug}/mcp"

    # ── 调用时要用的数据（跑在 Mock 服务那个协程里）──

    async def fetch_tool_runtime(self, tool_id) -> dict | None:
        """现查这个工具的返回配置。改返回值不用重载，就靠这一下。"""
        from app.deps.db import async_session_factory
        from app.models.mcp_mock import McpMockTool

        try:
            async with async_session_factory() as session:
                tool = await session.get(McpMockTool, tool_id)
                if tool is None:
                    return None
                return {
                    "mode": tool.mode,
                    "success_data": tool.success_data,
                    "custom_data": tool.custom_data,
                    "custom_is_error": tool.custom_is_error,
                    "error_message": tool.error_message,
                    "delay_ms": tool.delay_ms or 0,
                }
        except Exception:  # noqa: BLE001
            logger.exception("MCP Mock 读工具配置失败")
            return None

    async def record_call(
        self, *, server_id, tool_name: str, arguments: dict | None, response: Any,
        source: str, mode: str, is_error: bool, t0: float,
        reject_kind: str | None = None, reject_detail: str | None = None,
    ) -> None:
        """写一条调用日志。**写失败绝不能把这次调用打挂** —— 日志是旁路。"""
        from app.deps.db import async_session_factory
        from app.services import mcp_mock_service as svc

        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        try:
            body = json.dumps(response, ensure_ascii=False, default=str)[:20000]
        except Exception:  # noqa: BLE001
            body = str(response)[:20000]
        try:
            async with async_session_factory() as session:
                await svc.create_log(session, {
                    "server_id": server_id,
                    "tool_name": tool_name,
                    "arguments": arguments if isinstance(arguments, dict) else {"_raw": str(arguments)},
                    "response": body,
                    "source": source,
                    "mode": mode,
                    "is_error": is_error,
                    "reject_kind": reject_kind,
                    "reject_detail": reject_detail,
                    "elapsed_ms": elapsed,
                    "timestamp": datetime.now(timezone.utc),
                })
                if server_id is not None and source == "mock-server":
                    await svc.bump_call(session, server_id)
                await svc.trim_logs(session)
                await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("MCP Mock 写调用日志失败")

    # ── 建 app ──

    async def _snapshot(self) -> list[dict]:
        """从库里读出「现在该挂哪些服务、每个服务有哪些工具」。"""
        from app.deps.db import async_session_factory
        from app.services import mcp_mock_service as svc
        from app.services.mcp_mock_validate import normalize_params

        out: list[dict] = []
        async with async_session_factory() as session:
            for s in await svc.list_servers(session):
                if not s.enabled:
                    continue
                tools = [t for t in await svc.list_tools(session, s.id) if t.enabled]
                out.append({
                    "id": s.id,
                    "slug": s.slug,
                    "name": s.name,
                    "instructions": s.instructions or "",
                    "transport": s.transport,
                    "auth_type": s.auth_type,
                    "auth_config": s.auth_config,
                    "validate_mode": s.validate_mode,
                    "tools": [{
                        "id": t.id,
                        "name": t.name,
                        "description": t.description or t.name,
                        "params": normalize_params(t.params),
                    } for t in tools],
                })
        return out

    def _build_parent(self, snapshot: list[dict]):
        from fastmcp import FastMCP
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Mount, Route

        MockTool = _build_tool_class()
        from app.services.mcp_mock_validate import build_schema

        children: list[Any] = []
        routes: list[Any] = []
        mounted: list[dict] = []

        for snap in snapshot:
            mcp = FastMCP(
                name=snap["name"] or snap["slug"],
                instructions=snap["instructions"] or f"Lumiere MCP Mock —— {snap['name']}",
            )
            for t in snap["tools"]:
                try:
                    mcp.add_tool(MockTool(
                        name=t["name"],
                        description=t["description"],
                        parameters=build_schema(t["params"], snap["validate_mode"]),
                        server_id=snap["id"],
                        tool_id=t["id"],
                        server_slug=snap["slug"],
                        validate_mode=snap["validate_mode"],
                        param_defs=t["params"],
                    ))
                except Exception:  # noqa: BLE001
                    # **一个工具配坏了，代价应该是这一个用不了，不是整个服务起不来。**
                    logger.exception("MCP Mock 工具 %s/%s 注册失败，跳过", snap["slug"], t["name"])

            child = mcp.http_app(path="/mcp", transport=_transport_arg(snap["transport"]))
            children.append(child)
            routes.append(Mount(f"/{snap['slug']}", app=_AuthGate(child, snap)))
            mounted.append({
                "id": str(snap["id"]),
                "slug": snap["slug"],
                "name": snap["name"],
                "transport": snap["transport"],
                "authType": snap["auth_type"],
                "validateMode": snap["validate_mode"],
                "toolCount": len(snap["tools"]),
                "path": f"/{snap['slug']}/mcp",
            })

        async def _index(_request):
            return JSONResponse({
                "service": "Lumiere MCP Mock",
                "servers": [{
                    "name": m["name"], "slug": m["slug"], "url": f"/{m['slug']}/mcp",
                    "transport": m["transport"], "auth": m["authType"], "tools": m["toolCount"],
                } for m in mounted],
            })

        routes.append(Route("/", _index))

        @contextlib.asynccontextmanager
        async def lifespan(_app):
            # 每个子 MCP app 自己有 lifespan（会话管理器就在里面起）。
            # 挂到父 Starlette 底下之后**父的 lifespan 不会自动跑子的** ——
            # 少这一段的表现是：连接建得上，但 initialize 之后一调工具就挂，
            # 报的是 "Task group is not initialized"，看着像 fastmcp 的 bug。
            async with contextlib.AsyncExitStack() as stack:
                for sub in children:
                    await stack.enter_async_context(sub.router.lifespan_context(sub))
                yield

        self._mounted = mounted
        return Starlette(routes=routes, lifespan=lifespan)

    # ── 起停 ──

    async def start(self) -> None:
        if self.running:
            return
        snapshot = await self._snapshot()
        app = self._build_parent(snapshot)

        import uvicorn
        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="warning")
        server = uvicorn.Server(config)
        from app.services._mock_server_util import guarded_serve, unlatch_sse_shutdown
        # 起之前再掰一次：上一轮停服留下的全局「要退出了」标志会让新服务的
        # 每个 MCP 响应当场断流（细节见 unlatch_sse_shutdown 的注释）。
        unlatch_sse_shutdown()
        task = asyncio.create_task(guarded_serve(server, "MCP Mock"))
        self._task = task
        task.add_done_callback(self._on_task_done)
        self._server = server
        for _ in range(50):
            if server.started:
                break
            if task.done():
                self._server = None
                self._task = None
                raise RuntimeError(f"MCP Mock 启动失败，端口 {self.port} 可能被占用")
            await asyncio.sleep(0.1)
        logger.info("MCP Mock 服务已启动 %s:%d，挂了 %d 个服务", self.host, self.port, len(snapshot))
        self._save_state(True)

    async def stop(self) -> None:
        if self._server is not None:
            dead = self._server
            dead.should_exit = True
            if self._task:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(self._task, timeout=5)
            self._server = None
            self._task = None
            # 收尾必做：把 sse_starlette 那个进程级的「要退出了」开关掰回去，
            # 否则**整个后端**（含 :18800 上的 MCP）的 SSE 响应从此全断。
            from app.services._mock_server_util import unlatch_sse_shutdown
            unlatch_sse_shutdown(dead)
            logger.info("MCP Mock 服务已停止")
            self._save_state(False)

    async def _port_free(self) -> bool:
        """端口上还有没有人在听。连得上 = 旧实例还没收完尾。"""
        try:
            _, w = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", self.port), timeout=0.5
            )
            w.close()
            return False
        except Exception:  # noqa: BLE001
            return True

    async def reload(self) -> dict:
        """服务/工具清单改了之后，让**已经跑着的**服务重新加载一遍。

        工具是在 `_build_parent()` 里一次性注册进 FastMCP 的，改库不会影响
        已经跑起来的实例。实测过：页面上加一个工具，对面 CC 的 `tools/list` 里
        根本没有 —— 人看到的现象是「工具建了但用不了」，而页面没有任何地方提示要重启。

        重载 = 停 + 起。**停完立刻起会撞上端口还没释放**（实测踩过：一次重载之后
        服务再也没起来，页面显示已停止）。所以重试几次；真起不回来要如实报出去。

        返回 {"reloaded": bool, "reloadError": str|None}。键名不叫 error —— Mock 这一族
        用 `{"error": "..."}` 表示**这次操作失败了**，而重载失败时改动其实已经存下了，
        混用会让前端把「删成功了但没重载」报成「删除失败」。
        """
        if not self.running:
            return {"reloaded": False, "reloadError": None}
        await self.stop()
        # stop() 返回时旧监听 socket 未必已经彻底放开：SO_REUSEADDR 会让新的 bind
        # 直接成功，而新连接仍被路由到正在收尾的旧实例 —— 表现是 initialize 打过去
        # 连接被对端掐断（incomplete chunked read）。等端口真的空出来再起。
        for _ in range(20):
            if await self._port_free():
                break
            await asyncio.sleep(0.15)
        last = ""
        for _ in range(6):
            try:
                await self.start()
                return {"reloaded": True, "reloadError": None}
            except RuntimeError as e:
                last = str(e)
                await asyncio.sleep(0.4)
        logger.error("MCP Mock 重载失败，服务当前是停的: %s", last)
        return {"reloaded": False, "reloadError": f"改动已保存，但 Mock 服务没能重启（{last}）。请手动启动。"}

    def _on_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error("MCP Mock 服务异常退出: %s", exc)
        self._server = None
        self._task = None


mcp_mock_server = McpMockServerManager()
