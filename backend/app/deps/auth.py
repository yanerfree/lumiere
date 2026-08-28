import uuid
from collections.abc import Callable

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.permissions import canonical_project_role
from app.core.security import decode_token
from app.core.audit import set_audit_context
from app.deps.db import get_db
from app.models.project import ProjectMember
from app.models.user import User


async def get_current_user(request: Request, session: AsyncSession = Depends(get_db)) -> User:
    """从 Authorization header 提取 token，解码后查库返回 User 对象。"""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise UnauthorizedError(code="MISSING_TOKEN", message="未提供认证凭据")

    token = auth_header[7:]  # 去掉 "Bearer " 前缀
    claims = decode_token(token, expected_type="access")  # 失败时内部抛 UnauthorizedError

    user_id = claims.get("sub")
    if not user_id:
        raise UnauthorizedError(code="INVALID_TOKEN", message="token 无效或已过期")

    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise UnauthorizedError(code="INVALID_TOKEN", message="token 无效或已过期")

    result = await session.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise UnauthorizedError(code="USER_DISABLED", message="用户已禁用")

    # 设置审计上下文（供 @audit_log 装饰器使用）
    trace_id = getattr(getattr(request, "state", None), "trace_id", None)
    set_audit_context(user_id=user.id, trace_id=trace_id)

    return user


def require_role(*roles: str) -> Callable:
    """系统级角色检查依赖工厂。用法: Depends(require_role("admin"))"""
    async def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise ForbiddenError(code="ROLE_DENIED", message="无权限执行此操作")
        return current_user
    return _check


def require_project_role(*roles: str) -> Callable:
    """项目级角色检查依赖工厂。

    用法: Depends(require_project_role("project_admin", "developer", "tester"))

    规则：
    - 系统 admin 直接通过（绕过项目级检查）
    - 非 admin 用户必须绑定到该项目，且项目角色在 roles 列表中
    - 路径中必须包含 {project_id} 参数

    角色匹配走**规范名**（canonical_project_role）：新旧两套名互认 —— 传旧名 "project_admin"
    的守卫也放行 role="manager" 的成员，反之亦然。兼容期里存量端点全用旧名、个别新端点已用
    新名，不归一就会同一成员两拨端点表现不一致（见 core/permissions.canonical_project_role）。
    """
    async def _check(
        project_id: uuid.UUID,
        current_user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> User:
        # 设置项目级审计上下文
        set_audit_context(user_id=current_user.id, project_id=project_id)

        # 系统 admin 绕过项目级检查
        if current_user.role == "admin":
            return current_user

        # 查询项目成员记录
        result = await session.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == current_user.id,
            )
        )
        member = result.scalar_one_or_none()

        if member is None:
            raise ForbiddenError(code="NOT_PROJECT_MEMBER", message="未绑定到该项目")

        allowed = {canonical_project_role(r) for r in roles}
        if canonical_project_role(member.role) not in allowed:
            raise ForbiddenError(code="PROJECT_ROLE_DENIED", message="无权限执行此操作")

        return current_user
    return _check
