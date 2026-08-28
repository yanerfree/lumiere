import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.status import HTTP_201_CREATED

from app.core.audit import write_audit_log
from app.deps.auth import get_current_user, require_project_role, require_role
from app.deps.db import get_db
from app.models.user import User
from app.schemas.common import MessageResponse
from app.schemas.project import (
    AddMemberRequest,
    CreateProjectRequest,
    MemberResponse,
    ProjectResponse,
    UpdateMemberRequest,
    UpdateProjectRequest,
)
from app.services import member_service, project_service

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("", status_code=HTTP_201_CREATED)
async def create_project(
    body: CreateProjectRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = await project_service.create_project(session, body, current_user)
    await write_audit_log(session, action="create", target_type="project", target_id=project.id, target_name=project.name)
    return {
        "data": ProjectResponse.model_validate(project, from_attributes=True).model_dump(by_alias=True)
    }


@router.get("")
async def list_projects(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    projects = await project_service.list_projects(session, current_user)
    return {
        "data": [
            ProjectResponse.model_validate(p, from_attributes=True).model_dump(by_alias=True)
            for p in projects
        ]
    }


@router.get("/{project_id}")
async def get_project(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """单个项目详情。

    这条路由此前是缺的 —— 同一路径上 PUT 和 DELETE 都在，唯独没有 GET，
    于是 `GET /api/projects/{id}` 返回 **405**（路径匹配到了、方法不允许）。
    写接口测试的人会理所当然地假设它存在（实测第一次就撞上），
    而 405 比 404 更难懂：既不是"没这个东西"也不是"没权限"。
    服务层的 get_project 一直都有，缺的只是这一层。
    """
    project = await project_service.get_project(session, project_id)
    return {
        "data": ProjectResponse.model_validate(project, from_attributes=True).model_dump(by_alias=True)
    }


@router.put("/{project_id}")
async def update_project(
    project_id: uuid.UUID,
    body: UpdateProjectRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    project = await project_service.update_project(session, project_id, body)
    await write_audit_log(session, action="update", target_type="project", target_id=project.id, target_name=project.name)
    return {
        "data": ProjectResponse.model_validate(project, from_attributes=True).model_dump(by_alias=True)
    }


@router.delete("/{project_id}")
async def delete_project(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    project = await project_service.get_project(session, project_id)
    await project_service.delete_project(session, project_id)
    await write_audit_log(session, action="delete", target_type="project", target_id=project_id, target_name=project.name)
    return MessageResponse(message="删除成功").model_dump()


# ---- 项目成员管理 ----


@router.get("/{project_id}/members")
async def list_members(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    members = await member_service.list_members(session, project_id)
    return {
        "data": [
            MemberResponse(**m).model_dump(by_alias=True) for m in members
        ]
    }


@router.post("/{project_id}/members", status_code=HTTP_201_CREATED)
async def add_member(
    project_id: uuid.UUID,
    body: AddMemberRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin")),
):
    member = await member_service.add_member(session, project_id, body)
    await write_audit_log(session, action="add_member", target_type="project", target_id=project_id,
                          target_name=member.get("username"), changes={"role": body.role})
    return {"data": MemberResponse(**member).model_dump(by_alias=True)}


@router.put("/{project_id}/members/{user_id}")
async def update_member_role(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    body: UpdateMemberRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin")),
):
    member = await member_service.update_member_role(session, project_id, user_id, body)
    await write_audit_log(session, action="update_member", target_type="project", target_id=project_id,
                          target_name=member.get("username"), changes={"role": body.role})
    return {"data": MemberResponse(**member).model_dump(by_alias=True)}


@router.delete("/{project_id}/members/{user_id}")
async def remove_member(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin")),
):
    await member_service.remove_member(session, project_id, user_id)
    await write_audit_log(session, action="remove_member", target_type="project", target_id=project_id,
                          target_name=str(user_id))
    return MessageResponse(message="移除成功").model_dump()
