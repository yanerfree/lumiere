"""LLM Mock 服务管理器 — 管理独立端口的 Mock HTTP 服务"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.cors import CORSMiddleware

from app.deps.db import async_session_factory
from app.services import llm_mock_engine as engine
from app.services import llm_mock_service as svc
from app.services import llm_mock_smart as smart

logger = logging.getLogger("llm_mock")


_STATE_FILE = Path(__file__).resolve().parent.parent.parent / ".mock_state" / "llm_mock.json"

# 没配 /v1/embeddings 路由时用的内置兜底配置
_FALLBACK_EMBEDDING_ROUTE: dict = {
    "id": None,
    "name": "内置向量兜底",
    "status_code": 200,
    "response_type": "embedding",
    "response_mode": "default",
    "response_body": "",
    "model_mode": "follow_request",
    "custom_model": None,
    "token_mode": "auto",
    "custom_prompt_tokens": None,
    "custom_completion_tokens": None,
    "delay_ms": 0,
    "finish_reason": None,
    "response_headers": None,
    "stream_mode": "auto",
    "sse_chunk_size": 1,
    "smart_enabled": False,
    "smart_role": "auto",
    "smart_body_marker": None,
}


class MockServerManager:
    def __init__(self):
        self.port: int = 28100
        self.host: str = "0.0.0.0"
        self.capture_enabled: bool = True
        self.max_log_count: int = 1000
        self._server: asyncio.Server | None = None
        self._app: FastAPI | None = None
        self._task: asyncio.Task | None = None
        self._ws_clients: list = []
        # 断连场景下异步写日志的任务。必须持强引用，否则可能被 GC 掉、日志无声无息地丢
        self._log_tasks: set[asyncio.Task] = set()

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
            logger.warning("Mock 服务 task 已意外退出，清理状态")
            self._server = None
            self._task = None
            return False
        return getattr(self._server, 'started', False)

    async def start(self) -> None:
        if self.running:
            return
        self._app = self._create_app()
        import uvicorn
        config = uvicorn.Config(self._app, host=self.host, port=self.port, log_level="warning")
        server = uvicorn.Server(config)
        from app.services._mock_server_util import guarded_serve
        task = asyncio.create_task(guarded_serve(server, "LLM Mock"))
        self._task = task
        task.add_done_callback(self._on_task_done)
        self._server = server
        # 等待服务启动
        for _ in range(50):
            if server.started:
                break
            if task.done():
                self._server = None
                self._task = None
                raise RuntimeError(f"LLM Mock 启动失败，端口 {self.port} 可能被占用")
            await asyncio.sleep(0.1)
        logger.info("Mock 服务已启动 %s:%d", self.host, self.port)
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
            logger.info("Mock 服务已停止")
            self._save_state(False)

    def _on_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error("Mock 服务异常退出: %s", exc)
        self._server = None
        self._task = None

    def _create_app(self) -> FastAPI:
        app = FastAPI(title="LLM Mock Server")
        app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

        mgr = self

        @app.get("/v1/models")
        @app.get("/{prefix:path}/v1/models")
        async def list_models(prefix: str = ""):
            return mgr._build_models_response()

        # 探活。没有它的话，「上游到底活着没有」只能靠发一条业务请求去试，
        # 而那条请求会进请求日志、把「上游收到几次」这类断言搞脏。
        # 声明在 catch_all 之前，否则会被通配路由吃掉（路由都是 POST，GET /health 会 404）。
        @app.get("/health")
        async def health():
            return {"status": "ok", "service": "llm-mock", "port": mgr.port}

        @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
        async def catch_all(request: Request, path: str):
            return await mgr._handle_request(request, f"/{path}")

        return app

    def _build_models_response(self) -> dict:
        catalog = {
            "openai": [
                "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4",
                "gpt-3.5-turbo", "o1", "o1-mini", "o1-pro",
                "o3", "o3-mini", "o4-mini",
                "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
                # embedding 模型 —— 网关要在这里看到 embedding 才认这个 Provider 能做语义缓存
                "text-embedding-3-small", "text-embedding-3-large", "text-embedding-ada-002",
            ],
            "deepseek": [
                "deepseek-chat", "deepseek-reasoner",
            ],
            "qwen": [
                "qwen-turbo", "qwen-plus", "qwen-max", "qwen-long",
                "qwen2.5-72b-instruct", "qwen2.5-32b-instruct", "qwen2.5-14b-instruct", "qwen2.5-7b-instruct",
                "qwen3-235b-a22b", "qwen3-32b", "qwen3-8b",
                "text-embedding-v3", "text-embedding-v2",
            ],
            "zhipu": [
                "glm-4-plus", "glm-4-air", "glm-4-flash", "glm-4-long",
                "glm-4v-plus", "glm-4v",
                "embedding-3", "embedding-2",
            ],
            "anthropic": [
                "claude-opus-5", "claude-sonnet-5", "claude-fable-5",
                "claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
            ],
            "moonshot": [
                "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k",
            ],
            "baai": [
                "bge-m3", "bge-large-zh-v1.5",
            ],
        }
        created = int(time.time()) - 86400
        data = []
        for owner, models in catalog.items():
            for m in models:
                data.append({"id": m, "object": "model", "created": created, "owned_by": owner})
        return {"object": "list", "data": data}

    async def _handle_request(self, request: Request, path: str) -> JSONResponse | StreamingResponse:
        try:
            return await self._do_handle_request(request, path)
        except Exception:
            logger.exception("Mock 请求处理异常: %s %s", request.method, path)
            return JSONResponse(
                {"error": {"message": "Internal mock server error", "type": "server_error", "param": None, "code": None}},
                status_code=500,
            )

    async def _do_handle_request(self, request: Request, path: str) -> JSONResponse | StreamingResponse:
        t0 = time.perf_counter()
        method = request.method
        body_bytes = await request.body()
        try:
            request_body = json.loads(body_bytes) if body_bytes else {}
        except (json.JSONDecodeError, ValueError):
            request_body = {}

        # 匹配路由
        t_match_start = time.perf_counter()
        matched_route = await self._match_route(method, path)
        match_ms = (time.perf_counter() - t_match_start) * 1000

        if matched_route is None:
            # embeddings 内置兜底：没配路由也要能回向量，否则网关探测一次 404 就判定这个 Provider 不支持语义缓存。
            # 想改延迟/报错/固定向量，加一条 /v1/embeddings 路由即可覆盖它。
            if engine.is_embeddings_route({}, path):
                route_dict = dict(_FALLBACK_EMBEDDING_ROUTE)
                t_build = time.perf_counter()
                resp_body, extra_headers = engine.build_embeddings_response(route_dict, request_body)
                t_done = time.perf_counter()
                if self.capture_enabled:
                    await self._log_request(
                        route_dict, request, request_body, method, path,
                        200, json.dumps(engine.compact_embeddings_for_log(resp_body), ensure_ascii=False), extra_headers,
                        match_ms, (t_build - t0) * 1000, (t_done - t_build) * 1000, (t_done - t0) * 1000,
                    )
                return JSONResponse(resp_body, status_code=200, headers=extra_headers)
            return JSONResponse(
                {"error": {"message": f"No mock route matched for {method} {path}", "type": "not_found", "param": None, "code": None}},
                status_code=404,
            )

        route_dict = self._route_to_dict(matched_route)
        # 智能应答开着就由它接管：响应内容 / 状态码 / finish_reason / 流式全部由请求里的
        # 指令决定。放在最前面是因为 status_code 会影响下面的流式判定和日志。
        # 关着就是一条静态 mock，所有请求都回路由上配的那段 response_body。
        smart_meta: dict | None = None
        if route_dict.get("smart_enabled"):
            route_dict, smart_meta = smart.apply_smart(route_dict, request_body, path)
            logger.info(
                "智能应答: 角色=%s 形状=%s 指令=%s (路由 %s)",
                smart_meta.get("role"), smart_meta.get("shape"),
                smart_meta.get("directive") or "无", route_dict.get("name"),
            )
        shape = route_dict.get("_smart_shape") or "chat"
        is_embeddings = engine.is_embeddings_route(route_dict, path)
        # embeddings 没有流式、错误响应也不走流式，这两条压过 stream_mode
        if is_embeddings or route_dict["status_code"] >= 400:
            is_stream = False
        else:
            stream_mode = route_dict.get("stream_mode") or "auto"
            if stream_mode == "force_stream":
                # 故意不守约定：请求写 stream:false 也照样返事件流 —— 网关 fail-closed 就是靠这个验的
                is_stream = True
            elif stream_mode == "force_json":
                # 反过来耍赖：请求要流式，上游只给整包 JSON
                is_stream = False
            else:
                is_stream = bool(request_body.get("stream", False))

        # 延迟模拟
        delay = route_dict.get("delay_ms", 0)
        if delay > 0:
            await asyncio.sleep(delay / 1000.0)

        t_first_byte = time.perf_counter()
        first_byte_ms = (t_first_byte - t0) * 1000

        if is_stream:
            if shape == "anthropic":
                stream_builder = engine.build_anthropic_stream
            elif shape == "text":
                stream_builder = engine.build_text_completion_stream
            else:
                stream_builder = engine.build_response_stream

            async def stream_with_log():
                body_parts = []
                finished = False
                try:
                    async for chunk in stream_builder(route_dict, request_body):
                        body_parts.append(chunk)
                        yield chunk
                    finished = True
                finally:
                    # 走 finally 是因为**客户端中途断连也必须留下日志**。
                    # 护栏拦截的形态就是断连：网关判定要拦，直接掐掉与上游的连接。
                    # 只在正常读完时记的话，恰恰是最该查的那次请求在日志里完全不存在，
                    # 「被拦下来时上游已经发出去多少」就永远查不到了。
                    t_done = time.perf_counter()
                    if self.capture_enabled:
                        text = "".join(body_parts)
                        meta = dict(smart_meta) if isinstance(smart_meta, dict) else None
                        if not finished:
                            text += "\n[流未发完：客户端中途断开连接（护栏拦截通常就是这个形态）]"
                            if meta is not None:
                                meta["aborted"] = True
                        args = (
                            route_dict, request, request_body, method, path,
                            route_dict["status_code"], text, {},
                            match_ms, first_byte_ms, (t_done - t_first_byte) * 1000, (t_done - t0) * 1000,
                            meta,
                        )
                        if finished:
                            await self._log_request(*args)
                        else:
                            # ⚠ 断连时当前任务正在被取消，**这里绝不能直接 await 数据库**：
                            # 取消会把 asyncpg 连接掐在执行到一半的地方，坏连接回到池子里，
                            # 之后别的请求全报 "connection is closed"（实测踩过，整个后端跟着挂）。
                            # 甩给一个独立任务去写，它不受本次取消影响。
                            self._spawn_log_task(*args)

            headers = engine._build_headers(route_dict, "")
            headers["content-type"] = "text/event-stream; charset=utf-8"
            headers["cache-control"] = "no-cache"
            headers["connection"] = "keep-alive"
            return StreamingResponse(stream_with_log(), media_type="text/event-stream", headers=headers)
        else:
            if is_embeddings:
                resp_body, extra_headers = engine.build_embeddings_response(route_dict, request_body)
            elif shape == "anthropic":
                resp_body, extra_headers = engine.build_anthropic_message_json(route_dict, request_body)
            elif shape == "text":
                resp_body, extra_headers = engine.build_text_completion_json(route_dict, request_body)
            else:
                resp_body, extra_headers = engine.build_response_json(route_dict, request_body)
            t_done = time.perf_counter()
            body_ms = (t_done - t_first_byte) * 1000
            total_ms = (t_done - t0) * 1000

            status = route_dict["status_code"]
            if self.capture_enabled:
                log_body = engine.compact_embeddings_for_log(resp_body) if is_embeddings and status < 400 else resp_body
                await self._log_request(
                    route_dict, request, request_body, method, path,
                    status, json.dumps(log_body, ensure_ascii=False), extra_headers,
                    match_ms, first_byte_ms, body_ms, total_ms, smart_meta,
                )
            return JSONResponse(resp_body, status_code=status, headers=extra_headers)

    async def _match_route(self, method: str, path: str):
        async with async_session_factory() as session:
            routes = await svc.list_routes(session)
            enabled = [r for r in routes if r.enabled and r.method.upper() == method.upper()]
            # 1. 精确匹配
            for r in enabled:
                if r.path == path:
                    await svc.increment_hit(session, r.id)
                    await session.commit()
                    return r
            # 2. 通配匹配（路径里带 * 的，长模式优先）—— Azure 那种把部署名塞进路径的场景
            wildcards = sorted(
                (r for r in enabled if engine.has_wildcard(r.path)),
                key=lambda r: len(r.path), reverse=True,
            )
            for r in wildcards:
                if engine.path_matches(r.path, path):
                    await svc.increment_hit(session, r.id)
                    await session.commit()
                    return r
            # 3. 前缀匹配（长路径优先）
            enabled.sort(key=lambda r: len(r.path), reverse=True)
            for r in enabled:
                if r.path == "/" or path.startswith(r.path):
                    await svc.increment_hit(session, r.id)
                    await session.commit()
                    return r
            # 4. 后缀匹配（兼容不同厂商前缀，如 /compatible-mode/v1/chat/completions）
            for r in enabled:
                if r.path != "/" and path.endswith(r.path):
                    await svc.increment_hit(session, r.id)
                    await session.commit()
                    return r
        return None

    def _route_to_dict(self, route) -> dict:
        return {
            "id": route.id,
            "name": route.name,
            "method": route.method,
            "path": route.path,
            "delay_ms": route.delay_ms,
            "status_code": route.status_code,
            "response_format": route.response_format,
            "preset_mode": route.preset_mode,
            "response_mode": route.response_mode,
            "finish_reason": route.finish_reason,
            "response_body": route.response_body,
            "token_mode": route.token_mode,
            "custom_prompt_tokens": route.custom_prompt_tokens,
            "custom_completion_tokens": route.custom_completion_tokens,
            "model_mode": route.model_mode,
            "custom_model": route.custom_model,
            "response_headers": route.response_headers,
            "sse_chunk_delay_ms": route.sse_chunk_delay_ms,
            "sse_chunk_size": route.sse_chunk_size,
            "stream_mode": route.stream_mode,
            "response_type": route.response_type,
            "tool_calls": route.tool_calls,
            "smart_enabled": route.smart_enabled,
            "smart_role": route.smart_role,
            "smart_body_marker": route.smart_body_marker,
        }

    def _spawn_log_task(self, *args) -> None:
        """把写日志甩给独立任务 —— 只在「本次请求正在被取消」时用（见调用处注释）。"""
        try:
            task = asyncio.create_task(self._log_request(*args))
        except RuntimeError:
            return  # 事件循环已经在关了，日志写不成也别炸
        self._log_tasks.add(task)
        task.add_done_callback(self._log_tasks.discard)

    async def _log_request(
        self, route_dict, request, request_body, method, path,
        status_code, response_body_str, resp_headers,
        match_ms, first_byte_ms, body_ms, total_ms, smart_meta=None,
    ):
        req_headers = dict(request.headers) if request else {}
        caller = req_headers.get("user-agent", "")
        ip = request.client.host if request and request.client else ""
        req_model = request_body.get("model") if isinstance(request_body, dict) else None

        # 从响应体中提取 response model
        resp_model = None
        try:
            rb = json.loads(response_body_str) if isinstance(response_body_str, str) else {}
            resp_model = rb.get("model")
        except Exception:
            pass

        usage = {}
        try:
            rb = json.loads(response_body_str) if isinstance(response_body_str, str) else {}
            usage = rb.get("usage") or {}
        except Exception:
            pass

        log_data = {
            "route_id": route_dict.get("id"),
            "method": method,
            "path": path,
            "request_headers": req_headers,
            "request_body": request_body if isinstance(request_body, dict) else {},
            "caller": caller[:500] if caller else None,
            "ip": ip,
            "status_code": status_code,
            "response_body": response_body_str[:10000] if response_body_str else None,
            "response_headers_out": resp_headers if isinstance(resp_headers, dict) else None,
            "request_model": req_model,
            "response_model": resp_model,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "finish_reason": route_dict.get("finish_reason"),
            "smart_meta": smart_meta if isinstance(smart_meta, dict) else None,
            "match_ms": round(match_ms, 2),
            "first_byte_ms": round(first_byte_ms, 2),
            "body_ms": round(body_ms, 2),
            "total_ms": round(total_ms, 2),
        }
        try:
            async with async_session_factory() as session:
                await svc.create_log(session, log_data)
                await svc.trim_logs(session, self.max_log_count)
                await session.commit()
        except Exception:
            logger.exception("Failed to save mock request log")


mock_server = MockServerManager()
