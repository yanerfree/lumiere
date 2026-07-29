from fastapi import FastAPI
from fastapi.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware

from app.config import settings
from app.core.exceptions import AppError, app_error_handler, http_exception_handler, unhandled_exception_handler
from app.core.health import router as health_router
from app.api.auth import router as auth_router
from app.api.users import router as users_router
from app.api.projects import router as projects_router
from app.api.branches import router as branches_router
from app.api.cases import router as cases_router, folders_router
from app.api.variables import router as variables_router
from app.api.plans import router as plans_router, reports_router
from app.api.tasks import router as tasks_router
from app.api.logs import router as logs_router
from app.api.scripts import router as scripts_router, export_router as scripts_export_router
from app.api.scenario_variables import router as scenario_variables_router
from app.api.automation_resources import router as automation_resources_router
from app.api.i18n_messages import router as i18n_messages_router
from app.api.testforge import router as testforge_router
from app.api.debug import router as debug_router
from app.api.api_collections import router as api_collections_router
from app.api.llm_mock import router as llm_mock_router
from app.api.api_mock import router as api_mock_router
from app.api.ai import router as ai_router, config_router as ai_config_router
from app.api.ai_config import router as ai_provider_router, project_router as project_ai_config_router
from app.api.ai_capabilities import router as ai_capabilities_router
from app.api.skill_run import router as skill_run_router
from app.api.mcp_mock import router as mcp_mock_router
from app.api.protocol_mock import router as protocol_mock_router
from app.api.oauth2_mock import router as oauth2_mock_router
from app.api.load_test import router as load_test_router
from app.api.exploratory import router as exploratory_router
from app.api.documents import router as documents_router
from app.api.case_file import router as case_file_router
from app.api.api_test import router as api_test_router
from app.api.skill_manage import router as skill_manage_router
from app.api.knowledge import router as knowledge_router
from app.api.screenshots import router as screenshots_router
from app.api.toolbox import router as toolbox_router
from app.api.http_client import router as http_client_router
from app.api.scenario_gen import router as scenario_gen_router
from app.api.mcp_keys import router as mcp_keys_router
from app.core.middleware import CamelCaseResponse, TraceIdMiddleware

# --- MCP Server ---
from app.mcp import mcp
_mcp_raw = mcp.http_app(path="/")


# MCP 认证中间件 — 支持环境变量 MCP_API_KEY 或数据库 API Key（SHA-256 校验）
from starlette.responses import JSONResponse

class MCPAuthMiddleware:
    def __init__(self, app):
        self.app = app
        import os
        self.env_key = os.environ.get("MCP_API_KEY", "")

    @property
    def lifespan(self):
        return getattr(self.app, 'lifespan', None)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        auth = headers.get(b"authorization", b"").decode()
        bearer_token = auth[7:] if auth.startswith("Bearer ") else ""

        if not bearer_token:
            if self.env_key:
                response = JSONResponse({"error": "Unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return

        if self.env_key and bearer_token == self.env_key:
            await self.app(scope, receive, send)
            return

        import hashlib
        key_hash = hashlib.sha256(bearer_token.encode()).hexdigest()
        from app.deps.db import async_session_factory
        from sqlalchemy import select, update
        from app.models.mcp_api_key import McpApiKey
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    select(McpApiKey).where(McpApiKey.key_hash == key_hash, McpApiKey.is_active == True)
                )
                api_key = result.scalar_one_or_none()
                if api_key:
                    from datetime import datetime, timezone
                    api_key.last_used_at = datetime.now(timezone.utc)
                    await session.commit()
                    await self.app(scope, receive, send)
                    return
        except Exception:
            pass

        response = JSONResponse({"error": "Unauthorized"}, status_code=401)
        await response(scope, receive, send)

_mcp_app = MCPAuthMiddleware(_mcp_raw)

from contextlib import asynccontextmanager
import logging

_startup_logger = logging.getLogger("mock_startup")

@asynccontextmanager
async def lifespan(app):
    import asyncio
    import os
    from app.services.scenario_gen import pipeline as scenario_gen_pipeline
    async with _mcp_app.lifespan(app):
            # mock 恢复放后台执行，不阻塞服务启动（恢复慢时曾导致启动卡 10s+）
            restore_task = asyncio.create_task(_restore_mock_services())
            # 功能场景测试模块：孤儿任务扫描 + 看门狗（NFR17）
            maintenance_task = scenario_gen_pipeline.start_background_maintenance()
            # MCP 独立端口（给 Claude Code 连接，避免与主服务 8756 端口混用）
            mcp_server = _start_standalone_mcp_server()
            yield
            if mcp_server:
                mcp_server.should_exit = True
            restore_task.cancel()
            maintenance_task.cancel()


def _start_standalone_mcp_server():
    """在独立大端口暴露 MCP（复用主 app 已初始化的 session manager，no-op lifespan）。"""
    import os
    import asyncio
    import uvicorn
    from contextlib import asynccontextmanager as _acm
    from starlette.applications import Starlette
    from starlette.routing import Mount

    mcp_port = int(os.environ.get("MCP_PORT", "18800"))

    @_acm
    async def _noop_lifespan(_app):
        # session manager 已由主 app 的 lifespan 初始化，这里不重复初始化
        yield

    standalone = Starlette(
        routes=[Mount("/mcp", app=_mcp_app)],
        lifespan=_noop_lifespan,
    )
    config = uvicorn.Config(standalone, host="0.0.0.0", port=mcp_port, log_level="warning")
    server = uvicorn.Server(config)
    try:
        from app.services._mock_server_util import guarded_serve
        asyncio.create_task(guarded_serve(server, "Standalone MCP"))
        _startup_logger.info("MCP 独立服务已启动: http://0.0.0.0:%d/mcp/", mcp_port)
    except Exception as e:
        _startup_logger.warning("MCP 独立服务启动失败: %s", e)
        return None
    return server


async def _restore_mock_services():
    from app.services.llm_mock_manager import mock_server
    from app.services.api_mock_manager import api_mock_server
    from app.services.mcp_mock_manager import mcp_mock_server
    from app.services.ws_mock_manager import ws_mock_server
    from app.services.tcp_mock_manager import tcp_mock_server
    from app.services.udp_mock_manager import udp_mock_server
    from app.services.grpc_mock_manager import grpc_mock_server
    try:
        if mock_server._load_state():
            _startup_logger.info("自动恢复 LLM Mock 服务 (端口 %d)", mock_server.port)
            await mock_server.start()
    except Exception as e:
        _startup_logger.warning("LLM Mock 自动恢复失败: %s", e)
    try:
        if api_mock_server._load_state():
            _startup_logger.info("自动恢复 API Mock 服务 (端口 %d)", api_mock_server.port)
            await api_mock_server.start()
    except Exception as e:
        _startup_logger.warning("API Mock 自动恢复失败: %s", e)
    try:
        if mcp_mock_server._load_state():
            _startup_logger.info("自动恢复 MCP Mock 服务 (端口 %d)", mcp_mock_server.port)
            await mcp_mock_server.start()
    except Exception as e:
        _startup_logger.warning("MCP Mock 自动恢复失败: %s", e)
    try:
        if ws_mock_server._load_state():
            _startup_logger.info("自动恢复 WebSocket Mock 服务 (端口 %d)", ws_mock_server.port)
            await ws_mock_server.start()
    except Exception as e:
        _startup_logger.warning("WebSocket Mock 自动恢复失败: %s", e)
    try:
        if tcp_mock_server._load_state():
            _startup_logger.info("自动恢复 TCP Mock 服务 (端口 %d)", tcp_mock_server.port)
            await tcp_mock_server.start()
    except Exception as e:
        _startup_logger.warning("TCP Mock 自动恢复失败: %s", e)
    try:
        if udp_mock_server._load_state():
            _startup_logger.info("自动恢复 UDP Mock 服务 (端口 %d)", udp_mock_server.port)
            await udp_mock_server.start()
    except Exception as e:
        _startup_logger.warning("UDP Mock 自动恢复失败: %s", e)
    try:
        if grpc_mock_server._load_state():
            _startup_logger.info("自动恢复 gRPC Mock 服务 (端口 %d)", grpc_mock_server.port)
            await grpc_mock_server.start()
    except Exception as e:
        _startup_logger.warning("gRPC Mock 自动恢复失败: %s", e)
    try:
        from app.services.oauth2_mock_manager import oauth2_mock_server
        if oauth2_mock_server._prev_running():
            _startup_logger.info("自动恢复 OAuth2 Mock 服务 (端口 %d)", oauth2_mock_server.port)
            await oauth2_mock_server.start()
    except Exception as e:
        _startup_logger.warning("OAuth2 Mock 自动恢复失败: %s", e)

app = FastAPI(
    title="测试管理平台 API",
    default_response_class=CamelCaseResponse,
    lifespan=lifespan,
)

# --- 中间件（注册顺序：后注册先执行） ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TraceIdMiddleware)

# --- 异常处理器 ---
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

# --- 路由 ---
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(projects_router)
app.include_router(branches_router)
app.include_router(cases_router)
app.include_router(folders_router)
app.include_router(variables_router)
app.include_router(plans_router)
app.include_router(reports_router)
app.include_router(tasks_router)
app.include_router(logs_router)
app.include_router(scripts_router)
app.include_router(scenario_variables_router)
app.include_router(automation_resources_router)
app.include_router(i18n_messages_router)
app.include_router(scripts_export_router)
app.include_router(testforge_router)
app.include_router(debug_router)
app.include_router(api_collections_router)
app.include_router(llm_mock_router)
app.include_router(api_mock_router)
app.include_router(ai_router)
app.include_router(ai_config_router)
app.include_router(ai_provider_router)
app.include_router(project_ai_config_router)
app.include_router(ai_capabilities_router)
app.include_router(skill_run_router)
app.include_router(mcp_mock_router)
app.include_router(protocol_mock_router)
app.include_router(oauth2_mock_router)
app.include_router(load_test_router)
app.include_router(exploratory_router)
app.include_router(documents_router)
app.include_router(api_test_router)
app.include_router(scenario_gen_router)
app.include_router(case_file_router)
app.include_router(skill_manage_router)
app.include_router(knowledge_router)
app.include_router(screenshots_router)
app.include_router(toolbox_router)
app.include_router(http_client_router)
app.include_router(mcp_keys_router)

# --- MCP Server ---
# 只在独立端口（MCP_PORT，默认 18800）暴露，见 _start_standalone_mcp_server()。
# 曾经这里还 mount 过一份到主端口做向后兼容，但两处挂的是同一个 _mcp_app、
# 纯冗余，且导致产品内地址口径不一（页面写 18800、别处写 8756）。统一到 18800。
