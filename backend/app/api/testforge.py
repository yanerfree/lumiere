import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import permissions as perms
from app.core.exceptions import NotFoundError, ValidationError
from app.deps.auth import require_project_role
from app.deps.db import get_db
from app.models.user import User
from app.schemas.testforge import CreateTaskRequest, TaskResponse
from app.services import testforge_service

router = APIRouter(
    prefix="/api/projects/{project_id}/branches/{branch_id}/testforge",
    tags=["testforge"],
)


@router.post("/task")
async def create_task(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: CreateTaskRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*perms.TIER_WRITE)),
):
    """创建 TestForge 生成任务，返回 task JSON 供 Claude Code 使用"""
    api_url = str(request.base_url).rstrip("/")

    task = testforge_service.generate_task(
        project_id=project_id,
        branch_id=branch_id,
        user_id=current_user.id,
        user_role=current_user.role,
        target=body.target.model_dump(by_alias=True),
        interface_info=body.interface_info,
        business_rules=body.business_rules,
        api_url=api_url,
    )
    return {"data": task}


@router.get("/tasks")
async def list_tasks(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_READ)),
):
    """列出所有 TestForge 任务"""
    tasks = testforge_service.list_tasks()
    return {"data": tasks}


@router.get("/tasks/{task_id}")
async def get_task(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    task_id: str,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_READ)),
):
    """获取单个任务详情"""
    task = testforge_service.get_task(task_id)
    if not task:
        raise NotFoundError(code="TASK_NOT_FOUND", message=f"任务 {task_id} 不存在")
    return {"data": task}


@router.put("/tasks/{task_id}/status")
async def update_task_status(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    task_id: str,
    status: str = Query(...),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_WRITE)),
):
    """更新任务状态（pending / processing / completed / failed）"""
    if status not in ("pending", "processing", "completed", "failed"):
        raise ValidationError(code="INVALID_STATUS", message="状态只能是 pending/processing/completed/failed")
    task = testforge_service.update_task_status(task_id, status)
    if not task:
        raise NotFoundError(code="TASK_NOT_FOUND", message=f"任务 {task_id} 不存在")
    return {"data": task}
