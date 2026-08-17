"""UI 脚本 MCP 工具 — 生成/执行/查询 Playwright 测试脚本"""
from __future__ import annotations

import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case
from app.models.environment import EnvironmentVariable
from app.models.script import Script, ScriptRun
from app.services import script_service


async def generate_ui_script(
    case_id: str,
    env_id: str | None = None,
    session: AsyncSession = None,
) -> dict:
    """AI 生成 Playwright 脚本并保存"""
    from app.services.ai.ui_script_gen_service import generate_ui_script as _gen
    result = await _gen(case_id=case_id, session=session, env_id=env_id)
    await session.commit()
    return {
        "status": "ok",
        "script_id": result["script_id"],
        "version": result["version"],
        "content_preview": result["content"][:500],
        "message": f"已生成 Playwright 脚本 v{result['version']}",
    }


async def run_ui_script(
    case_id: str,
    env_id: str,
    session: AsyncSession = None,
    run_mode: str = "debug",
) -> dict:
    """执行用例的 UI 脚本并返回结果。

    run_mode 默认 debug —— 这个工具的语义就是"聚焦调试单条"，不进通过率口径；
    批量回归由 run_ui_scripts_batch 传 regression。"""
    cid = uuid.UUID(case_id)
    script = await script_service.get_active_script(session, cid, "ui")
    if not script:
        return {"status": "error", "message": "没有可执行的 UI 脚本，请先调用 tb_generate_ui_script 生成"}

    # 全局变量 + 环境变量（同名以环境为准）。见 variable_service.build_run_env ——
    # 四条执行路径原来各写一份 select，全局变量一条都没被注入过。
    from app.services.variable_service import build_run_env
    env_vars = await build_run_env(session, uuid.UUID(env_id) if env_id else None)

    # 注入场景变量：`SV_名字` 和裸名 `名字` 都注册 —— 和接口场景那边同一套规则。
    # 只注 SV_ 前缀的话，CC 照着「UI/接口共用同一份」写 os.getenv("PROJ_NAME")
    # 会静默拿到空串。
    from app.services.scenario_variable_service import (
        add_bare_names, resolve_scenario_variables,
    )
    add_bare_names(env_vars, await resolve_scenario_variables(
        session, cid, global_lookup=env_vars))

    # 注入鉴权 token TEST_TOKEN（S1.3）——脚本鉴权造数/清理用，避免 401
    if env_id:
        try:
            from app.services.token_service import get_target_token
            _case = await session.get(Case, cid)
            _pc = (_case.preconditions or "") if _case else ""
            _role = "TENANT" if any(k in _pc for k in ("租户", "tenant", "已授权")) else "ADMIN"
            _tok = await get_target_token(session, env_id, _role)
            if _tok:
                env_vars["TEST_TOKEN"] = _tok
        except Exception:
            pass

    file_name = script.file_name or "test_ui.py"
    content = script.content

    for var_name, var_value in env_vars.items():
        content = re.sub(
            rf'({re.escape(var_name)}\s*=\s*os\.getenv\(\s*"{re.escape(var_name)}"\s*,\s*)(["\']).*?\2',
            lambda m, v=var_value: f'{m.group(1)}{m.group(2)}{v}{m.group(2)}',
            content,
            count=1,
        )

    sandbox_dir = tempfile.mkdtemp(prefix="tb_ui_")
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

    # MCP 无登录上下文：executed_by 由 record_run 内部兜底取一个真实 active 用户，
    # 否则会命中 script_runs.executed_by 外键约束，导致「脚本明明跑通了却存不下结果」。
    from app.services import script_run_service
    await script_run_service.record_run(
        session,
        case_id=cid, script_id=script.id, script_type="ui",
        result=result, executed_by=None, run_mode=run_mode,
    )

    case = await session.get(Case, cid)
    script_run_service.apply_case_status(case, "ui", result.get("status"), run_mode)

    await session.commit()

    # **步骤必须回出来。** 此前只回 status + stdout_preview，而 stdout 前 1000 字
    # 全是 pytest 的启动横幅（platform/rootdir/plugins…），真正有用的那行
    # 「1 passed」和每一步验了什么都不在里面。于是跑挂之后只知道"挂了"，
    # 看不出挂在哪一步 —— 而 steps 本来就在 result 里带着，是这几行给扔了。
    steps = result.get("steps") or []
    out = {
        "status": result.get("status", "error"),
        "duration_ms": result.get("duration_ms"),
        "error_summary": result.get("error_summary"),
        "stepCount": len(steps),
        # seq 用枚举补 —— parse_step_json 的输出里没有这个键，
        # 取不到就全是 null，人没法说"第几步挂了"。
        "steps": [{
            "seq": i,
            "phase": s.get("step_phase") or s.get("phase"),
            "action": s.get("step_name") or s.get("action"),
            "status": s.get("status"),
            "durationMs": s.get("duration_ms"),
            **({"error": str(s.get("error_summary") or s.get("error"))[:300]}
               if s.get("error_summary") or s.get("error") else {}),
        } for i, s in enumerate(steps, 1)],
        "screenshots_count": len(result.get("screenshots") or []),
        "case_status": case.ui_status if case else None,
    }
    # 挂了就把失败那几步单独拎出来 —— 十几步里找那一行红的很费眼。
    bad = [s for s in out["steps"] if s.get("status") == "failed"]
    if bad:
        out["failedSteps"] = bad[:5]
    if not steps:
        out["stdout_preview"] = (result.get("stdout") or "")[-1500:]
        out["note"] = ("这次没解析到步骤。脚本用普通 Playwright 写法时平台会自动埋点"
                       "（断言和 goto/click/fill 各算一步）；一步都没有说明埋点没装上，"
                       "看 stdout_preview。")
    return out


async def run_ui_scripts_batch(
    case_ids: str,
    env_id: str,
    session: AsyncSession = None,
) -> dict:
    """批量执行多个用例的 UI 脚本（AI-free，逐个跑真实 Playwright），返回聚合结果。
    case_ids: 逗号分隔的用例 UUID 列表。用于减少人工、回归批量跑。"""
    ids = [x.strip() for x in (case_ids or "").split(",") if x.strip()]
    results = []
    passed = failed = skipped = 0
    for cid in ids:
        try:
            # 批量 = 回归，进通过率口径；失败允许把维度状态打回 debugging
            r = await run_ui_script(case_id=cid, env_id=env_id, session=session, run_mode="regression")
            st = r.get("status", "error")
        except Exception as e:
            st = "error"
            r = {"status": "error", "error_summary": str(e)[:200], "duration_ms": None}
        results.append({
            "case_id": cid,
            "status": st,
            "duration_ms": r.get("duration_ms"),
            "error_summary": r.get("error_summary"),
        })
        if st == "passed":
            passed += 1
        elif st == "skipped":
            skipped += 1
        else:
            failed += 1
    return {
        "total": len(ids),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "pass_rate": round(passed / len(ids) * 100, 1) if ids else 0,
        "results": results,
    }


async def get_ui_script_result(
    case_id: str,
    session: AsyncSession = None,
) -> dict:
    """获取最近一次 UI 脚本执行结果"""
    cid = uuid.UUID(case_id)

    script = await script_service.get_active_script(session, cid, "ui")

    result = await session.execute(
        select(ScriptRun)
        .where(ScriptRun.case_id == cid, ScriptRun.script_type == "ui")
        .order_by(ScriptRun.created_at.desc())
        .limit(1)
    )
    run = result.scalar_one_or_none()

    if not run:
        return {
            "has_script": script is not None,
            "script_version": script.version if script else None,
            "script_source": script.source if script else None,
            "last_run": None,
        }

    from app.services import run_evidence_service
    evidence = run_evidence_service.build(run)

    return {
        "has_script": script is not None,
        "script_version": script.version if script else None,
        "script_source": script.source if script else None,
        "last_run": {
            "run_id": str(run.id),
            "status": run.status,
            "run_mode": run.run_mode,
            "attempt": run.attempt,
            "duration_ms": run.duration_ms,
            "error_summary": run.error_summary,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            # 平台的初判：只判「是什么」（现象），「为什么」（归因）归你
            "failure_phenomenon": run.failure_phenomenon,
            **evidence,
        },
    }
