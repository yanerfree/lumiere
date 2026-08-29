"""
test_add_member — admin 添加项目成员
Test ID: 1.5-API-001
Priority: P0
"""
import pytest

from tests.conftest import create_test_user, make_auth_headers


class TestAddMember:
    """POST /api/projects/{id}/members"""

    @pytest.mark.asyncio
    async def test_add_member_success(self, client, db_session):
        # Given: admin 创建了项目，另有一个普通用户
        admin = await create_test_user(db_session, username="mem_admin", role="admin")
        user = await create_test_user(db_session, username="mem_user", role="user")
        headers, _ = make_auth_headers(admin)

        r = await client.post("/api/projects", headers=headers, json={
            "name": "mem-proj", "gitUrl": "git@x.com:r.git", "scriptBasePath": "/m",
        })
        project_id = r.json()["data"]["id"]

        # When: 添加成员
        response = await client.post(f"/api/projects/{project_id}/members", headers=headers, json={
            "userId": str(user.id),
            "role": "member",
        })

        # Then: 201 + 成员信息
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["username"] == "mem_user"
        assert data["role"] == "member"

    @pytest.mark.asyncio
    async def test_add_duplicate_member_returns_409(self, client, db_session):
        # Given: 用户已是项目成员
        admin = await create_test_user(db_session, username="dup_mem_admin", role="admin")
        user = await create_test_user(db_session, username="dup_mem_user", role="user")
        headers, _ = make_auth_headers(admin)

        r = await client.post("/api/projects", headers=headers, json={
            "name": "dup-mem-proj", "gitUrl": "git@x.com:r.git", "scriptBasePath": "/dm",
        })
        project_id = r.json()["data"]["id"]
        await client.post(f"/api/projects/{project_id}/members", headers=headers, json={
            "userId": str(user.id), "role": "member",
        })

        # When: 重复添加
        response = await client.post(f"/api/projects/{project_id}/members", headers=headers, json={
            "userId": str(user.id), "role": "manager",
        })

        # Then: 409
        assert response.status_code == 409
