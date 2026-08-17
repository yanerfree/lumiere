"""Integration 测试 — services/branch_service.py（需要 DB）"""
import pytest

from app.core.exceptions import ConflictError, ValidationError
from app.schemas.branch import CreateBranchRequest
from app.schemas.project import CreateProjectRequest
from app.services import branch_service, project_service
from tests.conftest import create_test_user


class TestBranchServiceCRUD:

    async def _setup(self, db_session):
        admin = await create_test_user(db_session, username="br_svc_admin", role="admin")
        req = CreateProjectRequest(name="br-proj", git_url="git@x.com:b/r.git", script_base_path="/tmp/b")
        project = await project_service.create_project(db_session, req, admin)
        return project

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_list_includes_default(self, db_session):
        project = await self._setup(db_session)
        branches = await branch_service.list_branches(db_session, project.id)
        assert len(branches) == 1
        assert branches[0].name == "default"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_branch(self, db_session):
        project = await self._setup(db_session)
        req = CreateBranchRequest(name="develop", branch="develop")
        branch = await branch_service.create_branch(db_session, project.id, req)
        assert branch.name == "develop"
        assert branch.branch == "develop"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_dotted_name_allowed(self, db_session):
        """v2.2.0 这种版本号要能建 —— 点号只做分隔符。"""
        project = await self._setup(db_session)
        branch = await branch_service.create_branch(
            db_session, project.id, CreateBranchRequest(name="v2.2.0")
        )
        assert branch.name == "v2.2.0"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_duplicate_name_raises_conflict(self, db_session):
        project = await self._setup(db_session)
        req = CreateBranchRequest(name="default", branch="main")
        with pytest.raises(ConflictError) as ei:
            await branch_service.create_branch(db_session, project.id, req)
        assert ei.value.code == "BRANCH_NAME_EXISTS"

    @pytest.mark.integration
    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", ["..", ".", ".hidden", "v2.", "a..b"])
    async def test_check_violation_is_not_reported_as_duplicate(self, db_session, bad):
        """CHECK 违反必须报「格式非法」，不能报「已存在」。

        model_construct 跳过 pydantic 校验，直接把非法名送到 DB —— 否则这条
        路径被上层挡住，谎报重名的 bug 就永远测不到。".." 会被拼进工作目录，
        是这条约束真正要拦的东西。
        """
        project = await self._setup(db_session)
        req = CreateBranchRequest.model_construct(
            name=bad, description=None, branch="main", source_branch_id=None, copy_modules=None
        )
        with pytest.raises(ValidationError) as ei:
            await branch_service.create_branch(db_session, project.id, req)
        assert ei.value.code == "BRANCH_NAME_INVALID"
        assert ei.value.status_code == 422


class TestBranchServiceArchive:

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_archive_and_activate(self, db_session):
        admin = await create_test_user(db_session, username="br_svc_arc", role="admin")
        req = CreateProjectRequest(name="arc-proj", git_url="git@x.com:a/r.git", script_base_path="/tmp/a")
        project = await project_service.create_project(db_session, req, admin)

        # 创建第二个分支
        await branch_service.create_branch(db_session, project.id, CreateBranchRequest(name="extra"))

        branches = await branch_service.list_branches(db_session, project.id)
        default = [b for b in branches if b.name == "default"][0]

        archived = await branch_service.archive_branch(db_session, default.id)
        assert archived.status == "archived"

        activated = await branch_service.activate_branch(db_session, default.id)
        assert activated.status == "active"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cannot_archive_last_active(self, db_session):
        admin = await create_test_user(db_session, username="br_svc_last", role="admin")
        req = CreateProjectRequest(name="last-proj", git_url="git@x.com:l/r.git", script_base_path="/tmp/l2")
        project = await project_service.create_project(db_session, req, admin)

        branches = await branch_service.list_branches(db_session, project.id)
        with pytest.raises(ValidationError):
            await branch_service.archive_branch(db_session, branches[0].id)
