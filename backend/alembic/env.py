import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.models.user import Base
from app.models.project import Project, Branch, ProjectMember  # noqa: F401 — 确保 metadata 发现
from app.models.case import CaseFolder, Case  # noqa: F401
from app.models.environment import GlobalVariable, Environment, EnvironmentVariable, NotificationChannel  # noqa: F401
from app.models.plan import Plan, PlanCase  # noqa: F401
from app.models.report import TestReport, TestReportScenario, TestReportStep  # noqa: F401
from app.models.audit_log import AuditLog  # noqa: F401
from app.models.api_collection import ApiNode  # noqa: F401
from app.models.llm_mock import MockRoute, MockRequestLog  # noqa: F401
from app.models.script import Script, ScriptRun  # noqa: F401
from app.models.scenario_gen import (  # noqa: F401 — 功能场景测试模块
    RequirementDoc,
    GenerationTask,
    RequirementPoint,
    ScenarioModel,
    GenerationItem,
    CaseGenEvent,
    TaskEvent,
)
from app.models.protocol_mock import (  # noqa: F401 — 协议 Mock
    WsMockEndpoint, WsMockLog,
    TcpMockHandler, TcpMockLog,
    UdpMockHandler, UdpMockLog,
    GrpcMockService, GrpcMockLog,
)
from app.models.load_test import (  # noqa: F401 — 压力测试
    LoadTestScenario, LoadTestStep, LoadTestRun,
)
from app.models.refresh_token import RefreshToken  # noqa: F401 — 登录 refresh token
from app.models.ai_provider_config import (  # noqa: F401 — AI 配置 + 能力档位 + 全局设置
    AIProviderConfig, ProjectAIConfig, AICapabilityBinding, AIGlobalSettings,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式"""
    url = settings.database_url
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """在线模式（async）"""
    connectable = create_async_engine(settings.database_url)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
