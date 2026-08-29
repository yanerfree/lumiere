"""
test_list_members — 查看项目成员列表
Test ID: 1.5-API-002
Priority: P0
"""
import pytest

from tests.conftest import create_test_user, make_auth_headers


class TestListMembers:
    """GET /api/projects/{id}/members"""

    @pytest.mark.asyncio
    async def test_list_members_includes_creator(self, client, db_session):
        # Given: admin 创建了项目（自动加入 member）
        admin = await create_test_user(db_session, username="list_mem_admin", role="admin")
        headers, _ = make_auth_headers(admin)

        r = await client.post("/api/projects", headers=headers, json={
            "name": "list-mem-proj", "gitUrl": "git@x.com:r.git", "scriptBasePath": "/lm",
        })
        project_id = r.json()["data"]["id"]

        # When: 查询成员列表
        response = await client.get(f"/api/projects/{project_id}/members", headers=headers)

        # Then: 包含创建者
        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) >= 1
        assert data[0]["username"] == "list_mem_admin"
        assert data[0]["role"] == "manager"
