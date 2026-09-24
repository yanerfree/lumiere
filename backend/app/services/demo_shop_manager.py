"""样例订单服务 —— 一个**真的**后端，跑在独立端口 29000。

和隔壁那几个 Mock 的根本区别：Mock 是「你问什么它答什么」，答案是配出来的；
这个是真的有库、有状态、有规则。同一个接口，下单成功之后再下一单库存就不够了，
第二次调会 409 —— 这件事 Mock 演不出来，而"第二次调结果不一样"恰恰是测试要验的东西。

**它是被测系统，不是平台的一部分。** 所以：
- 不挂平台的登录态，它有自己的账号（admin / clerk）—— 越权返回 403 要测得出来；
- 不走平台的 `/api` 前缀和权限依赖，BASE_URL 就是 `http://<host>:29000`，
  用例里直接填这个地址，和填任何一个外部被测系统没有区别；
- 数据存在平台同一个 pg 里（`demo_*` 三张表），因为它要的就是"真的落库"。

页面在「测试工具 → 订单服务」。自带 Swagger：http://<host>:29000/docs
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.cors import CORSMiddleware

from app.config import settings
from app.deps.db import async_session_factory
from app.services import demo_shop_service as svc
from app.services.demo_shop_service import DemoShopError

logger = logging.getLogger("demo_shop")

_STATE_FILE = Path(__file__).resolve().parent.parent.parent / ".mock_state" / "demo_shop.json"

DEFAULT_PORT = 29000

# 这几条不进请求日志：健康检查会被反复轮询，一条真实业务请求转眼就被它冲出屏幕。
# ⚠ 这份名单要发给页面（见 `/api/demo-shop/endpoints`）—— 否则「请求日志」那一栏
# 空着，看起来像「这条接口还没被调过」，而实际是调了、只是故意没记。
UNLOGGED_PATHS = ("/health", "/openapi.json")

# 内置账号。明文是故意的 —— 这是个给人练手的被测系统，账号要能直接写进用例里。
# 真系统别这么干，这里就是要让「密码错了返回什么」「换个角色能不能删」测得出来。
ACCOUNTS = {
    "admin": {"password": "admin123", "role": "admin", "displayName": "管理员"},
    "clerk": {"password": "clerk123", "role": "clerk", "displayName": "店员"},
}
TOKEN_TTL_SECONDS = 8 * 3600

MAX_LOG = 500


def _sign(raw: str) -> str:
    return hmac.new(settings.secret_key.encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]


def make_token(username: str, role: str) -> str:
    exp = int(time.time()) + TOKEN_TTL_SECONDS
    raw = f"{username}|{role}|{exp}"
    return base64.urlsafe_b64encode(f"{raw}|{_sign(raw)}".encode()).decode().rstrip("=")


def parse_token(token: str) -> dict | None:
    """token 坏了一律返回 None（调用方转 401）。不抛异常 —— 伪造 token 是
    用例会故意打的一条路径，它该得到 401 而不是 500。"""
    try:
        pad = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(token + pad).decode()
        username, role, exp, sig = raw.rsplit("|", 3)
        if not hmac.compare_digest(sig, _sign(f"{username}|{role}|{exp}")):
            return None
        if int(exp) < time.time():
            return None
        return {"username": username, "role": role,
                "expiresAt": datetime.fromtimestamp(int(exp), timezone.utc).isoformat()}
    except Exception:
        return None


class DemoShopManager:
    def __init__(self):
        self.port: int = DEFAULT_PORT
        self.host: str = "0.0.0.0"
        self._server = None
        self._app: FastAPI | None = None
        self._task: asyncio.Task | None = None
        self.logs: deque = deque(maxlen=MAX_LOG)
        self.started_at: str | None = None

    # ── 状态文件：重启后端之后自动把它拉起来 ──
    def _save_state(self, running: bool):
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(json.dumps({"running": running, "port": self.port}))
        except Exception:
            pass

    def _load_state(self) -> bool:
        try:
            data = json.loads(_STATE_FILE.read_text())
            self.port = data.get("port", self.port)
            return data.get("running", False)
        except Exception:
            return False

    @property
    def running(self) -> bool:
        if self._server is None:
            return False
        if self._task is not None and self._task.done():
            logger.warning("订单服务 task 已意外退出，清理状态")
            self._server = None
            self._task = None
            return False
        return getattr(self._server, "started", False)

    async def start(self) -> None:
        if self.running:
            return
        # 起服务之前先把样例商品补齐：空商品表下所有下单请求都 404，
        # 看起来像"服务坏了"，实际只是没数据 —— 这种假象要在源头掐掉。
        try:
            async with async_session_factory() as session:
                await svc.seed_products(session)
        except Exception as e:
            logger.warning("样例商品初始化失败（服务照常启动）：%s", e)

        self._app = self._create_app()
        import uvicorn
        config = uvicorn.Config(self._app, host=self.host, port=self.port, log_level="warning")
        server = uvicorn.Server(config)
        from app.services._mock_server_util import guarded_serve
        task = asyncio.create_task(guarded_serve(server, "订单服务"))
        self._task = task
        task.add_done_callback(self._on_task_done)
        self._server = server
        for _ in range(50):
            if server.started:
                break
            if task.done():
                self._server = None
                self._task = None
                raise RuntimeError(f"订单服务启动失败，端口 {self.port} 可能被占用")
            await asyncio.sleep(0.1)
        self.started_at = datetime.now(timezone.utc).isoformat()
        logger.info("订单服务已启动 %s:%d", self.host, self.port)
        self._save_state(True)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
            if self._task:
                try:
                    await asyncio.wait_for(self._task, timeout=5)
                except (asyncio.TimeoutError, Exception):
                    pass
            self._server = None
            self._task = None
            self.started_at = None
            logger.info("订单服务已停止")
            self._save_state(False)

    def _on_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error("订单服务异常退出: %s", exc)
        self._server = None
        self._task = None

    # ── 请求日志 ──
    def _log(self, method: str, path: str, status: int, ms: float, actor: str,
             req_body: str | None, resp_snippet: str | None):
        self.logs.appendleft({
            "id": f"{time.time_ns()}",
            "ts": datetime.now(timezone.utc).isoformat(),
            "method": method,
            "path": path,
            "status": status,
            "durationMs": round(ms, 1),
            "actor": actor or "-",
            "requestBody": (req_body or "")[:2000],
            "responseSnippet": (resp_snippet or "")[:2000],
        })

    # ── 应用 ──
    def _create_app(self) -> FastAPI:  # noqa: C901 - 路由多，拆开反而更难看懂
        mgr = self
        app = FastAPI(
            title="订单服务 Demo",
            version="1.0.0",
            description=(
                "Lumiere 自带的练手被测系统：真落库、有状态机、有权限。\n\n"
                "账号：`admin` / `admin123`（可删单）、`clerk` / `clerk123`（不可删单）。\n"
                "先 `POST /api/login` 拿 token，后续请求带 `Authorization: Bearer <token>`。"
            ),
        )
        app.add_middleware(CORSMiddleware, allow_origins=["*"],
                           allow_methods=["*"], allow_headers=["*"])

        @app.middleware("http")
        async def _record(request: Request, call_next):
            t0 = time.perf_counter()
            body = b""
            if request.method in ("POST", "PUT", "PATCH"):
                body = await request.body()
            response = await call_next(request)
            ms = (time.perf_counter() - t0) * 1000
            if request.url.path in UNLOGGED_PATHS:
                return response
            # 响应体要先收完再原样发出去。不收的话日志里「返回了什么」永远是空的，
            # 而一栏永远空着的日志比没有这一栏更坏：它看起来像"这次真的没返回内容"。
            chunks = [chunk async for chunk in response.body_iterator]
            raw = b"".join(chunks)
            user = parse_token(_bearer(request) or "")
            mgr._log(request.method, request.url.path, response.status_code, ms,
                     user["username"] if user else "",
                     body.decode("utf-8", errors="replace") if body else None,
                     raw.decode("utf-8", errors="replace") if raw else None)
            return Response(content=raw, status_code=response.status_code,
                            headers=dict(response.headers), media_type=response.media_type)

        @app.exception_handler(DemoShopError)
        async def _biz_error(request: Request, exc: DemoShopError):
            return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.status)

        def _bearer(request: Request) -> str | None:
            auth = request.headers.get("authorization") or ""
            if auth.lower().startswith("bearer "):
                return auth[7:].strip()
            return None

        def require_user(request: Request) -> dict:
            token = _bearer(request)
            user = parse_token(token) if token else None
            if not user:
                raise DemoShopError(401, "UNAUTHORIZED", "缺少或无效的 token，请先调 /api/login")
            return user

        def require_admin(request: Request) -> dict:
            user = require_user(request)
            if user["role"] != "admin":
                raise DemoShopError(403, "FORBIDDEN", "只有管理员能做这个操作")
            return user

        # ── 公开 ──

        @app.get("/health", tags=["系统"], summary="健康检查")
        async def health():
            return {"status": "ok", "service": "demo-shop", "time":
                    datetime.now(timezone.utc).isoformat()}

        @app.get("/", include_in_schema=False)
        async def root():
            return {"service": "订单服务 Demo", "docs": "/docs", "health": "/health"}

        @app.post("/api/login", tags=["登录"], summary="登录拿 token")
        async def login(payload: dict):
            username = str(payload.get("username") or "").strip()
            password = str(payload.get("password") or "")
            acc = ACCOUNTS.get(username)
            # 用户名不存在和密码错返回同一句话：区分开等于告诉对方"这个账号是存在的"
            if not acc or not hmac.compare_digest(acc["password"], password):
                raise DemoShopError(401, "BAD_CREDENTIALS", "用户名或密码错误")
            return {
                "token": make_token(username, acc["role"]),
                "username": username,
                "role": acc["role"],
                "displayName": acc["displayName"],
                "expiresIn": TOKEN_TTL_SECONDS,
            }

        @app.get("/api/me", tags=["登录"], summary="当前登录人")
        async def me(request: Request):
            user = require_user(request)
            acc = ACCOUNTS.get(user["username"], {})
            return {**user, "displayName": acc.get("displayName", user["username"])}

        # ── 商品 ──

        @app.get("/api/products", tags=["商品"], summary="商品列表")
        async def products(request: Request, keyword: str = "", onlyActive: bool = False,
                           page: int = 1, pageSize: int = 50):
            require_user(request)
            async with async_session_factory() as session:
                return await svc.list_products(session, keyword, onlyActive, page, pageSize)

        @app.get("/api/products/{sku}", tags=["商品"], summary="商品详情")
        async def product_detail(request: Request, sku: str):
            require_user(request)
            async with async_session_factory() as session:
                return svc.product_json(await svc.get_product(session, sku))

        @app.patch("/api/products/{sku}", tags=["商品"], summary="改商品（价格/库存/上下架）")
        async def product_update(request: Request, sku: str, payload: dict):
            require_admin(request)
            async with async_session_factory() as session:
                return await svc.update_product(session, sku, payload or {})

        # ── 订单 ──

        @app.get("/api/orders", tags=["订单"], summary="订单列表")
        async def orders(request: Request, status: str = "", keyword: str = "",
                         page: int = 1, pageSize: int = 20):
            require_user(request)
            async with async_session_factory() as session:
                return await svc.list_orders(session, status, keyword, page, pageSize)

        @app.get("/api/orders/{order_no}", tags=["订单"], summary="订单详情")
        async def order_detail(request: Request, order_no: str):
            require_user(request)
            async with async_session_factory() as session:
                return svc.order_json(await svc.get_order(session, order_no))

        @app.post("/api/orders", tags=["订单"], summary="下单（会真扣库存）", status_code=201)
        async def order_create(request: Request, payload: dict):
            user = require_user(request)
            async with async_session_factory() as session:
                return await svc.create_order(session, payload or {}, user["username"])

        @app.put("/api/orders/{order_no}", tags=["订单"], summary="改订单（仅待支付）")
        async def order_update(request: Request, order_no: str, payload: dict):
            require_user(request)
            async with async_session_factory() as session:
                return await svc.update_order(session, order_no, payload or {})

        @app.post("/api/orders/{order_no}/{action}", tags=["订单"],
                  summary="状态流转 pay/ship/complete/cancel")
        async def order_action(request: Request, order_no: str, action: str):
            require_user(request)
            async with async_session_factory() as session:
                return await svc.transition(session, order_no, action)

        @app.delete("/api/orders/{order_no}", tags=["订单"], summary="删订单（仅管理员）")
        async def order_delete(request: Request, order_no: str):
            require_admin(request)
            async with async_session_factory() as session:
                return await svc.delete_order(session, order_no)

        @app.get("/api/stats", tags=["统计"], summary="订单统计")
        async def stats(request: Request):
            require_user(request)
            async with async_session_factory() as session:
                return await svc.stats(session)

        @app.post("/api/admin/reset", tags=["系统"], summary="重置样例数据（清空订单）")
        async def reset(request: Request):
            require_admin(request)
            async with async_session_factory() as session:
                return await svc.reset_demo_data(session)

        return app


demo_shop_manager = DemoShopManager()
