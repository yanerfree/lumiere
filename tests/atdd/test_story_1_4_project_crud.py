"""
ATDD 验收测试 — Story 1.4: 项目 CRUD
TDD Red Phase: 验证项目创建、列表、更新和权限控制

覆盖验收标准:
- POST /api/projects 创建 + 默认 branch + 自动加入成员
- 项目名唯一 → 409
- Git URL 格式校验
- GET /api/projects (admin 全部 vs 普通用户仅绑定)
- PUT /api/projects/{id} 更新
- 非管理员 → 403
"""
import pytest

from tests.conftest import create_test_user, make_auth_headers


# ---------------------------------------------------------------------------
# 1.4-API-001: 创建项目 + 默认 branch + 自动加入成员
# Priority: P0
# ---------------------------------------------------------------------------
class TestCreateProject:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_admin_creates_project_with_defaults(self, client, db_session):
        """AC: POST /api/projects 创建成功 + 默认 branch + 创建者自动加入"""
        from sqlalchemy import select
        from app.models.project import Branch, ProjectMember

        admin = await create_test_user(db_session, username="proj_admin", role="admin")
        headers, _ = make_auth_headers(admin)

        response = await client.post("/api/projects", headers=headers, json={
            "name": "atdd-project-1",
            "gitUrl": "git@code.example.com:team/repo.git",
            "scriptBasePath": "/opt/scripts/atdd-1",
        })

        # Then: 201 成功
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["name"] == "atdd-project-1"
        project_id = data["id"]

        # Then: 自动创建默认 branch (name=default, branch=main)
        result = await db_session.execute(
            select(Branch).where(Branch.project_id == project_id)
        )
        branches = result.scalars().all()
        assert len(branches) == 1
        assert branches[0].name == "default"
        assert branches[0].branch == "main"

        # Then: 创建者自动加入 project_members (role=manager)
        result = await db_session.execute(
            select(ProjectMember).where(ProjectMember.project_id == project_id)
        )
        members = result.scalars().all()
        assert len(members) == 1
        assert members[0].user_id == admin.id
        assert members[0].role == "manager"


# ---------------------------------------------------------------------------
# 1.4-API-002: 项目名唯一
# Priority: P0
# ---------------------------------------------------------------------------
class TestCreateProjectDuplicate:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_duplicate_project_name_returns_409(self, client, db_session):
        """AC: 项目名称系统内唯一，重名返回 409"""
        admin = await create_test_user(db_session, username="dup_proj_admin", role="admin")
        headers, _ = make_auth_headers(admin)

        await client.post("/api/projects", headers=headers, json={
            "name": "duplicate-project",
            "gitUrl": "git@example.com:a/b.git",
            "scriptBasePath": "/tmp/dup1",
        })

        response = await client.post("/api/projects", headers=headers, json={
            "name": "duplicate-project",
            "gitUrl": "git@example.com:c/d.git",
            "scriptBasePath": "/tmp/dup2",
        })

        assert response.status_code == 409


# ---------------------------------------------------------------------------
# 1.4-API-003/004: Git URL 格式校验
# Priority: P1
# ---------------------------------------------------------------------------
class TestGitUrlValidation:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_ssh_git_url_accepted(self, client, db_session):
        """AC: 支持 git@host:user/repo.git 格式"""
        admin = await create_test_user(db_session, username="git_ssh_admin", role="admin")
        headers, _ = make_auth_headers(admin)

        response = await client.post("/api/projects", headers=headers, json={
            "name": "atdd-ssh-project",
            "gitUrl": "git@github.com:org/repo.git",
            "scriptBasePath": "/tmp/ssh",
        })
        assert response.status_code == 201

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_https_git_url_accepted(self, client, db_session):
        """AC: 支持 https:// 格式"""
        admin = await create_test_user(db_session, username="git_https_admin", role="admin")
        headers, _ = make_auth_headers(admin)

        response = await client.post("/api/projects", headers=headers, json={
            "name": "atdd-https-project",
            "gitUrl": "https://github.com/org/repo.git",
            "scriptBasePath": "/tmp/https",
        })
        assert response.status_code == 201

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_invalid_git_url_accepted_after_relaxation(self, client, db_session):
        """git_url 校验已放宽为可选字段，任何格式均接受"""
        admin = await create_test_user(db_session, username="git_bad_admin", role="admin")
        headers, _ = make_auth_headers(admin)

        response = await client.post("/api/projects", headers=headers, json={
            "name": "atdd-bad-url-project",
            "gitUrl": "not-a-valid-url",
            "scriptBasePath": "/tmp/bad",
        })
        assert response.status_code == 201


# ---------------------------------------------------------------------------
# 1.4-API-005/006: 项目列表 — 按角色过滤
# Priority: P0
# ---------------------------------------------------------------------------
class TestListProjects:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_admin_sees_all_projects(self, client, db_session):
        """AC: 系统管理员看到全部项目"""
        admin = await create_test_user(db_session, username="list_all_admin", role="admin")
        headers, _ = make_auth_headers(admin)

        # Given: 创建 2 个项目
        await client.post("/api/projects", headers=headers, json={
            "name": "proj-visible-1",
            "gitUrl": "git@x.com:a/b.git",
            "scriptBasePath": "/tmp/v1",
        })
        await client.post("/api/projects", headers=headers, json={
            "name": "proj-visible-2",
            "gitUrl": "git@x.com:c/d.git",
            "scriptBasePath": "/tmp/v2",
        })

        response = await client.get("/api/projects", headers=headers)
        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) >= 2

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_non_admin_sees_only_bound_projects(self, client, db_session):
        """AC: 其他角色仅看到已绑定的项目"""
        admin = await create_test_user(db_session, username="bound_admin", role="admin")
        user = await create_test_user(db_session, username="bound_user", role="user")
        admin_headers, _ = make_auth_headers(admin)
        user_headers, _ = make_auth_headers(user)

        # Given: admin 创建项目 (user 未绑定)
        await client.post("/api/projects", headers=admin_headers, json={
            "name": "not-bound-project",
            "gitUrl": "git@x.com:e/f.git",
            "scriptBasePath": "/tmp/nb",
        })

        # When: 非 admin 查看列表
        response = await client.get("/api/projects", headers=user_headers)
        assert response.status_code == 200
        data = response.json()["data"]
        # Then: 不包含未绑定的项目
        project_names = [p["name"] for p in data]
        assert "not-bound-project" not in project_names


# ---------------------------------------------------------------------------
# 1.4-API-007: 更新项目
# Priority: P1
# ---------------------------------------------------------------------------
class TestUpdateProject:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_update_project_info(self, client, db_session):
        """AC: PUT /api/projects/{id} 更新成功"""
        admin = await create_test_user(db_session, username="upd_proj_admin", role="admin")
        headers, _ = make_auth_headers(admin)

        create_resp = await client.post("/api/projects", headers=headers, json={
            "name": "atdd-update-proj",
            "gitUrl": "git@x.com:g/h.git",
            "scriptBasePath": "/tmp/upd",
        })
        project_id = create_resp.json()["data"]["id"]

        response = await client.put(f"/api/projects/{project_id}", headers=headers, json={
            "gitUrl": "git@x.com:new/repo.git",
        })

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 1.4-API-008: 非管理员 → 403
# Priority: P0
# ---------------------------------------------------------------------------
class TestProjectCrudForbidden:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_non_admin_can_create_project(self, client, db_session):
        """create_project 已开放给所有登录用户，非 admin 也能创建"""
        user = await create_test_user(db_session, username="forbidden_proj", role="user")
        headers, _ = make_auth_headers(user)

        response = await client.post("/api/projects", headers=headers, json={
            "name": "should-succeed-proj",
            "gitUrl": "git@x.com:i/j.git",
            "scriptBasePath": "/tmp/fail",
        })

        assert response.status_code == 201
