"""MCP 工具 — 测试报告"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.report_service import get_report_dashboard
from app.services.execution_service import get_report_with_scenarios


async def _plan_of(session: AsyncSession, plan_id: str | None, report_id: str | None):
    """两个报告工具都只要「哪次执行」。**给了 report_id 就该够** ——
    lum_run_plan 返回的是 taskId + reportId，提示也写着"拿 reportId 来查"，
    而这两个工具此前只认 plan_id：照提示做一定报参数错，
    错误还指向调用方而不是那句提示。报告本来就挂在计划上，自己查出来。
    """
    if plan_id:
        return uuid.UUID(plan_id)
    if not report_id:
        raise ValueError("plan_id 和 report_id 至少给一个（lum_list_plans / lum_list_reports 拿）")
    from app.models.report import TestReport
    rep = await session.get(TestReport, uuid.UUID(report_id))
    if rep is None:
        raise ValueError(f"报告 {report_id} 不存在")
    if rep.plan_id is None:
        raise ValueError(f"报告 {report_id} 不属于任何计划（批量执行的报告），"
                         "这两个工具只看计划执行")
    return rep.plan_id


async def get_report_summary(session: AsyncSession, plan_id: str | None = None,
                             report_id: str | None = None) -> dict | None:
    """获取测试报告摘要（通过/失败/跳过/通过率 + 模块级分布）。plan_id 和 report_id 给一个就行。"""
    return await get_report_dashboard(
        session,
        plan_id=await _plan_of(session, plan_id, report_id),
        report_id=uuid.UUID(report_id) if report_id else None,
    )


async def get_failed_scenarios(session: AsyncSession, plan_id: str | None = None,
                               report_id: str | None = None) -> dict:
    """获取报告中失败的用例场景（含步骤、前置条件、错误信息）。plan_id 和 report_id 给一个就行。"""
    report = await get_report_with_scenarios(
        session,
        plan_id=await _plan_of(session, plan_id, report_id),
        report_id=uuid.UUID(report_id) if report_id else None,
    )
    if not report:
        return {"total": 0, "failed": [], "usage": "这个计划还没有报告，先 lum_run_plan 跑一次。"}

    # get_report_with_scenarios 返回的 scenarios 是 **ORM 对象**（额外挂了 _case_steps 等），
    # 不是 dict —— 原来这里按 dict 取值，一调就 'TestReportScenario' object has no attribute 'get'。
    # 这个工具此前拿不到 plan_id、根本没人调得到，所以这个崩一直没暴露。
    from sqlalchemy import select

    from app.models.script import ScriptRun

    failed = []
    for sc in report.get("scenarios", []):
        if sc.status not in ("failed", "error"):
            continue
        # 把这条场景对应的执行记录带上 —— CC 下一步要用 runId 去调
        # lum_get_ui_script_result 拿证据包、调 lum_submit_analysis 提交归因。
        # 没有它，CC 拿到一堆失败却不知道从哪读证据。
        run = (await session.execute(
            select(ScriptRun)
            .where(ScriptRun.report_scenario_id == sc.id)
            .order_by(ScriptRun.attempt.desc())
            .limit(1)
        )).scalar_one_or_none()
        failed.append({
            "scenarioId": str(sc.id),
            "caseId": str(sc.case_id) if sc.case_id else None,
            "caseCode": sc.case_code,
            "caseTitle": sc.scenario_name,
            "status": sc.status,
            "executionType": sc.execution_type,
            "remark": sc.remark,
            "durationMs": sc.duration_ms,
            "errorSummary": sc.error_summary,
            "runId": str(run.id) if run else None,
            "phenomenon": run.failure_phenomenon if run else None,
            "attempts": run.attempt if run else None,
            "steps": getattr(sc, "_case_steps", None) or [],
            "preconditions": getattr(sc, "_preconditions", None) or "",
            "expectedResult": getattr(sc, "_expected_result", None) or "",
            "scriptFile": getattr(sc, "_script_ref_file", None) or "",
        })
    return {
        "total": len(failed),
        "failed": failed,
        "usage": "拿 **caseId** 调 lum_get_ui_script_result 看证据包（截图路径/流量/现象初判）"
                 "—— 那个工具的入参是 case_id，不是 runId；**runId 是下一步 "
                 "lum_submit_analysis 用的**。判完再调 lum_submit_analysis 回填归因。",
    }
