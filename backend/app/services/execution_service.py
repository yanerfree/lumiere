"""计划执行服务 — 启动执行、手动录入、确认完成"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.models.case import Case
from app.models.plan import Plan, PlanCase
from app.models.report import TestReport, TestReportScenario


async def start_execution(
    session: AsyncSession, plan_id: uuid.UUID, executed_by: uuid.UUID
) -> TestReport:
    """
    启动计划执行 — 创建 report + scenarios，计划状态改为 executing。

    自动化计划: scenarios 按 automation_status 设置 execution_type (automated/manual)
    手动计划: 所有 scenarios 设为 manual
    """
    result = await session.execute(select(Plan).where(Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if plan is None:
        raise NotFoundError(code="PLAN_NOT_FOUND", message="计划不存在")
    if plan.status not in ("draft", "completed", "paused"):
        raise ValidationError(code="INVALID_STATUS", message=f"当前状态「{plan.status}」不可执行")

    pc_result = await session.execute(
        select(PlanCase, Case)
        .join(Case, Case.id == PlanCase.case_id)
        .where(PlanCase.plan_id == plan_id)
        .order_by(PlanCase.sort_order)
    )
    plan_cases = pc_result.all()
    if not plan_cases:
        raise ValidationError(code="NO_CASES", message="计划中没有用例")

    now = datetime.now(timezone.utc)

    # 统计自动化/手动用例数
    automated_count = 0
    manual_count = 0
    for _, case in plan_cases:
        if plan.plan_type == "automated" and case.automation_status == "automated" and not case.is_flaky:
            automated_count += 1
        else:
            manual_count += 1

    report = TestReport(
        plan_id=plan_id,
        # project_id 此前漏了 —— 库里 21/89 条报告是 NULL，导致按项目查报告
        # （tb_list_reports、以及任何项目维度的报告列表）一条都查不到计划报告。
        project_id=plan.project_id,
        branch_id=plan.branch_id,
        environment_id=plan.environment_id,
        executed_by=executed_by,
        executed_at=now,
        total_scenarios=len(plan_cases),
        manual_count=manual_count,
    )
    session.add(report)
    await session.flush()

    for i, (pc, case) in enumerate(plan_cases):
        # 确定 execution_type
        if plan.plan_type == "automated" and case.automation_status == "automated" and not case.is_flaky:
            exec_type = "automated"
            status = "pending"
        elif plan.plan_type == "automated" and case.is_flaky:
            exec_type = "manual"
            status = "skipped"  # Flaky 跳过
        else:
            exec_type = "manual"
            status = "pending"

        scenario = TestReportScenario(
            report_id=report.id,
            case_id=case.id,
            case_code=case.case_code,
            scenario_name=case.title,
            status=status,
            execution_type=exec_type,
            sort_order=i,
            error_summary="Flaky 用例已跳过" if status == "skipped" else None,
        )
        session.add(scenario)

    plan.status = "executing"
    plan.executed_at = now
    await session.flush()
    await session.refresh(report)
    return report


async def record_manual_result(
    session: AsyncSession,
    report_id: uuid.UUID,
    scenario_id: uuid.UUID,
    status: str,
    remark: str | None = None,
    duration_ms: int | None = None,
) -> tuple[TestReportScenario, bool]:
    """录入单条手动测试结果。每次录入后实时更新报告统计。返回 (scenario, all_done)。"""
    result = await session.execute(
        select(TestReportScenario).where(
            TestReportScenario.id == scenario_id,
            TestReportScenario.report_id == report_id,
        )
    )
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise NotFoundError(code="SCENARIO_NOT_FOUND", message="测试场景不存在")

    scenario.status = status
    scenario.remark = remark
    scenario.duration_ms = duration_ms
    await session.flush()
    await session.refresh(scenario)

    # 实时更新报告统计
    stats = await session.execute(
        select(TestReportScenario.status, func.count())
        .where(TestReportScenario.report_id == report_id)
        .group_by(TestReportScenario.status)
    )
    status_counts = {row[0]: row[1] for row in stats.all()}

    report = (await session.execute(
        select(TestReport).where(TestReport.id == report_id)
    )).scalar_one()
    report.passed = status_counts.get("passed", 0)
    report.failed = status_counts.get("failed", 0)
    report.error = status_counts.get("error", 0)
    report.skipped = status_counts.get("skipped", 0)

    duration_result = await session.execute(
        select(func.sum(TestReportScenario.duration_ms))
        .where(TestReportScenario.report_id == report_id)
    )
    report.total_duration_ms = duration_result.scalar_one() or 0

    denominator = report.passed + report.failed + report.error
    if denominator > 0:
        report.pass_rate = Decimal(str(round(report.passed / denominator * 100, 2)))

    # 检查是否全部录入完成
    remaining = status_counts.get("pending", 0)
    all_done = remaining == 0

    if all_done:
        await complete_execution(session, report.plan_id)
    else:
        await session.flush()

    return scenario, all_done


async def complete_execution(session: AsyncSession, plan_id: uuid.UUID) -> Plan:
    """确认完成 — 计算汇总，更新计划状态。

    如果还有 pending 的手动用例，状态改为 pending_manual 而非 completed。
    """
    result = await session.execute(select(Plan).where(Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if plan is None:
        raise NotFoundError(code="PLAN_NOT_FOUND", message="计划不存在")

    report_result = await session.execute(
        select(TestReport).where(TestReport.plan_id == plan_id).order_by(TestReport.created_at.desc())
    )
    report = report_result.scalars().first()
    if report is None:
        raise ValidationError(code="NO_REPORT", message="未找到执行报告")

    # 汇总
    stats = await session.execute(
        select(TestReportScenario.status, func.count())
        .where(TestReportScenario.report_id == report.id)
        .group_by(TestReportScenario.status)
    )
    status_counts = {row[0]: row[1] for row in stats.all()}

    report.passed = status_counts.get("passed", 0)
    report.failed = status_counts.get("failed", 0)
    report.error = status_counts.get("error", 0)
    report.skipped = status_counts.get("skipped", 0)

    duration_result = await session.execute(
        select(func.sum(TestReportScenario.duration_ms))
        .where(TestReportScenario.report_id == report.id)
    )
    report.total_duration_ms = duration_result.scalar_one() or 0

    denominator = report.passed + report.failed + report.error
    if denominator > 0:
        report.pass_rate = Decimal(str(round(report.passed / denominator * 100, 2)))

    # 检查是否有待手动录入的用例
    pending_manual = status_counts.get("pending", 0)
    if pending_manual > 0 and plan.plan_type == "automated":
        plan.status = "pending_manual"
    else:
        plan.status = "completed"
        plan.completed_at = datetime.now(timezone.utc)
        report.completed_at = plan.completed_at

    await session.flush()
    await session.refresh(plan)
    return plan


async def get_report_with_scenarios(
    session: AsyncSession,
    plan_id: uuid.UUID | None = None,
    report_id: uuid.UUID | None = None,
) -> dict | None:
    """获取执行报告 + 场景列表。指定 report_id 精确查，否则按 plan_id 取最新。"""
    if report_id:
        report_result = await session.execute(
            select(TestReport).where(TestReport.id == report_id)
        )
    elif plan_id:
        report_result = await session.execute(
            select(TestReport).where(TestReport.plan_id == plan_id).order_by(TestReport.created_at.desc())
        )
    else:
        return None
    report = report_result.scalars().first()
    if report is None:
        return None

    scenarios_result = await session.execute(
        select(
            TestReportScenario,
            Case.script_ref_file, Case.script_ref_func,
            Case.steps, Case.preconditions, Case.expected_result,
        )
        .outerjoin(Case, TestReportScenario.case_id == Case.id)
        .where(TestReportScenario.report_id == report.id)
        .order_by(TestReportScenario.sort_order)
    )
    rows = scenarios_result.all()
    scenarios = []
    for scenario, script_file, script_func, case_steps, preconditions, expected_result in rows:
        scenario._script_ref_file = script_file
        scenario._script_ref_func = script_func
        scenario._case_steps = case_steps
        scenario._preconditions = preconditions
        scenario._expected_result = expected_result
        scenarios.append(scenario)

    return {"report": report, "scenarios": scenarios}
