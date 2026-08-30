import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.status import HTTP_201_CREATED

from app.core.audit import write_audit_log
from app.deps.auth import require_role
from app.deps.db import get_db
from app.models.user import User
from app.schemas.common import MessageResponse
from app.schemas.user import (
    CreateUserRequest,
    UpdateUserRequest,
    UserResponse,
    UserWithProjectsResponse,
)
from app.services import user_service

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("")
async def list_users(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    users = await user_service.list_users(session)
    project_map = await user_service.list_user_project_map(session)
    return {
        "data": [
            UserWithProjectsResponse.model_validate(
                {
                    **UserResponse.model_validate(u, from_attributes=True).model_dump(),
                    # 系统 admin 绕过项目成员绑定（deps/auth.py 的 require_project_role），
                    # 所以这里给的是**成员表里真有的那几行**，不是"他能进哪些项目"。
                    # 两者对 admin 不是一回事，前端负责把这个差别说清楚。
                    "projects": project_map.get(u.id, []),
                }
            ).model_dump(by_alias=True)
            for u in users
        ]
    }


@router.post("", status_code=HTTP_201_CREATED)
async def create_user(
    body: CreateUserRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    user = await user_service.create_user(session, body)
    await write_audit_log(session, action="create", target_type="user", target_id=user.id, target_name=user.username)
    return {
        "data": UserResponse.model_validate(user, from_attributes=True).model_dump(by_alias=True)
    }


@router.put("/{user_id}")
async def update_user(
    user_id: uuid.UUID,
    body: UpdateUserRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    user = await user_service.update_user(session, user_id, body)
    await write_audit_log(session, action="update", target_type="user", target_id=user.id, target_name=user.username)
    return {
        "data": UserResponse.model_validate(user, from_attributes=True).model_dump(by_alias=True)
    }


@router.delete("/{user_id}")
async def delete_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    user = await user_service.get_user(session, user_id)
    await user_service.delete_user(session, user_id)
    await write_audit_log(session, action="delete", target_type="user", target_id=user_id, target_name=user.username)
    return MessageResponse(message="删除成功").model_dump()
