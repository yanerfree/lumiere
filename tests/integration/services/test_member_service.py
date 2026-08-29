"""Integration 测试 — services/member_service.py（需要 DB）"""
import pytest

from app.core.exceptions import ConflictError, ValidationError
from app.schemas.project import AddMemberRequest, CreateProjectRequest
from app.services import member_service, project_service
from tests.conftest import create_test_user


class TestMemberServiceAdd:

    async def _setup(self, db_session):
        admin = await create_test_user(db_session, username="mem_svc_admin", role="admin")
        req = CreateProjectRequest(name="mem-proj", git_url="git@x.com:m/r.git", script_base_path="/tmp/m")
        project = await project_service.create_project(db_session, req, admin)
        return project, admin

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_add_member(self, db_session):
        project, _ = await self._setup(db_session)
        user = await create_test_user(db_session, username="mem_svc_new", role="user")
        data = AddMemberRequest(user_id=user.id, role="member")
        result = await member_service.add_member(db_session, project.id, data)
        assert result["role"] == "member"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_duplicate_member_raises_conflict(self, db_session):
        project, _ = await self._setup(db_session)
        user = await create_test_user(db_session, username="mem_svc_dup", role="user")
        data = AddMemberRequest(user_id=user.id, role="member")
        await member_service.add_member(db_session, project.id, data)
        with pytest.raises(ConflictError):
            await member_service.add_member(db_session, project.id, data)


class TestMemberServiceLastAdmin:

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cannot_remove_last_admin(self, db_session):
        admin = await create_test_user(db_session, username="mem_svc_last", role="admin")
        req = CreateProjectRequest(name="last-admin-proj", git_url="git@x.com:l/r.git", script_base_path="/tmp/l")
        project = await project_service.create_project(db_session, req, admin)
        with pytest.raises(ValidationError):
            await member_service.remove_member(db_session, project.id, admin.id)
