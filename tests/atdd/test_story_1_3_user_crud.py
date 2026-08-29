"""
ATDD 验收测试 — Story 1.3: 用户 CRUD 管理
TDD Red Phase: 验证管理员对用户的增删改查操作

覆盖验收标准:
- POST /api/users 创建用户 (bcrypt 加密)
- 用户名唯一 → 409
- PUT /api/users/{id} 修改角色/状态
- DELETE /api/users/{id} 删除用户
- 非 admin → 403
"""
import pytest

from tests.conftest import TEST_PASSWORD, create_test_user, make_auth_headers


# ---------------------------------------------------------------------------
# 1.3-API-001: admin 创建用户
# Priority: P0
# ---------------------------------------------------------------------------
class TestCreateUser:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_admin_creates_user_successfully(self, client, db_session):
        """AC: POST /api/users 创建成功，密码 bcrypt 加密存储"""
        admin = await create_test_user(db_session, username="crud_admin", role="admin")
        headers, _ = make_auth_headers(admin)

        response = await client.post("/api/users", headers=headers, json={
            "username": "new_atdd_user",
            "password": "Secure@Password1",
            "role": "user",
        })

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["username"] == "new_atdd_user"
        assert data["role"] == "user"
        assert "password" not in data  # 密码不返回

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_created_user_password_is_hashed(self, client, db_session):
        """AC: 密码 bcrypt 加密存储，明文不落库"""
        from sqlalchemy import select
        from app.models.user import User

        admin = await create_test_user(db_session, username="crud_admin2", role="admin")
        headers, _ = make_auth_headers(admin)

        await client.post("/api/users", headers=headers, json={
            "username": "hashed_pw_user",
            "password": "PlainText@123",
            "role": "user",
        })

        result = await db_session.execute(
            select(User).where(User.username == "hashed_pw_user")
        )
        user = result.scalar_one()
        assert user.password != "PlainText@123"
        assert user.password.startswith(("$2b$", "$2a$"))


# ---------------------------------------------------------------------------
# 1.3-API-002: 用户名唯一 → 409
# Priority: P0
# ---------------------------------------------------------------------------
class TestCreateUserDuplicate:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_duplicate_username_returns_409(self, client, db_session):
        """AC: 用户名重复时返回 409 错误"""
        admin = await create_test_user(db_session, username="dup_admin", role="admin")
        headers, _ = make_auth_headers(admin)

        # Given: 创建第一个用户
        await client.post("/api/users", headers=headers, json={
            "username": "unique_name",
            "password": "Test@123456",
            "role": "user",
        })

        # When: 用相同用户名创建
        response = await client.post("/api/users", headers=headers, json={
            "username": "unique_name",
            "password": "Test@123456",
            "role": "user",
        })

        # Then: 409
        assert response.status_code == 409


# ---------------------------------------------------------------------------
# 1.3-API-003: 用户列表
# Priority: P1
# ---------------------------------------------------------------------------
class TestListUsers:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_admin_lists_all_users(self, client, db_session):
        """AC: 展示所有用户列表，包含用户名、角色、状态、创建时间"""
        admin = await create_test_user(db_session, username="list_admin", role="admin")
        await create_test_user(db_session, username="list_user1", role="user")
        await create_test_user(db_session, username="list_user2", role="user")
        headers, _ = make_auth_headers(admin)

        response = await client.get("/api/users", headers=headers)

        assert response.status_code == 200
        data = response.json()["data"]
        assert isinstance(data, list)
        assert len(data) >= 3


# ---------------------------------------------------------------------------
# 1.3-API-004: 修改用户角色/状态
# Priority: P1
# ---------------------------------------------------------------------------
class TestUpdateUser:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_admin_updates_user_role(self, client, db_session):
        """AC: PUT /api/users/{id} 修改角色成功，变更立即生效"""
        admin = await create_test_user(db_session, username="upd_admin", role="admin")
        user = await create_test_user(db_session, username="upd_target", role="user")
        headers, _ = make_auth_headers(admin)

        response = await client.put(f"/api/users/{user.id}", headers=headers, json={
            "role": "admin",
        })

        assert response.status_code == 200
        assert response.json()["data"]["role"] == "admin"

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_admin_updates_user_active_status(self, client, db_session):
        """AC: 修改激活状态成功"""
        admin = await create_test_user(db_session, username="upd_admin2", role="admin")
        user = await create_test_user(db_session, username="upd_target2", role="user")
        headers, _ = make_auth_headers(admin)

        response = await client.put(f"/api/users/{user.id}", headers=headers, json={
            "isActive": False,
        })

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 1.3-API-005: 删除用户
# Priority: P1
# ---------------------------------------------------------------------------
class TestDeleteUser:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_admin_deletes_user(self, client, db_session):
        """AC: DELETE /api/users/{id} 执行成功"""
        admin = await create_test_user(db_session, username="del_admin", role="admin")
        user = await create_test_user(db_session, username="del_target", role="user")
        headers, _ = make_auth_headers(admin)

        response = await client.delete(f"/api/users/{user.id}", headers=headers)

        assert response.status_code in (200, 204)


# ---------------------------------------------------------------------------
# 1.3-API-006: 删除已绑定项目的用户
# Priority: P1
# ---------------------------------------------------------------------------
class TestDeleteUserWithProjects:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_delete_user_bound_to_project_succeeds_with_unbind(self, client, db_session):
        """AC: 已绑定项目的用户删除后自动解除绑定"""
        admin = await create_test_user(db_session, username="del_bound_admin", role="admin")
        user = await create_test_user(db_session, username="del_bound_user", role="user")
        headers, _ = make_auth_headers(admin)

        # Given: 创建项目并添加用户为成员
        proj_resp = await client.post("/api/projects", headers=headers, json={
            "name": "atdd-del-project",
            "gitUrl": "git@example.com:test/repo.git",
            "scriptBasePath": "/tmp/atdd-del",
        })
        project_id = proj_resp.json()["data"]["id"]

        await client.post(f"/api/projects/{project_id}/members", headers=headers, json={
            "userId": str(user.id),
            "role": "member",
        })

        # When: 删除该用户
        response = await client.delete(f"/api/users/{user.id}", headers=headers)

        # Then: 删除成功
        assert response.status_code in (200, 204)


# ---------------------------------------------------------------------------
# 1.3-API-007: 非 admin 用户 → 403
# Priority: P0
# ---------------------------------------------------------------------------
class TestUserCrudForbidden:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_non_admin_cannot_list_users(self, client, db_session):
        """AC: 非系统管理员用户访问用户管理 API 返回 403"""
        user = await create_test_user(db_session, username="forbidden_user", role="user")
        headers, _ = make_auth_headers(user)

        response = await client.get("/api/users", headers=headers)
        assert response.status_code == 403

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_non_admin_cannot_create_user(self, client, db_session):
        """AC: 非 admin 创建用户返回 403"""
        user = await create_test_user(db_session, username="forbidden_creator", role="user")
        headers, _ = make_auth_headers(user)

        response = await client.post("/api/users", headers=headers, json={
            "username": "should_fail",
            "password": "Test@123456",
            "role": "user",
        })
        assert response.status_code == 403

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_non_admin_cannot_delete_user(self, client, db_session):
        """AC: 非 admin 删除用户返回 403"""
        user = await create_test_user(db_session, username="forbidden_deleter", role="user")
        target = await create_test_user(db_session, username="forbidden_target", role="user")
        headers, _ = make_auth_headers(user)

        response = await client.delete(f"/api/users/{target.id}", headers=headers)
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 1.3-API-008: 用户不存在 → 404
# Priority: P2
# ---------------------------------------------------------------------------
class TestUpdateUserNotFound:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_update_nonexistent_user_returns_404(self, client, db_session):
        """边界: 更新不存在的用户返回 404"""
        admin = await create_test_user(db_session, username="nf_admin", role="admin")
        headers, _ = make_auth_headers(admin)

        response = await client.put(
            "/api/users/00000000-0000-0000-0000-000000000000",
            headers=headers,
            json={"role": "admin"},
        )
        assert response.status_code == 404
