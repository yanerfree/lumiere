import uuid

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core import permissions as perms
from app.core.audit import audit_log
from app.models.project import ProjectMember
from app.models.user import User
from app.schemas.project import AddMemberRequest, UpdateMemberRequest

# 「项目管理员」在库里可能是新名 manager，也可能是旧名 project_admin（兼容期并存）。
# LAST_ADMIN 保护必须两个名都认 —— 只比一个字面量的话，改名当天这个保护会**静默失效**：
# 计数返回 0，于是「最后一个管理员」变得可删/可降级，而没有任何报错提示这件事发生了。
_ADMIN_ROLE_NAMES: tuple[str, ...] = tuple(
    r for r in perms.PROJECT_ROLES_RECOGNIZED if perms.canonical_project_role(r) == "manager"
)


def _is_admin(role: str | None) -> bool:
    return perms.canonical_project_role(role) == "manager"


async def list_members(session: AsyncSession, project_id: uuid.UUID) -> list[dict]:
    """查询项目成员列表（含用户名）。返回 dict 列表供 MemberResponse 使用。"""
    stmt = (
        select(ProjectMember, User.username)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id)
        .order_by(ProjectMember.joined_at)
    )
    result = await session.execute(stmt)
    return [
        {
            "id": member.id,
            "user_id": member.user_id,
            "username": username,
            "role": member.role,
            "joined_at": member.joined_at,
        }
        for member, username in result.all()
    ]


@audit_log(action="create", target_type="project_member")
async def add_member(
    session: AsyncSession, project_id: uuid.UUID, data: AddMemberRequest
) -> dict:
    """添加成员到项目。重复绑定抛 409。"""
    # 先确认用户存在
    user_result = await session.execute(select(User).where(User.id == data.user_id))
    if user_result.scalar_one_or_none() is None:
        raise NotFoundError(code="USER_NOT_FOUND", message="用户不存在")

    member = ProjectMember(
        project_id=project_id,
        user_id=data.user_id,
        role=data.role,
    )
    session.add(member)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise ConflictError(code="MEMBER_EXISTS", message="该用户已是项目成员")

    await session.refresh(member)
    user = (await session.execute(select(User).where(User.id == data.user_id))).scalar_one()
    return {
        "id": member.id,
        "user_id": member.user_id,
        "username": user.username,
        "role": member.role,
        "joined_at": member.joined_at,
    }


async def _get_member(
    session: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID
) -> ProjectMember:
    """获取指定成员记录，不存在抛 404。"""
    result = await session.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise NotFoundError(code="MEMBER_NOT_FOUND", message="成员不存在")
    return member


async def _count_project_admins(session: AsyncSession, project_id: uuid.UUID) -> int:
    """统计项目里「项目管理员」的人数（新旧名都算，见 _ADMIN_ROLE_NAMES）。"""
    result = await session.execute(
        select(func.count()).where(
            ProjectMember.project_id == project_id,
            ProjectMember.role.in_(_ADMIN_ROLE_NAMES),
        )
    )
    return result.scalar_one()


@audit_log(action="update", target_type="project_member")
async def update_member_role(
    session: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID, data: UpdateMemberRequest
) -> dict:
    """修改成员角色。若降级最后一个项目管理员则 422。"""
    member = await _get_member(session, project_id, user_id)

    # 如果当前是项目管理员且要改为其他角色，检查是否是最后一个
    if _is_admin(member.role) and not _is_admin(data.role):
        count = await _count_project_admins(session, project_id)
        if count <= 1:
            raise ValidationError(
                code="LAST_ADMIN",
                message="项目至少需要一个管理员",
            )

    member.role = data.role
    await session.flush()
    await session.refresh(member)
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
    return {
        "id": member.id,
        "user_id": member.user_id,
        "username": user.username,
        "role": member.role,
        "joined_at": member.joined_at,
    }


@audit_log(action="delete", target_type="project_member")
async def remove_member(
    session: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """移除成员。若是最后一个项目管理员则 422。"""
    member = await _get_member(session, project_id, user_id)

    if _is_admin(member.role):
        count = await _count_project_admins(session, project_id)
        if count <= 1:
            raise ValidationError(
                code="LAST_ADMIN",
                message="项目至少需要一个管理员",
            )

    await session.delete(member)
    await session.flush()
