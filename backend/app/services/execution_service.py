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
# 模块级导入：此前只在 _will_run_automated 里 import，而 start_execution 也用它。
# 计划里只要有一条不是 executable 的用例就会走到那个分支 → NameError 把整个
# 计划执行打死。实测 lum_run_plan 直接崩「name 'flaky_service' is not defined」。
from app.services import flaky_service


async def _will_run_automated(session: AsyncSession, plan, case) -> bool:
    """这条用例在这次计划里会不会被自动执行。

    **判据是「有没有产物」，不是「状态到没到」。**
    原来要求该维度到某个「已发布」态，而那个态只有人在列表上勾选点「发布到回归」
    才给 —— 于是回归池永远是空的（实测 257 条里只有 1 条）。而且审核也被夹在这条路上：
    人不点，脚本就永远进不了回归，哪怕它已经跑绿几十次。
    现在只看 scripts 表有没有该维度的活跃脚本（接口维度看有没有绑定的编排场景）——
    **有产物就能跑，审核不挡回归**。审没审在 review_status 那个独立标签上，只做提示。
    Flaky 用例被执行器跳过，这里也不算自动。
    """
    if plan.plan_type != "automated" or flaky_service.should_skip(case):
        return False
    from app.engine.tasks.adhoc_execution import _has_new_style_script

    if await _has_new_style_script(session, case.id, plan.test_type):
        return True
    return case.automation_status == "automated" and bool(case.script_ref_file)


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

    # 谁会被自动跑，判据必须和执行器（engine/tasks/execution.py:204-211）**完全一致**。
    # 此前这里只认旧字段 automation_status=='automated'，而执行器认的是
    # 「该维度 executable + scripts 表有活跃脚本」——CC 回推的用例两个字段一个都不沾
    # 旧的，于是报告开头把它们全算成"手动"，进度条和手动数从一开始就是错的。
    auto_flags = {}
    for _, case in plan_cases:
        auto_flags[case.id] = await _will_run_automated(session, plan, case)

    automated_count = sum(1 for v in auto_flags.values() if v)
    manual_count = len(plan_cases) - automated_count

    report = TestReport(
        plan_id=plan_id,
        # project_id 此前漏了 —— 库里 21/89 条报告是 NULL，导致按项目查报告
        # （lum_list_reports、以及任何项目维度的报告列表）一条都查不到计划报告。
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
        if auto_flags.get(case.id):
            exec_type = "automated"
            status = "pending"
        elif plan.plan_type == "automated" and flaky_service.should_skip(case):
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
    report.flaky = status_counts.get("flaky", 0)
    report.xfail = status_counts.get("xfail", 0)

    duration_result = await session.execute(
        select(func.sum(TestReportScenario.duration_ms))
        .where(TestReportScenario.report_id == report_id)
    )
    report.total_duration_ms = duration_result.scalar_one() or 0

    # 规范口径：flaky 进分母（它跑了，只是不可信），skipped / xfail 不进
    denominator = report.passed + report.failed + report.error + report.flaky
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


async def recompute_report_stats(session: AsyncSession, report: TestReport) -> dict[str, int]:
    """按 test_report_scenarios 的实际状态重算一份报告的汇总，返回各状态计数。

    抽出来是因为**崩溃恢复也要算这一份**：执行崩了以后如果只把行改成 error、
    不重算汇总，报告页看到的是 0 通过 0 失败、通过率空白 —— 和"这次啥也没跑"
    长得一模一样，用户分不出是空报告还是崩了的报告。
    抽成一个函数而不是复制一份，是为了不让两处口径日后漂移。
    """
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
    report.flaky = status_counts.get("flaky", 0)
    report.xfail = status_counts.get("xfail", 0)

    duration_result = await session.execute(
        select(func.sum(TestReportScenario.duration_ms))
        .where(TestReportScenario.report_id == report.id)
    )
    report.total_duration_ms = duration_result.scalar_one() or 0

    # 规范口径：flaky 进分母（它跑了，只是不可信），skipped / xfail 不进
    denominator = report.passed + report.failed + report.error + report.flaky
    if denominator > 0:
        report.pass_rate = Decimal(str(round(report.passed / denominator * 100, 2)))
    return status_counts


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

    status_counts = await recompute_report_stats(session, report)

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
            Case.branch_id,
        )
        .outerjoin(Case, TestReportScenario.case_id == Case.id)
        .where(TestReportScenario.report_id == report.id)
        .order_by(TestReportScenario.sort_order)
    )
    rows = scenarios_result.all()
    scenarios = []
    for (scenario, script_file, script_func, case_steps,
         preconditions, expected_result, branch_id) in rows:
        scenario._script_ref_file = script_file
        scenario._script_ref_func = script_func
        scenario._case_steps = case_steps
        scenario._preconditions = preconditions
        scenario._expected_result = expected_result
        scenario._branch_id = branch_id
        scenarios.append(scenario)

    # 挂上每条场景最后一次执行的三层失败判断。
    # QA 看失败是在报告页，不是用例详情页 —— 现象/CC 归因/人工确认只做在用例详情里
    # 等于没做。一次查完再按场景分组，不走 N+1。
    await _attach_triage(session, scenarios)

    return {"report": report, "scenarios": scenarios}


async def _attach_triage(session: AsyncSession, scenarios: list) -> None:
    """给场景挂 _run（最后一次 attempt 的 ScriptRun）。没有执行记录的场景挂 None。"""
    from app.models.script import ScriptRun

    ids = [s.id for s in scenarios]
    for s in scenarios:
        s._run = None
    if not ids:
        return
    runs = (await session.execute(
        select(ScriptRun)
        .where(ScriptRun.report_scenario_id.in_(ids))
        .order_by(ScriptRun.report_scenario_id, ScriptRun.attempt.asc())
    )).scalars().all()
    by_scenario = {}
    for r in runs:  # 升序遍历，后写的覆盖前面的 → 留下 attempt 最大的那次
        by_scenario[r.report_scenario_id] = r
    for s in scenarios:
        s._run = by_scenario.get(s.id)
