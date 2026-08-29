"""
ATDD 验收测试 — Story 1.5: 项目成员管理
TDD Red Phase: 验证项目成员的添加、修改、移除和权限控制

覆盖验收标准:
- POST /api/projects/{id}/members 添加成员
- 同一用户不可重复绑定 → 409
- PUT 修改成员角色
- DELETE 移除成员
- 移除最后 manager → 422
- 非管理员操作 → 403
"""
import pytest

from tests.conftest import create_test_user, make_auth_headers


async def _create_project_with_admin(client, db_session, admin_username="mem_admin"):
    """辅助: 创建项目并返回 (project_id, admin, headers)"""
    admin = await create_test_user(db_session, username=admin_username, role="admin")
    headers, _ = make_auth_headers(admin)
    resp = await client.post("/api/projects", headers=headers, json={
        "name": f"mem-proj-{admin_username}",
        "gitUrl": f"git@x.com:{admin_username}/repo.git",
        "scriptBasePath": f"/tmp/{admin_username}",
    })
    project_id = resp.json()["data"]["id"]
    return project_id, admin, headers


# ---------------------------------------------------------------------------
# 1.5-API-001: 添加成员
# Priority: P0
# ---------------------------------------------------------------------------
class TestAddMember:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_add_member_to_project(self, client, db_session):
        """AC: POST /api/projects/{id}/members 绑定成功"""
        project_id, admin, headers = await _create_project_with_admin(client, db_session, "add_mem_admin")
        user = await create_test_user(db_session, username="new_member", role="user")

        response = await client.post(f"/api/projects/{project_id}/members", headers=headers, json={
            "userId": str(user.id),
            "role": "member",
        })

        assert response.status_code in (200, 201)

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_added_member_can_see_project(self, client, db_session):
        """AC: 绑定后该用户立即可在项目列表看到该项目"""
        project_id, admin, admin_headers = await _create_project_with_admin(client, db_session, "see_proj_admin")
        user = await create_test_user(db_session, username="see_proj_user", role="user")
        user_headers, _ = make_auth_headers(user)

        # Given: 添加用户为成员
        await client.post(f"/api/projects/{project_id}/members", headers=admin_headers, json={
            "userId": str(user.id),
            "role": "member",
        })

        # When: 用户查看项目列表
        response = await client.get("/api/projects", headers=user_headers)
        assert response.status_code == 200
        project_names = [p["name"] for p in response.json()["data"]]
        assert f"mem-proj-see_proj_admin" in project_names


# ---------------------------------------------------------------------------
# 1.5-API-002: 重复绑定 → 409
# Priority: P1
# ---------------------------------------------------------------------------
class TestDuplicateMember:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_duplicate_member_returns_409(self, client, db_session):
        """AC: 同一用户不可重复绑定同一项目"""
        project_id, admin, headers = await _create_project_with_admin(client, db_session, "dup_mem_admin")
        user = await create_test_user(db_session, username="dup_member", role="user")

        # Given: 第一次绑定
        await client.post(f"/api/projects/{project_id}/members", headers=headers, json={
            "userId": str(user.id),
            "role": "member",
        })

        # When: 重复绑定
        response = await client.post(f"/api/projects/{project_id}/members", headers=headers, json={
            "userId": str(user.id),
            "role": "member",
        })

        assert response.status_code == 409


# ---------------------------------------------------------------------------
# 1.5-API-003: 修改成员角色
# Priority: P1
# ---------------------------------------------------------------------------
class TestUpdateMemberRole:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_update_member_role(self, client, db_session):
        """AC: 角色变更立即生效"""
        project_id, admin, headers = await _create_project_with_admin(client, db_session, "role_upd_admin")
        user = await create_test_user(db_session, username="role_target", role="user")

        await client.post(f"/api/projects/{project_id}/members", headers=headers, json={
            "userId": str(user.id),
            "role": "member",
        })

        response = await client.put(
            f"/api/projects/{project_id}/members/{user.id}",
            headers=headers,
            json={"role": "manager"},
        )

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 1.5-API-004: 移除成员
# Priority: P1
# ---------------------------------------------------------------------------
class TestRemoveMember:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_remove_member_from_project(self, client, db_session):
        """AC: DELETE 执行成功，该用户立即失去项目访问权"""
        project_id, admin, admin_headers = await _create_project_with_admin(client, db_session, "rm_mem_admin")
        user = await create_test_user(db_session, username="rm_target", role="user")
        user_headers, _ = make_auth_headers(user)

        # Given: 添加成员
        await client.post(f"/api/projects/{project_id}/members", headers=admin_headers, json={
            "userId": str(user.id),
            "role": "member",
        })

        # When: 移除成员
        response = await client.delete(
            f"/api/projects/{project_id}/members/{user.id}",
            headers=admin_headers,
        )
        assert response.status_code in (200, 204)

        # Then: 用户不再能看到该项目
        list_resp = await client.get("/api/projects", headers=user_headers)
        project_names = [p["name"] for p in list_resp.json()["data"]]
        assert f"mem-proj-rm_mem_admin" not in project_names


# ---------------------------------------------------------------------------
# 1.5-API-005: 移除最后 manager → 422
# Priority: P0
# ---------------------------------------------------------------------------
class TestLastAdminProtection:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_cannot_remove_last_manager(self, client, db_session):
        """AC: 移除最后一个项目管理员返回 422"""
        project_id, admin, headers = await _create_project_with_admin(client, db_session, "last_admin")

        # When: 尝试移除唯一的 manager (创建者)
        response = await client.delete(
            f"/api/projects/{project_id}/members/{admin.id}",
            headers=headers,
        )

        # Then: 422 "项目至少需要一个管理员"
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# 1.5-API-006: 非管理员 → 403
# Priority: P0
# ---------------------------------------------------------------------------
class TestMemberManagementForbidden:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_non_admin_cannot_add_member(self, client, db_session):
        """AC: 非管理员角色添加成员返回 403"""
        project_id, admin, admin_headers = await _create_project_with_admin(client, db_session, "forbid_mem_admin")
        tester = await create_test_user(db_session, username="forbid_tester", role="user")
        new_user = await create_test_user(db_session, username="forbid_new", role="user")

        # Given: 添加一个普通成员
        await client.post(f"/api/projects/{project_id}/members", headers=admin_headers, json={
            "userId": str(tester.id),
            "role": "member",
        })

        tester_headers, _ = make_auth_headers(tester)

        # When: tester 尝试添加新成员
        response = await client.post(f"/api/projects/{project_id}/members", headers=tester_headers, json={
            "userId": str(new_user.id),
            "role": "member",
        })

        assert response.status_code == 403

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_non_admin_cannot_remove_member(self, client, db_session):
        """AC: 非管理员角色移除成员返回 403"""
        project_id, admin, admin_headers = await _create_project_with_admin(client, db_session, "forbid_rm_admin")
        tester = await create_test_user(db_session, username="forbid_rm_tester", role="user")
        target = await create_test_user(db_session, username="forbid_rm_target", role="user")

        # 添加两个成员
        await client.post(f"/api/projects/{project_id}/members", headers=admin_headers, json={
            "userId": str(tester.id), "role": "member",
        })
        await client.post(f"/api/projects/{project_id}/members", headers=admin_headers, json={
            "userId": str(target.id), "role": "member",
        })

        tester_headers, _ = make_auth_headers(tester)

        # When: tester 尝试移除 target
        response = await client.delete(
            f"/api/projects/{project_id}/members/{target.id}",
            headers=tester_headers,
        )

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 1.5-API-007: 获取成员列表
# Priority: P1
# ---------------------------------------------------------------------------
class TestListMembers:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_list_project_members(self, client, db_session):
        """AC: 获取项目成员列表"""
        project_id, admin, headers = await _create_project_with_admin(client, db_session, "list_mem_admin")
        user = await create_test_user(db_session, username="list_mem_user", role="user")

        await client.post(f"/api/projects/{project_id}/members", headers=headers, json={
            "userId": str(user.id),
            "role": "member",
        })

        response = await client.get(f"/api/projects/{project_id}/members", headers=headers)

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) >= 2  # admin + user
