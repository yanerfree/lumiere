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


def _derive_test_db_url(app_url: str) -> str:
    """从应用库连接串推测试库连接串：库名后面缀 `_test`。

    **别把库名写死。** 这里原先是拿旧库名写死的 `.replace(旧库名, 旧库名+"_test")`，
    这种写法有个不出声的坑：一旦库改名（左边换了、右边忘了同步），或者 `.env`
    里的库名跟代码里这个字面量不一样，replace 一处都不命中 —— 返回的**就是应用
    库本身**，而这个 fixture 收尾要 `drop_all`。跑一遍测试把开发库的表全删了，
    而报出来是「测试通过」。改成按库名推导，缀不上去就没法退化成应用库。
    """
    base, sep, name = app_url.rpartition("/")
    if not sep or not name:
        raise ValueError(f"连接串里读不出库名：{app_url}")
    db, qmark, query = name.partition("?")
    if db.endswith("_test") or "_test_" in db:
        return app_url          # 本来就指着测试库（如 xxx_test_2），照用
    return f"{base}/{db}_test{qmark}{query}"


# ── Fixtures ──

@pytest.fixture
async def db_session():
    if PLATFORM_BASE_URL:
        yield None
        return
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
    from app.config import settings
    from app.models.user import Base

    db_url = os.environ.get("DATABASE_URL", "").strip() or _derive_test_db_url(settings.database_url)
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
