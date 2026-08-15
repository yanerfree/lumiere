"""UI 脚本生成服务 — MCP Agent 探索式生成 TypeScript Playwright Test 脚本"""
from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import AsyncGenerator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case
from app.models.environment import EnvironmentVariable
from app.services import script_service

logger = logging.getLogger(__name__)


def _get_agent_stream():
    """按配置选 UI 生成引擎：cli（真 CLI 直驱 MCP，快）或 langgraph（旧，慢）。二者 SSEEvent 接口一致。"""
    from app.config import settings
    if (settings.ui_agent_engine or "cli").lower() == "cli":
        from app.services.ai.cli_agent import stream_cli_agent
        return stream_cli_agent
    from app.services.ai.mcp_agent import stream_mcp_agent
    return stream_mcp_agent


async def generate_ui_script_stream(
    case_id: str,
    session: AsyncSession,
    env_id: str | None = None,
    step_hints: dict | None = None,
) -> AsyncGenerator[str, None]:
    """SSE 流式 MCP Agent 生成。"""
    case = await session.get(Case, uuid.UUID(case_id))
    if not case:
        yield _sse("error", {"error": "用例不存在"})
        return

    env_vars = await _load_env_vars(session, env_id)
    base_url = env_vars.get("BASE_URL", "")
    if not base_url:
        yield _sse("error", {"error": "环境未配置 BASE_URL"})
        return

    fixture_name = _detect_fixture(case.preconditions or "")
    creds = _get_credentials(env_vars, fixture_name)

    stream_agent = _get_agent_stream()
    context_block, verify_env = await _build_gen_context(session, case, env_id, env_vars, fixture_name)
    extra_kwargs = {"context_block": context_block, "verify_env": verify_env} if _is_cli_engine() else {}
    ui_model = await _resolve_ui_model(session, case)
    if ui_model:
        extra_kwargs["model_name"] = ui_model

    # 引擎偶发不产脚本（旧引擎不吐 tool_call / CLI 未输出脚本块）→ 整体重试
    MAX_GEN_ATTEMPTS = 3
    script_content = ""
    all_passed = False
    collected_steps: list[dict] = []
    captured_requests: list[dict] = []

    try:
        for attempt in range(1, MAX_GEN_ATTEMPTS + 1):
            script_content = ""
            all_passed = False
            last_error = None
            collected_steps = []
            async for event in stream_agent(
                test_case_title=case.title,
                test_case_steps=case.steps or [],
                expected_result=case.expected_result,
                preconditions=case.preconditions or "",
                base_url=base_url,
                test_user=creds.get("username", "admin"),
                test_password=creds.get("password", "admin123"),
                **extra_kwargs,
            ):
                if event.event == "done":
                    script_content = event.data.get("script_content", "")
                    all_passed = event.data.get("all_passed", False)
                    captured_requests = event.data.get("captured_requests", []) or []
                elif event.event == "error":
                    last_error = event.data.get("content", "未知错误")
                    break
                else:
                    # 累积步骤，供保存到 ui_scenario（重开页面能恢复步骤视图）
                    if event.event == "step_start":
                        collected_steps.append({
                            "seq": event.data.get("seq"),
                            "action": event.data.get("action", ""),
                            "status": "running",
                        })
                    elif event.event == "step_done":
                        for s in collected_steps:
                            if s.get("seq") == event.data.get("seq"):
                                s["status"] = event.data.get("status", "passed")
                                break
                    yield _sse(event.event, event.data)

            if script_content.strip():
                break
            if attempt < MAX_GEN_ATTEMPTS:
                yield _sse("status", {"content": f"本轮未生成脚本，重试 ({attempt}/{MAX_GEN_ATTEMPTS})..."})
            else:
                yield _sse("error", {"error": last_error or f"Agent 未生成有效脚本（已重试 {MAX_GEN_ATTEMPTS} 次）"})
                return

        if script_content.strip():
            # SSE 流式期间 endpoint 传入的 session 已被 FastAPI 依赖关闭，
            # 保存必须用独立 session，否则 commit 静默失效（脚本存不进 DB）。
            from app.deps.db import async_session_factory
            async with async_session_factory() as save_session:
                case2 = await save_session.get(Case, case.id)
                script = await _save_script(save_session, case2, script_content, all_passed)
                # 状态体系 v2：生成自测通过→待审(需人工审核)，失败→调试中
                case2.ui_status = "pending_review" if all_passed else "debugging"
                if not case2.ui_scenario:
                    case2.ui_scenario = {}
                case2.ui_scenario = {
                    **case2.ui_scenario,
                    "scriptId": str(script.id),
                    "lastResults": collected_steps,   # 步骤视图重开可恢复
                    "capturedRequests": captured_requests,  # 接口视图（执行期 HAR 抓取）
                }
                await save_session.commit()

            await _harvest_i18n_safe(case.id, script_content)

            yield _sse("done", {
                "status": "passed" if all_passed else "failed",
                "all_passed": all_passed,
                "results": [],
                "captured_requests": captured_requests,
            })
        else:
            yield _sse("error", {"error": "Agent 未生成脚本"})

    except Exception as exc:
        logger.error("generate_ui_script_stream error", exc_info=True)
        yield _sse("error", {"error": str(exc)[:300]})


async def generate_ui_script(
    case_id: str,
    session: AsyncSession,
    env_id: str | None = None,
) -> dict:
    """同步生成（MCP 工具调用入口）。"""
    case = await session.get(Case, uuid.UUID(case_id))
    if not case:
        raise ValueError(f"用例不存在: {case_id}")

    env_vars = await _load_env_vars(session, env_id)
    base_url = env_vars.get("BASE_URL", "")
    fixture_name = _detect_fixture(case.preconditions or "")
    creds = _get_credentials(env_vars, fixture_name)

    stream_agent = _get_agent_stream()
    context_block, verify_env = await _build_gen_context(session, case, env_id, env_vars, fixture_name)
    extra_kwargs = {"context_block": context_block, "verify_env": verify_env} if _is_cli_engine() else {}
    ui_model = await _resolve_ui_model(session, case)
    if ui_model:
        extra_kwargs["model_name"] = ui_model

    # 引擎偶发不产脚本 → 整体重试
    MAX_GEN_ATTEMPTS = 3
    script_content = ""
    all_passed = False

    for attempt in range(1, MAX_GEN_ATTEMPTS + 1):
        script_content = ""
        all_passed = False
        async for event in stream_agent(
            test_case_title=case.title,
            test_case_steps=case.steps or [],
            expected_result=case.expected_result,
            preconditions=case.preconditions or "",
            base_url=base_url,
            test_user=creds.get("username", "admin"),
            test_password=creds.get("password", "admin123"),
            **extra_kwargs,
        ):
            if event.event == "done":
                script_content = event.data.get("script_content", "")
                all_passed = event.data.get("all_passed", False)
            elif event.event == "error":
                break
        if script_content.strip():
            break

    if not script_content.strip():
        raise ValueError(f"MCP Agent 未生成有效脚本（已重试 {MAX_GEN_ATTEMPTS} 次）")

    script = await _save_script(session, case, script_content, all_passed)
    await session.commit()

    await _harvest_i18n_safe(case.id, script_content)

    return {
        "script_id": str(script.id),
        "content": script_content,
        "case_id": case_id,
        "version": script.version,
    }


async def repair_ui_script(
    case_id: str,
    session: AsyncSession,
    error_summary: str,
    stdout: str = "",
) -> dict:
    """分析执行失败原因，修复脚本。"""
    from sqlalchemy import select as sa_select
    from app.models.script import ScriptRun
    from app.services.ai.llm_client import complete

    case = await session.get(Case, uuid.UUID(case_id))
    if not case:
        raise ValueError(f"用例不存在: {case_id}")

    current_script = await script_service.get_active_script(session, case.id, "ui")
    if not current_script:
        raise ValueError("没有可修复的 UI 脚本")

    runs_result = await session.execute(
        sa_select(ScriptRun)
        .where(ScriptRun.case_id == case.id, ScriptRun.script_type == "ui", ScriptRun.status != "passed")
        .order_by(ScriptRun.created_at.desc())
        .limit(5)
    )
    history = [{"error": r.error_summary or "", "stdout_tail": (r.stdout or "")[-500:]} for r in runs_result.scalars().all()]

    from app.services.ai.prompts.ui_script_repair import REPAIR_SYSTEM_PROMPT, get_repair_prompt
    messages = [
        {"role": "system", "content": REPAIR_SYSTEM_PROMPT},
        {"role": "user", "content": get_repair_prompt(current_script.content, error_summary, stdout, history)},
    ]

    resp = await complete(messages, max_tokens=4096, temperature=0.1)
    fixed_content = _extract_code(resp.content)

    if fixed_content == current_script.content:
        return {"changed": False, "message": "AI 未找到可修复的内容"}

    script = await script_service.create_script(
        session, case_id=case.id, script_type="ui", content=fixed_content,
        file_name=current_script.file_name, language="typescript", source="ai_generated",
    )
    await session.flush()
    return {"changed": True, "script_id": str(script.id), "version": script.version}


# ─── 内部工具函数 ──────────────────────────────────────────

async def _load_env_vars(session: AsyncSession, env_id: str | None) -> dict[str, str]:
    if not env_id:
        return {}
    rows = await session.execute(
        select(EnvironmentVariable)
        .where(EnvironmentVariable.environment_id == uuid.UUID(env_id))
    )
    return {v.key: v.value for v in rows.scalars().all()}


async def _save_script(session: AsyncSession, case: Case, content: str, all_passed: bool):
    """转正状态机：通过 → active，失败 → draft"""
    file_name = f"test_{case.case_code.lower().replace('-', '_')}_ui.spec.ts"
    if all_passed:
        return await script_service.create_script(
            session, case_id=case.id, script_type="ui", content=content,
            file_name=file_name, language="typescript", source="ai_generated",
        )
    existing = await script_service.get_active_script(session, case.id, "ui")
    if existing:
        from app.models.script import Script
        max_ver_result = await session.execute(
            select(Script.version).where(Script.case_id == case.id, Script.script_type == "ui")
            .order_by(Script.version.desc()).limit(1)
        )
        max_ver = max_ver_result.scalar_one_or_none() or 0
        script = Script(
            case_id=case.id, script_type="ui", version=max_ver + 1,
            language="typescript", content=content,
            file_name=file_name, status="draft", source="ai_generated",
        )
        session.add(script)
        await session.flush()
        return script
    return await script_service.create_script(
        session, case_id=case.id, script_type="ui", content=content,
        file_name=file_name, language="typescript", source="ai_generated",
    )


async def _harvest_i18n_safe(case_id, content: str):
    """生成脚本后把 UI 文案增量采集进项目级 i18n 词典（为二期脚本国际化打底座）。

    用独立 session、全程 try/except 降级：采集失败绝不影响脚本保存。
    """
    try:
        from app.deps.db import async_session_factory
        from app.models.project import Branch
        from app.services.i18n_harvest_service import harvest_from_script
        cid = case_id if isinstance(case_id, uuid.UUID) else uuid.UUID(str(case_id))
        async with async_session_factory() as s:
            case = await s.get(Case, cid)
            if not case or not case.branch_id:
                return
            branch = await s.get(Branch, case.branch_id)
            if not branch:
                return
            added = await harvest_from_script(s, branch.project_id, content)
            await s.commit()
            if added:
                logger.info("i18n 采集：新增 %d 条 UI 文案 (case=%s)", added, cid)
    except Exception:
        logger.warning("i18n 文案采集失败（已忽略，不影响脚本保存）", exc_info=True)


def _detect_fixture(preconditions: str) -> str:
    text = preconditions.lower()
    if any(kw in text for kw in ["租户", "tenant", "已授权"]):
        return "tenant_page"
    return "logged_in_page"


async def _build_gen_context(session, case, env_id, env_vars, fixture_name):
    """组装生成上下文块 + verify/执行注入环境（TEST_TOKEN + 场景变量 SV_*）。
    任一步失败都降级为空，绝不阻断生成。"""
    context_block, verify_env = "", {}
    role = "TENANT" if fixture_name == "tenant_page" else "ADMIN"
    try:
        # project_id via branch
        project_id = None
        if case.branch_id:
            from app.models.project import Branch
            branch = await session.get(Branch, case.branch_id)
            project_id = branch.project_id if branch else None
        from app.services.ai import automation_context_service as acs
        ctx = await acs.build_context(session, case.id, project_id, env_id, role, token_refresh=True)
        context_block = acs.render_prompt_block(ctx)
        if ctx.token:
            verify_env["TEST_TOKEN"] = ctx.token
    except Exception:
        logger.warning("build_gen_context failed (降级为空上下文)", exc_info=True)
    # 场景变量（Epic2）：UI/接口共用同一份，唯一化
    try:
        from app.services.scenario_variable_service import resolve_scenario_variables
        sv = await resolve_scenario_variables(session, case.id, global_lookup=env_vars)
        verify_env.update(sv)
    except Exception:
        logger.warning("resolve scenario vars failed", exc_info=True)
    return context_block, verify_env


def _is_cli_engine() -> bool:
    from app.config import settings
    return (settings.ui_agent_engine or "cli").lower() == "cli"


async def _resolve_ui_model(session: AsyncSession, case: Case) -> str | None:
    """按 ui_script 能力档位解析 UI 脚本生成模型(取不到则 None,agent 回退 .env)。"""
    try:
        from app.models.project import Branch
        from app.services.ai_config_resolver import resolve_ai_config
        branch = await session.get(Branch, case.branch_id)
        project_id = branch.project_id if branch else None
        cfg = await resolve_ai_config(project_id, session, capability="ui-script")
        return cfg.model if cfg else None
    except Exception:
        logger.warning("resolve ui-script model failed", exc_info=True)
        return None



def _get_credentials(env_vars: dict, fixture_name: str) -> dict:
    if fixture_name == "tenant_page":
        return {
            "username": env_vars.get("TENANT_USERNAME", env_vars.get("ADMIN_USERNAME", "")),
            "password": env_vars.get("TENANT_PASSWORD", env_vars.get("ADMIN_PASSWORD", "")),
        }
    return {
        "username": env_vars.get("ADMIN_USERNAME", ""),
        "password": env_vars.get("ADMIN_PASSWORD", ""),
    }


def _sse(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _extract_code(text: str) -> str:
    match = re.search(r"```(?:typescript|ts)?\s*\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    for pattern in (r"^import ", r"^from "):
        m = re.search(pattern, text, re.MULTILINE)
        if m:
            return text[m.start():].strip()
    return text.strip()
