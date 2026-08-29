"""
test_update_member_role — 修改成员角色 + 最后一个 admin 保护
Test ID: 1.5-API-003
Priority: P0
"""
import pytest

from tests.conftest import create_test_user, make_auth_headers


class TestUpdateMemberRole:
    """PUT /api/projects/{id}/members/{userId}"""

    @pytest.mark.asyncio
    async def test_update_role_success(self, client, db_session):
        # Given: admin 创建项目，添加一个 tester
        admin = await create_test_user(db_session, username="upd_role_admin", role="admin")
        user = await create_test_user(db_session, username="upd_role_user", role="user")
        headers, _ = make_auth_headers(admin)

        r = await client.post("/api/projects", headers=headers, json={
            "name": "upd-role-proj", "gitUrl": "git@x.com:r.git", "scriptBasePath": "/ur",
        })
        project_id = r.json()["data"]["id"]
        await client.post(f"/api/projects/{project_id}/members", headers=headers, json={
            "userId": str(user.id), "role": "member",
        })

        # When: 将 member 改为 manager
        response = await client.put(
            f"/api/projects/{project_id}/members/{user.id}", headers=headers, json={"role": "manager"}
        )

        # Then: 200，角色已更新
        assert response.status_code == 200
        assert response.json()["data"]["role"] == "manager"

    @pytest.mark.asyncio
    async def test_cannot_downgrade_last_admin(self, client, db_session):
        # Given: 项目只有一个 manager（创建者）
        admin = await create_test_user(db_session, username="last_admin", role="admin")
        headers, _ = make_auth_headers(admin)

        r = await client.post("/api/projects", headers=headers, json={
            "name": "last-admin-proj", "gitUrl": "git@x.com:r.git", "scriptBasePath": "/la",
        })
        project_id = r.json()["data"]["id"]

        # When: 尝试将唯一的 manager 降级
        response = await client.put(
            f"/api/projects/{project_id}/members/{admin.id}", headers=headers, json={"role": "member"}
        )

        # Then: 422
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "LAST_ADMIN"
