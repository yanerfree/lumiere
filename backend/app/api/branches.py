import uuid

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.status import HTTP_201_CREATED, HTTP_202_ACCEPTED

from app.core import permissions as perms
from app.core.exceptions import AppError, NotFoundError, ValidationError
from app.deps.auth import require_project_role
from app.deps.db import get_db
from app.engine.task_status import set_task_status
from app.engine.tasks.git_sync import run_git_sync_inline
from app.models.project import Branch, Project
from app.models.user import User
from app.schemas.branch import BranchResponse, CreateBranchRequest, UpdateBranchRequest
from app.services import branch_service

router = APIRouter(prefix="/api/projects/{project_id}/branches", tags=["branches"])


@router.get("")
async def list_branches(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_READ)),
):
    """分支配置列表（项目成员均可查看）"""
    branches = await branch_service.list_branches(session, project_id)
    return {
        "data": [
            BranchResponse.model_validate(b, from_attributes=True).model_dump(by_alias=True)
            for b in branches
        ]
    }


@router.post("", status_code=HTTP_201_CREATED)
async def create_branch(
    project_id: uuid.UUID,
    body: CreateBranchRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*perms.TIER_ADMIN)),
):
    """创建分支配置（project_admin 或系统 admin）。
    如果指定 source_branch_id + copy_modules，从源分支深拷贝数据。"""
    branch = await branch_service.create_branch(session, project_id, body)

    copy_stats = None
    if body.source_branch_id and body.copy_modules:
        source_bid = uuid.UUID(body.source_branch_id)
        source_branch = await session.get(Branch, source_bid)
        if not source_branch or source_branch.project_id != project_id:
            raise ValidationError(code="INVALID_SOURCE", message="源分支不属于当前项目")
        from app.services.branch_copy_service import copy_branch_data
        copy_stats = await copy_branch_data(
            session,
            source_branch_id=source_bid,
            target_branch_id=branch.id,
            project_id=project_id,
            modules=body.copy_modules,
            user_id=current_user.id,
        )

    result = BranchResponse.model_validate(branch, from_attributes=True).model_dump(by_alias=True)
    if copy_stats:
        result["copyStats"] = copy_stats
    return {"data": result}


@router.put("/{branch_id}")
async def update_branch(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: UpdateBranchRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_ADMIN)),
):
    """更新分支配置（name 不可改）"""
    branch = await branch_service.update_branch(session, branch_id, body, project_id)
    return {
        "data": BranchResponse.model_validate(branch, from_attributes=True).model_dump(by_alias=True)
    }


@router.post("/{branch_id}/archive")
async def archive_branch(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_ADMIN)),
):
    """归档分支配置"""
    branch = await branch_service.archive_branch(session, branch_id, project_id)
    return {
        "data": BranchResponse.model_validate(branch, from_attributes=True).model_dump(by_alias=True)
    }


@router.post("/{branch_id}/activate")
async def activate_branch(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_ADMIN)),
):
    """恢复已归档的分支配置"""
    branch = await branch_service.activate_branch(session, branch_id, project_id)
    return {
        "data": BranchResponse.model_validate(branch, from_attributes=True).model_dump(by_alias=True)
    }


@router.post("/{branch_id}/sync", status_code=HTTP_202_ACCEPTED)
async def sync_branch_endpoint(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_WRITE)),
):
    """
    同步分支脚本（更新脚本）— 后台执行，无需额外 Worker 进程。

    返回 202 + taskId，前端通过 GET /api/tasks/{taskId}/status 轮询进度。
    """
    result = await session.execute(select(Branch).where(Branch.id == branch_id))
    branch = result.scalar_one_or_none()
    if branch is None:
        raise NotFoundError(code="BRANCH_NOT_FOUND", message="分支配置不存在")

    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise NotFoundError(code="PROJECT_NOT_FOUND", message="项目不存在")

    if not project.git_url:
        raise ValidationError(code="NO_GIT_URL", message="项目未配置 Git 仓库地址")
    if not project.script_base_path:
        raise ValidationError(code="NO_SCRIPT_PATH", message="项目未配置脚本基础路径")
    if branch.status == "archived":
        raise ValidationError(code="BRANCH_ARCHIVED", message="已归档的分支不能同步")

    task_id = uuid.uuid4().hex
    await set_task_status(task_id, "pending", message="任务已提交，正在后台执行...")
    background_tasks.add_task(
        run_git_sync_inline, task_id, str(branch_id), str(project_id)
    )

    return {"data": {"taskId": task_id}}
