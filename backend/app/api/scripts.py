import uuid
import io
import json
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


def _sse_done(payload: dict) -> str:
    """SSE 的 `done` 事件 —— **必须自己驼峰化**。

    驼峰中间件只改 `JSONResponse.render()`，管不到 `StreamingResponse` 的 chunk。
    此前这里是手写 `json.dumps`，于是服务端发 `duration_ms` / `error_summary`，
    前端读的是 `durationMs` / `errorSummary` —— 两个都拿不到。表现是 UI 跑完
    面板上写「耗时未记录」（库里明明存着 13403ms），失败时更糟：**错误原文整段
    不显示**，人只看到一个红点。

    `to_camel_case` 现在会豁免装用户数据的字段（见 middleware._OPAQUE_KEYS），
    所以 screenshots 路径、步骤里的响应原文不会被改。
    """
    from app.core.middleware import to_camel_case
    body = json.dumps(to_camel_case(payload), ensure_ascii=False, default=str)
    return f"event: done\ndata: {body}\n\n"


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


# ── 平台侧 AI 生成/自愈已封存（2026-08-08）─────────────────────────────
# 原有三个路由 /generate、/generate-stream、/repair 在此下线：页面入口已全部
# 摘掉，留着路由就是留一条"平台也能生成"的暗路，与 docs/cc-platform-loop-spec.md
# 红线 1（平台侧生成能力归零，不是降权）直接冲突。
#
# 服务层代码没删（ui_script_gen_service / cli_agent，ui_agent_engine=cli 仍在），
# 重新启用的三条判据见该文档红线 1：①探索期数据隔离 ②跑满 20 条测广度
# ③两条都过了再评估作为「批量首稿」通道回来。
#
# 现在的通道：外部 Claude Code 本地写好跑通 → tb_sync_ui_script 回推 →
# tb_run_ui_script 让平台在标准环境执行确认。
# ────────────────────────────────────────────────────────────────────

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

    from app.services.scenario_variable_service import (
        add_bare_names, resolve_scenario_variables,
    )
    add_bare_names(env_vars, await resolve_scenario_variables(
        session, case_id, global_lookup=env_vars))
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
        base_url=env_vars.get("BASE_URL"),
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

    from app.services.scenario_variable_service import (
        add_bare_names, resolve_scenario_variables,
    )
    add_bare_names(env_vars, await resolve_scenario_variables(
        session, case_id, global_lookup=env_vars))
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
    recordHar: { path: './test-results/network.har', content: 'embed' },
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

        from app.engine.executor import _collect_screenshots
        from app.engine.har import har_path_for, parse_har
        ts_out = Path(sandbox_dir) / "test-results"
        screenshots = _collect_screenshots(str(ts_out))
        captured_requests = parse_har(har_path_for(ts_out))

        from app.services import script_run_service
        await script_run_service.record_run(
            session,
            case_id=case_id, script_id=script.id, script_type="ui",
            result={
                "status": status, "duration_ms": duration_ms,
                "error_summary": error_summary,
                "stdout": (stdout_text + stderr_text)[-5000:],
                "screenshots": screenshots,
                "captured_requests": captured_requests,
                # TS 流目前没有步骤解析，恒空；显式带上是为了以后加了别忘接线。
                "steps": [],
            },
            executed_by=user.id,
            run_mode=script_run_service.DEBUG,
        )

        case = await session.get(Case, case_id)
        script_run_service.apply_case_status(case, "ui", status, script_run_service.DEBUG)
        await session.commit()

        yield _sse_done({
            "status": status, "duration_ms": duration_ms,
            "error_summary": error_summary, "steps": [], "screenshots": screenshots,
            # 带上本次抓到的流量。不带的话前端只能沿用上一次加载的那份，
            # 而面板标题写的是「本次流量」—— 每次都恰好 150 条（截断上限），
            # 数字对得上，所以这个谎一直没被发现。
            "captured_requests": captured_requests,
        })

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
        from app.engine.har import har_path_for
        from app.engine.pw_conftest import write_playwright_conftest
        write_playwright_conftest(sandbox_dir, env_vars, har_path=har_path_for(pw_output_dir))

    plugin_src = Path(__file__).resolve().parent.parent / "engine" / "plugins" / "tea_capture.py"
    step_src = Path(__file__).resolve().parent.parent / "engine" / "plugins" / "tea_step.py"
    # 自动埋点插件。**必须跟着复制进沙箱** —— conftest 里 import 它，
    # 漏了这一行就静默走 except 分支，执行历史又变回只有 pytest 那一行。
    autolog_src = Path(__file__).resolve().parent.parent / "engine" / "plugins" / "tea_autolog.py"
    tea_plugins_dir = Path(sandbox_dir) / ".tea_plugins"
    tea_results_dir = Path(sandbox_dir) / ".tea_results"
    tea_plugins_dir.mkdir(parents=True, exist_ok=True)
    tea_results_dir.mkdir(parents=True, exist_ok=True)
    if plugin_src.exists():
        shutil.copy2(str(plugin_src), str(tea_plugins_dir / "tea_capture.py"))
    if step_src.exists():
        shutil.copy2(str(step_src), str(tea_plugins_dir / "tea_step.py"))
    if autolog_src.exists():
        shutil.copy2(str(autolog_src), str(tea_plugins_dir / "tea_autolog.py"))

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
        # **沉默超过 1.2 秒就说一句在干什么。**
        #
        # 最后一步跑完之后还有一段：pytest 收尾、关 Playwright 上下文、把 HAR 落盘
        # （98 条请求带响应体）。实测这段有 **2.2 秒**，期间一个事件都没有，
        # 面板停在「37 步完成，等待中...」—— 看着就是卡死了，被当成 bug 报了两次。
        #
        # 第一版把提示放在这个循环**之后**才发 —— 而沉默恰恰发生在循环**里面**
        # （stdout 还没关，只是没输出）。实测 finishing 在 14.02s 发出、done 在 14.06s，
        # 只差 40ms，等于没显示。所以要靠"读超时"来判沉默，不能等循环结束。
        # 收尾提示靠 conftest 的 `pytest_runtest_teardown` 打的 `##TEARDOWN##` 标记，
        # **不靠"沉默超过 N 秒"猜** —— 实测收尾是 2.2 秒，而中途的 wait_for_url /
        # expect 重试也能停 1.2 秒以上，用沉默判会在第 20 步时弹「正在收尾」，
        # 那是句假话。前两版分别踩了"启动沉默"和"中途沉默"，都是猜出来的。
        async for line in proc.stdout:
            text = line.decode("utf-8", errors="ignore").rstrip()
            stdout_chunks.append(text)
            # **按"行内查找"而不是"行首"。** 加了 `-s` 之后 pytest 打印用例名
            # 不带换行（`test_ui.py::test_xxx[chromium] `），第一个标记被拼到那一行
            # 末尾，用 startswith 就匹配不到 —— 于是 step_start 永远比 step_end 少一个，
            # 面板上「N 步完成」永远差一步，最后一步看着像卡住了。实测就是这个现象。
            if "##TEARDOWN##" in text:
                yield ('event: finishing\ndata: '
                       '{"message": "步骤跑完，正在收尾（关闭浏览器、保存本次流量）"}\n\n')
                continue
            for marker, ev in (("##STEP_START##", "step_start"), ("##STEP_END##", "step_end")):
                idx = text.find(marker)
                if idx >= 0:
                    yield f"event: {ev}\ndata: {text[idx + len(marker):]}\n\n"
                    break

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
        from app.engine.har import har_path_for, parse_har
        screenshots = _collect_screenshots(pw_output_dir) if pw_output_dir else []
        captured_requests = parse_har(har_path_for(pw_output_dir)) if pw_output_dir else []

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
                "captured_requests": captured_requests,
                # **必须存。** 这是页面「运行验证」走的路径；不存的话执行历史
                # 展开又只剩 pytest 那坨 —— 而 steps 就在手边（下面 done 事件里用的
                # 就是它），是这几行漏了。
                "steps": steps,
            },
            executed_by=user.id,
            run_mode=script_run_service.DEBUG,
        )
        case = await session.get(Case, case_id)
        script_run_service.apply_case_status(case, "ui", status, script_run_service.DEBUG)
        await session.commit()

        yield _sse_done({
            "status": status, "duration_ms": duration_ms, "error_summary": error_summary,
            "steps": steps, "screenshots": screenshots,
            "captured_requests": captured_requests,   # 见上面那条注释
        })

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
                # 下面两个库里一直有，只是从没送到前端过：
                # captured_requests 让「接口视图」打开页面就有内容（此前只有当场
                # 点了运行验证才有）；failure_phenomenon 是平台判好的失败现象
                # （超时/元素找不到/断言不符…），人扫一眼就知道往哪看，
                # 不用去读一坨 pytest stdout。
                "captured_requests": r.captured_requests,
                "failure_phenomenon": r.failure_phenomenon,
                # 步骤级结果。不给的话执行历史展开只有 pytest 那坨横幅，
                # 十几个 expect() 验了什么、挂在第几步全看不到 —— 实测被指出两轮。
                "steps": r.steps,
                "executed_by": str(r.executed_by),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in runs
        ]
    }


@router.get("/runs/{run_id}/analysis")
async def get_run_analysis(
    project_id: uuid.UUID, branch_id: uuid.UUID, case_id: uuid.UUID, run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """一次执行的三层失败判断：平台现象 / CC 归因 / 人工确认。"""
    from app.services.analysis_service import CAUSES
    run = (await session.execute(
        select(ScriptRun).where(ScriptRun.id == run_id, ScriptRun.case_id == case_id)
    )).scalar_one_or_none()
    if not run:
        raise NotFoundError(code="RUN_NOT_FOUND", message="执行记录不存在")
    return {"data": {
        "runId": str(run.id),
        "status": run.status,
        "phenomenon": run.failure_phenomenon,
        "ccAnalysis": run.cc_analysis,
        "confirmedCause": run.confirmed_cause,
        "confirmedNote": run.confirmed_note,
        "confirmedAt": run.confirmed_at.isoformat() if run.confirmed_at else None,
        "causeOptions": [{"value": k, "label": v} for k, v in CAUSES.items()],
    }}


@router.post("/runs/{run_id}/confirm")
async def confirm_run_cause(
    project_id: uuid.UUID, branch_id: uuid.UUID, case_id: uuid.UUID, run_id: uuid.UUID,
    cause: str = Body(..., embed=True),
    note: str = Body(default="", embed=True),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """人工确认失败原因 —— **这是结论的唯一写入口**。

    CC 的归因只是建议，进待确认队列；确认之后才算数。
    """
    from app.services import analysis_service
    try:
        run = await analysis_service.confirm(session, run_id, cause, note, user.id)
    except ValueError as e:
        raise AppError(code="INVALID_CONFIRM", message=str(e)) from e
    return {"data": {
        "runId": str(run.id),
        "confirmedCause": run.confirmed_cause,
        "confirmedNote": run.confirmed_note,
        "confirmedAt": run.confirmed_at.isoformat() if run.confirmed_at else None,
    }}


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


def backup_path(case_code: str | None, script_type: str,
                file_name: str | None, seen: set[str]) -> str:
    """备份包里一个脚本该放哪。抽成纯函数是为了能直接测"两个脚本不会同名"。

    CC 回推上来的脚本几乎都叫 test_ui.py / test_api.py。原来扁平放在
    `tests/{类型}/` 下，同名互相覆盖 —— 实测 8 个脚本压出来只剩 2 个文件。
    备份的意义是"平台没了资产还在"，覆盖掉就等于没备份。
    所以按**用例编号**分目录，同一用例同类型再撞就加序号。
    """
    code = (case_code or "case").lower().replace("-", "_")
    fname = file_name or "script.py"
    base = f"tests/{code}/{script_type}"
    path = f"{base}/{fname}"
    n = 1
    while path in seen:
        n += 1
        stem, _, ext = fname.rpartition(".")
        path = f"{base}/{stem}_{n}.{ext}" if ext else f"{base}/{fname}_{n}"
    return path


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

    # 用**用例编号**建目录，不能只用 file_name —— CC 回推上来的脚本几乎都叫
    # test_ui.py / test_api.py，扁平放一起会同名互相覆盖：实测 6 个脚本压出来
    # 只剩 2 个文件。备份的意义是"平台没了资产还在"，覆盖掉就等于没备份。
    buf = io.BytesIO()
    manifest = []
    seen: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for script_obj, case_code in rows:
            path = backup_path(case_code, script_obj.script_type,
                               script_obj.file_name, seen)
            seen.add(path)
            zf.writestr(path, script_obj.content)
            manifest.append(f"{case_code}\t{script_obj.script_type}\t{path}")

        zf.writestr("MANIFEST.tsv", "用例编号\t类型\t文件\n" + "\n".join(manifest))
        zf.writestr("README.md", (
            "# 用例脚本备份\n\n"
            f"共 {len(rows)} 个脚本，按用例编号分目录。对照见 MANIFEST.tsv。\n\n"
            "## 这份备份能干什么\n\n"
            "存档。平台哪天没了，脚本正文还在。\n\n"
            "## 直接跑得起来吗\n\n"
            "**跑不起来，这是预期的。** 脚本里的取值来自三层，都不在这个包里：\n\n"
            "1. 场景变量（平台按用例存，执行时注入）\n"
            "2. 项目级全局引用 —— `BASE_URL` / 账号 / token，按环境注入\n"
            "3. 步骤间提取物（上一步的响应里取的）\n\n"
            "**凭据一个字都不在这个包里**，也不会有「包含凭据」这种选项。\n"
            "要跑，把脚本放回平台执行，或在目标环境自己补齐上面这些值。\n"
        ))

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=scripts-backup.zip"},
    )
