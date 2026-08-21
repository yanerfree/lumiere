"""
Root conftest.py — 共享 fixtures for all test levels.

双模式运行:
  - 平台模式: 环境变量 BASE_URL 存在时，走真实 HTTP 请求到目标环境，不依赖 app 包和本地数据库
  - 本地模式: 无 BASE_URL，走 ASGI 进程内测试 (httpx ASGITransport)

规则: 平台下发的环境变量优先，没有则用脚本自己的默认值。
"""
import os
from collections.abc import AsyncGenerator

import pytest
from httpx import AsyncClient

# ── 环境变量: 平台下发 > 脚本默认 ──

PLATFORM_BASE_URL = os.environ.get("BASE_URL", "").strip() or None

TEST_PASSWORD = os.environ.get("TEST_PASSWORD", "Test@123456")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


# ── Fixtures ──

@pytest.fixture
async def db_session():
    if PLATFORM_BASE_URL:
        yield None
        return
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
    import app.main  # noqa: F401 — 先导入完整应用，确保全部模型注册进 Base.metadata（否则 create_all 建表不全）
    from app.config import settings
    from app.models.user import Base

    db_url = os.environ.get("DATABASE_URL", "").strip() or settings.database_url.replace("/testbench", "/testbench_test")
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def client(db_session) -> AsyncGenerator[AsyncClient, None]:
    if PLATFORM_BASE_URL:
        async with AsyncClient(base_url=PLATFORM_BASE_URL, timeout=30) as ac:
            yield ac
    else:
        from httpx import ASGITransport
        from app.main import app
        from app.deps.db import get_db

        async def _override_session():
            yield db_session
        app.dependency_overrides[get_db] = _override_session
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
        app.dependency_overrides.clear()


# ── 认证 Helpers ──

async def login_as(client: AsyncClient, username: str = None, password: str = None) -> dict:
    """通过登录接口获取 token，返回 headers dict。两种模式都可用。

    Usage:
        headers = await login_as(client)                          # 用默认管理员
        headers = await login_as(client, "tester", "pass123")     # 指定用户
    """
    username = username or ADMIN_USERNAME
    password = password or ADMIN_PASSWORD
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"login_as({username}) failed: {resp.status_code} {resp.text}"
    token = resp.json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


async def create_user_via_api(client: AsyncClient, admin_headers: dict,
                              username: str, password: str = None, role: str = "user") -> dict:
    """通过 API 创建用户，返回用户数据 dict。两种模式都可用。"""
    password = password or TEST_PASSWORD
    resp = await client.post("/api/users", headers=admin_headers, json={
        "username": username, "password": password, "role": role,
    })
    assert resp.status_code == 201, f"create_user_via_api({username}) failed: {resp.status_code} {resp.text}"
    return resp.json()["data"]


# ── 本地模式 Helpers（仅 ASGI 模式下使用，直接操作 DB）──

async def create_test_user(
    db_session,
    username: str = "testuser",
    password: str = None,
    role: str = "user",
    is_active: bool = True,
):
    """直接写 DB 创建用户。仅本地模式使用，平台模式自动跳过。"""
    if db_session is None:
        pytest.skip("此用例依赖本地数据库，平台模式下跳过（请用 login_as 替代）")
    from app.core.security import hash_password
    from app.models.user import User
    password = password or TEST_PASSWORD
    user = User(
        username=username,
        password=hash_password(password),
        role=role,
        is_active=is_active,
    )
    db_session.add(user)
    await db_session.flush()
    return user


def make_auth_headers(user) -> tuple[dict, str]:
    """本地签 JWT token。仅本地模式使用，平台模式请用 login_as。"""
    if user is None:
        pytest.skip("此用例依赖本地 JWT 签发，平台模式下跳过（请用 login_as 替代）")
    from app.core.security import create_access_token
    token = create_access_token(user.id, user.role)
    return {"Authorization": f"Bearer {token}"}, token


async def create_test_project(client, headers, name: str) -> str:
    """建一个项目，返回它的 id。

    环境和全局变量 2026-08-21 起是项目级的（迁移 zzo0envproj / zzp0gvarproj），
    所以这一族接口全部挂在 /api/projects/{project_id}/ 下面，测试得先有个项目。

    **注意新项目会自带默认数据**：4 个环境（development/testing/staging/production）
    和 5 个全局变量（API_TIMEOUT/BASE_WAIT/LOG_LEVEL/RETRY_COUNT/TEST_LANGUAGE）。
    所以测试自己造的 key/环境名别跟这些撞，否则第一次创建就 409。
    见 app/services/project_defaults.py。
    """
    r = await client.post("/api/projects", headers=headers, json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]
