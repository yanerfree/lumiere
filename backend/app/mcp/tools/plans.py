"""MCP 工具 — 测试计划（B1）。

补这组的直接原因：`tb_get_report_summary` / `tb_get_failed_scenarios` 都要 `plan_id`，
而此前**没有任何工具能吐出 plan_id** —— 这两个工具实际上是死的。

边界（docs/cc-platform-loop-spec.md §0）：CC 能建计划、能按触发按钮、能读报告，
但**执行永远跑在平台的执行器上、结果永远由执行器写**。所以"谁按的按钮"不重要，
报告的可信度来自"它是平台跑出来的"。CC 不能写执行结果，也不能改用例的通过状态。
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case
from app.models.plan import Plan, PlanCase
from app.models.report import TestReport


async def list_plans(
    session: AsyncSession,
    project_id: str,
    status: str | None = None,
    limit: int = 20,
) -> dict:
    """列出项目下的测试计划（含用例数、最近一次报告 id）。"""
    pid = uuid.UUID(project_id)
    stmt = select(Plan).where(Plan.project_id == pid)
    if status:
        stmt = stmt.where(Plan.status == status)
    plans = (await session.execute(stmt.order_by(Plan.created_at.desc()).limit(limit))).scalars().all()

    out = []
    for p in plans:
        cnt = (await session.execute(
            select(func.count()).select_from(PlanCase).where(PlanCase.plan_id == p.id)
        )).scalar_one()
        last = (await session.execute(
            select(TestReport.id, TestReport.executed_at)
            .where(TestReport.plan_id == p.id)
            .order_by(TestReport.executed_at.desc()).limit(1)
        )).first()
        out.append({
            "planId": str(p.id),
            "name": p.name,
            "planType": p.plan_type,
            "testType": p.test_type,
            "status": p.status,
            "caseCount": cnt,
            "environmentId": str(p.environment_id) if p.environment_id else None,
            "retryCount": p.retry_count,
            "lastReportId": str(last[0]) if last else None,
            "lastExecutedAt": last[1].isoformat() if last and last[1] else None,
        })
    return {
        "total": len(out),
        "plans": out,
        "usage": "拿 planId 去调 tb_run_plan 触发执行，或 tb_get_report_summary / "
                 "tb_get_failed_scenarios 看结果（不传 reportId 就是最近一次）。",
    }


async def create_plan(
    session: AsyncSession,
    project_id: str,
    branch_id: str,
    name: str,
    case_ids: str,
    test_type: str = "e2e",
    environment_id: str | None = None,
    retry_count: int = 0,
) -> dict:
    """新建一个自动化测试计划。case_ids 用逗号分隔。

    只建计划，不执行 —— 触发要另调 tb_run_plan。
    """
    ids = [x.strip() for x in (case_ids or "").split(",") if x.strip()]
    if not ids:
        return {"error": "case_ids 不能为空（逗号分隔的用例 UUID）"}
    if test_type not in ("api", "e2e"):
        return {"error": "test_type 只能是 api 或 e2e"}

    from app.mcp.middleware import current_caller_user_id
    from app.services import script_run_service
    caller = await current_caller_user_id()
    creator = uuid.UUID(caller) if caller else await script_run_service.fallback_user_id(session)
    if not creator:
        return {"error": "找不到可用的创建人（plans.created_by 是必填）"}

    uids = [uuid.UUID(i) for i in ids]
    found = (await session.execute(select(Case.id).where(Case.id.in_(uids)))).scalars().all()
    missing = set(uids) - set(found)
    if missing:
        return {"error": f"这些用例不存在：{[str(m) for m in missing]}"}

    plan = Plan(
        project_id=uuid.UUID(project_id),
        branch_id=uuid.UUID(branch_id),
        name=name,
        plan_type="automated",
        test_type=test_type,
        environment_id=uuid.UUID(environment_id) if environment_id else None,
        retry_count=retry_count,
        status="draft",
        created_by=creator,
    )
    session.add(plan)
    await session.flush()
    for i, cid in enumerate(uids):
        session.add(PlanCase(plan_id=plan.id, case_id=cid, sort_order=i))
    await session.commit()

    note = ""
    if not environment_id:
        note = " ⚠ 没指定 environment_id —— 执行时拿不到 BASE_URL 和账号，脚本会挂。先调 tb_list_environments。"
    return {
        "status": "ok",
        "planId": str(plan.id),
        "name": plan.name,
        "caseCount": len(uids),
        "message": f"已建计划「{name}」（{len(uids)} 条用例）。调 tb_run_plan 触发执行。{note}",
    }


async def run_plan(session: AsyncSession, plan_id: str) -> dict:
    """触发计划执行（平台执行器跑，进通过率口径）。

    立刻返回 taskId，执行是异步的 —— 拿 reportId 去调 tb_get_report_summary 轮询。
    """
    from app.engine.task_status import set_task_status
    from app.engine.tasks.execution import run_automated_execution
    from app.mcp.middleware import current_caller_user_id
    from app.services import execution_service, script_run_service

    pid = uuid.UUID(plan_id)
    plan = (await session.execute(select(Plan).where(Plan.id == pid))).scalar_one_or_none()
    if not plan:
        return {"error": "计划不存在"}
    if plan.plan_type != "automated":
        return {"error": "只有自动化计划能通过本工具触发"}

    caller = await current_caller_user_id()
    uid = uuid.UUID(caller) if caller else await script_run_service.fallback_user_id(session)
    if not uid:
        return {"error": "找不到可用的执行人（executed_by 是必填）"}

    report = await execution_service.start_execution(session, pid, uid)
    await session.commit()

    task_id = uuid.uuid4().hex
    await set_task_status(task_id, "pending", message="自动化执行任务已提交（MCP 触发）")
    # 后台跑：MCP 请求本身立刻返回，别把连接挂在一次完整回归上
    import asyncio
    asyncio.create_task(  # noqa: RUF006
        run_automated_execution(task_id, str(pid), str(report.id), str(uid))
    )
    return {
        "status": "started",
        "taskId": task_id,
        "reportId": str(report.id),
        "planName": plan.name,
        "totalScenarios": report.total_scenarios,
        "message": "已在平台执行器上触发。执行是异步的，拿 reportId 调 "
                   "tb_get_report_summary 看进度和结果；失败明细用 tb_get_failed_scenarios。",
    }


async def list_reports(
    session: AsyncSession,
    project_id: str,
    plan_id: str | None = None,
    limit: int = 20,
) -> dict:
    """列出测试报告（可按计划过滤），返回 reportId + 通过率。"""
    pid = uuid.UUID(project_id)
    # 历史报告有 21/89 条 project_id 为 NULL（start_execution 曾漏填），
    # 所以除了直接匹配，还要经 plan 兜一层，否则老报告在这里全查不到。
    stmt = select(TestReport).where(
        or_(
            TestReport.project_id == pid,
            TestReport.plan_id.in_(select(Plan.id).where(Plan.project_id == pid)),
        )
    )
    if plan_id:
        stmt = stmt.where(TestReport.plan_id == uuid.UUID(plan_id))
    rows = (await session.execute(
        stmt.order_by(TestReport.executed_at.desc()).limit(limit)
    )).scalars().all()
    return {
        "total": len(rows),
        "reports": [{
            "reportId": str(r.id),
            "planId": str(r.plan_id) if r.plan_id else None,
            "reportName": r.report_name,
            "executedAt": r.executed_at.isoformat() if r.executed_at else None,
            "completedAt": r.completed_at.isoformat() if r.completed_at else None,
            "total": r.total_scenarios,
            "passed": r.passed,
            "failed": r.failed,
            "error": r.error,
            "skipped": r.skipped,
            "passRate": float(r.pass_rate) if r.pass_rate is not None else None,
        } for r in rows],
    }
