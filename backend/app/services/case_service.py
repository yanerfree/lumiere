import uuid
from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete, func as sa_func, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.audit import audit_log
from app.models.case import Case
from app.models.plan import PlanCase
from app.models.report import TestReportScenario
from app.schemas.case import CreateCaseRequest, UpdateCaseRequest
from app.services.import_service import _get_or_create_folder, _next_case_code


@audit_log(action="create", target_type="case")
async def create_case(
    session: AsyncSession, branch_id: uuid.UUID, data: CreateCaseRequest, source: str = "manual"
) -> Case:
    """创建用例。自动生成 case_code，自动创建目录。

    编号走 `SELECT MAX(case_code)` → +1，两个 CC 同时在同一 branch+module 建用例
    会算出同一个号，被 `uq_case_branch_code` 拦住。数据不会写坏，但第二个写入方
    拿到的是原始的 IntegrityError —— CC 看到一个语焉不详的失败很可能去改脚本，
    而正确动作只是重试。所以在这里吞掉冲突并重试（重跑 MAX 就会拿到新号）。
    """
    from app.services.concurrency import retry_on_conflict

    async def _create() -> Case:
        folder_id, _, _ = await _get_or_create_folder(
            session, branch_id, data.module, data.submodule
        )
        case_code = await _next_case_code(session, branch_id, data.module)
        return await _build_and_flush(session, branch_id, case_code, folder_id, data, source)

    return await retry_on_conflict(_create, session, what="创建用例")


async def _build_and_flush(session, branch_id, case_code, folder_id, data, source) -> Case:
    case = Case(
        branch_id=branch_id,
        case_code=case_code,
        title=data.title,
        type=data.type,
        folder_id=folder_id,
        priority=data.priority,
        preconditions=data.preconditions,
        steps=data.steps,
        expected_result=data.expected_result,
        variables_used=data.variables_used,
        api_scenario=data.api_scenario,
        ui_scenario=data.ui_scenario,
        api_scenario_status=data.api_scenario_status,
        ui_scenario_status=data.ui_scenario_status,
        is_api_template=data.is_api_template,
        is_ui_template=data.is_ui_template,
        source=source,
        automation_status="pending",
        script_ref_file=data.script_ref_file,
        script_ref_func=data.script_ref_func,
        remark=data.remark,
        target_level=getattr(data, "target_level", None) or "spec",
    )
    session.add(case)
    await session.flush()
    await session.refresh(case)
    return case


async def get_case(session: AsyncSession, case_id: uuid.UUID) -> Case:
    """根据 ID 获取用例详情。"""
    result = await session.execute(
        select(Case).where(Case.id == case_id, Case.deleted_at.is_(None))
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise NotFoundError(code="CASE_NOT_FOUND", message="用例不存在")
    return case


@audit_log(action="update", target_type="case")
async def update_case(
    session: AsyncSession, case_id: uuid.UUID, data: UpdateCaseRequest
) -> Case:
    """更新用例。"""
    case = await get_case(session, case_id)

    if data.title is not None:
        case.title = data.title
    if data.type is not None:
        case.type = data.type
    if data.priority is not None:
        case.priority = data.priority
    if data.preconditions is not None:
        case.preconditions = data.preconditions
    # 改了步骤或预期结果，之前那次「预期结果已确认」就作废 —— 确认的是当时那一版。
    # 不作废的话，确认会变成一次性的终身通行证：确认完再把预期改成模糊的，
    # 页面照样显示「已确认」，等于白确认。
    #
    # ⚠ 四个字段必须一起清。只清 at/by 的话：改完步骤 → 在平台上点一次「确认」
    # （那个接口只写 at/by）→ 页面就会把**改动前那一版**的确认内容当成本次的展示出来。
    # 实测踩过，所以抽成一个函数，别再分头清。
    def _invalidate_confirmation() -> None:
        case.expected_confirmed_at = None
        case.expected_confirmed_by = None
        case.expected_confirmed_actor = None
        case.expected_confirmed_note = None

    if data.steps is not None:
        if data.steps != case.steps:
            _invalidate_confirmation()
        case.steps = data.steps
    if data.expected_result is not None:
        if data.expected_result != case.expected_result:
            _invalidate_confirmation()
        case.expected_result = data.expected_result
    if data.variables_used is not None:
        case.variables_used = data.variables_used
    if data.api_scenario is not None:
        case.api_scenario = data.api_scenario
    if data.ui_scenario is not None:
        case.ui_scenario = data.ui_scenario
    if data.api_scenario_status is not None:
        case.api_scenario_status = data.api_scenario_status
    if data.ui_scenario_status is not None:
        case.ui_scenario_status = data.ui_scenario_status
    if data.is_api_template is not None:
        case.is_api_template = data.is_api_template
    if data.is_ui_template is not None:
        case.is_ui_template = data.is_ui_template
    if data.is_core is not None:
        case.is_core = data.is_core
    if data.script_ref_file is not None:
        case.script_ref_file = data.script_ref_file
    if data.script_ref_func is not None:
        case.script_ref_func = data.script_ref_func
    if data.is_flaky is not None:
        case.is_flaky = data.is_flaky
    if data.remark is not None:
        case.remark = data.remark
    # AI 审核扩展（FR21-FR28）
    if data.review_status is not None:
        if data.review_status == "rejected" and not (data.review_reason and data.review_reason.get("category")):
            from app.core.exceptions import AppError
            raise AppError(code="REASON_REQUIRED", message="拒绝必须填写理由", status_code=400)
        case.review_status = data.review_status
    if data.review_reason is not None:
        case.review_reason = data.review_reason
    # 状态体系 v2（可编辑；含复制/模板生成后人工调整）
    if data.lifecycle_status is not None:
        case.lifecycle_status = data.lifecycle_status
    if data.manual_status is not None:
        case.manual_status = data.manual_status
    if data.ui_status is not None:
        case.ui_status = data.ui_status
    if data.api_status is not None:
        case.api_status = data.api_status

    # module 变更时更新 folder
    if data.module is not None:
        folder_id, _, _ = await _get_or_create_folder(
            session, case.branch_id, data.module, data.submodule
        )
        case.folder_id = folder_id

    await session.flush()
    await session.refresh(case)
    return case


async def list_cases(
    session: AsyncSession,
    branch_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
    case_type: str | None = None,
    folder_id: uuid.UUID | None = None,
    priority: str | None = None,
    automation_status: str | None = None,
    is_flaky: bool | None = None,
    keyword: str | None = None,
    include_deleted: bool = False,
    review_status: str | None = None,
    lifecycle_status: str | None = None,
    manual_status: str | None = None,
    ui_status: str | None = None,
    api_status: str | None = None,
    pushed_within: str | None = None,
) -> tuple[list[Case], int]:
    """分页查询用例列表，支持多条件筛选。返回 (cases, total)。

    `pushed_within`（today / week）筛「最近由外部 Claude Code 回推的」。
    CC 是**写一条推一条**、没有批量接口，所以一次会话的产出在时间上天然连成一片 ——
    用时间窗就能把"这一轮干了什么"聚出来，不需要再造一个批次实体。
    判据是 `source='ai' 且没有生成批次`：平台侧那条「喂需求文档批量产用例」的
    流水线用的也是 source='ai'，靠 generation_task_id 才分得开（它的产物挂着批次，
    CC 回推的没有）。那条路的入口已经下线，所以这个判据只会越来越准。
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, or_

    base = select(Case).where(Case.branch_id == branch_id)

    if include_deleted:
        base = base.where(Case.deleted_at.is_not(None))
    else:
        base = base.where(Case.deleted_at.is_(None))

    if pushed_within in ("today", "week"):
        now = datetime.now(timezone.utc).astimezone()
        if pushed_within == "today":
            since = now.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            since = now - timedelta(days=7)
        base = base.where(
            Case.created_at >= since,
            Case.source == "ai",
            Case.generation_task_id.is_(None),
        )

    if case_type:
        base = base.where(Case.type == case_type)
    if folder_id:
        # 查该目录及所有子目录下的用例
        from app.services.folder_service import _collect_descendant_ids
        descendant_ids = await _collect_descendant_ids(session, folder_id)
        all_ids = [folder_id] + descendant_ids
        base = base.where(Case.folder_id.in_(all_ids))
    if priority:
        base = base.where(Case.priority == priority)
    if automation_status:
        base = base.where(Case.automation_status == automation_status)
    if is_flaky is not None:
        base = base.where(Case.is_flaky == is_flaky)
    if keyword:
        like = f"%{keyword}%"
        base = base.where(or_(Case.title.ilike(like), Case.case_code.ilike(like)))
    if review_status:
        base = base.where(Case.review_status == review_status)
    if lifecycle_status:
        base = base.where(Case.lifecycle_status == lifecycle_status)
    if manual_status:
        base = base.where(Case.manual_status == manual_status)
    if ui_status:
        base = base.where(Case.ui_status == ui_status)
    if api_status:
        base = base.where(Case.api_status == api_status)

    # 总数
    count_result = await session.execute(
        select(func.count()).select_from(base.subquery())
    )
    total = count_result.scalar_one()

    # 分页
    stmt = base.order_by(Case.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await session.execute(stmt)
    cases = list(result.scalars().all())

    return cases, total


async def list_case_assets(session: AsyncSession, case_ids: list) -> dict:
    """每条用例**真的**有哪几样东西：手动步骤 / 接口场景 / UI 脚本。

    列表的「场景」列原先自己拍脑袋：「手动」标签写死无条件显示（257/257 全亮，
    等于没说），「API」只看 `cases.api_scenario` 这个内嵌字段——而 MCP 回推的接口
    场景写的是 `api_test_scenarios` 表。实测 7 条绑了用例的回推场景，
    对应用例的内嵌字段 100% 是 null，于是**全部在列表上隐身**。

    所以这里把三样东西各自的真实来源都查一遍，两个存储取并集。
    """
    if not case_ids:
        return {}
    from sqlalchemy import func

    from app.models.api_test import ApiTestScenario
    from app.models.script import Script

    out = {cid: {"hasManual": False, "hasApi": False, "hasUi": False} for cid in case_ids}

    # 内嵌字段：注意 jsonb 的 'null' 不是 SQL NULL，必须判类型，
    # 否则 184 条存着 JSON null 的用例会被算成"有场景"。
    rows = (await session.execute(
        select(
            Case.id,
            func.jsonb_array_length(Case.steps) > 0,
            func.jsonb_typeof(Case.api_scenario) == "object",
            func.jsonb_typeof(Case.ui_scenario) == "object",
        ).where(Case.id.in_(case_ids))
    )).all()
    for cid, has_steps, has_api, has_ui in rows:
        out[cid] = {
            "hasManual": bool(has_steps),
            "hasApi": bool(has_api),
            "hasUi": bool(has_ui),
        }

    for cid, in (await session.execute(
        select(ApiTestScenario.source_case_id)
        .where(ApiTestScenario.source_case_id.in_(case_ids)).distinct()
    )).all():
        if cid in out:
            out[cid]["hasApi"] = True

    for cid, in (await session.execute(
        select(Script.case_id)
        .where(Script.case_id.in_(case_ids), Script.script_type == "ui").distinct()
    )).all():
        if cid in out:
            out[cid]["hasUi"] = True

    return out


async def list_templates(
    session: AsyncSession,
    branch_id: uuid.UUID,
    scenario_type: str = "api",
) -> list[Case]:
    """查询标记为模板的用例。scenario_type: api | ui。"""
    from sqlalchemy import or_

    base = select(Case).where(
        Case.branch_id == branch_id,
        Case.deleted_at.is_(None),
    )

    if scenario_type == "api":
        base = base.where(Case.is_api_template.is_(True))
    elif scenario_type == "ui":
        base = base.where(Case.is_ui_template.is_(True))
    else:
        base = base.where(or_(Case.is_api_template.is_(True), Case.is_ui_template.is_(True)))

    result = await session.execute(base.order_by(Case.created_at.desc()))
    return list(result.scalars().all())


async def batch_cases(
    session: AsyncSession,
    branch_id: uuid.UUID,
    action: str,
    case_ids: list[uuid.UUID],
    folder_id: uuid.UUID | None = None,
    priority: str | None = None,
    dimension: str | None = None,
) -> dict:
    """批量操作用例。返回 { succeeded, failed, errors }。"""
    succeeded = 0
    failed = 0
    errors = []

    for cid in case_ids:
        # 恢复操作要找的恰恰是**已软删**的行，别的操作只认活着的
        q = select(Case).where(Case.id == cid, Case.branch_id == branch_id)
        q = q.where(Case.deleted_at.is_not(None) if action == "restore"
                    else Case.deleted_at.is_(None))
        result = await session.execute(q)
        case = result.scalar_one_or_none()
        if case is None:
            failed += 1
            errors.append(f"{cid}: 用例不存在")
            continue

        # 已归档用例只允许 unarchive 操作
        if case.automation_status == "archived" and action != "unarchive":
            failed += 1
            errors.append(f"{case.case_code}: 已归档用例不可操作")
            continue

        if action == "move":
            case.folder_id = folder_id
        elif action == "archive":
            case.automation_status = "archived"
        elif action == "unarchive":
            case.automation_status = "pending"
        elif action == "set_priority":
            case.priority = priority
        elif action == "set_flaky":
            case.is_flaky = True
        elif action == "unset_flaky":
            case.is_flaky = False
        elif action == "delete":
            case.deleted_at = datetime.now(timezone.utc)
            # **不清 folder_id**。原来清掉是为了让目录计数掉下去，但计数本来就
            # 过滤软删（folder_service 里两处都带 deleted_at.is_(None)）——
            # 清它没有任何收益，却销毁了「这条属于哪个模块」这唯一的记录，
            # 于是恢复出来的用例回不到原目录。
        elif action == "restore":
            case.deleted_at = None
        elif action in ("publish", "unpublish"):
            # 人拍板：把某一维（或三维）推到「可执行」= 能进回归，或打回调试。
            #
            # 这是**唯一**能推到 executable 的路径 —— CC 改不了状态（红线：
            # 它说"能跑了"等于自证）。但人也不该为此一条条开详情页：
            # 一批用例跑绿之后，逐条点进去改状态，几次之后就没人改了，
            # 于是整个回归池永远是空的（实测：257 条里只有 1 条 executable）。
            dims = [dimension] if dimension else ["manual", "ui", "api"]
            touched = False
            for d in dims:
                attr = f"{d}_status"
                cur = getattr(case, attr, None)
                if action == "publish":
                    # 「无」的那一维不给发布 —— 那一维压根没东西，
                    # 发布了它会进回归然后必挂，是一条假的绿。
                    if cur in ("debugging", "pending_review", "needs_fix"):
                        setattr(case, attr, "executable"); touched = True
                    elif d == "manual" and (case.steps or []) and cur != "executable":
                        setattr(case, attr, "executable"); touched = True
                else:
                    if cur == "executable":
                        setattr(case, attr, "debugging"); touched = True
            # **只数真改了的**。外面那句 succeeded += 1 数的是"处理了几条"，
            # 拿它当发布数，空维度也会报「已发布 1 条，能进回归了」——
            # 而那一维根本没东西，这是句假话。实测被自己的反向用例照出来。
            if not touched:
                failed += 1
                errors.append({"caseId": str(case.id),
                               "error": "这一维还是「无」，没东西可发布"})
                continue

        succeeded += 1

    await session.flush()
    return {"succeeded": succeeded, "failed": failed, "errors": errors}


@audit_log(action="delete", target_type="case")
async def delete_case(session: AsyncSession, case_id: uuid.UUID) -> None:
    """软删除用例（标记 deleted_at）。"""
    case = await get_case(session, case_id)
    case.deleted_at = datetime.now(timezone.utc)
    await session.flush()


async def _detach_blocking_refs(session: AsyncSession, case_ids: list[uuid.UUID]) -> None:
    """解开两处**没有** ON DELETE 级联的外键，否则彻底删除会撞 ForeignKeyViolation。

    引用 cases 的 11 个外键里，其余 9 个（scripts / script_runs / scenario_variables /
    healing_archives / case_file_events / case_gen_events / generation_items×2 /
    api_test_scenarios.source_case_id）在 DB 层已是 CASCADE 或 SET NULL，交给数据库处理。
      - plan_cases：NOT NULL + NO ACTION，只能删（用例没了，计划成员关系无意义）
      - test_report_scenarios：可空 + NO ACTION，解绑而非删除——该表冗余存了
        case_code/scenario_name/status/duration_ms/error_summary，解绑不丢历史报告
    """
    if not case_ids:
        return
    await session.execute(sa_delete(PlanCase).where(PlanCase.case_id.in_(case_ids)))
    await session.execute(
        sa_update(TestReportScenario)
        .where(TestReportScenario.case_id.in_(case_ids))
        .values(case_id=None)
    )


async def hard_delete_case(session: AsyncSession, case_id: uuid.UUID) -> None:
    """彻底删除已软删除的用例。"""
    result = await session.execute(
        select(Case).where(Case.id == case_id, Case.deleted_at.is_not(None))
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise NotFoundError(code="CASE_NOT_FOUND", message="用例不存在或未处于已删除状态")
    await _detach_blocking_refs(session, [case_id])
    await session.delete(case)
    await session.flush()


async def batch_hard_delete(session: AsyncSession, case_ids: list[uuid.UUID]) -> dict:
    """批量彻底删除已软删除的用例。"""
    succeeded = 0
    failed = 0
    errors = []
    deletable = []
    for cid in case_ids:
        result = await session.execute(
            select(Case).where(Case.id == cid, Case.deleted_at.is_not(None))
        )
        case = result.scalar_one_or_none()
        if case is None:
            failed += 1
            errors.append(f"{cid}: 用例不存在或未处于已删除状态")
            continue
        deletable.append((cid, case))

    # 先统一解引用，再逐条删除——避免删到一半撞外键导致整批回滚
    await _detach_blocking_refs(session, [cid for cid, _ in deletable])
    for _, case in deletable:
        await session.delete(case)
        succeeded += 1
    await session.flush()
    # 不动目录。目录是**模块分类**，不是用例的容器 —— 删掉最后一条用例
    # 不代表这个模块不存在了，替人把分类删掉是越权。
    # 空目录攒多了由人在导航栏「清理空目录」里看着名单勾选处理。
    return {"succeeded": succeeded, "failed": failed, "errors": errors}


async def empty_trash(session: AsyncSession, branch_id: uuid.UUID) -> dict:
    """清空该分支回收站——彻底删除全部已软删除的用例。

    回收站可能积上百条、跨多页，逐条勾选不现实，故提供一键清空。
    """
    result = await session.execute(
        select(Case).where(Case.branch_id == branch_id, Case.deleted_at.is_not(None))
    )
    cases = list(result.scalars().all())
    if not cases:
        return {"succeeded": 0, "failed": 0, "errors": []}

    await _detach_blocking_refs(session, [c.id for c in cases])
    for case in cases:
        await session.delete(case)
    await session.flush()
    return {"succeeded": len(cases), "failed": 0, "errors": []}


async def copy_cases_from_branch(
    session: AsyncSession,
    target_branch_id: uuid.UUID,
    source_branch_id: uuid.UUID,
    case_ids: list[uuid.UUID],
) -> dict:
    """跨分支复制用例（深拷贝）。返回 { copied: N }。"""
    from app.services.import_service import _get_or_create_folder, _next_case_code
    from app.models.case import CaseFolder

    copied = 0
    for cid in case_ids:
        result = await session.execute(
            select(Case).where(Case.id == cid, Case.branch_id == source_branch_id, Case.deleted_at.is_(None))
        )
        source = result.scalar_one_or_none()
        if source is None:
            continue

        # 获取源用例的 module 信息（从 folder path 反推）
        module = None
        submodule = None
        if source.folder_id:
            folder_result = await session.execute(
                select(CaseFolder).where(CaseFolder.id == source.folder_id)
            )
            folder = folder_result.scalar_one_or_none()
            if folder:
                parts = folder.path.split("/")
                module = parts[0] if len(parts) >= 1 else None
                submodule = parts[1] if len(parts) >= 2 else None

        # 在目标分支创建目录 + 生成新 case_code
        folder_id = None
        if module:
            folder_id, _, _ = await _get_or_create_folder(session, target_branch_id, module, submodule)
        case_code = await _next_case_code(session, target_branch_id, module or "UNKNOWN")

        new_case = Case(
            branch_id=target_branch_id,
            case_code=case_code,
            title=source.title,
            type=source.type,
            folder_id=folder_id,
            priority=source.priority,
            preconditions=source.preconditions,
            steps=source.steps,
            expected_result=source.expected_result,
            source=source.source,
            automation_status="pending",
            script_ref_file=source.script_ref_file,
            script_ref_func=source.script_ref_func,
            remark=source.remark,
        )
        session.add(new_case)
        copied += 1

    await session.flush()
    return {"copied": copied}
