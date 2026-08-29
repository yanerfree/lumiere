"""权限点依赖工厂 —— core/permissions 的 FastAPI 接线层。

core/permissions.py 是**纯**的（无 FastAPI、无 DB），只管「角色 → 权限点集合」。
本模块把它接到请求上：查项目成员、解析权限点、拦不够权限的请求。

与 deps/auth.py 的 require_project_role 并存、口径一致（admin 直通、非成员 403）：
- require_project_role 现有端点继续用（按角色名挡）；
- require_permission 是**声明式**替代（按权限点挡），新端点/重构端点用它，
  好处是「这个动作需要什么权限」写在路由上、和前端菜单/助手能力面读同一份 core 映射。

本轮不强制迁移存量端点（那是 outward-facing 的大改），只提供工具 + /api/me/permissions
消费它，把「唯一事实源」这件事坐实。
"""
import uuid
from collections.abc import Callable

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import permissions as perms
from app.core.exceptions import ForbiddenError
from app.deps.auth import get_current_user
from app.deps.db import get_db
from app.models.project import ProjectMember
from app.models.user import User


async def load_project_role(
    session: AsyncSession, user: User, project_id: uuid.UUID
) -> str | None:
    """取用户在某项目的项目角色；非成员返回 None。

    admin 不在此判定 —— admin 全权、无需项目角色，调用方应先短路。
    """
    result = await session.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
        )
    )
    member = result.scalar_one_or_none()
    return member.role if member is not None else None


async def resolve_for_request(
    session: AsyncSession, user: User, project_id: uuid.UUID | None
) -> frozenset[str]:
    """当前用户在（可选）项目语境下的权限点集合。admin 直接全集。"""
    if user.role == "admin":
        return perms.ALL_PERMISSIONS
    project_role = None
    if project_id is not None:
        project_role = await load_project_role(session, user, project_id)
    return perms.resolve_permissions(user.role, project_role)


def require_permission(*required: str) -> Callable:
    """项目级权限点检查依赖工厂。路径必须含 {project_id}。

    用法: Depends(require_permission(perms.P_CASE_WRITE))
    规则：admin 直通；否则须持有**全部** required 权限点，缺一即 403。
    """
    async def _check(
        project_id: uuid.UUID,
        current_user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> User:
        if current_user.role == "admin":
            return current_user
        project_role = await load_project_role(session, current_user, project_id)
        if project_role is None:
            raise ForbiddenError(code="NOT_PROJECT_MEMBER", message="未绑定到该项目")
        held = perms.resolve_permissions(current_user.role, project_role)
        if not set(required).issubset(held):
            raise ForbiddenError(code="PERMISSION_DENIED", message="无权限执行此操作")
        return current_user
    return _check


def require_system_permission(*required: str) -> Callable:
    """系统级权限点检查依赖工厂（不依赖项目语境）。

    用法: Depends(require_system_permission(perms.P_SYS_CHANNEL_MANAGE))
    规则：admin 直通；否则按系统角色权限点判定。
    """
    async def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role == "admin":
            return current_user
        # 走 resolve_permissions 而不是 system_permissions —— 后者不过封顶，
        # 游客会在这里拿到未削减的系统权限（本模型最该避免的「自报一套、强制另一套」）。
        held = perms.resolve_permissions(current_user.role)
        if not set(required).issubset(held):
            raise ForbiddenError(code="PERMISSION_DENIED", message="无权限执行此操作")
        return current_user
    return _check
