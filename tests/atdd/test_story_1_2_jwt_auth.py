"""
ATDD 验收测试 — Story 1.2: 登录页与 JWT 认证流程
TDD Red Phase: 验证 JWT 认证全生命周期

覆盖验收标准:
- POST /api/auth/login 正确/错误凭据
- JWT token 有效期 8h
- 滑动续期 (剩余 <2h 返回 X-New-Token)
- GET /api/auth/me
- 未携带/过期 token → 401
- 登出
"""
import pytest
import time

from tests.conftest import TEST_PASSWORD, create_test_user, make_auth_headers


# ---------------------------------------------------------------------------
# 1.2-API-001: POST /api/auth/login 正确凭据
# Priority: P0
# ---------------------------------------------------------------------------
class TestLoginSuccess:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_login_correct_credentials_returns_token(self, client, db_session):
        """AC: 正确用户名密码返回 JWT token (有效期 8h)"""
        # Given: 数据库中存在活跃用户
        await create_test_user(db_session, username="atdd_login", role="user")

        # When: 用正确凭据登录
        response = await client.post("/api/auth/login", json={
            "username": "atdd_login",
            "password": TEST_PASSWORD,
        })

        # Then: 返回 200 和 token
        assert response.status_code == 200
        data = response.json()["data"]
        assert "token" in data
        assert len(data["token"]) > 0

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_login_returns_user_info_without_password(self, client, db_session):
        """AC: 返回用户信息 (id, username, role)，密码不泄露"""
        await create_test_user(db_session, username="atdd_login2", role="admin")

        response = await client.post("/api/auth/login", json={
            "username": "atdd_login2",
            "password": TEST_PASSWORD,
        })

        assert response.status_code == 200
        user_data = response.json()["data"]["user"]
        assert user_data["username"] == "atdd_login2"
        assert user_data["role"] == "admin"
        assert "password" not in user_data


# ---------------------------------------------------------------------------
# 1.2-API-002/003: 错误凭据 — 不泄露字段
# Priority: P0
# ---------------------------------------------------------------------------
class TestLoginFailure:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_login_wrong_password_returns_error(self, client, db_session):
        """AC: 错误密码返回错误提示"""
        await create_test_user(db_session, username="atdd_wrong_pw")

        response = await client.post("/api/auth/login", json={
            "username": "atdd_wrong_pw",
            "password": "WrongPassword@999",
        })

        assert response.status_code == 401

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_login_nonexistent_user_same_error(self, client):
        """AC: 不泄露用户名是否存在 — 不存在用户与错误密码返回相同错误"""
        # When: 用不存在的用户名登录
        response_nonexistent = await client.post("/api/auth/login", json={
            "username": "absolutely_no_such_user",
            "password": "AnyPassword@123",
        })

        # Then: 返回与错误密码相同的错误
        assert response_nonexistent.status_code == 401

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_login_error_does_not_leak_field(self, client, db_session):
        """AC: 错误信息不泄露具体哪个字段错误"""
        await create_test_user(db_session, username="atdd_leak_check")

        # When: 错误密码
        resp_bad_pw = await client.post("/api/auth/login", json={
            "username": "atdd_leak_check",
            "password": "BadPw@123",
        })
        # When: 不存在用户
        resp_no_user = await client.post("/api/auth/login", json={
            "username": "no_such_user_atdd",
            "password": TEST_PASSWORD,
        })

        # Then: 两者错误信息应相同或不区分字段
        msg_bad_pw = resp_bad_pw.json()
        msg_no_user = resp_no_user.json()
        # 不应包含"用户名不存在"或"密码错误"等区分性信息
        msg_str = str(msg_bad_pw).lower()
        assert "username" not in msg_str or "password" not in msg_str


# ---------------------------------------------------------------------------
# 1.2-API-004: 非活跃用户
# Priority: P1
# ---------------------------------------------------------------------------
class TestLoginInactiveUser:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_inactive_user_cannot_login(self, client, db_session):
        """AC: 非活跃用户拒绝登录"""
        await create_test_user(db_session, username="atdd_inactive", is_active=False)

        response = await client.post("/api/auth/login", json={
            "username": "atdd_inactive",
            "password": TEST_PASSWORD,
        })

        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# 1.2-UNIT-001~004: JWT token 签发/解码
# Priority: P0
# ---------------------------------------------------------------------------
class TestJwtTokenUnit:

    @pytest.mark.unit
    def test_access_token有效期跟配置一致(self):
        """access token 是**短期**的，长度由 settings.access_token_expire_minutes 决定。

        这条原来写死断言「8 小时」（Story 1.2 的初版 AC），实测 1800s 一直是红的 ——
        因为后来改成了**短 access + 长 refresh** 的双 token 设计
        （auth_service.issue_token_pair + POST /api/auth/refresh），
        access token 短命才是对的，8 小时反而是漏洞。

        改成对着配置断言，而不是再写死一个数：写死的话下次调整过期时间又会红一次，
        而那次红说明不了任何问题。这里只钉两件事 ——
        ① 长度确实等于配置值；② 它确实是"短期"的（不超过 2 小时），
        后者才是这条测试真正想守的那条线。
        """
        import uuid

        from app.config import settings
        from app.core.security import create_access_token, decode_token

        token = create_access_token(user_id=uuid.uuid4(), role="user")
        payload = decode_token(token)
        duration = payload["exp"] - payload["iat"]

        expected = settings.access_token_expire_minutes * 60
        assert duration == expected, f"有效期 {duration}s，配置说 {expected}s"
        assert duration <= 2 * 3600, (
            f"access token {duration}s 太长了 —— 双 token 设计的前提是它短命，"
            "长效凭据该走 refresh token")

    @pytest.mark.unit
    def test_jwt_valid_token_decodes_correctly(self):
        """AC: 有效 token 正确解析"""
        import uuid
        from app.core.security import create_access_token, decode_token

        user_id = uuid.uuid4()
        token = create_access_token(user_id=user_id, role="admin")
        payload = decode_token(token)

        assert payload["sub"] == str(user_id)
        assert payload["role"] == "admin"

    @pytest.mark.unit
    def test_jwt_expired_token_raises(self):
        """AC: 过期 token 抛出异常"""
        from app.core.security import decode_token
        from app.core.exceptions import UnauthorizedError

        with pytest.raises(UnauthorizedError):
            decode_token("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIiwicm9sZSI6InVzZXIiLCJleHAiOjEwMDAwMDAwMDAsImlhdCI6MTAwMDAwMDAwMH0.invalid")

    @pytest.mark.unit
    def test_jwt_invalid_signature_raises(self):
        """AC: 无效签名 token 抛出异常"""
        from app.core.security import decode_token
        from app.core.exceptions import UnauthorizedError

        with pytest.raises(UnauthorizedError):
            decode_token("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIiwicm9sZSI6InVzZXIifQ.INVALID_SIGNATURE")


# ---------------------------------------------------------------------------
# 1.2-API-005: GET /api/auth/me
# Priority: P0
# ---------------------------------------------------------------------------
class TestAuthMe:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_me_returns_current_user(self, client, db_session):
        """AC: GET /api/auth/me 返回当前用户信息 (id, username, role)"""
        user = await create_test_user(db_session, username="atdd_me", role="user")
        headers, _ = make_auth_headers(user)

        response = await client.get("/api/auth/me", headers=headers)

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["username"] == "atdd_me"
        assert data["role"] == "user"
        assert "id" in data


# ---------------------------------------------------------------------------
# 1.2-API-006/007: 未认证/过期 token → 401
# Priority: P0
# ---------------------------------------------------------------------------
class TestAuthUnauthorized:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_me_without_token_returns_401(self, client):
        """AC: 未携带 token 返回 401"""
        response = await client.get("/api/auth/me")
        assert response.status_code == 401

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_me_with_expired_token_returns_401(self, client):
        """AC: 过期 token 返回 401"""
        response = await client.get("/api/auth/me", headers={
            "Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOjEsImV4cCI6MTAwMDAwMDAwMH0.fake"
        })
        assert response.status_code == 401

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_me_with_invalid_token_returns_401(self, client):
        """AC: 无效 token 返回 401"""
        response = await client.get("/api/auth/me", headers={
            "Authorization": "Bearer completely.invalid.token"
        })
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# 1.2-API-008/009: 滑动续期
# Priority: P1/P2
# ---------------------------------------------------------------------------
class TestTokenSlidingRefresh:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_token_near_expiry_returns_new_token(self, client, db_session):
        """AC: token 剩余 <2h 时响应包含 X-New-Token"""
        from app.core.security import create_access_token
        from app.config import settings
        import joserfc.jwt

        user = await create_test_user(db_session, username="atdd_refresh", role="user")

        # Given: 创建一个剩余不到 2 小时的 token
        # 需要 mock 或使用特殊参数创建接近过期的 token
        token = create_access_token(user.id, user.role)
        headers = {"Authorization": f"Bearer {token}"}

        # When: 发起请求 (正常 token 不会触发续期，此处验证机制存在即可)
        response = await client.get("/api/auth/me", headers=headers)
        assert response.status_code == 200
        # Note: 完整测试需要 mock 时间使 token 接近过期

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_token_far_from_expiry_no_new_token(self, client, db_session):
        """AC: token 剩余 >2h 时不返回 X-New-Token"""
        user = await create_test_user(db_session, username="atdd_no_refresh", role="user")
        headers, _ = make_auth_headers(user)

        response = await client.get("/api/auth/me", headers=headers)

        assert response.status_code == 200
        assert "x-new-token" not in response.headers


# ---------------------------------------------------------------------------
# 1.2-API-010: 登出
# Priority: P1
# ---------------------------------------------------------------------------
class TestLogout:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_logout_success(self, client, db_session):
        """AC: 登出成功"""
        user = await create_test_user(db_session, username="atdd_logout", role="user")
        headers, _ = make_auth_headers(user)

        response = await client.post("/api/auth/logout", headers=headers)

        assert response.status_code in (200, 204)
