"""
test_projects_forbidden — 非 admin 用户创建/编辑/删除项目返回 403
Test ID: 1.4-API-006
Priority: P0
"""
import uuid

import pytest

from tests.conftest import create_test_user, make_auth_headers


class TestProjectsForbidden:
    """项目 CUD 权限拦截：非 admin 返回 403"""

    @pytest.mark.asyncio
    async def test_regular_user_can_create_project(self, client, db_session):
        # Given: 普通用户（create_project 已改为所有登录用户可用）
        user = await create_test_user(db_session, username="proj_user", role="user")
        headers, _ = make_auth_headers(user)

        # When: 创建项目
        response = await client.post("/api/projects", headers=headers, json={
            "name": "user-proj",
            "gitUrl": "git@x.com:r.git",
            "scriptBasePath": "/f",
        })

        # Then: 201 成功（用户自动成为 manager）
        assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_regular_user_cannot_delete_project(self, client, db_session):
        # Given: 普通用户
        user = await create_test_user(db_session, username="proj_user2", role="user")
        headers, _ = make_auth_headers(user)

        # When: 尝试删除项目
        response = await client.delete(f"/api/projects/{uuid.uuid4()}", headers=headers)

        # Then: 403
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_unauthenticated_cannot_list(self, client):
        # Given: 不携带 token
        # When: 访问项目列表
        response = await client.get("/api/projects")

        # Then: 401
        assert response.status_code == 401
