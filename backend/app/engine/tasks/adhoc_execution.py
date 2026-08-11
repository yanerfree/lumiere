"""
批量执行（Ad-hoc）后台任务 — 不走测试计划，直接执行选中的用例并生成报告。

流程: 加载用例 → 合并变量 → 创建沙箱 → 逐条执行 → 写入结果 → 清理沙箱 → 汇总
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import anyio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.engine.task_status import set_task_status
from app.models.api_test import ApiTestScenario, ApiTestStep
from app.models.case import Case
from app.models.environment import EnvironmentVariable, GlobalVariable
from app.models.project import Branch, Project
from app.models.report import TestReport, TestReportScenario, TestReportStep

logger = logging.getLogger(__name__)

_execution_semaphore = asyncio.Semaphore(6)
_EXECUTION_TIMEOUT = 600


async def _has_new_style_script(session: AsyncSession, case_id, test_type: str):
    """该用例这一维有没有**可执行的东西**。有就返回它，没有返回 None。

    接口这一维有两种载体，此前只认第一种：
    1. `scripts` 表里 script_type='api' 的 pytest 脚本
    2. `api_test_scenarios` 里绑了这条用例的**编排接口场景** —— MCP
       `tb_sync_orchestrated_scenario` 回推的就是这个

    实测：全平台 8 条有接口场景的用例，**0 条**有 api 脚本。只认第一种的话，
    CC 这条链的接口产物一条都进不了计划回归 —— 只能用 tb_run_api_test 即席跑，
    不进计划通过率。而建计划时还会说"这条会执行"，跑起来又变成"记成待人工录入"。
    """
    from app.services import script_service
    stype = "api" if test_type == "api" else "ui"
    script = await script_service.get_active_script(session, case_id, stype)
    if script is not None:
        return script
    if stype == "api":
        sc = (await session.execute(
            select(ApiTestScenario).where(ApiTestScenario.source_case_id == case_id)
            .order_by(ApiTestScenario.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        return sc
    return None


def _script_fk(asset):
    """给 script_runs.script_id 用的值。

    这一列是 `scripts` 表的外键。可执行产物有两种：pytest 脚本（有这个 id）、
    编排接口场景（`api_test_scenarios`，**不是**同一张表）。把后者的 id 塞进来
    会撞外键，而错误发生在记账阶段 —— 执行明明成功了，整次计划却被打死。
    """
    return None if (asset is None or isinstance(asset, ApiTestScenario)) else asset.id


async def _run_orchestrated_scenario(session: AsyncSession, scenario, env_id: str) -> dict:
    """跑一条编排接口场景，并把结果转成执行器那套 result 形状。

    调用方（计划执行 / 批量执行）只认 {status, duration_ms, error_summary, stdout}，
    所以这里负责翻译；轨迹用和用例「执行历史」同一套中文写法，别再造第二种。
    """
    from app.services import api_test_runner, environment_service

    base_env: dict = {}
    if env_id:
        try:
            merged = await environment_service.get_merged_variables(session, uuid.UUID(env_id))
            base_env = {i["key"]: i["value"] for i in merged}
        except Exception:  # noqa: BLE001
            pass

    result = None
    # 全部用关键字传 —— run_batch 的前两个位置参数是 (scenario_ids, session)，
    # 位置传很容易反过来，反了之后报错在 run_batch 内部，看不出是调用方传错。
    #
    # user_id 传 None 是**故意的**：run_batch 只在 user_id 有值时才另外建一份
    # api_test 报告。这里是计划回归，结果要记进计划自己那份报告，
    # 再开一份会让同一次执行在报告页出现两条。
    async for ev in api_test_runner.run_batch(
        scenario_ids=[scenario.id],
        session=session,
        user_id=None,
        project_id=scenario.project_id,
        base_env=base_env,
        branch_id=scenario.branch_id,
    ):
        if ev.type == "scenario_done":
            result = ev.data
    if result is None:
        return {"status": "error", "duration_ms": 0,
                "error_summary": "接口场景没有产出结果（可能一步都没执行）", "stdout": ""}

    steps = (await session.execute(
        select(ApiTestStep).where(ApiTestStep.scenario_id == scenario.id)
        .order_by(ApiTestStep.sort_order)
    )).scalars().all()
    lines = [f"场景：{scenario.title}", ""]
    first_err = None
    total_ms = 0
    for i, st in enumerate(steps, 1):
        resp = st.last_response or {}
        ms = resp.get("duration", 0) or 0
        total_ms += ms
        mark = {"pass": "✅", "fail": "❌", "skip": "⏭"}.get(st.last_status, "•")
        code = resp.get("statusCode")
        # 用**实际发出去**的 URL，不是步骤定义里的模板 —— 打印 ${BASE_URL}
        # 等于让人自己去脑补解析结果，出问题时最想看的恰恰是真实地址。
        url = ((resp.get("request") or {}).get("url")) or st.url
        lines.append(f"{mark} {i}. {st.name}  [{st.method} {url}"
                     + (f" → {code}" if code is not None else "") + f"]  {ms}ms")
        if st.last_status == "fail" and first_err is None:
            first_err = f"步骤「{st.name}」：{resp.get('error') or '断言不通过'}"
    return {
        "status": "passed" if result.get("passed") else "failed",
        "duration_ms": total_ms,
        "error_summary": first_err,
        "stdout": "\n".join(lines),
    }


async def _run_new_style_script(session: AsyncSession, case, test_type: str, base_env_vars: dict, env_id: str, script):
    """执行该用例这一维的可执行产物：pytest 脚本，或编排接口场景。"""
    if isinstance(script, ApiTestScenario):
        return await _run_orchestrated_scenario(session, script, env_id)

    import re as _re
    import shutil
    import tempfile
    from pathlib import Path as _P

    from app.engine.executor import execute_single_case
    from app.services.scenario_variable_service import (
        add_bare_names, resolve_scenario_variables,
    )

    env_vars = dict(base_env_vars or {})
    try:
        add_bare_names(env_vars, await resolve_scenario_variables(
            session, case.id, global_lookup=env_vars))
    except Exception:
        pass
    if env_id:
        try:
            from app.services.token_service import get_target_token
            pc = case.preconditions or ""
            role = "TENANT" if any(k in pc for k in ("租户", "tenant", "已授权")) else "ADMIN"
            tok = await get_target_token(session, env_id, role)
            if tok:
                env_vars["TEST_TOKEN"] = tok
        except Exception:
            pass

    file_name = script.file_name or ("test_ui.py" if test_type != "api" else "test_api.py")
    content = script.content or ""
    # Python 脚本把 os.getenv 默认值替换为实际值；TS 脚本靠 process.env 注入(ts_runner 传 env)
    for vn, vv in env_vars.items():
        content = _re.sub(
            rf'({_re.escape(vn)}\s*=\s*os\.getenv\(\s*"{_re.escape(vn)}"\s*,\s*)(["\']).*?\2',
            lambda m, v=vv: f'{m.group(1)}{m.group(2)}{v}{m.group(2)}',
            content, count=1,
        )
    sandbox = tempfile.mkdtemp(prefix="tb_batch_")
    try:
        sp = _P(sandbox) / file_name
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(content, encoding="utf-8")
        return await anyio.to_thread.run_sync(
            lambda: execute_single_case(
                sandbox_dir=sandbox, script_ref_file=file_name,
                script_ref_func=script.func_name, env_vars=env_vars, timeout=180,
            )
        )
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)



async def run_adhoc_execution(
    task_id: str,
    report_id: str,
    case_ids: list[str],
    env_id: str,
    test_type: str,
    project_id: str,
    branch_id: str,
    user_id: str,
) -> dict:
    async with _execution_semaphore:
        try:
            return await asyncio.wait_for(
                _run_adhoc_inner(task_id, report_id, case_ids, env_id, test_type, project_id, branch_id, user_id),
                timeout=_EXECUTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            await set_task_status(task_id, "failed", message=f"执行超时（{_EXECUTION_TIMEOUT}s）")
            return {"error": "timeout"}


async def _run_adhoc_inner(
    task_id: str, report_id: str, case_ids: list[str],
    env_id: str, test_type: str, project_id: str, branch_id: str, user_id: str,
) -> dict:
    await set_task_status(task_id, "running", message="正在准备执行环境...")

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            result = await _execute_adhoc(
                session, task_id, report_id, case_ids, env_id, test_type, project_id, branch_id,
                user_id,
            )
            await session.commit()
            return result
    except Exception as e:
        logger.exception("Adhoc execution task failed")
        await set_task_status(task_id, "failed", message=f"执行异常: {str(e)[:200]}")
        return {"error": str(e)}
    finally:
        await engine.dispose()


async def _execute_adhoc(
    session: AsyncSession, task_id: str, report_id: str,
    case_ids: list[str], env_id: str, test_type: str,
    project_id: str, branch_id: str, user_id: str | None = None,
) -> dict:
    from app.engine.executor import execute_single_case
    from app.engine.sandbox import cleanup_sandbox, create_sandbox
    from app.services import script_run_service
    from app.services.environment_service import get_merged_variables

    rid = uuid.UUID(report_id)

    project = (await session.execute(
        select(Project).where(Project.id == uuid.UUID(project_id))
    )).scalar_one()
    branch = (await session.execute(
        select(Branch).where(Branch.id == uuid.UUID(branch_id))
    )).scalar_one()

    cases = (await session.execute(
        select(Case).where(Case.id.in_([uuid.UUID(c) for c in case_ids]))
    )).scalars().all()

    env_vars = {}
    if env_id:
        merged = await get_merged_variables(session, uuid.UUID(env_id))
        env_vars = {v["key"]: v["value"] for v in merged}
        report = (await session.execute(select(TestReport).where(TestReport.id == rid))).scalar_one()
        report.variables_snapshot = merged
        await session.flush()

    # 判断哪些用例可执行：新式(该维度==executable + scripts表有活跃脚本) 优先；兼容旧式(script_ref_file+automated)
    executable = []
    new_scripts = {}  # case.id -> 活跃脚本（新式 AI 生成）
    for case in cases:
        dim = case.api_status if test_type == "api" else case.ui_status
        script = await _has_new_style_script(session, case.id, test_type)
        if dim == "executable" and script:
            executable.append(case)
            new_scripts[case.id] = script
        elif bool(case.script_ref_file) and case.automation_status == "automated":
            executable.append(case)

    # 创建沙箱（如果有可执行用例且项目配置了脚本路径）
    sandbox_dir = None
    bare_repo = None
    # 同 execution.py：先判这批里有没有旧式(script_ref_file)用例真需要沙箱。
    # 否则项目上一个过期的 script_base_path 会把整批打死，而这批可能全是新式脚本。
    use_sandbox = bool(
        project.script_base_path and branch.last_commit_sha
        and any(c.script_ref_file for c in executable)
    )

    if use_sandbox:
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

    total = len(cases)
    executed = 0
    passed = 0
    failed = 0

    try:
        for i, case in enumerate(cases):
            scenario = (await session.execute(
                select(TestReportScenario).where(
                    TestReportScenario.report_id == rid,
                    TestReportScenario.case_id == case.id,
                )
            )).scalar_one_or_none()
            if scenario is None:
                continue

            if case not in executable:
                continue

            await set_task_status(
                task_id, "running",
                message=f"执行中 ({i+1}/{total}): {case.title[:50]}"
            )

            scenario.status = "running"
            scenario.started_at = datetime.now(timezone.utc)
            await session.commit()

            if case.id in new_scripts:
                # 新式：跑 scripts 表的 AI 生成脚本(注入场景变量+token)
                case_result = await _run_new_style_script(
                    session, case, test_type, env_vars, env_id, new_scripts[case.id]
                )
            else:
                # 旧式：沙箱 + script_ref_file
                case_result = await anyio.to_thread.run_sync(
                    lambda c=case: execute_single_case(
                        sandbox_dir=str(sandbox_dir) if sandbox_dir else None,
                        script_ref_file=c.script_ref_file,
                        script_ref_func=c.script_ref_func,
                        env_vars=env_vars,
                        timeout=300,
                    )
                )

            case_completed = datetime.now(timezone.utc)
            scenario.status = case_result["status"]
            scenario.duration_ms = case_result["duration_ms"]
            scenario.error_summary = case_result.get("error_summary")
            scenario.execution_log = (case_result.get("stdout") or "")[:10000]
            scenario.completed_at = case_completed
            scenario.execution_type = "automated"
            await session.flush()

            # 批量回归也要记账 —— 失败证据挂在 script_runs 上，这条路不记就看不到
            await script_run_service.record_run(
                session,
                case_id=case.id,
                script_id=_script_fk(new_scripts.get(case.id)),
                script_type="api" if test_type == "api" else "ui",
                result=case_result,
                executed_by=user_id,
                run_mode=script_run_service.REGRESSION,
                report_scenario_id=scenario.id,
                base_url=env_vars.get("BASE_URL"),
            )

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
            elif case_result["status"] in ("failed", "error"):
                failed += 1

    finally:
        if sandbox_dir and bare_repo:
            await set_task_status(task_id, "running", message="正在清理执行沙箱...")
            try:
                await anyio.to_thread.run_sync(
                    lambda: cleanup_sandbox(bare_repo, sandbox_dir)
                )
            except Exception:
                logger.exception("Failed to cleanup sandbox: %s", sandbox_dir)

    # 汇总报告
    report = (await session.execute(select(TestReport).where(TestReport.id == rid))).scalar_one()
    skipped_count = total - len(executable)
    report.total_scenarios = total
    report.passed = passed
    report.failed = failed
    report.error = 0
    report.skipped = skipped_count
    report.manual_count = 0
    report.completed_at = datetime.now(timezone.utc)
    denominator = passed + failed
    report.pass_rate = Decimal(str(round(passed / denominator * 100, 2))) if denominator > 0 else None
    total_duration = sum(
        s.duration_ms or 0
        for s in (await session.execute(
            select(TestReportScenario).where(TestReportScenario.report_id == rid)
        )).scalars().all()
    )
    report.total_duration_ms = total_duration
    await session.flush()

    result_data = {
        "executed": executed, "passed": passed, "failed": failed,
        "skipped": skipped_count, "total": total,
    }
    await set_task_status(
        task_id, "completed",
        message=f"执行完成: {passed} 通过 / {failed} 失败 / {skipped_count} 跳过",
        result=result_data,
    )
    return result_data
