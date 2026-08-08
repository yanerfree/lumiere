import uuid
import io
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import anyio
from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, NotFoundError
from app.deps.auth import require_project_role
from app.deps.db import get_db
from app.models.case import Case
from app.models.environment import EnvironmentVariable
from app.models.script import Script, ScriptRun
from app.models.user import User
from app.schemas.script import CreateScriptRequest, ScriptResponse
from app.services import script_service

router = APIRouter(
    prefix="/api/projects/{project_id}/branches/{branch_id}/cases/{case_id}/scripts",
    tags=["scripts"],
)


async def _inject_test_token(session, env_id, case_id, env_vars: dict) -> None:
    """注入鉴权 token TEST_TOKEN（S1.3）——脚本鉴权造数/清理用，避免 401。失败静默降级。"""
    if not env_id:
        return
    try:
        from app.services.token_service import get_target_token
        case = await session.get(Case, case_id)
        pc = (case.preconditions or "") if case else ""
        role = "TENANT" if any(k in pc for k in ("租户", "tenant", "已授权")) else "ADMIN"
        tok = await get_target_token(session, env_id, role)
        if tok:
            env_vars["TEST_TOKEN"] = tok
    except Exception:
        pass


@router.get("")
async def list_script_versions(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    script_type: str = Query(alias="type", default="api"),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    versions = await script_service.list_versions(session, case_id, script_type)
    return {
        "data": [
            ScriptResponse.model_validate(s, from_attributes=True).model_dump(by_alias=True)
            for s in versions
        ]
    }


@router.get("/active")
async def get_active_script(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    script_type: str = Query(alias="type", default="api"),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    script = await script_service.get_active_script(session, case_id, script_type)
    if not script:
        return {"data": None}
    return {
        "data": ScriptResponse.model_validate(script, from_attributes=True).model_dump(by_alias=True)
    }


@router.post("")
async def create_script(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    body: CreateScriptRequest,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    script = await script_service.create_script(
        session,
        case_id=case_id,
        script_type=body.script_type,
        content=body.content,
        file_name=body.file_name,
        func_name=body.func_name,
        language=body.language,
        source=body.source,
        created_by=user.id,
    )
    return {
        "data": ScriptResponse.model_validate(script, from_attributes=True).model_dump(by_alias=True)
    }


@router.post("/generate")
async def generate_script_ai(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    script_type: str = Query(alias="type", default="ui"),
    env_id: uuid.UUID | None = Body(default=None, alias="envId", embed=True),
    step_hints: dict | None = Body(default=None, alias="stepHints", embed=True),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """AI 生成 Playwright 测试脚本"""
    if script_type != "ui":
        raise AppError(code="INVALID_TYPE", message="AI 生成仅支持 UI 脚本类型")

    from app.services.ai.ui_script_gen_service import generate_ui_script
    result = await generate_ui_script(
        case_id=str(case_id),
        session=session,
        env_id=str(env_id) if env_id else None,
    )
    await session.commit()
    return {"data": result}


@router.post("/generate-stream")
async def generate_script_ai_stream(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    script_type: str = Query(alias="type", default="ui"),
    env_id: uuid.UUID | None = Body(default=None, alias="envId", embed=True),
    step_hints: dict | None = Body(default=None, alias="stepHints", embed=True),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """SSE 流式 AI 生成 — MCP Agent 探索式脚本生成"""
    if script_type != "ui":
        raise AppError(code="INVALID_TYPE", message="仅支持 UI 脚本")

    case = await session.get(Case, case_id)
    if not case:
        raise NotFoundError(code="CASE_NOT_FOUND", message="用例不存在")

    from app.services.ai.ui_script_gen_service import generate_ui_script_stream

    async def event_generator():
        async for chunk in generate_ui_script_stream(
            case_id=str(case_id),
            session=session,
            env_id=str(env_id) if env_id else None,
            step_hints=step_hints,
        ):
            yield chunk

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/repair")
async def repair_script_ai(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    error_summary: str = Body(default="", alias="errorSummary"),
    stdout: str = Body(default=""),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """AI 分析执行失败原因并修复脚本"""
    from app.services.ai.ui_script_gen_service import repair_ui_script
    result = await repair_ui_script(
        case_id=str(case_id),
        session=session,
        error_summary=error_summary,
        stdout=stdout,
    )
    await session.commit()
    return {"data": result}


@router.get("/preflight")
async def preflight_run(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    env_id: uuid.UUID | None = Query(default=None, alias="envId"),
    role: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """执行前门禁：预检全局资源(缺→待确认) + 报告 token/场景变量就绪情况。
    前端跑前调用；envVars 不回传敏感值(仅键名 + token 是否就绪)。"""
    from app.services import run_context_service
    report = await run_context_service.preflight(session, case_id, env_id, role)
    return {"data": {
        "ready": report["ready"],
        "missing": report["missing"],
        "role": report["role"],
        "tokenAcquired": report["tokenAcquired"],
        "scriptUsesToken": report.get("scriptUsesToken", False),
        "envKeys": sorted(report["envVars"].keys()),
    }}


@router.post("/run")
async def run_script(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    script_type: str = Query(alias="type", default="api"),
    env_id: uuid.UUID | None = Body(default=None, alias="envId", embed=True),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """直接运行 DB 中的脚本，返回执行结果并持久化到 script_runs 表。"""
    script = await script_service.get_active_script(session, case_id, script_type)
    if not script:
        raise NotFoundError(code="SCRIPT_NOT_FOUND", message="没有可执行的脚本")

    env_vars: dict[str, str] = {}
    if env_id:
        rows = await session.execute(
            select(EnvironmentVariable)
            .where(EnvironmentVariable.environment_id == env_id)
        )
        for v in rows.scalars().all():
            env_vars[v.key] = v.value

    from app.services.scenario_variable_service import resolve_scenario_variables
    env_vars.update(await resolve_scenario_variables(session, case_id, global_lookup=env_vars))
    await _inject_test_token(session, env_id, case_id, env_vars)

    file_name = script.file_name or f"test_{script_type}.py"
    content = script.content

    # 把环境变量注入脚本中 os.getenv 的默认值
    for var_name, var_value in env_vars.items():
        content = re.sub(
            rf'({re.escape(var_name)}\s*=\s*os\.getenv\(\s*"{re.escape(var_name)}"\s*,\s*)(["\']).*?\2',
            lambda m, v=var_value: f'{m.group(1)}{m.group(2)}{v}{m.group(2)}',
            content,
            count=1,
        )

    sandbox_dir = tempfile.mkdtemp(prefix="tb_run_")
    try:
        script_path = Path(sandbox_dir) / file_name
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(content, encoding="utf-8")

        from app.engine.executor import execute_single_case
        result = await anyio.to_thread.run_sync(
            lambda: execute_single_case(
                sandbox_dir=sandbox_dir,
                script_ref_file=file_name,
                script_ref_func=script.func_name,
                env_vars=env_vars,
                timeout=120,
            )
        )
    finally:
        shutil.rmtree(sandbox_dir, ignore_errors=True)

    from app.services import script_run_service
    run_record = await script_run_service.record_run(
        session,
        case_id=case_id, script_id=script.id, script_type=script_type,
        result=result, executed_by=user.id,
        run_mode=script_run_service.DEBUG,
    )

    # 更新用例 UI 场景状态（debug 只许向前推进，失败不打回——见 apply_case_status）
    if script_type == "ui":
        case = await session.get(Case, case_id)
        script_run_service.apply_case_status(case, "ui", result.get("status"), script_run_service.DEBUG)

    await session.commit()
    if run_record is None:
        return {"data": result}
    await session.refresh(run_record)

    result["id"] = str(run_record.id)
    result["created_at"] = run_record.created_at.isoformat()
    return {"data": result}


@router.post("/run-stream")
async def run_script_stream(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    script_type: str = Query(alias="type", default="ui"),
    env_id: uuid.UUID | None = Body(default=None, alias="envId", embed=True),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """SSE 流式执行脚本 — 支持 Python pytest 和 TypeScript npx playwright test"""
    import asyncio
    import json
    import time as time_mod

    script = await script_service.get_active_script(session, case_id, script_type)
    if not script:
        raise NotFoundError(code="SCRIPT_NOT_FOUND", message="没有可执行的脚本")

    env_vars: dict[str, str] = {}
    if env_id:
        rows = await session.execute(
            select(EnvironmentVariable).where(EnvironmentVariable.environment_id == env_id)
        )
        for v in rows.scalars().all():
            env_vars[v.key] = v.value

    from app.services.scenario_variable_service import resolve_scenario_variables
    env_vars.update(await resolve_scenario_variables(session, case_id, global_lookup=env_vars))
    await _inject_test_token(session, env_id, case_id, env_vars)

    is_typescript = (script.language == "typescript"
                     or (script.file_name or "").endswith(".ts")
                     or "from '../fixtures'" in script.content
                     or "from '@playwright/test'" in script.content)

    if is_typescript:
        return StreamingResponse(
            _run_typescript_stream(script, case_id, env_vars, user, session),
            media_type="text/event-stream",
        )

    return StreamingResponse(
        _run_python_stream(script, case_id, env_vars, user, session),
        media_type="text/event-stream",
    )


async def _run_typescript_stream(script, case_id, env_vars, user, session):
    """用 npx playwright test 执行 TypeScript 脚本"""
    import asyncio
    import json
    import time as time_mod

    sandbox_dir = tempfile.mkdtemp(prefix="tb_ts_run_")
    try:
        from app.services.ai.verify_tool import FIXTURE_SHIM, GLOBAL_SETUP, _link_node_modules
        import os as os_mod

        _link_node_modules(sandbox_dir)

        tests_dir = Path(sandbox_dir) / "tests"
        tests_dir.mkdir()
        fixtures_dir = Path(sandbox_dir) / "fixtures"
        fixtures_dir.mkdir()

        (tests_dir / "test.spec.ts").write_text(script.content, encoding="utf-8")
        (fixtures_dir / "index.ts").write_text(FIXTURE_SHIM, encoding="utf-8")
        (Path(sandbox_dir) / "global-setup.js").write_text(GLOBAL_SETUP, encoding="utf-8")

        base_url = env_vars.get("BASE_URL", "")
        config = f"""module.exports = {{
  testDir: './tests',
  timeout: 120000,
  retries: 0,
  use: {{
    baseURL: '{base_url}',
    headless: true,
    screenshot: 'on',
    video: 'on',
    locale: 'zh-CN',
  }},
  reporter: [['json', {{ outputFile: 'report.json' }}]],
  outputDir: './test-results',
}};"""
        (Path(sandbox_dir) / "playwright.config.js").write_text(config, encoding="utf-8")

        run_env = os_mod.environ.copy()
        run_env.update(env_vars)
        run_env["CI"] = "1"
        from app.engine.ts_runner import resolve_node_path
        run_env["NODE_PATH"] = resolve_node_path()

        # 根据用例前置条件选正确凭据
        case = await session.get(Case, case_id)
        preconditions = (case.preconditions or "").lower() if case else ""
        if any(kw in preconditions for kw in ["租户", "tenant"]):
            run_env["TEST_USER"] = env_vars.get("TENANT_USERNAME", env_vars.get("ADMIN_USERNAME", ""))
            run_env["TEST_PASSWORD"] = env_vars.get("TENANT_PASSWORD", env_vars.get("ADMIN_PASSWORD", ""))
        else:
            run_env["TEST_USER"] = env_vars.get("ADMIN_USERNAME", "")
            run_env["TEST_PASSWORD"] = env_vars.get("ADMIN_PASSWORD", "")

        start_time = time_mod.time()
        proc = await asyncio.create_subprocess_exec(
            "npx", "playwright", "test",
            f"--config={sandbox_dir}/playwright.config.js",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=sandbox_dir,
            env=run_env,
        )

        stdout_chunks = []
        stderr_chunks = []

        async def drain(stream, buf):
            async for line in stream:
                buf.append(line.decode("utf-8", errors="ignore"))

        await asyncio.gather(
            drain(proc.stdout, stdout_chunks),
            drain(proc.stderr, stderr_chunks),
        )
        await proc.wait()

        duration_ms = int((time_mod.time() - start_time) * 1000)
        stdout_text = "".join(stdout_chunks)
        stderr_text = "".join(stderr_chunks)

        status = "passed" if proc.returncode == 0 else "failed"
        error_summary = None
        if proc.returncode != 0:
            from app.services.ai.verify_tool import _parse_errors_from_report
            report_path = Path(sandbox_dir) / "report.json"
            error_summary = _parse_errors_from_report(str(report_path))
            if not error_summary:
                error_summary = (stderr_text + stdout_text)[-2000:]

        from app.services import script_run_service
        await script_run_service.record_run(
            session,
            case_id=case_id, script_id=script.id, script_type="ui",
            result={
                "status": status, "duration_ms": duration_ms,
                "error_summary": error_summary,
                "stdout": (stdout_text + stderr_text)[-5000:],
            },
            executed_by=user.id,
            run_mode=script_run_service.DEBUG,
        )

        case = await session.get(Case, case_id)
        script_run_service.apply_case_status(case, "ui", status, script_run_service.DEBUG)
        await session.commit()

        final = json.dumps({
            "status": status, "duration_ms": duration_ms,
            "error_summary": error_summary, "steps": [], "screenshots": [],
        }, ensure_ascii=False, default=str)
        yield f"event: done\ndata: {final}\n\n"

    finally:
        shutil.rmtree(sandbox_dir, ignore_errors=True)


async def _run_python_stream(script, case_id, env_vars, user, session):
    """用 pytest 执行 Python 脚本（原有逻辑）"""
    import asyncio
    import json
    import time as time_mod
    import os as os_mod

    file_name = script.file_name or "test_ui.py"
    content = script.content
    for var_name, var_value in env_vars.items():
        content = re.sub(
            rf'({re.escape(var_name)}\s*=\s*os\.getenv\(\s*"{re.escape(var_name)}"\s*,\s*)(["\']).*?\2',
            lambda m, v=var_value: f'{m.group(1)}{m.group(2)}{v}{m.group(2)}',
            content, count=1,
        )

    sandbox_dir = tempfile.mkdtemp(prefix="tb_run_")
    script_path = Path(sandbox_dir) / file_name
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(content, encoding="utf-8")

    from app.engine.command_builder import build_pytest_command, is_playwright_script

    pw_output_dir = None
    if is_playwright_script(content):
        pw_output_dir = str(Path(sandbox_dir) / ".pw_results")
        Path(pw_output_dir).mkdir(parents=True, exist_ok=True)
        from app.engine.pw_conftest import write_playwright_conftest
        write_playwright_conftest(sandbox_dir, env_vars)

    plugin_src = Path(__file__).resolve().parent.parent / "engine" / "plugins" / "tea_capture.py"
    step_src = Path(__file__).resolve().parent.parent / "engine" / "plugins" / "tea_step.py"
    tea_plugins_dir = Path(sandbox_dir) / ".tea_plugins"
    tea_results_dir = Path(sandbox_dir) / ".tea_results"
    tea_plugins_dir.mkdir(parents=True, exist_ok=True)
    tea_results_dir.mkdir(parents=True, exist_ok=True)
    if plugin_src.exists():
        shutil.copy2(str(plugin_src), str(tea_plugins_dir / "tea_capture.py"))
    if step_src.exists():
        shutil.copy2(str(step_src), str(tea_plugins_dir / "tea_step.py"))

    import sys
    junit_path = tempfile.mktemp(suffix=".xml")
    cmd = build_pytest_command(sandbox_dir, file_name, script.func_name, junit_path, plugin_src.exists(), pw_output_dir)

    run_env = os_mod.environ.copy()
    run_env.update(env_vars)
    run_env["PYTHONPATH"] = str(tea_plugins_dir) + ":" + run_env.get("PYTHONPATH", "")
    run_env["TEA_CAPTURE_DIR"] = str(tea_results_dir)

    start_time = time_mod.time()
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=sandbox_dir, env=run_env,
    )
    stderr_chunks = []
    # stdout 原本是逐行消费掉只转发步骤标记，不留存 —— 于是执行历史展开后是空日志。
    stdout_chunks = []

    async def drain_stderr():
        async for line in proc.stderr:
            stderr_chunks.append(line.decode("utf-8", errors="ignore"))

    stderr_task = asyncio.create_task(drain_stderr())

    try:
        async for line in proc.stdout:
            text = line.decode("utf-8", errors="ignore").rstrip()
            stdout_chunks.append(text)
            if text.startswith("##STEP_START##"):
                data = text[len("##STEP_START##"):]
                yield f"event: step_start\ndata: {data}\n\n"
            elif text.startswith("##STEP_END##"):
                data = text[len("##STEP_END##"):]
                yield f"event: step_end\ndata: {data}\n\n"

        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

        await stderr_task
        stderr = "".join(stderr_chunks)
        duration_ms = int((time_mod.time() - start_time) * 1000)

        from app.engine.result_parser import parse_junit_xml, parse_step_json
        junit_results = parse_junit_xml(junit_path)

        status = "passed"
        error_summary = None
        if not junit_results:
            status = "error" if proc.returncode != 0 else "passed"
            error_summary = stderr[:2000] if proc.returncode != 0 else None
        else:
            statuses = [r["status"] for r in junit_results]
            if "error" in statuses: status = "error"
            elif "failed" in statuses: status = "failed"
            error_msgs = [r["message"] for r in junit_results if r["message"]]
            error_summary = "; ".join(error_msgs)[:2000] if error_msgs else None

        steps = []
        for jf in sorted(tea_results_dir.glob("*.json")):
            steps = parse_step_json(str(jf))
            if steps: break

        from app.engine.executor import _collect_screenshots
        screenshots = _collect_screenshots(pw_output_dir) if pw_output_dir else []

        # 页面「运行验证」走的就是这条路，此前一行都不记 —— 用户跑完，
        # 执行历史纹丝不动，看起来像执行没生效。
        from app.services import script_run_service
        await script_run_service.record_run(
            session,
            case_id=case_id, script_id=script.id, script_type="ui",
            result={
                "status": status, "duration_ms": duration_ms,
                "error_summary": error_summary,
                "stdout": ("\n".join(stdout_chunks) + ("\n--- STDERR ---\n" + stderr if stderr else ""))[-10000:],
                "screenshots": screenshots,
            },
            executed_by=user.id,
            run_mode=script_run_service.DEBUG,
        )
        case = await session.get(Case, case_id)
        script_run_service.apply_case_status(case, "ui", status, script_run_service.DEBUG)
        await session.commit()

        final = json.dumps({
            "status": status, "duration_ms": duration_ms, "error_summary": error_summary,
            "steps": steps, "screenshots": screenshots,
        }, ensure_ascii=False, default=str)
        yield f"event: done\ndata: {final}\n\n"

    finally:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        try: os_mod.unlink(junit_path)
        except: pass
        shutil.rmtree(sandbox_dir, ignore_errors=True)


@router.get("/runs")
async def list_script_runs(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    script_type: str | None = Query(alias="type", default=None),
    limit: int = Query(default=20, le=100),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """获取用例的脚本执行历史列表。

    不传 type 就返回该用例的全部执行记录。一条用例可以同时挂接口脚本和 UI 脚本
    （用例的 type 是 api/e2e，脚本的 type 是 api/ui，两者不是一回事），
    页面按单一类型过滤会看不全——原先前端把用例 type 直接当脚本 type 传，
    e2e 用例永远查不到任何记录。
    """
    stmt = select(ScriptRun).where(ScriptRun.case_id == case_id)
    if script_type:
        stmt = stmt.where(ScriptRun.script_type == script_type)
    result = await session.execute(
        stmt.order_by(ScriptRun.created_at.desc()).limit(limit)
    )
    runs = result.scalars().all()
    return {
        "data": [
            {
                "id": str(r.id),
                "case_id": str(r.case_id),
                "script_type": r.script_type,
                "run_mode": r.run_mode,
                "attempt": r.attempt,
                "status": r.status,
                "duration_ms": r.duration_ms,
                "error_summary": r.error_summary,
                "stdout": r.stdout,
                "screenshots": r.screenshots,
                "executed_by": str(r.executed_by),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in runs
        ]
    }


@router.post("/{script_id}/activate")
async def activate_script_version(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    script_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    script = await script_service.activate_version(session, script_id)
    if not script:
        raise NotFoundError(code="SCRIPT_NOT_FOUND", message="脚本版本不存在")
    return {
        "data": ScriptResponse.model_validate(script, from_attributes=True).model_dump(by_alias=True)
    }


# --- 导出路由（分支级别） ---
export_router = APIRouter(
    prefix="/api/projects/{project_id}/branches/{branch_id}/scripts",
    tags=["scripts"],
)


@export_router.get("/export")
async def export_scripts(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    script_type: str | None = Query(default=None, alias="type"),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """导出分支下所有 active 脚本为 zip 压缩包（可直接 pytest 运行）。"""
    query = (
        select(Script, Case.case_code)
        .join(Case, Script.case_id == Case.id)
        .where(Case.branch_id == branch_id, Script.status == "active")
    )
    if script_type:
        query = query.where(Script.script_type == script_type)

    result = await session.execute(query)
    rows = result.all()

    if not rows:
        raise NotFoundError(code="NO_SCRIPTS", message="没有可导出的脚本")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for script_obj, case_code in rows:
            folder = f"tests/{script_obj.script_type}"
            fname = script_obj.file_name or f"{case_code.lower().replace('-', '_')}.py"
            zf.writestr(f"{folder}/{fname}", script_obj.content)

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=scripts-export.zip"},
    )
