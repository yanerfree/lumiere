import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.permissions import canonical_project_role
from app.core.security import hash_password
from app.core.audit import audit_log
from app.models.project import Project, ProjectMember
from app.models.user import User
from app.schemas.user import CreateUserRequest, UpdateUserRequest


async def list_users(session: AsyncSession) -> list[User]:
    """查询所有用户，按 created_at 降序。"""
    stmt = select(User).order_by(User.created_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_user_project_map(session: AsyncSession) -> dict[uuid.UUID, list[dict]]:
    """user_id -> 该用户加入的项目列表 [{id, name, role}]，按项目名排序。

    一条 JOIN 查全部人的成员关系，在内存里分组 —— **别改成按用户逐个查**：
    用户列表默认一页 20 行、最大 500 行，逐个查就是 500 次往返。
    没有成员关系的用户在返回的 dict 里**没有键**（不是空列表），调用方自己 `.get(id, [])`。
    """
    stmt = (
        select(ProjectMember.user_id, Project.id, Project.name, ProjectMember.role)
        .join(Project, Project.id == ProjectMember.project_id)
        .order_by(Project.name)
    )
    result = await session.execute(stmt)
    mapping: dict[uuid.UUID, list[dict]] = {}
    for user_id, project_id, project_name, role in result.all():
        mapping.setdefault(user_id, []).append({
            # 归一成规范名再出门：库里存量行可能还是旧名（project_admin/developer），
            # 前端拿旧名去查标签表会查不到，静默显示成空白。
            "id": project_id, "name": project_name,
            "role": canonical_project_role(role) or role,
        })
    return mapping


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


async def count_active_admins(
    session: AsyncSession, exclude_id: uuid.UUID | None = None
) -> int:
    """还剩几个**启用中**的系统管理员（可排除某一个，用来预演"删掉他之后"）。

    只数 is_active 的：停用的管理员登不进来，把他算进"还有人管"等于自欺 ——
    真出事时那个账号一样救不了场，而它的存在会让最后一道删除保护静默失效。
    """
    stmt = select(func.count()).select_from(User).where(
        User.role == "admin", User.is_active.is_(True)
    )
    if exclude_id is not None:
        stmt = stmt.where(User.id != exclude_id)
    return int((await session.execute(stmt)).scalar_one())


@audit_log(action="delete", target_type="user")
async def delete_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    """删除用户。"""
    user = await get_user(session, user_id)
    await session.delete(user)
    await session.flush()
