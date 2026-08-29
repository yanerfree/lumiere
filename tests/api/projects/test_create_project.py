"""
test_create_project — admin 创建项目（含默认 branch + 自动加入 member）
Test ID: 1.4-API-001
Priority: P0
"""
import pytest
from sqlalchemy import select

from app.models.project import Branch, ProjectMember
from tests.conftest import create_test_user, make_auth_headers


class TestCreateProject:
    """POST /api/projects：admin 创建项目"""

    @pytest.mark.asyncio
    async def test_create_project_success(self, client, db_session):
        # Given: admin 已登录
        admin = await create_test_user(db_session, username="proj_admin", role="admin")
        headers, _ = make_auth_headers(admin)

        # When: 创建项目
        response = await client.post("/api/projects", headers=headers, json={
            "name": "my-test-project",
            "gitUrl": "git@github.com:team/repo.git",
            "scriptBasePath": "/opt/scripts/my-test-project",
            "description": "测试项目",
        })

        # Then: 返回 201 和项目信息
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["name"] == "my-test-project"
        assert data["gitUrl"] == "git@github.com:team/repo.git"
        assert "id" in data

        # Then: 自动创建了默认 branch
        project_id = data["id"]
        result = await db_session.execute(
            select(Branch).where(Branch.project_id == project_id)
        )
        branches = result.scalars().all()
        assert len(branches) == 1
        assert branches[0].name == "default"
        assert branches[0].branch == "main"

        # Then: 创建者自动加入 project_members
        result = await db_session.execute(
            select(ProjectMember).where(ProjectMember.project_id == project_id)
        )
        members = result.scalars().all()
        assert len(members) == 1
        assert members[0].user_id == admin.id
        assert members[0].role == "manager"
