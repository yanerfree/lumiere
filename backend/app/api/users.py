import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.status import HTTP_201_CREATED

from app.core.audit import write_audit_log
from app.core.exceptions import ForbiddenError
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

# 内置管理员账号名 —— 装机种子建的那一个。
BUILTIN_ADMIN = "admin"


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
    current_user: User = Depends(require_role("admin")),
):
    """删除用户。

    2026-08-31 起**其他系统管理员也能删**（前端 UserManagement.jsx 同步放开）。
    此前整个 admin 角色都删不掉，结果是这一档只进不出 —— 历史管理员账号只能先降级再删，
    而中间那一步没人记得做。

    放开之后补两道**真正在挡的**，外加一道兜底：
      1. 内置 admin 不能删 —— 按账号名判。改了名这道就失效，所以它只是第一道；
      2. 不能删自己 —— 删完当场掉线，是这次放宽才够得着的新坑（以前管理员互相删不了，
         自然也删不到自己）。
      3. 不能删掉最后一个启用中的管理员。

    **第 3 道今天走这个接口到不了** —— 别以为是它在兜底，说清楚它为什么还留着：
    调用方必过 require_role("admin")，而停用账号连认证都过不了（deps/auth.py:39），
    所以调用方一定是个**启用中的管理员**；第 2 道又保证他删的不是自己 ——
    于是删完之后他自己还在，启用管理员数永远 ≥ 1。
    留着它是因为它是这三条里**唯一不依赖账号名、也不依赖"谁在操作"**的判据：
    哪天第 2 道被放宽（比如加个批量删、或者支持代他人操作），它就立刻变成实际在挡的那道。
    它的语义由 tests/api/users/test_delete_user_guards.py 在 service 层直接盯着
    （count_active_admins 只数启用中的管理员），不是靠这个接口的用例。

    顺带一个**这次没动**的锁死口子：用户列表里的启用/停用开关不挡自己，
    管理员可以把自己停用 —— 那条路一样能走到"没人能管理系统"，但它属于 PUT 不属于这里。
    """
    user = await user_service.get_user(session, user_id)

    if user.username == BUILTIN_ADMIN:
        raise ForbiddenError(
            code="BUILTIN_ADMIN_PROTECTED",
            message="内置管理员账号 admin 不可删除",
        )
    if user.id == current_user.id:
        raise ForbiddenError(
            code="CANNOT_DELETE_SELF",
            message="不能删除当前登录的账号",
        )
    if user.role == "admin" and user.is_active:
        others = await user_service.count_active_admins(session, exclude_id=user.id)
        if others == 0:
            raise ForbiddenError(
                code="LAST_ADMIN_PROTECTED",
                message="这是最后一个启用中的系统管理员，删除后将无人可管理系统",
            )

    await user_service.delete_user(session, user_id)
    await write_audit_log(session, action="delete", target_type="user", target_id=user_id, target_name=user.username)
    return MessageResponse(message="删除成功").model_dump()
