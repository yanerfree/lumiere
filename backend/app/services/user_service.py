import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import hash_password
from app.core.audit import audit_log
from app.models.user import User
from app.schemas.user import CreateUserRequest, UpdateUserRequest


async def list_users(session: AsyncSession) -> list[User]:
    """查询所有用户，按 created_at 降序。"""
    stmt = select(User).order_by(User.created_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


@audit_log(action="create", target_type="user")
async def create_user(session: AsyncSession, data: CreateUserRequest) -> User:
    """创建用户，密码 bcrypt 加密。用户名重复时抛 ConflictError。"""
    user = User(
        username=data.username,
        password=hash_password(data.password),
        role=data.role,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise ConflictError(code="USERNAME_EXISTS", message="用户名已存在")
    await session.refresh(user)
    return user


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    """根据 ID 查询用户，不存在时抛 NotFoundError。"""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise NotFoundError(code="USER_NOT_FOUND", message="用户不存在")
    return user


@audit_log(action="update", target_type="user")
async def update_user(session: AsyncSession, user_id: uuid.UUID, data: UpdateUserRequest) -> User:
    """更新用户的角色、激活状态，或由管理员重置密码。"""
    user = await get_user(session, user_id)
    if data.role is not None:
        user.role = data.role
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.password:
        from app.core.security import hash_password
        from app.services import auth_service
        user.password = hash_password(data.password)
        # 密码换了就得把旧登录全清掉，否则拿着旧 token 的人照样进得来
        await auth_service.revoke_all_user_tokens(session, user.id)
    await session.flush()
    await session.refresh(user)  # 重新加载 DB 侧更新的字段（如 updated_at）
    return user


@audit_log(action="delete", target_type="user")
async def delete_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    """删除用户。"""
    user = await get_user(session, user_id)
    await session.delete(user)
    await session.flush()
