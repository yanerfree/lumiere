"""Integration 测试 — services/project_service.py（需要 DB）"""
import pytest
from sqlalchemy import select

from app.core.exceptions import ConflictError, NotFoundError
from app.models.project import Branch, Project, ProjectMember
from app.schemas.project import CreateProjectRequest
from app.services import project_service
from tests.conftest import create_test_user


class TestProjectServiceCreate:

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_project_with_defaults(self, db_session):
        creator = await create_test_user(db_session, username="proj_svc_admin", role="admin")
        req = CreateProjectRequest(
            name="svc-test-proj",
            git_url="git@x.com:t/r.git",
            script_base_path="/tmp/svc",
        )
        project = await project_service.create_project(db_session, req, creator)

        assert project.name == "svc-test-proj"

        # 默认 branch
        branches = (await db_session.execute(
            select(Branch).where(Branch.project_id == project.id)
        )).scalars().all()
        assert len(branches) == 1
        assert branches[0].name == "default"

        # 创建者加入 members
        members = (await db_session.execute(
            select(ProjectMember).where(ProjectMember.project_id == project.id)
        )).scalars().all()
        assert len(members) == 1
        assert members[0].user_id == creator.id
        assert members[0].role == "manager"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_duplicate_name_raises_conflict(self, db_session):
        creator = await create_test_user(db_session, username="proj_svc_dup", role="admin")
        req = CreateProjectRequest(name="dup-proj", git_url="git@x.com:a/b.git", script_base_path="/tmp/d")
        await project_service.create_project(db_session, req, creator)
        with pytest.raises(ConflictError):
            await project_service.create_project(db_session, req, creator)


class TestProjectServiceList:

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_admin_sees_all(self, db_session):
        admin = await create_test_user(db_session, username="proj_svc_list", role="admin")
        req = CreateProjectRequest(name="visible-proj", git_url="git@x.com:v/p.git", script_base_path="/tmp/v")
        await project_service.create_project(db_session, req, admin)
        projects = await project_service.list_projects(db_session, admin)
        assert len(projects) >= 1

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_regular_user_sees_only_bound(self, db_session):
        admin = await create_test_user(db_session, username="proj_svc_owner", role="admin")
        user = await create_test_user(db_session, username="proj_svc_other", role="user")
        req = CreateProjectRequest(name="owner-proj", git_url="git@x.com:o/p.git", script_base_path="/tmp/o")
        await project_service.create_project(db_session, req, admin)
        projects = await project_service.list_projects(db_session, user)
        names = [p.name for p in projects]
        assert "owner-proj" not in names
