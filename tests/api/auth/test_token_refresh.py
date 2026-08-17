"""
test_token_refresh — refresh token 轮换、重放检测、并发重叠窗口
Test ID: 1.2-API-007
Priority: P1

这个文件原来测的是 `X-New-Token` 滑动续期响应头：快过期时中间件在响应头里塞一个
新 token。那套机制已经在 a1457a4「登录改为短期access(30m)+长期refresh(7d)双令牌」
里整个换掉了，后端搜不到 X-New-Token，前端也不读它。

留下来的两条测试于是变成：一条断言「响应头里没有 X-New-Token」—— 功能删掉之后
恒为真，一直在假通过；另一条断言它存在 —— 稳定红。而真正的续期入口
POST /api/auth/refresh 一条测试都没有，文件名却叫 test_token_refresh，
看起来像覆盖了。

所以整个重写，按实现（auth_service.rotate_refresh_token）实际的契约来测。
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.refresh_token import RefreshToken
from tests.conftest import create_test_user, make_auth_headers


async def _login(client, username, password="Passw0rd!"):
    """走真实登录接口拿 token 对。"""
    r = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    return d["token"], d["refreshToken"]


class TestRefreshTokenRotation:
    """POST /api/auth/refresh"""

    @pytest.mark.asyncio
    async def test_valid_refresh_rotates_both_tokens(self, client, db_session):
        # Given: 登录拿到一对令牌
        await create_test_user(db_session, username="rt_ok", role="admin", password="Passw0rd!")
        old_access, old_refresh = await _login(client, "rt_ok")

        # When: 用 refresh token 换新的
        r = await client.post("/api/auth/refresh", json={"refreshToken": old_refresh})

        # Then: 200，refresh token 必须换掉 —— 这是轮换不是续期，也是这个机制的安全前提
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["refreshToken"] != old_refresh

        # 不断言 access token 变了：JWT 的 iat/exp 是秒级，同一秒内签发、claims 又一样，
        # HS256 是确定性签名，必然产出同一串。实测就是相等。这无害（exp 也一样，
        # 等于什么都没发生），但断言它不等会让这条测试变成随机红。
        assert d["token"], "至少得给一个 access token"
        _ = old_access

        # Then: 新 access token 真的能用
        me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {d['token']}"})
        assert me.status_code == 200
        assert me.json()["data"]["username"] == "rt_ok"

    @pytest.mark.asyncio
    async def test_unknown_refresh_token_rejected(self, client, db_session):
        # Given / When: 一个库里不存在的 refresh token
        r = await client.post("/api/auth/refresh", json={"refreshToken": "definitely-not-a-real-token"})

        # Then: 401，且不泄露是「不存在」还是「过期」
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"

    @pytest.mark.asyncio
    async def test_access_token_cannot_be_used_as_refresh_token(self, client, db_session):
        """access token 不能拿来当 refresh 用 —— 两者不是一种东西。

        refresh token 是随机串、库里存哈希；access 是 JWT。混用应该直接不认。
        """
        # Given: 登录拿到 access token
        await create_test_user(db_session, username="rt_mix", role="admin", password="Passw0rd!")
        access, _ = await _login(client, "rt_mix")

        # When: 把 access token 当 refresh token 递过去
        r = await client.post("/api/auth/refresh", json={"refreshToken": access})

        # Then: 401
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"

    @pytest.mark.asyncio
    async def test_reuse_inside_grace_window_is_allowed(self, client, db_session):
        """轮换重叠窗口内的重试要放行。

        弱网、多标签页并发刷新、服务重启都会让新 token 送不到客户端，客户端于是
        拿旧的重试。把这种重试当盗用处理的实际后果是：正常开两个标签页就会把该
        账号全端踢下线。所以这条盯的是「别把重试当攻击」。
        """
        # Given: 刚轮换过一次（旧 token 处于 grace 窗口内）
        assert settings.refresh_token_grace_seconds > 0, "grace 关掉了，这条测试的前提不成立"
        await create_test_user(db_session, username="rt_grace", role="admin", password="Passw0rd!")
        _, first_refresh = await _login(client, "rt_grace")
        r1 = await client.post("/api/auth/refresh", json={"refreshToken": first_refresh})
        assert r1.status_code == 200

        # When: 立刻用已经被轮换掉的旧 token 再试一次
        r2 = await client.post("/api/auth/refresh", json={"refreshToken": first_refresh})

        # Then: 照常签发，不算重放
        assert r2.status_code == 200, r2.text
        assert r2.json()["data"]["token"]

    @pytest.mark.asyncio
    async def test_reuse_outside_grace_window_revokes_whole_family(self, client, db_session):
        """窗口外重放按盗用处理：拒绝，并吊销该用户全部活跃 token。

        这是 OAuth 2.0 Security BCP 要求的行为 —— 光拒绝这一次不够，
        因为攻击者手里那份可能还是有效的。
        """
        # Given: 轮换过一次，然后把旧 token 的吊销时间往前挪到 grace 窗口之外
        await create_test_user(db_session, username="rt_reuse", role="admin", password="Passw0rd!")
        _, first_refresh = await _login(client, "rt_reuse")
        r1 = await client.post("/api/auth/refresh", json={"refreshToken": first_refresh})
        assert r1.status_code == 200
        fresh_refresh = r1.json()["data"]["refreshToken"]

        from app.core.security import hash_refresh_token
        old_rec = await db_session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(first_refresh))
        )
        assert old_rec is not None and old_rec.revoked_at is not None
        old_rec.revoked_at = datetime.now(timezone.utc) - timedelta(
            seconds=settings.refresh_token_grace_seconds + 60
        )
        await db_session.flush()

        # When: 拿这个早已被轮换掉的 token 重放
        r2 = await client.post("/api/auth/refresh", json={"refreshToken": first_refresh})

        # Then: 401 且明确是重放
        assert r2.status_code == 401, r2.text
        assert r2.json()["error"]["code"] == "REFRESH_TOKEN_REUSED"

        # Then: 连坐吊销 —— 刚才那个本来有效的 token 也不能再用了
        r3 = await client.post("/api/auth/refresh", json={"refreshToken": fresh_refresh})
        assert r3.status_code == 401, "重放检测必须吊销整个 token family，只拒当次等于没防住"
