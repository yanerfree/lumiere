"""
test_list_projects — 项目列表按角色过滤
Test ID: 1.4-API-003
Priority: P0
"""
import pytest

from app.models.project import Project, ProjectMember
from tests.conftest import create_test_user, make_auth_headers


class TestListProjects:
    """GET /api/projects：admin 全部可见，普通用户仅看绑定的"""

    @pytest.mark.asyncio
    async def test_admin_sees_all_projects(self, client, db_session):
        # Given: admin 创建了两个项目
        admin = await create_test_user(db_session, username="list_admin", role="admin")
        headers, _ = make_auth_headers(admin)
        await client.post("/api/projects", headers=headers, json={
            "name": "proj-a", "gitUrl": "git@x.com:a.git", "scriptBasePath": "/a",
        })
        await client.post("/api/projects", headers=headers, json={
            "name": "proj-b", "gitUrl": "git@x.com:b.git", "scriptBasePath": "/b",
        })

        # When: admin 查询列表
        response = await client.get("/api/projects", headers=headers)

        # Then: 看到所有项目
        assert response.status_code == 200
        names = [p["name"] for p in response.json()["data"]]
        assert "proj-a" in names
        assert "proj-b" in names

    @pytest.mark.asyncio
    async def test_regular_user_sees_only_bound_projects(self, client, db_session):
        # Given: admin 创建了两个项目，普通用户只绑定了一个
        admin = await create_test_user(db_session, username="list_admin2", role="admin")
        admin_headers, _ = make_auth_headers(admin)

        r1 = await client.post("/api/projects", headers=admin_headers, json={
            "name": "visible-proj", "gitUrl": "git@x.com:v.git", "scriptBasePath": "/v",
        })
        await client.post("/api/projects", headers=admin_headers, json={
            "name": "invisible-proj", "gitUrl": "git@x.com:i.git", "scriptBasePath": "/i",
        })

        # 创建普通用户并绑定到第一个项目
        user = await create_test_user(db_session, username="list_user", role="user")
        project_id = r1.json()["data"]["id"]
        member = ProjectMember(project_id=project_id, user_id=user.id, role="member")
        db_session.add(member)
        await db_session.flush()

        user_headers, _ = make_auth_headers(user)

        # When: 普通用户查询列表
        response = await client.get("/api/projects", headers=user_headers)

        # Then: 只看到绑定的项目
        assert response.status_code == 200
        names = [p["name"] for p in response.json()["data"]]
        assert "visible-proj" in names
        assert "invisible-proj" not in names
