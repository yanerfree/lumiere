"""MCP 工具 — 接口场景的查询与执行。

生成不在这里：2026-08-15 下线「接口测试」模块时一并摘掉了 tb_generate_api_test
（凭接口文档 AI 造场景）。生成归外部 Claude Code，平台只做呈现和回推通道。
回推走 tb_sync_orchestrated_scenario。
"""
from __future__ import annotations

import json
import uuid
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_test import ApiTestScenario, ApiTestStep


async def list_api_test_scenarios(
    session: AsyncSession,
    branch_id: str,
    folder_id: str | None = None,
    status: str | None = None,
) -> dict:
    """列出接口测试场景。

    ⚠ 返回类型标注必须跟真实返回一致：这里返回的是 `{scenarios, total, usage}` 对象，
    标注写成 `list[dict]` 的话 **FastMCP 会照标注生成 outputSchema**，
    真调时客户端拿数组的 schema 去校验对象，直接
    `RuntimeError: Invalid structured content returned by tool tb_list_api_tests`。
    页面侧没事（它不校验 schema），只有 MCP 那条路会炸 —— 活体自测撞出来的。
    """
    q = select(ApiTestScenario).where(ApiTestScenario.branch_id == uuid.UUID(branch_id))
    if folder_id:
        q = q.where(ApiTestScenario.folder_id == uuid.UUID(folder_id))
    if status:
        q = q.where(ApiTestScenario.status == status)
    q = q.order_by(ApiTestScenario.created_at.desc())

    result = await session.execute(q)
    scenarios = result.scalars().all()

    # 原来这里分 boundToCases / standalone 两组返回，因为库里混着两个功能的产物。
    # 2026-08-15 之后只剩一种：接口场景必属于某条用例（source_case_id NOT NULL +
    # 外键 CASCADE，迁移 zz9orph1），standalone 那组恒为空 —— 保留一个永远空的
    # 分组只会让人以为"另一类还在"。平列表返回。
    #
    # 但**判重仍然不能看这里**：一个用例一条场景，这个列表只说明"接口维度做没做"，
    # 说明不了"这个测试点写没写过"。那条实测跑偏过（CC 看到一条全绿就不写新用例了），
    # 所以 usage 那句话留着。
    rows = []
    for sc in scenarios:
        rows.append({
            "id": str(sc.id), "code": sc.code, "title": sc.title,
            "status": sc.status, "source": sc.source, "priority": sc.priority,
            "stepCount": await _count_steps(session, sc.id),
            "sourceCaseId": str(sc.source_case_id),
        })
    return {
        "scenarios": rows,
        "total": len(rows),
        "usage": "这里列的是**各用例的接口维度产物**，一个用例最多一条。"
                 "判「这个测试点写没写过」用 tb_list_cases，别拿这个列表判 —— "
                 "看到一条全绿就以为「已经有了」，实测跑偏过。",
    }


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
            # **写进去的等待/重试必须读得回来**。此前这三个字段没在返回里，
            # 而 CC 唯一的读回通道就是这里 —— 它按工具说明写了 retry_timeout_ms，
            # 读回来看不见，就判定「平台把它剥掉了」，转头退回插占位步骤凑时间窗，
            # 正是 retry 想消灭的那个反模式。实测踩到：AT-0013 四步 retry=10000
            # 确实落库也确实生效（跑日志「重试 2 次后通过」），CC 却报「被丢弃」。
            #
            # 恒为 0 的也照给 —— 缺字段和「值是 0」在读的人眼里是两回事，
            # 省掉零值等于把「我没设过」和「平台没存住」重新混成一种。
            "retryTimeoutMs": st.retry_timeout_ms,
            "retryIntervalMs": st.retry_interval_ms,
            "waitMs": st.wait_ms,
            # 顺带给顺序和分组：CC 按 sort_order 定位「第几步」，
            # 没有它就只能靠数组下标猜，禁用步骤一多就对不上。
            "sortOrder": st.sort_order,
            "groupName": st.group_name,
            "enabled": st.enabled,
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


class _Finished(NamedTuple):
    """一条场景跑完之后，落用例接口维度需要的那几样。"""
    scenario_id: uuid.UUID
    passed: bool
    duration_ms: int
    error_summary: str | None


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
    finished: list[_Finished] = []
    # 批量跑多条时，耗时只能算「本场景的步骤」——从上一条 scenario_done 之后数起。
    # 直接 sum(results) 会把前面场景的步骤一起算进来，越往后的场景耗时越离谱。
    cursor = 0
    async for event in run_batch(ids, session, base_env=base_env):
        if event.type == "step_result":
            row = {
                "step": event.data.get("stepName"),
                "status": event.data.get("status"),
                "statusCode": event.data.get("statusCode"),
                "duration": event.data.get("duration"),
            }
            # 挂了的步骤要说清为什么。**通过的步骤保持精简** —— 十几步全带上
            # 响应体，CC 的 context 直接被这一个返回值吃掉。
            #
            # 之前这里只回 {step,status,statusCode,duration}，于是 CC 看到的是
            # 「确认推送已收敛到全部网关节点 / status=fail / statusCode=200」——
            # 200 却失败，它完全不知道该改什么，只能绕过。而 error / assertions /
            # responseBody 这些**本来就在事件里带着**，是这几行给扔了。
            if row["status"] == "fail":
                from app.services.api_test_runner import failure_detail
                row.update(failure_detail(event.data.get("assertions"), event.data.get("error")))
                body = event.data.get("responseBody")
                if body is not None:
                    row["responseSample"] = str(body)[:400]
            results.append(row)
        elif event.type == "precheck_result":
            # 共享资源探测：探不到的话后面引用 ${资源名} 的步骤会直接不发请求。
            # 不回给调用方，CC 只能看到"某一步 statusCode=null"，无从查起。
            for c in (event.data.get("autoCreated") or []):
                results.append({"precheck": f"共享资源「{c['name']}」当前环境没有，"
                                            f"平台已按 create_def 补建（keep=true，不会被清理）",
                                "detail": c.get("autoCreated")})
            miss = event.data.get("missing") or []
            if miss:
                results.append({
                    "precheck": "共享资源没探到 —— 引用 ${资源名} 的步骤会直接不发请求（statusCode 为空）",
                    "missing": miss,
                    "howToFix": "按 tb_list_global_data(probe=true) 看它的 createDef 自己把资源造出来；"
                                "或检查 exists_check.extract 的 JSONPath 是不是没抽到值。",
                })
        elif event.type == "scenario_done":
            results.append({
                "scenario": event.data.get("title"),
                "passed": event.data.get("passed"),
                "passCount": event.data.get("passCount"),
                "failCount": event.data.get("failCount"),
            })
            sid = event.data.get("scenarioId")
            if sid:
                mine = [r for r in results[cursor:] if "step" in r]
                # 错误摘要给 failure_triage 判「现象」用。不给的话现象一律 unknown，
                # 用例的执行历史里只剩一个红点，看不出是断言不符还是根本没发出请求。
                bad = [r for r in mine if r.get("status") == "fail"]
                err = "；".join(
                    f"{r.get('step')}：{r.get('why') or r.get('error') or '断言未通过'}"
                    for r in bad[:3]
                ) or None
                finished.append(_Finished(
                    scenario_id=uuid.UUID(sid),
                    passed=bool(event.data.get("passed")),
                    duration_ms=sum(r.get("duration") or 0 for r in mine),
                    error_summary=err,
                ))
            cursor = len(results)

    applied = await _apply_case_dimension(session, finished)

    out = {"results": results, "totalSteps": len([r for r in results if "step" in r])}
    if applied:
        out["caseStatus"] = applied
    return out


async def _apply_case_dimension(
    session: AsyncSession,
    finished: list[_Finished],
) -> list[dict]:
    """把这次执行落到**用例的接口维度**上：执行历史 + api_status。

    为什么非做不可：此前这段只存在于 `_create_report()` 里，而它被
    `if all_results and user_id:` 挡着 —— MCP 通道调 run_batch 不传 user_id，
    于是整段跳过。后果是步骤级 last_status 存了，用例级一个字段都不动：
    api_status 永远停在 debugging，而 `_owes()` 判它是「CC 还欠着」，
    于是 pending_only 的断点续跑对接口维度**永不收敛** —— 已经跑绿的场景
    每一轮都会被当成待办重做一遍。实测：AT-0013 跑到 19/19 全绿，
    TC-FWGL-00003 的 owes 仍然返回 ["api"]。

    **这里是「接口测试模块」和「用例接口维度」的分界线，只放编排场景过去。**
    单接口场景（source_case_id 为空）是接口测试模块的本职产物，不属于任何用例，
    它跑成什么样都不该改动任何用例的状态 —— 混过去的后果是用例的接口维度被一条
    与它无关的场景推着走，页面上显示"这条用例的接口测好了"，而那条场景根本不测它。
    孤儿场景（曾经绑过、用例已删）同样拦在外面：source_case_id 指向的用例取不到就跳过。

    run_mode 用 DEBUG 而不是 REGRESSION：CC 手动跑一条是"我正在调"，
    跑挂了不代表这条用例坏了。用 REGRESSION 会在失败时把状态打回 debugging，
    而断点续跑正是靠状态判待办 —— 一次调试失败就能让已完成的用例被捡回来重做。
    真回归（计划执行）仍走 _create_report 那条，那里该打回就打回。
    """
    if not finished:
        return []

    from app.models.api_test import ApiTestScenario
    from app.models.case import Case
    from app.services import script_run_service

    applied: list[dict] = []
    for fin in finished:
        scenario = await session.get(ApiTestScenario, fin.scenario_id)
        if scenario is None or scenario.source_case_id is None:
            continue                      # ← 单接口场景到此为止，不碰用例
        case = await session.get(Case, scenario.source_case_id)
        if case is None:
            continue                      # ← 孤儿场景同理
        status = "passed" if fin.passed else "failed"
        # executed_by=None：MCP 无登录上下文，record_run 内部兜底取一个真实
        # active 用户，否则命中 executed_by 外键约束，跑通了却存不下。
        await script_run_service.record_run(
            session,
            case_id=case.id, script_type="api",
            result={"status": status, "duration_ms": fin.duration_ms,
                    "error_summary": fin.error_summary},
            executed_by=None, run_mode=script_run_service.DEBUG,
        )
        before = case.api_status
        script_run_service.apply_case_status(case, "api", status, script_run_service.DEBUG)
        applied.append({"caseCode": case.case_code, "apiStatus": case.api_status,
                        "changed": before != case.api_status})

    await session.commit()
    return applied


async def _count_steps(session: AsyncSession, scenario_id: uuid.UUID) -> int:
    from sqlalchemy import func
    result = await session.execute(
        select(func.count()).where(ApiTestStep.scenario_id == scenario_id)
    )
    return result.scalar() or 0


async def check_assertion_bite(
    session: AsyncSession,
    case_id: str,
    skip_steps: str,
    env_id: str | None = None,
) -> dict:
    """把动作步跳掉跑一遍，看后面的断言会不会红 —— **回答"这条断言到底有没有用"**。

    绿的用例不等于有效的用例：一条方向写反的断言是绿的，一条恒真断言
    （动作前后都成立）也是绿的。数量和指纹都判不了这件事，只有**删掉原因、
    看结果是否消失**能判。

    用法：`skip_steps` 填那个**改状态的动作步**名字（审批通过／禁用服务／驳回／删除），
    别填产出 id 的创建步 —— 跳掉创建，后面全部卡在"变量未解析"，什么也证明不了。

    只读：不写步骤状态、不建报告、不动用例维度 —— 它是一次诊断，不是一次回归。
    ⚠ 但**请求是真发的**：没被跳掉的步骤照跑，会在被测系统里造数据。
    跳的正是清理步时，那一趟的残留不会被删，也不在 tb_check_env_hygiene 的视野里
    （变异运行不留痕）—— 自己收尾。

    参数: case_id(用例UUID), skip_steps(要跳掉的步骤名，多个用逗号分隔),
    env_id(强烈建议，不传就没有 BASE_URL/账号，链子跑不起来)
    """
    from app.services.assertion_bite import check_assertion_bite as _run

    scenario = (await session.execute(
        select(ApiTestScenario).where(ApiTestScenario.source_case_id == uuid.UUID(case_id))
        .order_by(ApiTestScenario.created_at)
    )).scalars().first()
    if scenario is None:
        return {"error": f"用例 {case_id} 还没有接口场景，没东西可验。"}

    base_env: dict = {}
    env_name = None
    if env_id:
        from app.services import environment_service
        try:
            merged = await environment_service.get_merged_variables(session, uuid.UUID(env_id))
            base_env = {item["key"]: item["value"] for item in merged}
        except Exception:  # noqa: BLE001
            pass
    names = [n.strip() for n in (skip_steps or "").split(",") if n.strip()]
    return await _run(session, scenario.id, names, base_env=base_env, env_name=env_name)


async def check_env_hygiene(
    session: AsyncSession,
    project_id: str,
    branch_id: str | None = None,
) -> dict:
    """被测环境里有没有测试残留 —— 平台只报**它能证明的那部分**。

    两类：①这条链造了东西却没有清理步骤（每跑一次留一份）②最后一次运行没跑到清理，
    那次造的 id 已从创建步骤的响应里抽出来，删它的请求就是那条清理步骤。

    ⚠ 只看接口场景、且只看得见最后一次运行 —— 报 0 条不等于环境是干净的。

    参数: project_id(项目UUID), branch_id(可选，只看某个分支)
    """
    from app.services.env_hygiene import check_env_hygiene as _run
    return await _run(session, project_id, branch_id)
