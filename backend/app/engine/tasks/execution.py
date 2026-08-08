"""
自动化计划执行 — BackgroundTasks 后台任务。

流程: 加载计划 → 合并变量 → 创建沙箱 → 逐条执行 → 写入结果 → 清理沙箱 → 汇总
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.engine.task_status import set_task_status
from app.models.case import Case
from app.models.plan import Plan, PlanCase
from app.models.project import Branch, Project
from app.models.report import TestReport, TestReportScenario, TestReportStep

logger = logging.getLogger(__name__)

_execution_semaphore = asyncio.Semaphore(6)
_EXECUTION_TIMEOUT = 600


async def run_automated_execution(
    task_id: str,
    plan_id: str,
    report_id: str,
    user_id: str,
) -> dict:
    """后台任务: 执行自动化测试计划。"""
    async with _execution_semaphore:
        try:
            return await asyncio.wait_for(
                _run_execution_inner(task_id, plan_id, report_id, user_id),
                timeout=_EXECUTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            await set_task_status(task_id, "failed", message=f"执行超时（{_EXECUTION_TIMEOUT}s）")
            return {"error": "timeout"}


async def _run_execution_inner(task_id: str, plan_id: str, report_id: str, user_id: str) -> dict:
    await set_task_status(task_id, "running", message="正在准备执行环境...")

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            result = await _execute(session, task_id, plan_id, report_id, user_id)
            await session.commit()
            return result
    except Exception as e:
        logger.exception("Execution task failed")
        await set_task_status(task_id, "failed", message=f"执行异常: {str(e)[:200]}")
        return {"error": str(e)}
    finally:
        await engine.dispose()


async def _execute(
    session: AsyncSession, task_id: str, plan_id: str, report_id: str, user_id: str | None = None
) -> dict:
    """执行核心逻辑。"""
    from app.engine.executor import execute_single_case
    from app.engine.tasks.adhoc_execution import _has_new_style_script, _run_new_style_script
    from app.engine.sandbox import cleanup_sandbox, create_sandbox
    from app.services import script_run_service
    from app.services.environment_service import get_merged_variables

    pid = uuid.UUID(plan_id)
    rid = uuid.UUID(report_id)

    # 1. 加载 plan + project + branch
    plan = (await session.execute(select(Plan).where(Plan.id == pid))).scalar_one()
    project = (await session.execute(select(Project).where(Project.id == plan.project_id))).scalar_one()

    # 加载关联的用例（通过 PlanCase）
    plan_cases = (await session.execute(
        select(PlanCase, Case)
        .join(Case, PlanCase.case_id == Case.id)
        .where(PlanCase.plan_id == pid)
        .order_by(PlanCase.sort_order)
    )).all()

    if not plan_cases:
        await set_task_status(task_id, "completed", message="无用例可执行")
        return {"executed": 0}

    # 取第一个用例的 branch 来确定 bare repo 和 commit SHA
    first_case = plan_cases[0][1]
    branch = (await session.execute(select(Branch).where(Branch.id == first_case.branch_id))).scalar_one()

    # 2. 合并变量并写入快照
    env_vars = {}
    if plan.environment_id:
        merged = await get_merged_variables(session, plan.environment_id)
        env_vars = {v["key"]: v["value"] for v in merged}
        report = (await session.execute(select(TestReport).where(TestReport.id == rid))).scalar_one()
        report.variables_snapshot = merged
        await session.flush()

    # 3. 创建沙箱（仅旧式 script_ref_file 用例需要；AI 生成脚本走各自 tempdir，不依赖 git 脚本库）
    #
    # 必须先判"这批里有没有人真需要沙箱"：项目上留着一个过期的 script_base_path，
    # 就会让整个计划在这里 return error，哪怕计划里每一条都是新式脚本、压根用不着它。
    # 症状是全部 scenario 停在 pending，报告永远不 complete，日志里只有一句"不是有效的 Git 仓库"。
    needs_sandbox = any(c.script_ref_file for _, c in plan_cases)
    sandbox_dir = None
    if needs_sandbox and project.script_base_path and branch.last_commit_sha:
        bare_repo = Path(project.script_base_path) / ".repos" / "repo.git"
        execution_id = str(rid)
        sandbox_dir = Path(project.script_base_path) / ".sandboxes" / execution_id
        await set_task_status(task_id, "running", message="正在创建执行沙箱...")
        try:
            await anyio.to_thread.run_sync(
                lambda: create_sandbox(bare_repo, sandbox_dir, branch.last_commit_sha)
            )
        except Exception as e:
            await set_task_status(task_id, "failed", message=f"创建沙箱失败: {str(e)[:200]}")
            return {"error": str(e)}

    # 4. 逐条执行
    total = len(plan_cases)
    executed = 0
    passed = 0
    failed = 0
    consecutive_failures = 0

    # 熔断配置
    cb = plan.circuit_breaker or {}
    cb_consecutive = cb.get("consecutive", 5)
    cb_rate = cb.get("rate", 50)

    try:
        for i, (plan_case, case) in enumerate(plan_cases):
            # 熔断检查
            if consecutive_failures >= cb_consecutive:
                plan.status = "paused"
                await session.flush()
                from app.services.notification_service import notify_plan_result
                try:
                    await notify_plan_result(
                        session, plan, project_name=project.name, trigger="circuit_break",
                        total=total, passed=passed, failed=failed, skipped=total - executed,
                    )
                except Exception:
                    logger.exception("Failed to send circuit break notification")
                await set_task_status(
                    task_id, "completed",
                    message=f"熔断触发：连续失败 {consecutive_failures} 条，计划已暂停",
                    result={"executed": executed, "passed": passed, "failed": failed, "total": total, "paused": True},
                )
                return {"executed": executed, "paused": True, "reason": "consecutive_failures"}

            if executed > 0 and failed > 0:
                fail_rate = (failed / executed) * 100
                if fail_rate >= cb_rate and executed >= 5:
                    plan.status = "paused"
                    await session.flush()
                    from app.services.notification_service import notify_plan_result
                    try:
                        await notify_plan_result(
                            session, plan, project_name=project.name, trigger="circuit_break",
                            total=total, passed=passed, failed=failed, skipped=total - executed,
                        )
                    except Exception:
                        logger.exception("Failed to send circuit break notification")
                    await set_task_status(
                        task_id, "completed",
                        message=f"熔断触发：失败率 {fail_rate:.0f}% 超过阈值 {cb_rate}%，计划已暂停",
                        result={"executed": executed, "passed": passed, "failed": failed, "total": total, "paused": True},
                    )
                    return {"executed": executed, "paused": True, "reason": "fail_rate"}

            # 检查计划是否被手动暂停
            await session.refresh(plan)
            if plan.status == "paused":
                await set_task_status(
                    task_id, "completed",
                    message=f"计划已被手动暂停，已执行 {executed}/{total}",
                    result={"executed": executed, "passed": passed, "failed": failed, "total": total, "paused": True},
                )
                return {"executed": executed, "paused": True, "reason": "manual_pause"}

            # 找到对应的 scenario
            scenario = (await session.execute(
                select(TestReportScenario).where(
                    TestReportScenario.report_id == rid,
                    TestReportScenario.case_id == case.id,
                )
            )).scalar_one_or_none()

            if scenario is None:
                continue

            # 跳过非自动化用例（保持 pending，等待手动录入）
            # 新式(该维度==executable + scripts表有活跃脚本) 优先；兼容旧式(script_ref_file+automated)
            _dim = case.api_status if plan.test_type == "api" else case.ui_status
            new_script = await _has_new_style_script(session, case.id, plan.test_type)
            if new_script and _dim == "executable":
                pass  # 新式可执行
            elif case.automation_status == "automated" and case.script_ref_file:
                new_script = None  # 走旧式沙箱路径
            else:
                continue

            # Flaky 用例跳过
            if case.is_flaky:
                scenario.status = "skipped"
                scenario.error_summary = "Flaky 用例已跳过"
                await session.flush()
                continue

            await set_task_status(
                task_id, "running",
                message=f"执行中 ({i+1}/{total}): {case.title[:50]}"
            )

            # 在线程中执行 pytest（含重试）
            retry_count = plan.retry_count or 0
            case_result = None
            actual_attempts = 0
            retry_logs = []
            case_started = datetime.now(timezone.utc)

            # 标记开始执行
            scenario.status = "running"
            scenario.started_at = case_started
            await session.commit()

            for attempt in range(retry_count + 1):
                actual_attempts = attempt + 1
                attempt_start = datetime.now(timezone.utc)
                case_result = await anyio.to_thread.run_sync(
                    lambda c=case: execute_single_case(
                        sandbox_dir=str(sandbox_dir) if sandbox_dir else None,
                        script_ref_file=c.script_ref_file,
                        script_ref_func=c.script_ref_func,
                        env_vars=env_vars,
                        timeout=300,
                    )
                ) if not new_script else await _run_new_style_script(
                    session, case, plan.test_type, env_vars,
                    str(plan.environment_id) if plan.environment_id else None, new_script,
                )
                attempt_end = datetime.now(timezone.utc)
                attempt_duration = f"{(attempt_end - attempt_start).total_seconds():.1f}s"

                # 每次尝试单独记一行 —— 只记最后一次的话，flaky 判定要的
                # "同一版本多次结果翻转"就永远攒不到。
                await script_run_service.record_run(
                    session,
                    case_id=case.id,
                    script_id=new_script.id if new_script else None,
                    script_type="api" if plan.test_type == "api" else "ui",
                    result=case_result,
                    executed_by=user_id,
                    run_mode=script_run_service.REGRESSION,
                    attempt=actual_attempts,
                    report_scenario_id=scenario.id,
                )

                if case_result["status"] == "passed" or attempt == retry_count:
                    break
                retry_logs.append(
                    f"--- 第 {attempt+1} 次执行: {case_result['status']} | "
                    f"{attempt_start.strftime('%H:%M:%S')} ~ {attempt_end.strftime('%H:%M:%S')} ({attempt_duration}) ---\n"
                    f"{case_result.get('stdout', '')}"
                )
                await set_task_status(
                    task_id, "running",
                    message=f"重试中 ({attempt+1}/{retry_count}): {case.title[:50]}"
                )

            case_completed = datetime.now(timezone.utc)

            # 拼接执行日志（包含重试记录）
            final_log = case_result.get("stdout", "")
            if retry_logs:
                retry_section = "\n".join(retry_logs)
                final_duration = f"{(case_completed - (attempt_start if actual_attempts > 1 else case_started)).total_seconds():.1f}s"
                final_log = (
                    f"{retry_section}\n"
                    f"--- 第 {actual_attempts} 次执行（最终）: {case_result['status']} | "
                    f"{attempt_start.strftime('%H:%M:%S')} ~ {case_completed.strftime('%H:%M:%S')} ({final_duration}) ---\n"
                    f"{final_log}"
                )

            # 更新 scenario
            scenario.status = case_result["status"]
            scenario.duration_ms = case_result["duration_ms"]
            scenario.error_summary = case_result.get("error_summary")
            scenario.execution_log = final_log[:10000]
            scenario.completed_at = case_completed
            if actual_attempts > 1:
                retry_note = f"重试 {actual_attempts - 1} 次，最终{('通过' if case_result['status'] == 'passed' else '失败')}"
                scenario.remark = retry_note
            scenario.execution_type = "automated"
            await session.flush()

            # 创建 steps
            for j, step in enumerate(case_result.get("steps", [])):
                session.add(TestReportStep(
                    scenario_id=scenario.id,
                    step_name=step.get("step_name"),
                    step_label=step.get("step_label"),
                    step_phase=step.get("step_phase"),
                    status=step.get("status", "passed"),
                    http_method=step.get("http_method"),
                    url=step.get("url"),
                    status_code=step.get("status_code"),
                    duration_ms=step.get("duration_ms"),
                    sort_order=j,
                    request_data=step.get("request_data"),
                    response_data=step.get("response_data"),
                    assertions=step.get("assertions"),
                    error_summary=step.get("error_summary"),
                ))
            await session.commit()

            executed += 1
            if case_result["status"] == "passed":
                passed += 1
                consecutive_failures = 0
            elif case_result["status"] in ("failed", "error"):
                failed += 1
                consecutive_failures += 1

    finally:
        # 5. 清理沙箱（无论成败；仅旧式创建了沙箱时才清理）
        if sandbox_dir:
            await set_task_status(task_id, "running", message="正在清理执行沙箱...")
            try:
                await anyio.to_thread.run_sync(
                    lambda: cleanup_sandbox(bare_repo, sandbox_dir)
                )
            except Exception:
                logger.exception("Failed to cleanup sandbox: %s", sandbox_dir)

    # 6. 汇总报告
    from app.services.execution_service import complete_execution
    from app.services.notification_service import notify_plan_result
    await complete_execution(session, pid)

    # 7. 发送通知
    await session.refresh(plan)
    report = (await session.execute(
        select(TestReport).where(TestReport.plan_id == pid).order_by(TestReport.created_at.desc())
    )).scalars().first()
    try:
        await notify_plan_result(
            session, plan, project_name=project.name, trigger="completed",
            total=total, passed=passed, failed=failed, skipped=total - executed,
            pass_rate=float(report.pass_rate) if report and report.pass_rate is not None else None,
        )
    except Exception:
        logger.exception("Failed to send notification")

    result_data = {
        "executed": executed,
        "passed": passed,
        "failed": failed,
        "total": total,
    }
    await set_task_status(
        task_id, "completed",
        message=f"执行完成: {passed} 通过 / {failed} 失败 / {total} 总计",
        result=result_data,
    )
    return result_data
