"""
test_remove_member — 移除成员 + 最后一个 admin 保护
Test ID: 1.5-API-004
Priority: P0
"""
import pytest

from tests.conftest import create_test_user, make_auth_headers


class TestRemoveMember:
    """DELETE /api/projects/{id}/members/{userId}"""

    @pytest.mark.asyncio
    async def test_remove_member_success(self, client, db_session):
        # Given: admin 创建项目并添加了一个 tester
        admin = await create_test_user(db_session, username="rm_mem_admin", role="admin")
        user = await create_test_user(db_session, username="rm_mem_user", role="user")
        headers, _ = make_auth_headers(admin)

        r = await client.post("/api/projects", headers=headers, json={
            "name": "rm-mem-proj", "gitUrl": "git@x.com:r.git", "scriptBasePath": "/rm",
        })
        project_id = r.json()["data"]["id"]
        await client.post(f"/api/projects/{project_id}/members", headers=headers, json={
            "userId": str(user.id), "role": "member",
        })

        # When: 移除成员
        response = await client.delete(f"/api/projects/{project_id}/members/{user.id}", headers=headers)

        # Then: 200
        assert response.status_code == 200
        assert response.json()["message"] == "移除成功"

        # Then: 成员列表中不再包含该用户
        list_r = await client.get(f"/api/projects/{project_id}/members", headers=headers)
        usernames = [m["username"] for m in list_r.json()["data"]]
        assert "rm_mem_user" not in usernames

    @pytest.mark.asyncio
    async def test_cannot_remove_last_admin(self, client, db_session):
        # Given: 项目只有一个 manager
        admin = await create_test_user(db_session, username="rm_last_admin", role="admin")
        headers, _ = make_auth_headers(admin)

        r = await client.post("/api/projects", headers=headers, json={
            "name": "rm-last-proj", "gitUrl": "git@x.com:r.git", "scriptBasePath": "/rl",
        })
        project_id = r.json()["data"]["id"]

        # When: 尝试移除唯一的 manager
        response = await client.delete(f"/api/projects/{project_id}/members/{admin.id}", headers=headers)

        # Then: 422
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "LAST_ADMIN"
