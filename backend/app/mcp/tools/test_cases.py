"""MCP 工具 — 测试用例 + 文件夹"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import case_service
from app.services.folder_service import list_folder_tree


def _case_to_dict(c) -> dict:
    return {
        "id": str(c.id),
        "caseCode": c.case_code,
        "title": c.title,
        "type": c.type,
        "priority": c.priority,
        "folderId": str(c.folder_id) if c.folder_id else None,
        "preconditions": c.preconditions,
        "steps": c.steps,
        "expectedResult": c.expected_result,
        "automationStatus": c.automation_status,
        "source": c.source,
    }


async def _check_module(session: AsyncSession, branch_id, module: str | None,
                        submodule: str | None) -> tuple[list[str], list[str]]:
    """模块/子模块名过一遍规范。同级已有的名字要查出来给门禁 ——
    「是不是同一个模块换了写法」只能跟同级比，跟全库比会把别的模块下的同名子模块算进来。
    """
    from sqlalchemy import select

    from app.models.case import CaseFolder
    from app.services import intake_gate

    errors: list[str] = []
    warns: list[str] = []
    bid = branch_id if not isinstance(branch_id, str) else uuid.UUID(branch_id)
    if module:
        tops = [r[0] for r in (await session.execute(
            select(CaseFolder.name).where(CaseFolder.branch_id == bid,
                                          CaseFolder.parent_id.is_(None))
        )).all()]
        e, w = intake_gate.check_module_name(module, tops, is_top_level=True)
        errors += e
        warns += w
    if submodule and module and not errors:
        parent = (await session.execute(
            select(CaseFolder).where(CaseFolder.branch_id == bid,
                                     CaseFolder.path == module.upper())
        )).scalars().first()
        sibs = [r[0] for r in (await session.execute(
            select(CaseFolder.name).where(CaseFolder.branch_id == bid,
                                          CaseFolder.parent_id == parent.id)
        )).all()] if parent else []
        # 子模块里带分隔符是允许的（「审批-二级」这种确实是一个名字），
        # 只查重名写法 —— 三级目录很少，硬拆反而添乱。
        e, w = intake_gate.check_module_name(submodule, sibs, is_top_level=False)
        errors += e
        warns += w
    return errors, warns


async def list_cases(
    session: AsyncSession,
    branch_id: str,
    page: int = 1,
    page_size: int = 50,
    keyword: str | None = None,
    folder_id: str | None = None,
    priority: str | None = None,
    case_type: str | None = None,
    module: str | None = None,
    target_level: str | None = None,
    ui_status: str | None = None,
    api_status: str | None = None,
    manual_status: str | None = None,
    pending_only: bool = False,
    bug_state: str | None = None,
) -> dict:
    """列出分支下的测试用例，支持分页和筛选。

    **断点续跑就靠这个**（C2）：传 pending_only=true 只返回"还欠着的" ——
    target_level 说要做到哪一步，三个维度状态说已经做到哪一步，差集就是待办。
    中断之后重跑不用从头来，也不会把做完的又捡回来重做一遍。

    `bug_state`：
      · `blocked` —— 有 open 的 bug，**还没验回来**。这就是你的待办来源：
        从 git 拉已关闭的 issue，跟这批用例的 `bugRefs` 取交集，回来把它们调通，
        调通了把那条关联标成 `fixed`。没关闭的别动（批量回归也会跳过）。
      · `fixed` —— **痕迹**：曾经抓到过 bug、已经验回来了。不是待办，
        用来回答"哪些用例真抓到过问题"。
      · `none` —— 从没关联过 bug。
    每条用例的 `bugRefs` / `blockedByBug` / `hasFixedBug` / `bugFoundCount` 一起返回。
    """
    from sqlalchemy import and_, cast, or_, select
    from sqlalchemy.dialects.postgresql import JSONB

    from app.models.case import Case

    stmt = select(Case).where(Case.branch_id == uuid.UUID(branch_id), Case.deleted_at.is_(None))
    if keyword:
        stmt = stmt.where(Case.title.ilike(f"%{keyword}%"))
    if folder_id:
        stmt = stmt.where(Case.folder_id == uuid.UUID(folder_id))
    if priority:
        stmt = stmt.where(Case.priority == priority)
    if case_type:
        stmt = stmt.where(Case.type == case_type)
    if module:
        # 模块名存在目录上，这里按目录名匹配，省得调用方先去查 folder_id
        from app.models.case import CaseFolder
        stmt = stmt.where(Case.folder_id.in_(
            select(CaseFolder.id).where(CaseFolder.name == module)
        ))
    if target_level:
        stmt = stmt.where(Case.target_level == target_level)
    if ui_status:
        stmt = stmt.where(Case.ui_status == ui_status)
    if api_status:
        stmt = stmt.where(Case.api_status == api_status)
    if manual_status:
        stmt = stmt.where(Case.manual_status == manual_status)
    # 关联 bug 两态。**在 SQL 里筛** —— 拿当前页在内存里过滤，翻到第 3 页只剩一条。
    if bug_state in ("blocked", "fixed", "none"):
        # NOT 写在 text 里 —— 对 text() 取 `~` 会 AssertionError（实测 500）
        from sqlalchemy import text as sa_text
        OPEN = "cases.bug_refs @> '[{\"status\": \"open\"}]'::jsonb"
        FIXED = "cases.bug_refs @> '[{\"status\": \"fixed\"}]'::jsonb"
        if bug_state == "blocked":
            stmt = stmt.where(sa_text(OPEN))
        elif bug_state == "fixed":
            stmt = stmt.where(sa_text(f"{FIXED} AND NOT ({OPEN})"))
        else:
            stmt = stmt.where(sa_text("(cases.bug_refs IS NULL OR jsonb_typeof(cases.bug_refs) <> 'array'"
                                " OR jsonb_array_length(cases.bug_refs) = 0)"))

    if pending_only:
        # 「还欠着」= **CC 还有活要干**，不是"人审没审过"。
        #
        # 正常流程是：回推 → debugging → 平台跑通 → pending_review → 人审 → executable。
        # 用 `!= executable` 当判据，等人审的用例会被 CC 一遍遍捡回来重做，**这个循环
        # 永远不收敛**（dogfood 实测踩到：UI 都跑通了 owes 还挂着）。
        # pending_review / executable 都是"轮到人了"，CC 该放手。
        todo = ("not_started", "draft", "debugging")
        stmt = stmt.where(or_(
            # 手工步骤按"有没有写"判 —— 步骤是内容不是执行物，没有"跑通"这回事，
            # manual_status 只有人工在页面上才会推进，拿它当判据同样不收敛。
            or_(Case.steps.is_(None), Case.steps == cast("[]", JSONB)),
            and_(Case.target_level.in_(("spec_api", "full")), Case.api_status.in_(todo)),
            and_(Case.target_level == "full", Case.ui_status.in_(todo)),
        ))

    from sqlalchemy import func as sa_func
    total = (await session.execute(
        select(sa_func.count()).select_from(stmt.subquery())
    )).scalar_one()
    rows = (await session.execute(
        stmt.order_by(Case.case_code).offset((page - 1) * min(page_size, 100)).limit(min(page_size, 100))
    )).scalars().all()

    return {
        "cases": [{**_case_to_dict(c), "targetLevel": c.target_level,
                   "owes": _owes(c),
                   # 关联 bug 的三样一起给：清单、还卡着吗、该不该重跑
                   "bugRefs": [f"{r.get('ref')}({r.get('status')})"
                               for r in (c.bug_refs or [])] or None,
                   "blockedByBug": c.blocked_by_bug or None,
                   "hasFixedBug": c.has_fixed_bug or None,
                   "bugFoundCount": c.bug_found_count or None,
                   "tags": c.tags or None} for c in rows],
        "total": total,
        "page": page,
        "pageSize": page_size,
        "usage": "owes 列出这条还欠哪几维。断点续跑：pending_only=true 只拿还欠着的，"
                 "做完一维就回推一维，对应维度状态会自己往前走。"
                 "bug_state='blocked' 拿「关联的 bug 还没验回来」那批（跟 git 上已关闭的 "
                 "issue 取交集就是你该回来调的）；'fixed' 是抓到过 bug 已验回来的痕迹。",
    }


_CC_TODO = ("not_started", "draft", "debugging")


def _owes(c) -> list[str]:
    """这条用例**CC 还欠哪几维**（按 target_level 判）。

    判据是"CC 还有活要干"，不是"人审没审过" —— pending_review / executable
    都轮到人了，CC 该放手。手工步骤按有没有写判，它不是执行物、没有跑通这回事。
    """
    owes = []
    if not (c.steps or []):
        owes.append("manual")
    if c.target_level in ("spec_api", "full") and c.api_status in _CC_TODO:
        owes.append("api")
    if c.target_level == "full" and c.ui_status in _CC_TODO:
        owes.append("ui")
    return owes


async def get_case(session: AsyncSession, case_id: str) -> dict | None:
    """获取单条用例详情。"""
    case = await case_service.get_case(session, uuid.UUID(case_id))
    if not case:
        return None
    return _case_to_dict(case)


async def create_case(
    session: AsyncSession,
    branch_id: str,
    title: str,
    module: str,
    case_type: str = "e2e",
    submodule: str | None = None,
    priority: str = "P2",
    preconditions: str | None = None,
    steps: list | None = None,
    expected_result: str | None = None,
    target_level: str = "spec",
    target_level_reason: str | None = None,
    expected_confirmed_by: str | None = None,
    expected_confirmed_note: str | None = None,
) -> dict:
    """创建一条测试用例。自动生成 case_code 和目录。

    入库前过门禁（C3/C4）：完全同名硬拒、模糊词硬拒；相似标题只提醒不拦 ——
    字符串相似度分不清"同一测试点换说法"和"不同测试点用词像"，误拦会逼你把标题
    改得看不出关系来绕过，比多一条重复用例有害得多。

    **P0 三件套不再拦你**。原来是硬拦（先只回推步骤用例、人去平台页面点确认、
    再回来挂接口和 UI），每条 P0 走一趟太贵。现在改成：你在对话里跟用户确认
    「这个场景到底要验什么」，然后把确认内容用 expected_confirmed_by /
    expected_confirmed_note 带上来，平台只记录、不拦截。没带就回一句提醒。
    """
    from app.services import intake_gate

    warnings = _validate_case_quality(title, module, priority, preconditions, steps, expected_result)

    if target_level not in ("spec", "spec_api", "full"):
        return {"error": "target_level 只能是 spec / spec_api / full"}

    # 模块名规范先过 —— 目录一旦建歪（二级拼成一级、同一个模块拼成好几个），
    # 后面每条用例都跟着落错地方，收拾起来比拦一次贵得多。
    mod_errors, mod_warns = await _check_module(session, branch_id, module, submodule)
    if mod_errors:
        return {"error": "模块名不规范，改好再传：", "problems": mod_errors}
    warnings = list(warnings) + list(mod_warns)

    gate_errors, gate_warns = await intake_gate.check_one(
        session, uuid.UUID(branch_id), title, module, priority
    )
    # P0 一次性出三件套只提醒不拦（见 intake_gate.p0_confirmation_hint 的说明）
    gate_warns = list(gate_warns) + intake_gate.p0_confirmation_hint(
        priority, target_level, expected_confirmed_note
    )
    if gate_errors:
        return {
            "error": "用例没通过入库门禁，改完再传：",
            "problems": gate_errors,
        }
    warnings = list(warnings or []) + gate_warns

    if steps:
        # 自动拆分粒度过粗的步骤（"一步一动作"规范）
        steps = _split_coarse_steps(steps)
        warnings = list(warnings) + _split_warnings(steps)
        for i, s in enumerate(steps):
            if not s.get("seq"):
                s["seq"] = i + 1

    if preconditions:
        import re
        preconditions = preconditions.replace("\\n", "\n")
        preconditions = re.sub(r'；\s*(\d+)\.\s*', r'\n\1. ', preconditions)
        preconditions = re.sub(r';\s*(\d+)\.\s*', r'\n\1. ', preconditions)

    from app.schemas.case import CreateCaseRequest
    data = CreateCaseRequest(
        title=title,
        type=case_type,
        module=module,
        submodule=submodule,
        priority=priority,
        preconditions=preconditions,
        steps=steps or [],
        expected_result=expected_result,
    )
    case = await case_service.create_case(session, uuid.UUID(branch_id), data, source="ai")
    case.target_level = target_level
    if (target_level_reason or "").strip():
        case.target_level_reason = target_level_reason.strip()[:1000]
    # CC 侧确认记录：平台只存不判。改了步骤/预期结果会被 update_case 自动清掉，
    # 所以它始终指向"确认的是哪一版"。
    if (expected_confirmed_note or "").strip():
        from datetime import datetime, timezone
        case.expected_confirmed_note = expected_confirmed_note.strip()[:2000]
        case.expected_confirmed_actor = (expected_confirmed_by or "未署名").strip()[:100]
        case.expected_confirmed_at = datetime.now(timezone.utc)
    await session.commit()
    result = {**_case_to_dict(case), "targetLevel": case.target_level}
    if case.expected_confirmed_at:
        result["expectedConfirmed"] = {
            "by": case.expected_confirmed_actor,
            "note": case.expected_confirmed_note,
            "at": case.expected_confirmed_at.isoformat(),
        }
    warnings = list(warnings or []) + list(gate_warns or [])
    # 「不要 UI / 不要接口」是个判断，必须留下理由 —— 只有一个 target_level 值时，
    # 人分不出「你判断这条不需要」和「你没想、用了默认值」，而这两件事后果完全不同。
    # 只提醒不硬拦：真有那种确实不需要的，写一句话的成本就够了。
    if target_level != "full" and not (target_level_reason or "").strip():
        missing = "UI" if target_level == "spec_api" else "UI 和接口"
        warnings.append({
            "field": "target_level_reason",
            "value": f"这条 target_level={target_level}，也就是**不做 {missing} 维度**，"
                     f"但没说为什么。人看不出你是判断过不需要、还是没想就用了默认值。"
                     f"补一句话（比如「纯接口验证，页面没有对应落点」）。",
        })
    if warnings:
        result["_qualityWarnings"] = warnings
    result["targetLevelReason"] = case.target_level_reason
    return result


async def update_case(
    session: AsyncSession,
    case_id: str,
    title: str | None = None,
    priority: str | None = None,
    preconditions: str | None = None,
    steps: list | None = None,
    expected_result: str | None = None,
    target_level: str | None = None,
    target_level_reason: str | None = None,
    expected_confirmed_by: str | None = None,
    expected_confirmed_note: str | None = None,
    reconfirm: bool = False,
    blocked_external: str | None = None,
    bug_refs: list | None = None,
    tags: list | None = None,
    module: str | None = None,
    submodule: str | None = None,
) -> dict:
    """改一条已有用例的内容。只传要改的字段，没传的原样不动。

    **为什么要有这个工具**：实测撞到过 —— CC 自己把标题打错了一个字、步骤 8 写的
    是想当然的页面行为（说跳列表，实测跳详情页），发现之后**改不掉**，只能让人
    去平台上手工修。一个能写不能改的通道，等于每个笔误都要惊动人一次。

    过的是和建用例**同一套门禁**（模糊词、同模块同名、步骤粒度），但同名检查会
    排除自己 —— 否则原样保存都会被判成"重复入库"。

    **不能改状态**：ui_status / api_status / manual_status 一概不收。状态由平台
    按执行事实推进，或由人拍板，这是红线；你要说"这条现在能跑了"，去跑一遍，
    让执行结果说话。

    `blocked_external`：这条**卡在外部条件上**（等环境变量加上、等某接口上线）就写一句
    等什么。它不是状态、不影响任何流程，只解决一件事 ——「我没写」和「我写不了，
    因为外面缺东西」在看板上长得一模一样，于是每轮都要人挨个来问。条件到位了传空串撤掉。

    `bug_refs`：这条**跑出来是红的、但红的原因不在用例**（产品 bug）就关联上去。
    每条 `{"ref": "UAG-123 或一句话", "url": "可选", "status": "open|fixed", "note": "可选"}`。
    整份覆盖。**关联是永久痕迹，不要清掉** —— 清了就看不出这条用例曾经抓到过 bug，
    而"哪些用例真抓到过问题"是评估用例价值的唯一依据。传 `[]` 只用于关联错了。

    两个状态的含义分清：
      · `open`  —— 发现了、还没验回来。`tb_run_ui_scripts_batch` 会跳过这条用例
        （重跑除了刷红没有信息量），也不计入通过率。
      · `fixed` —— **你回来调通了**才标。不是"据说修好了"：issue 关了但你还没调，
        它就该留在 open。标完这条关联作为历史记录留着，不再跳过。

    `tags`：自由分拣词（`冒烟`、`需要真数据`、`等三方联调`），最多 20 个、每个 32 字内。
    别拿它表达状态或审核结论 —— 那两样有确定语义、驱动门禁，标签只用来筛。

    `module` / `submodule`：**放错目录自己搬**，目录不存在会自动建。
    只传 module 就搬到模块根下；两个都传就搬进子目录。
    以前这两个参数没有，于是"这条该放二级目录、那条漏传了 submodule"只能人去界面上
    一条条拖 —— 而漏传本来就是常见笔误（实测一个模块 21 条里 3 条漏在了根目录）。
    **用例编号不跟着变**：TC-DYGL-00013 搬进「跨租户订阅」之后编号还是 TC-DYGL-00013 ——
    编号是你回推、脚本、报告共用的锚点，跟着目录改等于把已发出的引用全断掉。

    改步骤/预期会清掉「预期已确认」。`reconfirm=True` 用于**措辞润色**：
    实质没变（补一句措辞、改错别字），依据沿用原落款，只重盖时间。
    实测一轮因此重填了 12 条几百字的依据 —— 重填时人不会真的重读，
    等于把"确认"变成了走过场。实质变了就别用它，老老实实重新对一遍。
    """
    from sqlalchemy import select

    from app.schemas.case import UpdateCaseRequest
    from app.services import case_service, intake_gate

    module_arg = module          # module 下面会被"当前目录"兜底覆盖，原始入参单独留一份
    cid = uuid.UUID(case_id)
    case = await case_service.get_case(session, cid)
    if not case:
        return {"error": f"用例 {case_id} 不存在"}

    # 同名检查按**搬过去之后**的模块判：同名只在同一模块内算重复，
    # 拿旧目录判会在"搬家顺带改标题"时判错（旧模块里不重名、新模块里重名）。
    cur_module = None
    if case.folder_id:
        from app.models.case import CaseFolder
        cur_module = (await session.execute(
            select(CaseFolder.name).where(CaseFolder.id == case.folder_id)
        )).scalar_one_or_none()
    module = module if module is not None else cur_module

    new_title = title if title is not None else case.title
    new_priority = priority if priority is not None else case.priority
    warnings = _validate_case_quality(
        new_title, module or "", new_priority,
        preconditions if preconditions is not None else case.preconditions,
        steps if steps is not None else case.steps,
        expected_result if expected_result is not None else case.expected_result,
    )

    if module_arg is not None or submodule is not None:
        mod_errors, mod_warns = await _check_module(session, case.branch_id,
                                                   module_arg or cur_module, submodule)
        if mod_errors:
            return {"error": "模块名不规范，改好再传：", "problems": mod_errors}
        warnings = list(warnings) + list(mod_warns)

    if title is not None and module:
        gate_errors, gate_warns = await intake_gate.check_one(
            session, case.branch_id, new_title, module, new_priority, exclude_case_id=cid,
        )
        if gate_errors:
            return {"error": "改完没通过入库门禁，改好再传：", "problems": gate_errors}
        warnings = list(warnings) + list(gate_warns)

    if steps is not None:
        steps = _split_coarse_steps(steps)
        warnings = list(warnings) + _split_warnings(steps)
        for i, s in enumerate(steps):
            if not s.get("seq"):
                s["seq"] = i + 1

    if target_level is not None and target_level not in ("spec", "spec_api", "full"):
        return {"error": "target_level 只能是 spec / spec_api / full"}

    # 落款要在 update_case **之前**取 —— 改步骤/预期会把四个字段一起清掉，
    # 清完再取就只剩 None，reconfirm 沿用不到任何东西。
    prev_conf = (case.expected_confirmed_note, case.expected_confirmed_actor,
                 case.expected_confirmed_by)

    changed = [k for k, v in (("title", title), ("priority", priority),
                              ("preconditions", preconditions), ("steps", steps),
                              ("expectedResult", expected_result),
                              ("bugRefs", bug_refs), ("tags", tags),
                              ("module", module_arg), ("submodule", submodule)) if v is not None]
    data = UpdateCaseRequest(
        title=title, priority=priority, preconditions=preconditions,
        steps=steps, expected_result=expected_result,
        bug_refs=bug_refs, tags=tags,
        module=module if (module is not None or submodule is not None) else None,
        submodule=submodule,
    )
    case = await case_service.update_case(session, cid, data)
    if target_level_reason is not None:
        case.target_level_reason = (target_level_reason or "").strip()[:1000] or None
    if target_level is not None:
        case.target_level = target_level
    # 「卡在外部条件上」：写一句等什么，写空字符串＝解除标注（条件到位了自己撤）
    if blocked_external is not None:
        case.blocked_external = blocked_external.strip()[:500] or None

    reconfirmed = False
    if (expected_confirmed_note or "").strip():
        from datetime import datetime, timezone
        case.expected_confirmed_note = expected_confirmed_note.strip()[:2000]
        case.expected_confirmed_actor = (expected_confirmed_by or "未署名").strip()[:100]
        case.expected_confirmed_at = datetime.now(timezone.utc)
    elif reconfirm and prev_conf[0]:
        # 措辞润色：依据原样沿用，只重盖时间。落款文本不动 ——
        # 让 CC 重打一遍几百字，重填出来的也不是新确认。
        from datetime import datetime, timezone
        (case.expected_confirmed_note, case.expected_confirmed_actor,
         case.expected_confirmed_by) = prev_conf
        case.expected_confirmed_at = datetime.now(timezone.utc)
        reconfirmed = True
    await session.commit()

    result = {**_case_to_dict(case), "targetLevel": case.target_level,
              "targetLevelReason": case.target_level_reason, "changed": changed}
    if case.bug_refs or bug_refs is not None:
        result["bugRefs"] = case.bug_refs or []
        result["blockedByBug"] = case.blocked_by_bug
        result["hasFixedBug"] = case.has_fixed_bug
    if case.tags or tags is not None:
        result["tags"] = case.tags or []
    if module_arg is not None or submodule is not None:
        # 搬完把落点回给它 —— 只回 folderId 的话它没法确认搬对了没有
        from app.models.case import CaseFolder
        result["folderPath"] = (await session.execute(
            select(CaseFolder.path).where(CaseFolder.id == case.folder_id)
        )).scalar_one_or_none() if case.folder_id else None
        result["caseCodeUnchanged"] = case.case_code
    if reconfirmed:
        result["reconfirmed"] = case.expected_confirmed_actor
    elif reconfirm and not prev_conf[0]:
        warnings = list(warnings) + [
            "reconfirm=True 但这条本来就没有落款，没东西可沿用 —— 要带 expected_confirmed_note。"]
    # 改了步骤或预期，平台会把"预期已确认"标记清掉 —— 说出来，否则 CC 以为还确认着
    if ("steps" in changed or "expectedResult" in changed) and not case.expected_confirmed_at:
        warnings = list(warnings) + [
            "步骤/预期改动了，之前的「预期已确认」标记已失效 —— 要重新跟用户对一遍。"]
    if warnings:
        result["_qualityWarnings"] = warnings
    return result


_FUZZY_WORDS = ["操作成功", "显示正常", "无报错", "符合预期", "正确显示", "成功返回", "正常运行", "有效数据", "合法数据"]
_API_PATTERNS = ["POST /", "GET /", "PUT /", "DELETE /", "PATCH /", "返回 2", "返回 4", "返回 5", "HTTP ", "curl "]


def _validate_case_quality(title, module, priority, preconditions, steps, expected_result) -> list[str]:
    warnings = []

    if not module or module.strip() == "-":
        warnings.append("module 为空：用例必须归属到具体模块（如'服务管理/创建服务'）")

    if not preconditions or len(preconditions.strip()) < 5:
        warnings.append("preconditions 为空或过短：必须声明前置条件（登录状态、测试数据等）")

    if "/" in title and "—" not in title:
        warnings.append(f"标题含 '/' 可能混合了多个场景：'{title}'。建议拆分为独立用例")

    if steps:
        for i, s in enumerate(steps):
            action = s.get("action", "")
            expected = s.get("expected", "")

            for pat in _API_PATTERNS:
                if pat in action:
                    warnings.append(f"步骤 {i+1} 使用了接口调用风格（'{pat}...'），应改为页面操作描述")
                    break

            for fw in _FUZZY_WORDS:
                if fw in expected:
                    warnings.append(f"步骤 {i+1} 预期含模糊词'{fw}'，应改为具体可验证描述")
                    break

    if expected_result:
        for fw in _FUZZY_WORDS:
            if fw in expected_result:
                warnings.append(f"expected_result 含模糊词'{fw}'")

    return warnings


# 动作连接词——表示一个步骤包含多个独立动作
_SPLIT_PATTERNS = ["，点击", "，配置", "，填写", "，输入", "，确认", "，勾选",
                   ",点击", ",配置", ",填写", ",输入", ",确认", ",勾选"]


def _split_coarse_steps(steps: list[dict]) -> list[dict]:
    """自动拆分粒度过粗的步骤——一个 action 含多个独立动作时拆成多步"""
    import re
    result = []
    for s in steps:
        action = s.get("action", "")
        expected = s.get("expected", "")

        # 检测是否有多个动作
        # 模式：中文逗号/英文逗号 + 动作动词（点击/填写/选择/配置/输入/确认）
        split_points = []
        for pat in _SPLIT_PATTERNS:
            idx = action.find(pat)
            while idx > 0:
                split_points.append(idx)
                idx = action.find(pat, idx + 1)

        if not split_points:
            result.append(s)
            continue

        # 去重排序
        split_points = sorted(set(split_points))

        # 拆分
        parts = []
        prev = 0
        for sp in split_points:
            part = action[prev:sp].strip().rstrip("，,")
            if part:
                parts.append(part)
            prev = sp + 1  # 跳过逗号
        last = action[prev:].strip()
        if last:
            parts.append(last)

        if len(parts) <= 1:
            result.append(s)
            continue

        # 生成拆分后的步骤。
        #
        # 拆出来的中间步骤**留空预期，不编**。原先填的是「操作完成，页面状态更新」——
        # 那正是入库门禁 _FUZZY_WORDS 要拦的模糊词，平台自己注进去的：
        # 人写这句会被拒，平台写就通过。而且它看起来像填了，实际什么都没说，
        # 比空着更糟 —— 空着至少一眼看得出这里缺东西。
        for j, part in enumerate(parts):
            result.append({
                "seq": len(result) + 1,
                "action": part,
                "expected": expected if j == len(parts) - 1 else "",
                "_autoSplit": j != len(parts) - 1,
            })

    # 重新编号
    for i, s in enumerate(result):
        s["seq"] = i + 1

    return result


def _split_warnings(steps: list[dict]) -> list[str]:
    """把"哪几步是自动拆出来的、预期还空着"说出来，并清掉内部标记。

    不说的话，回推方以为自己写的步骤原样入库了，等到有人看用例才发现
    中间几步没有预期 —— 而那时候已经不知道是谁留的空。
    """
    blanks = [s["seq"] for s in steps if s.pop("_autoSplit", False)]
    if not blanks:
        return []
    return [
        f"第 {'、'.join(map(str, blanks))} 步是平台按「一步一动作」自动拆出来的，"
        "预期结果留空了 —— 拆得对不对、每步该验什么，需要你补上再回推一次。"
        "（平台不替你编预期：编出来的那句话会看起来像填了，实际什么都没说。）"
    ]


async def get_folder_tree(session: AsyncSession, branch_id: str) -> list[dict]:
    """获取用例文件夹树形结构，含每层用例数量。"""
    return await list_folder_tree(session, uuid.UUID(branch_id))
