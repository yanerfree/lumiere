"""测试计划服务"""
import uuid

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.audit import audit_log
from app.models.plan import Plan, PlanCase


@audit_log(action="create", target_type="plan")
async def create_plan(
    session: AsyncSession,
    project_id: uuid.UUID,
    created_by: uuid.UUID,
    name: str,
    plan_type: str,
    test_type: str,
    case_ids: list[uuid.UUID],
    environment_id: uuid.UUID | None = None,
    channel_id: uuid.UUID | None = None,
    retry_count: int = 0,
    circuit_breaker: dict | None = None,
    branch_id: uuid.UUID | None = None,
) -> Plan:
    """创建测试计划 + 关联用例。"""
    if not case_ids:
        raise ValidationError(code="NO_CASES", message="用例集不能为空")
    if len(case_ids) > 1000:
        raise ValidationError(code="TOO_MANY_CASES", message="单个计划最多 1000 条用例")

    plan = Plan(
        project_id=project_id,
        branch_id=branch_id,
        name=name,
        plan_type=plan_type,
        test_type=test_type,
        environment_id=environment_id,
        channel_id=channel_id,
        retry_count=retry_count,
        circuit_breaker=circuit_breaker or {"consecutive": 5, "rate": 50},
        created_by=created_by,
    )
    session.add(plan)
    await session.flush()

    for i, cid in enumerate(case_ids):
        session.add(PlanCase(plan_id=plan.id, case_id=cid, sort_order=i))
    await session.flush()
    await session.refresh(plan)
    return plan


async def list_plans(
    session: AsyncSession,
    project_id: uuid.UUID,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    branch_id: uuid.UUID | None = None,
) -> tuple[list[dict], int]:
    """查询计划列表（含用例数）。branch_id 给定时过滤该分支 + 历史数据。"""
    base = select(Plan).where(Plan.project_id == project_id)
    if status:
        base = base.where(Plan.status == status)
    if branch_id:
        base = base.where((Plan.branch_id == branch_id) | (Plan.branch_id == None))

    count_result = await session.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar_one()

    stmt = base.order_by(Plan.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await session.execute(stmt)
    plans = result.scalars().all()

    # 查每个计划的用例数
    items = []
    for p in plans:
        case_count_r = await session.execute(
            select(func.count()).where(PlanCase.plan_id == p.id)
        )
        env_name = None
        if p.environment_id:
            from app.models.environment import Environment
            env_r = await session.execute(select(Environment.name).where(Environment.id == p.environment_id))
            env_name = env_r.scalar_one_or_none()
        items.append({
            "plan": p,
            "case_count": case_count_r.scalar_one(),
            "environment_name": env_name,
        })
    return items, total


async def get_plan(session: AsyncSession, plan_id: uuid.UUID) -> Plan:
    result = await session.execute(select(Plan).where(Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if plan is None:
        raise NotFoundError(code="PLAN_NOT_FOUND", message="计划不存在")
    return plan


@audit_log(action="update", target_type="plan")
async def update_plan(session: AsyncSession, plan_id: uuid.UUID, data) -> Plan:
    """更新测试计划（执行中不可修改）。"""
    plan = await get_plan(session, plan_id)
    if plan.status == "executing":
        raise ValidationError(code="INVALID_STATUS", message="执行中的计划不可编辑")

    if data.name is not None:
        plan.name = data.name
    if data.environment_id is not None:
        plan.environment_id = data.environment_id
    if data.channel_id is not None:
        plan.channel_id = data.channel_id
    if data.retry_count is not None:
        plan.retry_count = data.retry_count
    if data.circuit_breaker is not None:
        plan.circuit_breaker = data.circuit_breaker

    if data.case_ids is not None:
        if not data.case_ids:
            raise ValidationError(code="NO_CASES", message="用例集不能为空")
        if len(data.case_ids) > 1000:
            raise ValidationError(code="TOO_MANY_CASES", message="单个计划最多 1000 条用例")
        await session.execute(delete(PlanCase).where(PlanCase.plan_id == plan_id))
        for i, cid in enumerate(data.case_ids):
            session.add(PlanCase(plan_id=plan.id, case_id=cid, sort_order=i))

    await session.flush()
    await session.refresh(plan)
    return plan


async def get_plan_cases(session: AsyncSession, plan_id: uuid.UUID) -> list[PlanCase]:
    result = await session.execute(
        select(PlanCase).where(PlanCase.plan_id == plan_id).order_by(PlanCase.sort_order)
    )
    return list(result.scalars().all())


@audit_log(action="archive", target_type="plan")
async def archive_plan(session: AsyncSession, plan_id: uuid.UUID) -> Plan:
    plan = await get_plan(session, plan_id)
    if plan.status not in ("completed", "draft"):
        raise ValidationError(code="INVALID_STATUS", message=f"当前状态「{plan.status}」不可归档")
    plan.status = "archived"
    await session.flush()
    await session.refresh(plan)
    return plan


@audit_log(action="delete", target_type="plan")
async def delete_plan(session: AsyncSession, plan_id: uuid.UUID) -> None:
    plan = await get_plan(session, plan_id)
    if plan.status == "executing":
        raise ValidationError(code="INVALID_STATUS", message="执行中的计划不可删除")
    await session.execute(delete(PlanCase).where(PlanCase.plan_id == plan_id))
    await session.delete(plan)
    await session.flush()


@audit_log(action="reopen", target_type="plan")
async def reopen_plan(session: AsyncSession, plan_id: uuid.UUID) -> Plan:
    """重新打开已完成的计划，状态改为 executing，已有结果保留。"""
    plan = await get_plan(session, plan_id)
    if plan.status != "completed":
        raise ValidationError(code="INVALID_STATUS", message=f"当前状态「{plan.status}」不可重新打开，仅已完成的计划可重新打开")
    plan.status = "executing"
    await session.flush()
    await session.refresh(plan)
    return plan


async def unarchive_plan(session: AsyncSession, plan_id: uuid.UUID) -> Plan:
    """取消归档 —— 归档必须有回头路。

    `archive_plan` 收 draft / completed 两种状态，而 `reopen_plan` 只认 completed，
    所以归档一旦点下去就**回不来了**（archived 不在任何一个入口的允许列表里）。
    页面上给了「已归档」这个筛选页签却没有出口，等于一扇单向门。

    回到哪个状态不需要额外存字段：**跑过就回 completed，没跑过就回 draft**
    —— 有没有报告是可查的事实。
    """
    from sqlalchemy import func, select

    from app.models.report import TestReport

    plan = await get_plan(session, plan_id)
    if plan.status != "archived":
        raise ValidationError(code="INVALID_STATUS", message=f"当前状态「{plan.status}」不是已归档，不用取消归档")

    ran = (await session.execute(
        select(func.count()).select_from(TestReport).where(TestReport.plan_id == plan_id)
    )).scalar() or 0
    plan.status = "completed" if ran else "draft"
    await session.flush()
    await session.refresh(plan)
    return plan
