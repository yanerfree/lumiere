"""初始化种子数据 — 幂等执行（重复运行不会重复创建）"""
import asyncio

from sqlalchemy import select

from app.config import settings
from app.core.security import hash_password
from app.deps.db import async_session_factory
from app.models.user import User
from app.models.environment import Environment, EnvironmentVariable, GlobalVariable, NotificationChannel
# 环境和全局变量 2026-08-21 改成项目级之后，这两张表的 project_id 外键指向 projects。
# 不导入 Project，metadata 里就没有那张表，一 import 就 NoReferencedTableError ——
# **新库压根初始化不了**。（这个漏是项目化那次改动留下的，seed 没跟着更新。）
from app.models.project import Project, Branch, ProjectMember  # noqa: F401


# ---- 默认全局变量 ----
DEFAULT_GLOBAL_VARIABLES = [
    {"key": "API_TIMEOUT", "value": "30", "description": "API 请求超时（秒）"},
    {"key": "RETRY_COUNT", "value": "3", "description": "失败重试次数"},
    {"key": "LOG_LEVEL", "value": "INFO", "description": "日志级别"},
    {"key": "BASE_WAIT", "value": "1000", "description": "基础等待时间（毫秒）"},
]

# ---- 默认环境 ----
DEFAULT_ENVIRONMENTS = [
    {
        "name": "development",
        "description": "开发环境",
        "variables": [
            {"key": "BASE_URL", "value": "http://localhost:8000", "description": "开发服务地址"},
            {"key": "DB_HOST", "value": "localhost", "description": "数据库地址"},
            {"key": "DB_PORT", "value": "5432", "description": "数据库端口"},
            {"key": "DEBUG", "value": "true", "description": "调试模式"},
        ],
    },
    {
        "name": "testing",
        "description": "测试环境",
        "variables": [
            {"key": "BASE_URL", "value": "http://10.0.1.100:8000", "description": "测试服务地址"},
            {"key": "DB_HOST", "value": "10.0.1.100", "description": "数据库地址"},
            {"key": "DB_PORT", "value": "5432", "description": "数据库端口"},
            {"key": "DEBUG", "value": "false", "description": "调试模式"},
        ],
    },
    {
        "name": "staging",
        "description": "预发布环境",
        "variables": [
            {"key": "BASE_URL", "value": "https://staging.example.com", "description": "预发布地址"},
            {"key": "DB_HOST", "value": "staging-db.internal", "description": "数据库地址"},
            {"key": "DB_PORT", "value": "5432", "description": "数据库端口"},
            {"key": "DEBUG", "value": "false", "description": "调试模式"},
        ],
    },
    {
        "name": "production",
        "description": "生产环境",
        "variables": [
            {"key": "BASE_URL", "value": "https://api.example.com", "description": "生产地址"},
            {"key": "DB_HOST", "value": "prod-db.internal", "description": "数据库地址"},
            {"key": "DB_PORT", "value": "5432", "description": "数据库端口"},
            {"key": "DEBUG", "value": "false", "description": "调试模式"},
        ],
    },
]


async def seed_admin():
    """创建管理员用户"""
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.username == "admin"))
        if result.scalar_one_or_none():
            print("[admin] 已存在，跳过")
            return
        session.add(User(
            username="admin",
            password=hash_password(settings.admin_default_password),
            role="admin",
            is_active=True,
        ))
        await session.commit()
        print("[admin] 创建成功")


async def seed_global_variables():
    """创建默认全局变量"""
    async with async_session_factory() as session:
        for var in DEFAULT_GLOBAL_VARIABLES:
            result = await session.execute(select(GlobalVariable).where(GlobalVariable.key == var["key"]))
            if result.scalar_one_or_none():
                print(f"[全局变量] {var['key']} 已存在，跳过")
                continue
            session.add(GlobalVariable(**var))
            print(f"[全局变量] {var['key']} = {var['value']}")
        await session.commit()


async def seed_environments():
    """创建默认环境和环境变量"""
    async with async_session_factory() as session:
        for env_data in DEFAULT_ENVIRONMENTS:
            result = await session.execute(select(Environment).where(Environment.name == env_data["name"]))
            existing = result.scalar_one_or_none()
            if existing:
                print(f"[环境] {env_data['name']} 已存在，跳过")
                continue

            env = Environment(name=env_data["name"], description=env_data["description"])
            session.add(env)
            await session.flush()

            for i, var in enumerate(env_data["variables"]):
                session.add(EnvironmentVariable(
                    environment_id=env.id,
                    key=var["key"],
                    value=var["value"],
                    description=var.get("description"),
                    sort_order=i,
                ))
            print(f"[环境] {env_data['name']} + {len(env_data['variables'])} 个变量")

        await session.commit()


async def main():
    print("=" * 50)
    print("testBench 种子数据初始化")
    print("=" * 50)
    await seed_admin()
    # **环境和全局变量不在这里铺了。** 2026-08-21 它们从全平台改成项目级
    # （`project_id` NOT NULL），铺全局的那两个函数在新库上直接撞非空约束 ——
    # 新库压根初始化不了。新项目的默认环境/变量由 app/services/project_defaults.py
    # 在建项目时铺，要改默认值改那一个文件。
    #
    # 两个老函数留着不删：它们对**改造之前建的库**还有意义（补齐历史缺的），
    # 但不再进默认流程 —— 别让 seed 的失败挡住整个初始化。
    print("=" * 50)
    print("初始化完成（环境/全局变量已改项目级，建项目时自动铺，见 project_defaults.py）")


if __name__ == "__main__":
    asyncio.run(main())
