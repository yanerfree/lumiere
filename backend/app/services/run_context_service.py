"""执行前上下文准备 + 门禁 —— 跑用例脚本前：
1) 预检项目全局资源(S1.4)：缺失 → 拦截返回「待确认」，不盲跑；
2) 通过 → 解析场景变量(Epic2) + 鉴权token(S1.3)，产出可注入执行环境的 env_vars。

让「单用例执行」自包含、可反复、缺前置早暴露而非跑到一半 401/找不到数据才失败。
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case


def _role_for_case(case: Case | None) -> str:
    pc = (case.preconditions or "") if case else ""
    return "TENANT" if any(k in pc for k in ("租户", "tenant", "已授权")) else "ADMIN"


async def _project_id_for_case(session: AsyncSession, case: Case):
    if not case or not case.branch_id:
        return None
    from app.models.project import Branch
    branch = await session.get(Branch, case.branch_id)
    return branch.project_id if branch else None


async def preflight(session: AsyncSession, case_id, env_id, role: str | None = None) -> dict:
    """执行前门禁 + 环境准备。
    返回 {ready, missing[], envVars{}, tokenAcquired, role}。
    ready=False 表示有全局前置缺失，应交用户确认/补建后再跑。"""
    cid = uuid.UUID(str(case_id))
    case = await session.get(Case, cid)
    role = (role or _role_for_case(case)).upper()
    project_id = await _project_id_for_case(session, case)

    # 1) 全局资源预检（S1.4）
    missing: list[dict] = []
    if project_id and env_id:
        from app.services import precheck_service
        report = await precheck_service.check_resources(session, project_id, env_id, role)
        missing = report.get("missing", []) or []

    # 2) 解析场景变量 + token（即使 missing 也解析，便于前端展示；但 ready 由 missing 决定）
    env_vars: dict[str, str] = {}
    token_acquired = False
    try:
        from app.services.scenario_variable_service import (
            add_bare_names, resolve_scenario_variables,
        )
        add_bare_names(env_vars, await resolve_scenario_variables(
            session, cid, global_lookup={}))
    except Exception:
        pass
    if env_id:
        try:
            from app.services.token_service import get_target_token
            tok = await get_target_token(session, env_id, role)
            if tok:
                env_vars["TEST_TOKEN"] = tok
                token_acquired = True
        except Exception:
            pass

    # 3) 该用例的 UI 脚本是否依赖鉴权 token（用于前端跑前提示：需 token 但当前环境取不到 → 会 401）
    script_uses_token = False
    try:
        from app.services import script_service
        script = await script_service.get_active_script(session, cid, "ui")
        if script and "TEST_TOKEN" in (script.content or ""):
            script_uses_token = True
    except Exception:
        pass

    return {
        "ready": len(missing) == 0,
        "missing": missing,
        "envVars": env_vars,
        "tokenAcquired": token_acquired,
        "scriptUsesToken": script_uses_token,
        "role": role,
    }
