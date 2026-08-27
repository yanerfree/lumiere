from fastapi import Depends, FastAPI
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
from app.api.case_review import router as case_review_router
from app.api.variables import router as variables_router
from app.api.variables import env_router, gvar_router
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
from app.api.proxy_probe import router as proxy_probe_router
from app.api.ai import router as ai_router, config_router as ai_config_router
from app.api.ai_config import router as ai_provider_router, project_router as project_ai_config_router
from app.api.ai_capabilities import router as ai_capabilities_router
from app.api.skill_run import router as skill_run_router
from app.api.mcp_mock import router as mcp_mock_router
from app.api.protocol_mock import router as protocol_mock_router
from app.api.oauth2_mock import router as oauth2_mock_router
from app.api.load_test import router as load_test_router
from app.api.case_file import router as case_file_router
from app.api.api_test import router as api_test_router
from app.api.skill_manage import router as skill_manage_router
from app.api.project_skills import router as project_skills_router
from app.api.knowledge import router as knowledge_router
from app.api.screenshots import router as screenshots_router
from app.api.toolbox import router as toolbox_router
from app.api.http_client import router as http_client_router
from app.api.scenario_gen import router as scenario_gen_router
from app.api.mcp_keys import router as mcp_keys_router
from app.api.mcp_keys import project_scope_router as mcp_scope_router
from app.api.qa_catalog import router as qa_catalog_router
from app.api.system_services import router as system_services_router
from app.api.me import router as me_router
from app.api.assistant import router as assistant_router
from app.core.middleware import CamelCaseResponse, TraceIdMiddleware
from app.deps.auth import get_current_user, require_project_role
from app.deps.scope import verify_case_access, verify_path_scope

# --- MCP Server ---
from app.mcp import mcp
_mcp_raw = mcp.http_app(path="/")


# MCP 认证中间件 — 支持环境变量 MCP_API_KEY 或数据库 API Key（SHA-256 校验）
from starlette.responses import JSONResponse

class MCPAuthMiddleware:
    """MCP 的唯一入口认证。**没有匿名通道。**

    此前这里有一条「没带 bearer 且 MCP_API_KEY 未设 → 直接放行」的分支，
    而 MCP_API_KEY 从来没设过 —— 于是那个口子一直全开。实测（2026-08-21）：
    不带任何凭据就能 initialize，然后 lum_list_projects 列出全部 6 个项目，
    再 lum_list_branches 往下读到任意项目的分支和用例。平台监听 0.0.0.0，
    同局域网里谁都能当它的客户端。

    **修法是删掉那条分支，不是"记得去 .env 里设一个 MCP_API_KEY"** ——
    靠环境变量兜底的开关，.env 一丢就静默恢复成全开，而且页面上看不出来。
    env_key 仍然支持（CI / 一次性脚本用），但它现在是**可选的额外通道**，
    不再是"设了才开始检查"的总开关。

    注意顶栏「服务 N/17」里的 MCP 探活走的是 `_tcp_alive`（只连端口，不发 HTTP），
    所以堵掉匿名 HTTP 不会让它变成 DOWN。
    """

    def __init__(self, app):
        self.app = app
        import os
        self.env_key = os.environ.get("MCP_API_KEY", "")

    @staticmethod
    async def _deny(scope, receive, send):
        response = JSONResponse({"error": "Unauthorized"}, status_code=401)
        await response(scope, receive, send)

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
            # 不带凭据一律 401 —— 这里以前会放行（见类文档）
            await self._deny(scope, receive, send)
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
            # 查库失败也不放行 —— 认证这条路必须 fail closed
            pass

        await self._deny(scope, receive, send)

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
            # 执行是进程内的后台任务：进程被 kill 时一行 except 都不会跑，
            # 计划会永远停在 executing 再也触发不了。这个看门狗负责收拾现场。
            from app.services import stuck_recovery
            stuck_task = await stuck_recovery.start_watchdog()
            # 审核队列同理：进程被 kill 时正在跑的那批永远停在 running，
            # 页面进度条转到天荒地老。退回排队 + 把 worker 拉起来接着跑。
            try:
                from app.services.review import queue as review_queue
                await review_queue.recover_orphans()
            except Exception as e:  # noqa: BLE001
                _startup_logger.warning("审核队列重启收尾失败: %s", e)
            # MCP 独立端口（给 Claude Code 连接，避免与主服务 8756 端口混用）
            mcp_server = _start_standalone_mcp_server()
            yield
            if mcp_server:
                mcp_server.should_exit = True
            restore_task.cancel()
            maintenance_task.cancel()
            stuck_task.cancel()


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
        from app.services.proxy_probe_manager import proxy_probe
        if proxy_probe._load_state():
            _startup_logger.info("自动恢复代理观测监听器 (端口 %d)", proxy_probe.port)
            await proxy_probe.start()
    except Exception as e:
        _startup_logger.warning("代理观测监听器自动恢复失败: %s", e)
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
#
# ⚠ Mock / 测试工具这一族的鉴权是**挂在这里**的，不在各自的函数签名里。
#
# 起因：不带 Authorization 把全部端点打了一遍，发现 74 个未认证可达，全集中在
# Mock 和测试工具这一族 —— 平台主体（项目/用例/计划/用户）都老老实实 401，
# 只有这一族当初漏了。实测后果按严重程度排：
#   · POST /api/toolbox/http-request 和 /api/http-client/send —— **SSRF**：
#     不用登录就能让服务器去请求任意地址，包括内网。平台监听 0.0.0.0:8756，
#     同一个局域网里谁都能用它当跳板。
#   · PUT /api/proxy-probe/config、POST /api/*-mock/... —— 改代理配置、
#     增删 Mock 路由，等于替别人改测试环境的行为。
#   · DELETE /api/*/logs —— 清空各类 Mock 请求日志。
#
# 为什么统一加在 include_router 而不是逐个函数：这一族有 74 个端点分散在 13 个
# 文件里，逐个加必漏，而且下次新增端点又会漏。挂在挂载点上，**新加的路由自动就有**。
# 对应的封样在 tests/test_endpoint_auth.py。
_AUTHED = [Depends(get_current_user)]

# 分支/用例这条链上的路由器：除了"你是不是这个项目的成员"，还得验路径里的
# branch_id / case_id 确实属于这个项目、这个分支。见 app/deps/scope.py 的说明
# （实测越权读到过、也改掉过别的项目的用例）。
_SCOPED = [Depends(verify_path_scope)]

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(projects_router)
app.include_router(branches_router, dependencies=_SCOPED)
app.include_router(cases_router, dependencies=_SCOPED)
app.include_router(case_review_router, dependencies=_SCOPED)
app.include_router(folders_router, dependencies=_SCOPED)
app.include_router(variables_router)
# 环境是项目级的：既要验成员身份（各路由自己的 require_project_role），
# 也要验路径里的 env_id 真属于这个项目（_SCOPED）。少了后者，
# 路径写自己的项目、env_id 填别人的就能读到别人的凭证。
app.include_router(env_router, dependencies=_SCOPED)
app.include_router(gvar_router, dependencies=_SCOPED)
app.include_router(plans_router)
app.include_router(reports_router)
app.include_router(tasks_router)
app.include_router(logs_router)
app.include_router(scripts_router, dependencies=_SCOPED)
app.include_router(scenario_variables_router, dependencies=_SCOPED)
app.include_router(automation_resources_router)
app.include_router(i18n_messages_router)
app.include_router(scripts_export_router, dependencies=_SCOPED)
app.include_router(testforge_router, dependencies=_SCOPED)
app.include_router(debug_router, dependencies=_AUTHED)
app.include_router(api_collections_router)
app.include_router(llm_mock_router, dependencies=_AUTHED)
app.include_router(api_mock_router, dependencies=_AUTHED)
app.include_router(proxy_probe_router, dependencies=_AUTHED)
app.include_router(ai_router, dependencies=_SCOPED)
app.include_router(ai_config_router, dependencies=_AUTHED)
app.include_router(ai_provider_router)
app.include_router(project_ai_config_router)
app.include_router(ai_capabilities_router)
app.include_router(skill_run_router, dependencies=_SCOPED)
app.include_router(mcp_mock_router, dependencies=_AUTHED)
app.include_router(protocol_mock_router, dependencies=_AUTHED)
app.include_router(oauth2_mock_router, dependencies=_AUTHED)
app.include_router(load_test_router, dependencies=_AUTHED)
app.include_router(api_test_router, dependencies=_SCOPED)
app.include_router(scenario_gen_router, dependencies=_SCOPED)
app.include_router(case_file_router, dependencies=[Depends(verify_case_access)])
app.include_router(skill_manage_router, dependencies=_AUTHED)
app.include_router(project_skills_router)
app.include_router(knowledge_router)
app.include_router(screenshots_router)
app.include_router(toolbox_router, dependencies=_AUTHED)
app.include_router(http_client_router, dependencies=_AUTHED)
app.include_router(mcp_keys_router)
# 项目级 MCP 工具范围（角色校验挂在各 handler 上，不在这里加 dependencies）
app.include_router(mcp_scope_router)
# QA 场景清单：只读别人的验收仓，成员校验挂在各 handler 上
app.include_router(qa_catalog_router)
app.include_router(system_services_router, dependencies=_AUTHED)
app.include_router(me_router)
# AI 助手：能力面按登录用户权限过滤，各端点自带 get_current_user + 执行前复检
app.include_router(assistant_router)

# --- MCP Server ---
# 只在独立端口（MCP_PORT，默认 18800）暴露，见 _start_standalone_mcp_server()。
# 曾经这里还 mount 过一份到主端口做向后兼容，但两处挂的是同一个 _mcp_app、
# 纯冗余，且导致产品内地址口径不一（页面写 18800、别处写 8756）。统一到 18800。
