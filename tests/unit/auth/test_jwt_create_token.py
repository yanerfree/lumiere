"""
test_jwt_create_token — JWT token 签发功能验证
Test ID: 1.2-UNIT-001
Priority: P0
"""
import time
import uuid

from joserfc import jwt
from joserfc.jwk import OctKey

from app.config import settings
from app.core.security import create_access_token


class TestJwtCreateToken:
    """JWT token 签发：验证 payload 结构、有效期、签名正确性"""

    def test_token_contains_required_claims(self):
        # Given: 一个有效的用户 ID 和角色
        user_id = uuid.uuid4()
        role = "admin"

        # When: 签发 token
        token = create_access_token(user_id, role)

        # Then: 解码后包含 sub、role、iat、exp
        key = OctKey.import_key(settings.secret_key)
        tok = jwt.decode(token, key)
        claims = tok.claims
        assert claims["sub"] == str(user_id)
        assert claims["role"] == role
        assert "iat" in claims
        assert "exp" in claims

    def test_token_expiry_matches_config(self):
        # Given: access token 的有效期看 access_token_expire_minutes（默认 30 分钟）。
        # 原来断言的是 jwt_expire_hours * 3600（8 小时）—— 那个配置项在改成
        # 短 access + 长 refresh 双令牌（a1457a4）之后已标「已废弃」，
        # create_access_token 早就不读它了，所以这条一直红着。
        user_id = uuid.uuid4()
        now = int(time.time())

        # When: 签发 token
        token = create_access_token(user_id, "user")

        # Then: exp - iat 等于配置的分钟数
        key = OctKey.import_key(settings.secret_key)
        tok = jwt.decode(token, key)
        claims = tok.claims
        delta = claims["exp"] - claims["iat"]
        assert delta == settings.access_token_expire_minutes * 60

    def test_different_users_produce_different_tokens(self):
        # Given: 两个不同的用户
        uid1 = uuid.uuid4()
        uid2 = uuid.uuid4()

        # When: 分别签发 token
        t1 = create_access_token(uid1, "user")
        t2 = create_access_token(uid2, "user")

        # Then: token 不同
        assert t1 != t2
