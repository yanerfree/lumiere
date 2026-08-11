"""MCP 工具 — 接口测试场景的生成、查询、执行"""
from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_test import ApiTestScenario, ApiTestStep
from app.models.api_test_folder import ApiTestFolder


async def generate_api_test(
    session: AsyncSession,
    branch_id: str,
    api_info: str,
    folder_name: str | None = None,
) -> dict:
    """根据接口定义 AI 生成测试场景。Claude Code 通过此工具提交接口信息，平台 AI 生成测试用例。
    api_info 应包含完整的接口定义（method, url, 参数约束, 响应格式等）。
    folder_name 可选，指定生成到哪个文件夹（不存在则自动创建）。"""
    from app.services.ai_config_resolver import resolve_ai_config
    from app.services.ai.api_scenario_gen_service import generate_api_test as _generate

    bid = uuid.UUID(branch_id)
    scenario = await session.execute(select(ApiTestScenario).where(ApiTestScenario.branch_id == bid).limit(1))
    sc = scenario.scalars().first()
    if not sc:
        return {"error": "分支下没有任何场景，无法确定 project_id。请先在平台创建一个场景。"}

    project_id = sc.project_id
    user_id = sc.created_by

    folder_id = None
    if folder_name:
        fr = await session.execute(
            select(ApiTestFolder).where(ApiTestFolder.branch_id == bid, ApiTestFolder.name == folder_name)
        )
        folder = fr.scalars().first()
        if not folder:
            folder = ApiTestFolder(branch_id=bid, name=folder_name)
            session.add(folder)
            await session.flush()
        folder_id = folder.id

    ai_config = await resolve_ai_config(project_id, session, capability="api-test-generate")
    if not ai_config:
        return {"error": "AI 服务未配置"}

    created = []
    async for event in _generate(
        project_id=project_id, branch_id=bid,
        api_info=api_info, api_ids=None,
        env_variables=None, folder_id=folder_id,
        ai_config=ai_config, session=session, user_id=user_id,
    ):
        if event.type == "scenario_created":
            created.append({"code": event.data["code"], "title": event.data["title"], "stepCount": event.data["stepCount"]})
        elif event.type == "error":
            return {"error": event.data["message"], "partialResults": created}

    return {"scenarios": created, "total": len(created)}


async def list_api_test_scenarios(
    session: AsyncSession,
    branch_id: str,
    folder_id: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """列出接口测试场景"""
    q = select(ApiTestScenario).where(ApiTestScenario.branch_id == uuid.UUID(branch_id))
    if folder_id:
        q = q.where(ApiTestScenario.folder_id == uuid.UUID(folder_id))
    if status:
        q = q.where(ApiTestScenario.status == status)
    q = q.order_by(ApiTestScenario.created_at.desc())

    result = await session.execute(q)
    scenarios = result.scalars().all()
    return [{
        "id": str(s.id),
        "code": s.code,
        "title": s.title,
        "status": s.status,
        "source": s.source,
        "priority": s.priority,
        "stepCount": await _count_steps(session, s.id),
    } for s in scenarios]


async def get_api_test_scenario(
    session: AsyncSession,
    scenario_id: str,
) -> dict:
    """获取场景详情（含所有步骤）"""
    s = await session.get(ApiTestScenario, uuid.UUID(scenario_id))
    if not s:
        return {"error": "场景不存在"}

    steps_result = await session.execute(
        select(ApiTestStep).where(ApiTestStep.scenario_id == s.id).order_by(ApiTestStep.sort_order)
    )
    steps = steps_result.scalars().all()

    return {
        "id": str(s.id),
        "code": s.code,
        "title": s.title,
        "status": s.status,
        "source": s.source,
        "priority": s.priority,
        "steps": [{
            "id": str(st.id),
            "name": st.name,
            "method": st.method,
            "url": st.url,
            "headers": st.headers,
            "body": st.body,
            "assertions": st.assertions,
            "variablesExtract": st.variables_extract,
            "lastStatus": st.last_status,
            # 只给 pass/fail 等于告诉 CC「挂了，自己猜」。跑挂之后最需要的三样：
            # 错误原文、实际状态码、每条提取到底取到没有 —— 都在 last_response 里，
            # 此前一个都没送出来，CC 只能去猜或者放弃。
            **_last_run_facts(st.last_response),
        } for st in steps],
    }


def _last_run_facts(last_response: dict | None) -> dict:
    """最近一次执行留下的可诊断信息。没跑过就返回空，别塞一堆 None。"""
    if not isinstance(last_response, dict):
        return {}
    out: dict = {}
    if last_response.get("error"):
        out["lastError"] = str(last_response["error"])[:600]
    if last_response.get("statusCode") is not None:
        out["lastStatusCode"] = last_response["statusCode"]
    # 断言逐条的通过情况：哪一条没过、期望是什么、实际是什么
    fails = [a for a in (last_response.get("assertions") or [])
             if isinstance(a, dict) and not a.get("passed")]
    if fails:
        out["failedAssertions"] = fails[:5]
    # 提取物：取没取到最关键 —— 取不到的话，后面用它的步骤会全挂，
    # 而报错会落在那些步骤上，指错地方。
    ex = ((last_response.get("request") or {}).get("extracted")) or []
    bad = [e for e in ex if isinstance(e, dict) and not e.get("ok")]
    if bad:
        out["failedExtracts"] = bad[:5]
    return out


async def run_api_test(
    session: AsyncSession,
    scenario_ids: str,
    env_id: str | None = None,
) -> dict:
    """执行接口测试场景（同步执行，返回结果汇总）。

    env_id：可选但**强烈建议**——传了才会把该环境的变量（BASE_URL/账号/token 等）
    注入执行环境，${BASE_URL}/${ADMIN_USERNAME} 这类引用才能解析。不传则只有场景自带
    env_variables + 场景变量 + 运行时内置，编排场景多半会因缺 BASE_URL 而失败。"""
    from app.services.api_test_runner import run_batch

    base_env: dict = {}
    if env_id:
        from app.services import environment_service
        try:
            merged = await environment_service.get_merged_variables(session, uuid.UUID(env_id))
            base_env = {item["key"]: item["value"] for item in merged}
        except Exception:
            pass

    ids = [uuid.UUID(sid.strip()) for sid in scenario_ids.split(",")]
    results = []
    async for event in run_batch(ids, session, base_env=base_env):
        if event.type == "step_result":
            results.append({
                "step": event.data.get("stepName"),
                "status": event.data.get("status"),
                "statusCode": event.data.get("statusCode"),
                "duration": event.data.get("duration"),
            })
        elif event.type == "scenario_done":
            results.append({
                "scenario": event.data.get("title"),
                "passed": event.data.get("passed"),
                "passCount": event.data.get("passCount"),
                "failCount": event.data.get("failCount"),
            })

    return {"results": results, "totalSteps": len([r for r in results if "step" in r])}


async def _count_steps(session: AsyncSession, scenario_id: uuid.UUID) -> int:
    from sqlalchemy import func
    result = await session.execute(
        select(func.count()).where(ApiTestStep.scenario_id == scenario_id)
    )
    return result.scalar() or 0
