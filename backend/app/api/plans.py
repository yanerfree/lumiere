"""测试计划 API"""
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.status import HTTP_201_CREATED

from app.core.exceptions import AppError, ValidationError
from app.core.audit import write_audit_log
from app.deps.auth import get_current_user, require_project_role
from app.deps.db import get_db
from app.engine.task_status import set_task_status
from app.engine.tasks.execution import run_automated_execution
from app.models.user import User
from app.schemas.common import BaseSchema, MessageResponse
from app.schemas.plan import CreatePlanRequest, UpdatePlanRequest, PlanListItem, PlanResponse
from app.services import environment_service, execution_service, export_service, plan_service, report_service

router = APIRouter(prefix="/api/projects/{project_id}/plans", tags=["plans"])


# ---- API ----

@router.post("", status_code=HTTP_201_CREATED)
async def create_plan(
    project_id: uuid.UUID,
    body: CreatePlanRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """创建测试计划"""
    # environment_id 从 body 来，路径上的两道校验都管不到它 ——
    # 不验的话本项目的计划能挂上别的项目的环境，执行时注入别人的 BASE_URL/账号
    await environment_service.assert_env_in_project(session, body.environment_id, project_id)
    plan = await plan_service.create_plan(
        session, project_id, current_user.id,
        name=body.name, plan_type=body.plan_type, test_type=body.test_type,
        case_ids=body.case_ids, environment_id=body.environment_id,
        channel_id=body.channel_id, retry_count=body.retry_count,
        circuit_breaker=body.circuit_breaker, branch_id=body.branch_id,
    )
    return {"data": PlanResponse.model_validate(plan, from_attributes=True).model_dump(by_alias=True)}


@router.get("")
async def list_plans(
    project_id: uuid.UUID,
    status: str | None = Query(default=None),
    branch_id: uuid.UUID | None = Query(default=None, alias="branchId"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """计划列表"""
    items, total = await plan_service.list_plans(session, project_id, status, page, page_size, branch_id)
    return {
        "data": [
            PlanListItem(
                id=it["plan"].id, name=it["plan"].name,
                plan_type=it["plan"].plan_type, test_type=it["plan"].test_type,
                status=it["plan"].status, case_count=it["case_count"],
                environment_name=it.get("environment_name"),
                created_at=it["plan"].created_at,
            ).model_dump(by_alias=True)
            for it in items
        ],
        "pagination": {"page": page, "pageSize": page_size, "total": total},
    }


@router.get("/{plan_id}")
async def get_plan(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """计划详情"""
    plan = await plan_service.get_plan(session, plan_id)
    plan_cases = await plan_service.get_plan_cases(session, plan_id)
    data = PlanResponse.model_validate(plan, from_attributes=True).model_dump(by_alias=True)
    data["caseIds"] = [str(pc.case_id) for pc in plan_cases]
    # 补充环境和渠道名称
    if plan.environment_id:
        from app.models.environment import Environment
        from sqlalchemy import select as sa_select
        env = (await session.execute(sa_select(Environment).where(Environment.id == plan.environment_id))).scalar_one_or_none()
        data["environmentName"] = env.name if env else None
    if plan.channel_id:
        from app.models.environment import NotificationChannel
        ch = (await session.execute(sa_select(NotificationChannel).where(NotificationChannel.id == plan.channel_id))).scalar_one_or_none()
        data["channelName"] = ch.name if ch else None
    return {"data": data}


@router.put("/{plan_id}")
async def update_plan(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    body: UpdatePlanRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """更新测试计划（仅 draft 状态）"""
    plan = await plan_service.update_plan(session, plan_id, body)
    plan_cases = await plan_service.get_plan_cases(session, plan_id)
    data = PlanResponse.model_validate(plan, from_attributes=True).model_dump(by_alias=True)
    data["caseIds"] = [str(pc.case_id) for pc in plan_cases]
    return {"data": data}


@router.post("/{plan_id}/archive")
async def archive_plan(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin")),
):
    """归档计划"""
    plan = await plan_service.archive_plan(session, plan_id)
    return {"data": PlanResponse.model_validate(plan, from_attributes=True).model_dump(by_alias=True)}


@router.post("/{plan_id}/unarchive")
async def unarchive_plan(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin")),
):
    """取消归档 —— 归档不能是单向门。

    跑过的回「已完成」，没跑过的回「草稿」。
    """
    plan = await plan_service.unarchive_plan(session, plan_id)
    await write_audit_log(session, action="unarchive", target_type="plan",
                          target_id=plan_id, target_name=plan.name)
    return {"data": PlanResponse.model_validate(plan, from_attributes=True).model_dump(by_alias=True)}


@router.delete("/{plan_id}")
async def delete_plan(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin")),
):
    """删除计划（执行中不可删除）"""
    await plan_service.delete_plan(session, plan_id)
    return MessageResponse(message="删除成功").model_dump()


@router.post("/{plan_id}/reopen")
async def reopen_plan(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """重新打开已完成的计划（已有结果保留，可继续补充录入）"""
    plan = await plan_service.get_plan(session, plan_id)

    # 权限：仅 project_admin 或计划创建者
    if current_user.role != "admin" and current_user.id != plan.created_by:
        from app.deps.auth import require_project_role as _rpr
        # 非创建者需要 project_admin 权限
        from sqlalchemy import select
        from app.models.project import ProjectMember
        result = await session.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == current_user.id,
                ProjectMember.role == "project_admin",
            )
        )
        if result.scalar_one_or_none() is None:
            from app.core.exceptions import ForbiddenError
            raise ForbiddenError(code="REOPEN_DENIED", message="仅项目管理员或计划创建者可重新打开")

    plan = await plan_service.reopen_plan(session, plan_id)
    return {"data": PlanResponse.model_validate(plan, from_attributes=True).model_dump(by_alias=True)}


# ---- 执行相关 Schema ----

class ManualRecordRequest(BaseSchema):
    scenario_id: uuid.UUID
    status: Literal["passed", "failed"]
    remark: str | None = None
    duration_ms: int | None = None

class ScenarioResponse(BaseSchema):
    id: uuid.UUID
    case_id: uuid.UUID | None
    case_code: str | None
    scenario_name: str
    status: str
    execution_type: str
    duration_ms: int | None
    error_summary: str | None = None
    execution_log: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    remark: str | None
    sort_order: int
    script_ref_file: str | None = None
    script_ref_func: str | None = None


def _scenario_payload(s) -> dict:
    """报告场景 → 前端。带上最后一次执行的三层失败判断，报告页才有得下钻。

    branchId/runId 是前端调 /scripts/runs/{id}/analysis|confirm 的入口，
    缺一个人就只能回用例详情页绕一圈。
    """
    run = getattr(s, "_run", None)
    return {
        **ScenarioResponse.model_validate(s, from_attributes=True).model_dump(by_alias=True),
        "scriptRefFile": getattr(s, "_script_ref_file", None),
        "scriptRefFunc": getattr(s, "_script_ref_func", None),
        "caseSteps": getattr(s, "_case_steps", None),
        "preconditions": getattr(s, "_preconditions", None),
        "expectedResult": getattr(s, "_expected_result", None),
        "branchId": str(getattr(s, "_branch_id", None)) if getattr(s, "_branch_id", None) else None,
        "runId": str(run.id) if run else None,
        "phenomenon": run.failure_phenomenon if run else None,
        "ccAnalysis": run.cc_analysis if run else None,
        "confirmedCause": run.confirmed_cause if run else None,
    }


class ReportResponse(BaseSchema):
    id: uuid.UUID
    plan_id: uuid.UUID | None = None
    report_type: str | None = "plan"
    report_name: str | None = None
    executed_at: datetime
    completed_at: datetime | None
    total_scenarios: int
    passed: int
    failed: int
    error: int
    skipped: int
    flaky: int = 0
    xfail: int = 0
    pass_rate: float | None
    manual_count: int


# ---- 执行 API ----

@router.post("/{plan_id}/execute")
async def execute_plan(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """启动计划执行。

    手动计划: 创建报告 + scenarios，直接返回。
    自动化计划: 创建报告后通过 BackgroundTasks 异步执行，返回 taskId 供轮询。
    """
    report = await execution_service.start_execution(session, plan_id, current_user.id)
    plan = await plan_service.get_plan(session, plan_id)
    await write_audit_log(session, action="execute", target_type="plan", target_id=plan_id, target_name=plan.name)

    if plan.plan_type == "automated":
        await session.commit()
        task_id = uuid.uuid4().hex
        await set_task_status(task_id, "pending", message="自动化执行任务已提交...")
        background_tasks.add_task(
            run_automated_execution, task_id, str(plan_id), str(report.id), str(current_user.id),
        )
        return {
            "data": {
                **ReportResponse.model_validate(report, from_attributes=True).model_dump(by_alias=True),
                "taskId": task_id,
            }
        }

    return {"data": ReportResponse.model_validate(report, from_attributes=True).model_dump(by_alias=True)}


@router.post("/{plan_id}/manual-record")
async def manual_record(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    body: ManualRecordRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """录入单条手动测试结果"""
    # 先获取报告 ID
    data = await execution_service.get_report_with_scenarios(session, plan_id)
    if data is None:
        from app.core.exceptions import NotFoundError
        raise NotFoundError(code="NO_REPORT", message="计划尚未执行")
    scenario, all_done = await execution_service.record_manual_result(
        session, data["report"].id, body.scenario_id, body.status, body.remark, body.duration_ms
    )
    return {"data": {
        **ScenarioResponse.model_validate(scenario, from_attributes=True).model_dump(by_alias=True),
        "allDone": all_done,
    }}


@router.post("/{plan_id}/complete")
async def complete_plan(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """确认完成执行"""
    plan = await execution_service.complete_execution(session, plan_id)
    await write_audit_log(session, action="complete", target_type="plan", target_id=plan_id, target_name=plan.name)
    return {"data": PlanResponse.model_validate(plan, from_attributes=True).model_dump(by_alias=True)}


@router.get("/{plan_id}/executions")
async def list_plan_executions(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """计划的执行历史列表"""
    from sqlalchemy import select as sa_select
    from app.models.report import TestReport
    result = await session.execute(
        sa_select(TestReport).where(TestReport.plan_id == plan_id).order_by(TestReport.created_at.desc())
    )
    reports = result.scalars().all()
    return {
        "data": [
            {
                "id": str(r.id),
                "executedAt": r.executed_at.isoformat() if r.executed_at else None,
                "completedAt": r.completed_at.isoformat() if r.completed_at else None,
                "totalScenarios": r.total_scenarios,
                "passed": r.passed,
                "failed": r.failed,
                "error": r.error,
                "skipped": r.skipped,
                "flaky": r.flaky,
                "xfail": r.xfail,
                "passRate": float(r.pass_rate) if r.pass_rate is not None else None,
                "totalDurationMs": r.total_duration_ms,
            }
            for r in reports
        ]
    }


@router.get("/{plan_id}/results")
async def get_results(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """获取计划执行结果（报告 + 场景列表）"""
    data = await execution_service.get_report_with_scenarios(session, plan_id)
    if data is None:
        return {"data": None}
    return {
        "data": {
            "report": ReportResponse.model_validate(data["report"], from_attributes=True).model_dump(by_alias=True),
            "scenarios": [_scenario_payload(s) for s in data["scenarios"]],
        }
    }


@router.get("/{plan_id}/report")
async def get_report_dashboard(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """报告仪表盘（L1 汇总 + L2 模块分组）"""
    data = await report_service.get_report_dashboard(session, plan_id)
    if data is None:
        return {"data": None}
    return {"data": data}


@router.get("/{plan_id}/export/excel")
async def export_excel(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """导出 Excel 报告"""
    from fastapi.responses import StreamingResponse
    output = await export_service.export_excel(session, plan_id)
    if output is None:
        from app.core.exceptions import NotFoundError
        raise NotFoundError(code="NO_REPORT", message="报告不存在")
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=report-{plan_id}.xlsx"},
    )


@router.get("/{plan_id}/scenarios/{scenario_id}/steps")
async def get_scenario_steps(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    scenario_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """获取场景的步骤列表（L3 下钻）"""
    from sqlalchemy import select
    from app.models.report import TestReportStep
    result = await session.execute(
        select(TestReportStep)
        .where(TestReportStep.scenario_id == scenario_id)
        .order_by(TestReportStep.sort_order)
    )
    steps = result.scalars().all()
    return {
        "data": [
            {
                "id": str(s.id),
                "stepName": s.step_name,
                "stepLabel": s.step_label,
                "stepPhase": s.step_phase,
                "httpMethod": s.http_method,
                "url": s.url,
                "status": s.status,
                "statusCode": s.status_code,
                "durationMs": s.duration_ms,
                "sortOrder": s.sort_order,
                "errorSummary": s.error_summary,
                "requestData": s.request_data,
                "responseData": s.response_data,
                "assertions": s.assertions,
            }
            for s in steps
        ]
    }


# ---- Story 4.5: 处理人分配 ----

class AssignRequest(BaseSchema):
    scenario_ids: list[uuid.UUID]
    assignee_id: uuid.UUID


@router.put("/{plan_id}/assign")
async def assign_scenarios(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    body: AssignRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """批量分配处理人"""
    from sqlalchemy import select, update
    from app.models.plan import PlanCase
    from app.models.report import TestReportScenario

    for sid in body.scenario_ids:
        # 更新 PlanCase 的 assignee
        scenario = (await session.execute(
            select(TestReportScenario).where(TestReportScenario.id == sid)
        )).scalar_one_or_none()
        if scenario and scenario.case_id:
            await session.execute(
                update(PlanCase).where(
                    PlanCase.plan_id == plan_id,
                    PlanCase.case_id == scenario.case_id,
                ).values(assignee_id=body.assignee_id)
            )
    await session.flush()
    return MessageResponse(message="分配成功").model_dump()


# ---- Story 4.6: 暂停/恢复/终止 ----

@router.post("/{plan_id}/pause")
async def pause_plan(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """手动暂停执行中的计划"""
    plan = await plan_service.get_plan(session, plan_id)
    if plan.status != "executing":
        raise ValidationError(code="INVALID_STATUS", message=f"当前状态「{plan.status}」不可暂停")
    plan.status = "paused"
    await session.flush()
    await write_audit_log(session, action="pause", target_type="plan", target_id=plan_id, target_name=plan.name)
    await session.refresh(plan)
    return {"data": PlanResponse.model_validate(plan, from_attributes=True).model_dump(by_alias=True)}


@router.post("/{plan_id}/resume")
async def resume_plan(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """恢复已暂停的计划"""
    plan = await plan_service.get_plan(session, plan_id)
    if plan.status != "paused":
        raise ValidationError(code="INVALID_STATUS", message=f"当前状态「{plan.status}」不可恢复")
    plan.status = "executing"
    await session.flush()
    await write_audit_log(session, action="resume", target_type="plan", target_id=plan_id, target_name=plan.name)
    await session.refresh(plan)
    return {"data": PlanResponse.model_validate(plan, from_attributes=True).model_dump(by_alias=True)}


@router.post("/{plan_id}/abort")
async def abort_plan(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """终止计划 — 未执行用例标记为 skipped，状态改为 completed"""
    from sqlalchemy import select, update
    from app.models.report import TestReportScenario, TestReport

    plan = await plan_service.get_plan(session, plan_id)
    if plan.status not in ("executing", "paused"):
        raise ValidationError(code="INVALID_STATUS", message=f"当前状态「{plan.status}」不可终止")

    # 获取报告
    report = (await session.execute(
        select(TestReport).where(TestReport.plan_id == plan_id).order_by(TestReport.created_at.desc())
    )).scalars().first()

    if report:
        # 未执行的 scenario 标记为 skipped
        await session.execute(
            update(TestReportScenario).where(
                TestReportScenario.report_id == report.id,
                TestReportScenario.status == "pending",
            ).values(status="skipped", error_summary="计划已终止")
        )

    plan.status = "completed"
    plan.completed_at = datetime.now(timezone.utc)
    await session.flush()
    await write_audit_log(session, action="abort", target_type="plan", target_id=plan_id, target_name=plan.name)

    # 汇总报告
    if report:
        await execution_service.complete_execution(session, plan_id)

    # refresh 必须无条件做，不能挂在 `if report` 里：`updated_at` 是库侧 onupdate，
    # flush 之后它处于 expired 状态，pydantic 序列化时去读会触发一次惰性加载，
    # 而那已经不在 async 上下文里 —— 直接 500（MissingGreenlet），事务回滚，
    # 用户点了「终止」看到"服务内部错误"，计划纹丝不动。
    # 没有报告的计划就会走进这条路（比如报告被删过），实测复现过。
    await session.refresh(plan)
    return {"data": PlanResponse.model_validate(plan, from_attributes=True).model_dump(by_alias=True)}


# ---- 报告列表（项目级） ----

reports_router = APIRouter(prefix="/api/projects/{project_id}/reports", tags=["reports"])


async def _exec_kind_by_report(
    session: AsyncSession, report_ids: list, plan_test_type: dict,
    report_kind: dict | None = None,
) -> dict:
    """这份报告到底跑的是 UI 脚本还是接口场景。返回 report_id -> 'ui'|'api'|'mixed'|None。

    三级回退，越靠前越是"真的发生过什么"：
    1. `script_runs.script_type` —— 执行留下的痕迹，最可信
    2. 计划自己声明的 `test_type`（e2e 即 UI）—— 老计划报告在 record_run 接进来
       之前生成，没有第 1 条
    3. `report_type == 'api_test'` —— 这条通道只跑接口场景，是定义使然不是猜测

    三条都不命中就返回 None，页面显示「—」。宁可留白也别编一个。
    """
    if not report_ids:
        return {}
    from sqlalchemy import select

    from app.models.report import TestReportScenario
    from app.models.script import ScriptRun

    rows = (await session.execute(
        select(TestReportScenario.report_id, ScriptRun.script_type)
        .join(ScriptRun, ScriptRun.report_scenario_id == TestReportScenario.id)
        .where(TestReportScenario.report_id.in_(report_ids))
        .distinct()
    )).all()

    kinds: dict = {}
    for rid, stype in rows:
        prev = kinds.get(rid)
        kinds[rid] = stype if prev in (None, stype) else "mixed"

    for rid in report_ids:
        if kinds.get(rid):
            continue
        tt = plan_test_type.get(rid)
        kinds[rid] = (
            "ui" if tt == "e2e"
            else "api" if tt == "api"
            else "api" if (report_kind or {}).get(rid) == "api_test"
            else None
        )
    return kinds


# 一份报告"完了没有"此前只看 completed_at 有没有值，没有就一律显示「执行中」。
# 于是库里那条 8-12 的报告在页面上"执行了 12 天" —— 它其实是**手动计划在等人录结果**
# （plan.status = pending_manual，两条场景都是 manual/pending）。
# 「在跑」和「在等人」是两件完全不同的事：前者只能等，后者是**待办**，
# 而混成一个词的代价是这条待办永远不会被认领。
_STALE_AFTER_MIN = 30      # 自动化场景超过这么久还没动静，就不是"在跑"了


async def _report_status_map(session: AsyncSession, reports: list) -> dict:
    """report_id -> {status, pendingManual, pendingAuto}。只算没有 completed_at 的那些。"""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, select

    from app.models.report import TestReportScenario

    open_ids = [r.id for r in reports if not r.completed_at]
    if not open_ids:
        return {}

    rows = (await session.execute(
        select(TestReportScenario.report_id, TestReportScenario.execution_type,
               TestReportScenario.status, func.count())
        .where(TestReportScenario.report_id.in_(open_ids),
               TestReportScenario.status.in_(["pending", "running"]))
        .group_by(TestReportScenario.report_id, TestReportScenario.execution_type,
                  TestReportScenario.status)
    )).all()

    agg: dict = {}
    for rid, exec_type, st, n in rows:
        a = agg.setdefault(rid, {"manual": 0, "auto": 0, "running": 0})
        if st == "running":
            a["running"] += n
        if exec_type == "manual":
            a["manual"] += n
        else:
            a["auto"] += n

    now = datetime.now(timezone.utc)
    out = {}
    for r in reports:
        if r.completed_at:
            continue
        a = agg.get(r.id, {"manual": 0, "auto": 0, "running": 0})
        fresh = r.executed_at and (now - r.executed_at) < timedelta(minutes=_STALE_AFTER_MIN)
        if a["running"] or (a["auto"] and fresh):
            status = "running"
        elif a["manual"] and not a["auto"]:
            status = "pending_manual"
        elif a["auto"] or a["manual"]:
            status = "stalled"          # 该自己跑完的没跑完，也没人在跑了
        else:
            # 一条待办都不剩却没盖 completed_at —— 结果是全的，只是收尾那一步没落
            status = "done_unsealed"
        out[r.id] = {"status": status, "pendingManual": a["manual"], "pendingAuto": a["auto"]}
    return out


@reports_router.get("")
async def list_reports(
    project_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    report_type: str | None = Query(default=None, alias="reportType"),
    branch_id: uuid.UUID | None = Query(default=None, alias="branchId"),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """项目下所有执行报告列表"""
    from app.models.report import TestReport
    from app.models.plan import Plan
    from app.models.environment import Environment
    from sqlalchemy import func, select, or_

    base = (
        select(TestReport, Plan.name.label("plan_name"), Plan.plan_type, Plan.test_type, Environment.name.label("env_name"))
        .outerjoin(Plan, Plan.id == TestReport.plan_id)
        .outerjoin(Environment, Environment.id == TestReport.environment_id)
        .where(or_(Plan.project_id == project_id, TestReport.project_id == project_id))
        .order_by(TestReport.created_at.desc())
    )

    if report_type:
        base = base.where(TestReport.report_type == report_type)
    if branch_id:
        base = base.where(or_(TestReport.branch_id == branch_id, TestReport.branch_id == None))

    count_result = await session.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar_one()

    result = await session.execute(base.offset((page - 1) * page_size).limit(page_size))
    rows = result.all()

    exec_kinds = await _exec_kind_by_report(
        session,
        [r[0].id for r in rows],
        {r[0].id: r[3] for r in rows},
        {r[0].id: r[0].report_type for r in rows},
    )

    status_map = await _report_status_map(session, [r[0] for r in rows])

    data = []
    for report, plan_name, plan_type, test_type, env_name in rows:
        st = status_map.get(report.id)
        data.append({
            # 「已完成 / 在跑 / 等人录 / 断了」四种，前端别再自己按 completedAt 猜
            "status": "completed" if report.completed_at else (st or {}).get("status", "running"),
            "pendingManual": (st or {}).get("pendingManual", 0),
            "pendingAuto": (st or {}).get("pendingAuto", 0),
            # 「类型」说的是从哪个入口发起的，不是跑的什么 —— 两件事此前挤在一列里，
            # 结果报告页清一色「接口测试」，用例页清一色 UI，看着像在自相矛盾。
            "execKind": exec_kinds.get(report.id),
            "id": str(report.id),
            "planId": str(report.plan_id) if report.plan_id else None,
            "planName": plan_name,
            "planType": plan_type,
            "testType": test_type,
            "reportType": report.report_type,
            "reportName": report.report_name or plan_name or "未命名报告",
            "environmentName": env_name,
            "executedAt": report.executed_at.isoformat() if report.executed_at else None,
            "completedAt": report.completed_at.isoformat() if report.completed_at else None,
            "totalScenarios": report.total_scenarios,
            "passed": report.passed,
            "failed": report.failed,
            "error": report.error,
            "skipped": report.skipped,
            "flaky": report.flaky,
            "xfail": report.xfail,
            "passRate": float(report.pass_rate) if report.pass_rate is not None else None,
            "totalDurationMs": report.total_duration_ms,
        })

    return {"data": data, "pagination": {"page": page, "pageSize": page_size, "total": total}}


class AdhocExecuteRequest(BaseModel):
    case_ids: list[uuid.UUID] = Field(alias="caseIds")
    branch_id: uuid.UUID = Field(alias="branchId")
    type: str = Field(pattern="^(api|ui)$")
    env_id: uuid.UUID = Field(alias="envId")
    title: str | None = None

    model_config = {"populate_by_name": True}


@reports_router.post("/execute-adhoc")
async def execute_adhoc(
    project_id: uuid.UUID,
    body: AdhocExecuteRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """批量执行选中用例（不走测试计划），直接生成报告。"""
    from sqlalchemy import select
    from app.models.report import TestReport, TestReportScenario
    from app.models.case import Case
    from app.engine.tasks.adhoc_execution import run_adhoc_execution

    if not body.case_ids:
        raise ValidationError(code="NO_CASES", message="请至少选择一条用例")

    case_id_list = [str(c) for c in body.case_ids]
    cases = (await session.execute(
        select(Case).where(Case.id.in_(body.case_ids))
    )).scalars().all()

    # **废弃的不许进计划。** 进了就会算进那份正式回归报告的通过率分母，
    # 而它已经是"决定不再测"的场景了。静默剔掉不行 —— 报错说清是哪几条，
    # 让人自己把它们从选择里去掉（多半是拿旧的勾选列表建的计划）。
    deprecated = [c.case_code for c in cases if c.lifecycle_status == "deprecated"]
    if deprecated:
        raise ValidationError(
            code="DEPRECATED_CASES",
            message=f"这些用例已废弃，不能进计划：{'、'.join(deprecated)}。"
                    f"废弃的用例不算进通过率分母 —— 要重新测就先在详情页撤销废弃。",
        )

    if not cases:
        raise ValidationError(code="NO_CASES", message="未找到有效用例")

    # 预检：统计可执行 / 跳过
    executable_count = 0
    skipped_count = 0
    for case in cases:
        # 有产物就算能跑 —— 不再要求维度 == executable（见 execution_service 那段说明）
        from app.engine.tasks.adhoc_execution import _has_new_style_script
        _has_artifact = await _has_new_style_script(session, case.id, body.type) is not None
        has_script = _has_artifact or (bool(case.script_ref_file) and case.automation_status == "automated")
        if has_script:
            executable_count += 1
        else:
            skipped_count += 1

    # env_id 从 body 来（这条是「批量执行」，不走计划），路径上的两道校验管不到它。
    # 不验的话能拿别的项目的环境去跑本项目的用例 —— 注进去的是别人的 BASE_URL 和账号。
    await environment_service.assert_env_in_project(session, body.env_id, project_id)

    now = datetime.now(timezone.utc)
    # 名字给人看，时间就得是人所在时区的。用 UTC 拼名字，列表右边按本地渲染
    # createdAt，同一行会显示「批量执行 · 08-10 08:17」和「16:17:06」，差 8 小时。
    # astimezone() 不传参 = 转服务器本地时区；executed_at 仍然存 UTC，只有名字换算。
    report_title = body.title or f"批量执行 · {now.astimezone().strftime('%m-%d %H:%M')}"

    report = TestReport(
        plan_id=None,
        report_type="adhoc",
        report_name=report_title,
        project_id=project_id,
        branch_id=body.branch_id,
        environment_id=body.env_id,
        executed_by=user.id,
        executed_at=now,
        total_scenarios=len(cases),
        skipped=skipped_count,
    )
    session.add(report)
    await session.flush()

    for i, case in enumerate(cases):
        # 有产物就算能跑 —— 不再要求维度 == executable（见 execution_service 那段说明）
        from app.engine.tasks.adhoc_execution import _has_new_style_script
        _has_artifact = await _has_new_style_script(session, case.id, body.type) is not None
        has_script = _has_artifact or (bool(case.script_ref_file) and case.automation_status == "automated")
        scenario = TestReportScenario(
            report_id=report.id,
            case_id=case.id,
            case_code=case.case_code,
            scenario_name=case.title,
            status="skipped" if not has_script else "pending",
            execution_type="automated",
            sort_order=i,
        )
        if not has_script:
            scenario.error_summary = "无可执行脚本"
        session.add(scenario)
    await session.commit()

    task_id = f"adhoc_{report.id}"
    background_tasks.add_task(
        run_adhoc_execution,
        task_id=task_id,
        report_id=str(report.id),
        case_ids=case_id_list,
        env_id=str(body.env_id),
        test_type=body.type,
        project_id=str(project_id),
        branch_id=str(body.branch_id),
        user_id=str(user.id),
    )

    return {
        "data": {
            "reportId": str(report.id),
            "taskId": task_id,
            "total": len(cases),
            "executable": executable_count,
            "skipped": skipped_count,
        }
    }


@reports_router.delete("/{report_id}")
async def delete_report(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """删除单条测试报告及其关联的场景和步骤"""
    from sqlalchemy import select as sa_select, delete as sa_delete
    from app.models.report import TestReport, TestReportScenario, TestReportStep

    report = (await session.execute(
        sa_select(TestReport).where(TestReport.id == report_id)
    )).scalar_one_or_none()
    if report is None:
        from app.core.exceptions import NotFoundError
        raise NotFoundError(code="REPORT_NOT_FOUND", message="报告不存在")

    scenario_ids = (await session.execute(
        sa_select(TestReportScenario.id).where(TestReportScenario.report_id == report_id)
    )).scalars().all()
    if scenario_ids:
        await session.execute(
            sa_delete(TestReportStep).where(TestReportStep.scenario_id.in_(scenario_ids))
        )
    await session.execute(
        sa_delete(TestReportScenario).where(TestReportScenario.report_id == report_id)
    )
    await session.delete(report)
    await session.flush()
    from app.models.plan import Plan
    plan_row = (await session.execute(sa_select(Plan.name).where(Plan.id == report.plan_id))).scalar_one_or_none()
    await write_audit_log(session, action="delete", target_type="report", target_id=report_id, target_name=plan_row or str(report_id))
    return MessageResponse(message="删除成功").model_dump()


@reports_router.get("/{report_id}/dashboard")
async def get_report_dashboard_by_id(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """按报告 ID 获取仪表盘数据"""
    data = await report_service.get_report_dashboard(session, report_id=report_id)
    if data is None:
        return {"data": None}
    return {"data": data}


@reports_router.get("/{report_id}/results")
async def get_results_by_report_id(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """按报告 ID 获取场景列表"""
    data = await execution_service.get_report_with_scenarios(session, report_id=report_id)
    if data is None:
        return {"data": None}
    return {
        "data": {
            "report": ReportResponse.model_validate(data["report"], from_attributes=True).model_dump(by_alias=True),
            "scenarios": [_scenario_payload(s) for s in data["scenarios"]],
        }
    }


@reports_router.get("/{report_id}/export/excel")
async def export_excel_by_report_id(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """按报告 ID 导出 Excel"""
    from fastapi.responses import StreamingResponse
    output = await export_service.export_excel(session, report_id=report_id)
    if output is None:
        from app.core.exceptions import NotFoundError
        raise NotFoundError(code="NO_REPORT", message="报告不存在")
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=report-{report_id}.xlsx"},
    )


@reports_router.get("/{report_id}/scenarios/{scenario_id}/steps")
async def get_scenario_steps_by_report_id(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    scenario_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """按报告 ID 获取场景步骤"""
    from sqlalchemy import select as sa_select
    from app.models.report import TestReportStep
    result = await session.execute(
        sa_select(TestReportStep)
        .where(TestReportStep.scenario_id == scenario_id)
        .order_by(TestReportStep.sort_order)
    )
    steps = result.scalars().all()
    return {
        "data": [
            {
                "id": str(s.id),
                "stepName": s.step_name,
                "stepLabel": s.step_label,
                "stepPhase": s.step_phase,
                "httpMethod": s.http_method,
                "url": s.url,
                "status": s.status,
                "statusCode": s.status_code,
                "durationMs": s.duration_ms,
                "sortOrder": s.sort_order,
                "errorSummary": s.error_summary,
                "requestData": s.request_data,
                "responseData": s.response_data,
                "assertions": s.assertions,
            }
            for s in steps
        ]
    }
