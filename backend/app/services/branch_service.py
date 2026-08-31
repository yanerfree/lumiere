import uuid

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.audit import audit_log
from app.core.db_errors import reraise_integrity_error
from app.models.case import Case
from app.models.project import Branch
from app.schemas.branch import CreateBranchRequest, UpdateBranchRequest


async def list_branches(session: AsyncSession, project_id: uuid.UUID) -> list[Branch]:
    """查询项目下所有分支配置，活跃的在前。"""
    stmt = (
        select(Branch)
        .where(Branch.project_id == project_id)
        .order_by(Branch.status, Branch.created_at)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_cases_by_branch(
    session: AsyncSession, project_id: uuid.UUID
) -> dict[uuid.UUID, int]:
    """项目下各分支的存活用例数（不含回收站）。没有用例的分支不出现在返回里。

    为什么这个数要跟分支列表一起给出去：**分支选错了，和「这个项目一条数据都没有」
    在页面上长得一模一样** —— 都是「暂无目录」+「暂无用例」+「共 0 条」，
    页面上没有任何一处告诉你别的分支有货。而分支列表是按 (status, created_at) 排的，
    建项目时自动铺的 default 永远排第一，版本升级的活儿却是在后来开的分支上干的，
    所以「第一次进项目就落在空分支上」是默认结局，不是巧合。
    2026-08-31 就这么报过来一次：UAG 的 41 条用例全在 v2.2.0，站在 default 上看是 0。

    一次 group by 查完，不按分支逐条打。用例数只是个提示量，不参与任何判定。
    """
    stmt = (
        select(Case.branch_id, func.count(Case.id))
        .join(Branch, Branch.id == Case.branch_id)
        .where(Branch.project_id == project_id, Case.deleted_at.is_(None))
        .group_by(Case.branch_id)
    )
    result = await session.execute(stmt)
    return {row[0]: row[1] for row in result.all()}


@audit_log(action="create", target_type="branch")
async def create_branch(
    session: AsyncSession, project_id: uuid.UUID, data: CreateBranchRequest
) -> Branch:
    """创建分支配置。名称项目内唯一。"""
    branch = Branch(
        project_id=project_id,
        name=data.name,
        description=data.description,
        branch=data.branch,
    )
    session.add(branch)
    try:
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        # 别把 CHECK 违反也报成「已存在」—— 那会让人对着一个没有重名的列表查半天
        reraise_integrity_error(
            e,
            conflict_code="BRANCH_NAME_EXISTS",
            conflict_message="分支配置名称已存在",
            check_messages={
                "ck_branch_name_format": (
                    "BRANCH_NAME_INVALID",
                    "分支名称格式非法：仅支持字母、数字、下划线、连字符、点号"
                    "（点号不能开头、结尾或连用）",
                ),
            },
        )
    await session.refresh(branch)
    return branch


async def _get_branch(session: AsyncSession, branch_id: uuid.UUID, project_id: uuid.UUID = None) -> Branch:
    """根据 ID 获取分支，不存在抛 404。可选校验 project_id 归属。"""
    result = await session.execute(select(Branch).where(Branch.id == branch_id))
    branch = result.scalar_one_or_none()
    if branch is None:
        raise NotFoundError(code="BRANCH_NOT_FOUND", message="分支配置不存在")
    if project_id and branch.project_id != project_id:
        raise NotFoundError(code="BRANCH_NOT_FOUND", message="分支不属于该项目")
    return branch


async def update_branch(
    session: AsyncSession, branch_id: uuid.UUID, data: UpdateBranchRequest, project_id: uuid.UUID = None
) -> Branch:
    """更新分支配置（name 不可改）。"""
    branch = await _get_branch(session, branch_id, project_id)
    if data.description is not None:
        branch.description = data.description
    if data.branch is not None:
        branch.branch = data.branch
    await session.flush()
    await session.refresh(branch)
    return branch


async def _count_active_branches(session: AsyncSession, project_id: uuid.UUID) -> int:
    """统计项目中活跃分支数量。"""
    result = await session.execute(
        select(func.count()).where(
            Branch.project_id == project_id,
            Branch.status == "active",
        )
    )
    return result.scalar_one()


@audit_log(action="archive", target_type="branch")
async def archive_branch(session: AsyncSession, branch_id: uuid.UUID, project_id: uuid.UUID = None) -> Branch:
    """归档分支配置。最后一个活跃分支不可归档。"""
    branch = await _get_branch(session, branch_id, project_id)
    if branch.status == "archived":
        raise ValidationError(code="ALREADY_ARCHIVED", message="分支已处于归档状态")
    count = await _count_active_branches(session, branch.project_id)
    if count <= 1:
        raise ValidationError(code="LAST_ACTIVE_BRANCH", message="项目至少保留一个活跃分支配置")
    branch.status = "archived"
    await session.flush()
    await session.refresh(branch)
    return branch


@audit_log(action="activate", target_type="branch")
async def activate_branch(session: AsyncSession, branch_id: uuid.UUID, project_id: uuid.UUID = None) -> Branch:
    """恢复已归档的分支配置。"""
    branch = await _get_branch(session, branch_id, project_id)
    if branch.status == "active":
        raise ValidationError(code="ALREADY_ACTIVE", message="分支已处于活跃状态")
    branch.status = "active"
    await session.flush()
    await session.refresh(branch)
    return branch
