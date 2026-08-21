"""分支深拷贝服务 — 新建分支时从源分支复制数据（ADR-5）"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def copy_branch_data(
    session: AsyncSession,
    source_branch_id: uuid.UUID,
    target_branch_id: uuid.UUID,
    project_id: uuid.UUID,
    modules: list[str],
    user_id: uuid.UUID,
) -> dict:
    """深拷贝源分支数据到目标分支。

    modules: ["cases", "api_test", "apis"] 中的子集
    所有 ID 映射为新 ID，分支间完全独立。
    返回各模块复制的数量统计。
    """
    stats = {}
    api_node_map: dict[uuid.UUID, uuid.UUID] = {}
    case_map: dict[uuid.UUID, uuid.UUID] = {}

    if "cases" in modules:
        stats["cases"], case_map = await _copy_cases(session, source_branch_id, target_branch_id, user_id)

    if "apis" in modules:
        stats["apis"], api_node_map = await _copy_api_nodes(session, source_branch_id, target_branch_id, project_id, user_id)

    if "api_test" in modules:
        # 接口场景必须绑用例（source_case_id NOT NULL），所以它只能跟着用例走：
        # 没勾「用例」就没有 case_map，场景在目标分支找不到宿主，只能整体跳过。
        # 这不是缺陷是定义 —— 一条不属于任何用例的接口场景，本来就不该存在。
        stats["apiTest"] = await _copy_api_tests(
            session, source_branch_id, target_branch_id, project_id, user_id,
            api_node_map, case_map,
        )

    # **指纹必须最后算。** 它盖的是三份产物（手工步骤/预期、接口场景正文、
    # UI 脚本正文），而接口场景是在 _copy_api_tests 里复制的 —— 在 _copy_cases
    # 里算就只盖到手工步骤那一份，等于给"接口断言被改过"的用例发了通行证。
    if case_map:
        await _stamp_fingerprints(session, list(case_map.values()))

    await session.commit()
    logger.info("Branch copy done: %s -> %s, stats=%s", source_branch_id, target_branch_id, stats)
    return stats


async def _stamp_fingerprints(session: AsyncSession, case_ids: list[uuid.UUID]) -> None:
    """给刚复制出来的用例盖内容指纹。

    这是照抄堆自动过审条件 2（内容与源分支逐字一致）的**唯一**机械依据：
    平台自己比内容，不听 CC 声明「我没改」。CC 改了任何一个字（包括标题）
    指纹就对不上，自动过审立刻降级成必须 AI 审。

    算不出来（比如某条用例中途被删）就留 NULL —— **NULL 的语义是"不知道它跟谁
    一致" → 条件 2 不成立 → 走人审**。这个方向是安全的（多审一次），
    反过来（算不出来就当一致）是假绿。
    """
    from app.models.case import Case
    from app.services.branch_diff_service import compute_fingerprint

    for cid in case_ids:
        try:
            fp = await compute_fingerprint(session, cid)
        except Exception:  # noqa: BLE001 — 一条算挂了不该把整次分支复制打死
            logger.exception("算内容指纹失败，这条留 NULL（会走人审）: case=%s", cid)
            continue
        if not fp:
            continue
        case = (await session.execute(select(Case).where(Case.id == cid))).scalar_one_or_none()
        if case is not None:
            case.content_fingerprint = fp
    await session.flush()


async def _copy_api_nodes(
    session: AsyncSession,
    source_branch_id: uuid.UUID,
    target_branch_id: uuid.UUID,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
) -> tuple[dict, dict[uuid.UUID, uuid.UUID]]:
    """复制 API 接口树（含历史无分支数据），返回 (统计, 旧ID→新ID 映射)。"""
    from app.models.api_collection import ApiNode

    nodes_result = await session.execute(
        select(ApiNode).where(
            ApiNode.project_id == project_id,
            (ApiNode.branch_id == source_branch_id) | (ApiNode.branch_id == None),
        ).order_by(ApiNode.sort_order, ApiNode.created_at)
    )
    nodes = nodes_result.scalars().all()

    node_map: dict[uuid.UUID, uuid.UUID] = {}
    pending = list(nodes)
    while pending:
        progressed = False
        remaining = []
        for n in pending:
            if n.parent_id is None or n.parent_id in node_map:
                new_node = ApiNode(
                    project_id=project_id,
                    branch_id=target_branch_id,
                    parent_id=node_map.get(n.parent_id) if n.parent_id else None,
                    node_type=n.node_type,
                    name=n.name,
                    sort_order=n.sort_order,
                    method=n.method,
                    url=n.url,
                    params=n.params,
                    headers=n.headers,
                    body=n.body,
                    body_type=n.body_type,
                    auth=n.auth,
                    description=n.description,
                    created_by=user_id,
                )
                session.add(new_node)
                await session.flush()
                node_map[n.id] = new_node.id
                progressed = True
            else:
                remaining.append(n)
        if not progressed:
            logger.warning("ApiNode copy stuck, orphan parents: %s", [str(n.id) for n in remaining])
            break
        pending = remaining

    return {"nodes": len(node_map)}, node_map


async def _copy_cases(
    session: AsyncSession,
    source_branch_id: uuid.UUID,
    target_branch_id: uuid.UUID,
    user_id: uuid.UUID,
) -> tuple[dict, dict[uuid.UUID, uuid.UUID]]:
    """复制用例文件夹 + 用例。返回 (统计, 旧用例id → 新用例id)。"""
    from app.models.case import Case, CaseFolder

    folders_result = await session.execute(
        select(CaseFolder).where(CaseFolder.branch_id == source_branch_id).order_by(CaseFolder.depth)
    )
    folders = folders_result.scalars().all()

    folder_map: dict[uuid.UUID, uuid.UUID] = {}
    for f in folders:
        new_folder = CaseFolder(
            branch_id=target_branch_id,
            name=f.name,
            path=f.path,
            parent_id=folder_map.get(f.parent_id) if f.parent_id else None,
            depth=f.depth,
            sort_order=f.sort_order,
        )
        session.add(new_folder)
        await session.flush()
        folder_map[f.id] = new_folder.id

    cases_result = await session.execute(
        select(Case).where(Case.branch_id == source_branch_id)
    )
    cases = cases_result.scalars().all()

    count = 0
    case_map: dict[uuid.UUID, uuid.UUID] = {}
    for c in cases:
        if c.deleted_at:
            continue
        new_case = Case(
            branch_id=target_branch_id,
            folder_id=folder_map.get(c.folder_id) if c.folder_id else None,
            case_code=c.case_code,
            tea_id=c.tea_id,
            title=c.title,
            type=c.type,
            priority=c.priority,
            preconditions=c.preconditions,
            steps=c.steps,
            expected_result=c.expected_result,
            variables_used=c.variables_used,
            api_scenario=c.api_scenario,
            ui_scenario=c.ui_scenario,
            source=c.source,
            automation_status="pending",
            script_ref_file=c.script_ref_file,
            script_ref_func=c.script_ref_func,
            remark=c.remark,
            # 承诺（做几维）和预期落款跟着走。不带过去，新分支上每条用例都是
            # 「没人确认过预期」，第一次回推就被门禁挡住，人得把依据重填一遍。
            target_level=c.target_level,
            target_level_reason=c.target_level_reason,
            expected_confirmed_at=c.expected_confirmed_at,
            expected_confirmed_by=c.expected_confirmed_by,
            expected_confirmed_actor=c.expected_confirmed_actor,
            expected_confirmed_note=c.expected_confirmed_note,
        )
        # **我是从哪条复制来的。** 跨分支只靠 case_code / tea_id 对同一条不够 ——
        # 那两个是人给的编号，复制之后源分支那条还会继续改，编号一样内容早就不一样了。
        new_case.source_case_id = c.id
        session.add(new_case)
        await session.flush()          # 要立刻拿到新 id 去建映射
        # 场景变量 + UI 脚本正文。script_ref_file 上面已经拷了，正文不拷就是个空指针。
        from app.services.case_service import copy_case_side_assets, sync_manual_status
        sync_manual_status(new_case)
        await copy_case_side_assets(session, c.id, new_case.id)
        # **强行置回草稿 / 待提审。** sync_manual_status 上面把 manual_status 推成
        # completed（步骤确实有内容），而它顺手调的 sync_review_status 对
        # target_level=spec 的用例来说 dims 只有 manual 一维 —— all_done 立刻成立，
        # 于是一条只承诺手工步骤的用例**一复制过来就显示「完成 + 待审」**，
        # 而它在新版本上一次都没验过。实测三个档位的连锁结果：
        #     spec     → lifecycle=done  manual=completed review=pending  ← 假显示
        #     spec_api → lifecycle=draft manual=completed review=None
        #     full     → lifecycle=draft manual=completed review=None
        # 纪律是「新版本上没验过就不能算完成」，手工步骤也一样（新版本的页面
        # 可能根本不那么走了）。
        #
        # 光在这里置回是不够的 —— 任何一次后续调用都会重新推回「待审」。
        # 耐用的那一半在 script_run_service.copied_unverified()：它看
        # content_fingerprint（下面那一步写），所以这两处必须一起改。
        new_case.lifecycle_status = "draft"
        new_case.review_status = None
        case_map[c.id] = new_case.id
        count += 1

    await session.flush()
    # 顺带回一份 旧用例id → 新用例id。接口场景靠它改绑到目标分支的用例上
    # （source_case_id 是 NOT NULL，不改绑就只能整体跳过）。
    return {"folders": len(folders), "cases": count}, case_map


async def _copy_api_tests(
    session: AsyncSession,
    source_branch_id: uuid.UUID,
    target_branch_id: uuid.UUID,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    api_node_map: dict[uuid.UUID, uuid.UUID] | None = None,
    case_map: dict[uuid.UUID, uuid.UUID] | None = None,
) -> dict:
    """复制接口场景的文件夹 + 场景 + 步骤。状态重置为 draft，执行历史清空。

    `case_map`（旧用例id → 新用例id）：场景必须绑用例（source_case_id NOT NULL），
    所以每条场景都要改绑到目标分支的那条用例上。映射里找不到宿主的**跳过**，
    数量记在 `skippedNoCase` 里 —— 静默少复制几条比报错更难查。

    `api_node_map` 参数已废弃：它是给 source_api_ids 重映射用的，
    而那一列 2026-08-15 随「接口测试」模块一起删了（迁移 zza0dead1）。
    形参先留着不动调用方，下次清理时一并摘。
    """
    from app.models.api_test import ApiTestScenario, ApiTestStep
    from app.models.api_test_folder import ApiTestFolder

    folders_result = await session.execute(
        select(ApiTestFolder).where(ApiTestFolder.branch_id == source_branch_id)
    )
    folders = folders_result.scalars().all()

    # 先复制没有父级的，再复制子级（简单两轮，最多支持两层嵌套按 parent 排序）
    folder_map: dict[uuid.UUID, uuid.UUID] = {}
    pending = list(folders)
    while pending:
        progressed = False
        remaining = []
        for f in pending:
            if f.parent_id is None or f.parent_id in folder_map:
                new_folder = ApiTestFolder(
                    branch_id=target_branch_id,
                    name=f.name,
                    parent_id=folder_map.get(f.parent_id) if f.parent_id else None,
                    sort_order=f.sort_order,
                )
                session.add(new_folder)
                await session.flush()
                folder_map[f.id] = new_folder.id
                progressed = True
            else:
                remaining.append(f)
        if not progressed:
            logger.warning("Folder copy stuck, orphan parents: %s", [str(f.id) for f in remaining])
            break
        pending = remaining

    scenarios_result = await session.execute(
        select(ApiTestScenario).where(ApiTestScenario.branch_id == source_branch_id)
    )
    scenarios = scenarios_result.scalars().all()

    scenario_count = 0
    step_count = 0
    skipped_no_case = 0
    for sc in scenarios:
        # 场景跟着用例走。目标分支里没有对应用例就跳过 —— 硬建的话会撞
        # source_case_id 的非空约束，把整次分支复制打死。
        new_case_id = (case_map or {}).get(sc.source_case_id)
        if new_case_id is None:
            skipped_no_case += 1
            continue
        new_scenario = ApiTestScenario(
            project_id=project_id,
            branch_id=target_branch_id,
            code=sc.code,
            title=sc.title,
            folder_id=folder_map.get(sc.folder_id) if sc.folder_id else None,
            priority=sc.priority,
            description=sc.description,
            status="draft",
            source=sc.source,
            source_case_id=new_case_id,
            env_variables=sc.env_variables,
            created_by=user_id,
        )
        session.add(new_scenario)
        await session.flush()
        scenario_count += 1

        steps_result = await session.execute(
            select(ApiTestStep).where(ApiTestStep.scenario_id == sc.id).order_by(ApiTestStep.sort_order)
        )
        for st in steps_result.scalars().all():
            session.add(ApiTestStep(
                scenario_id=new_scenario.id,
                sort_order=st.sort_order,
                group_name=st.group_name,
                name=st.name,
                method=st.method,
                url=st.url,
                headers=st.headers,
                body=st.body,
                assertions=st.assertions,
                variables_extract=st.variables_extract,
                enabled=st.enabled,
                pre_script=st.pre_script,
                post_script=st.post_script,
            ))
            step_count += 1

    await session.flush()
    if skipped_no_case:
        # 别静默 —— 少复制了几条，人得知道为什么（多半是没勾「用例」那个模块）
        logger.warning(
            "分支复制跳过 %d 条接口场景：目标分支里没有对应用例（复制时要一起勾选「用例」）",
            skipped_no_case,
        )
    return {"folders": len(folder_map), "scenarios": scenario_count,
            "steps": step_count, "skippedNoCase": skipped_no_case}
