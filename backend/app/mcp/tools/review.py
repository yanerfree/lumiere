"""CC 自审通道 —— 回推完自己先过一遍评审，别等人。

为什么给 CC 开这个口子：评审用的六个维度里，五个都是它自己能改的
（场景合不合理、验证点够不够、接口有没有多余、UI 脚本对不对、纪律）。
让它回推完自己评一次、按 findings 改完再评，人看到的就是已经过审的东西。
这也是"AI 评审替掉人工待审"的前半段 —— 后半段是平台在页面上按同一套判据出结论。
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession


async def review_case(
    session: AsyncSession,
    case_id: str,
    run_first: bool = False,
    env_id: str | None = None,
) -> dict:
    """按六个维度评审一条用例，回结论 + 每条 finding 指到具体位置。

    维度：场景合理性 / 验证点到位 / 接口必要性 / UI 脚本正确性 / 本条覆盖完整性 / 可执行与纪律。
    不适用的维度自动摊掉权重（没写 UI 脚本就不评 UI 那一维）。

    **判定不由 AI 说，也不看分数**：有 blocker 一律不过、major >= 2 不过、
    没真跑成功落第三种结论 `inconclusive`（无法审核）—— 规则写在 score_and_verdict 里。
    **分数只做体检和排序，不参与判定**（理由见那个函数的注释：模型给的分会抖，
    同一条两次 86 / 78 是常事，拿抖动的数当闸门没法照着改）。
    blocker 的定义是"放进回归就是假绿或根本跑不了"：断言恒真、只断控制面状态就当生效、
    预期照着实现抄、UI 脚本必挂。

    `run_first=True` 会先真跑一遍这条的接口场景再评（debug 模式，不进通过率口径）。
    断言咬不咬得住静态看不出来 —— "改完读回来还是 200" 长得完全正常。

    结论会落库：审核标签（approved/rejected/inconclusive）、评分、findings、一轮记录。
    评完照着 findings 改，改完再评一次；rejected 的 blocker 一条都不许留着交上去。
    """
    from app.services.ai_config_resolver import resolve_ai_config
    from app.services.review import reviewer
    from app.models.case import Case
    from sqlalchemy import select

    cid = uuid.UUID(case_id)
    case = (await session.execute(select(Case).where(Case.id == cid))).scalars().first()
    if case is None:
        return {"error": f"用例 {case_id} 不存在"}

    # ── 三岔路口 ──────────────────────────────────────────
    # 这个工具是 CC 每轮对每条用例都会调的那一个，所以版本升级新增的两件事
    # 都合进来，不新开工具（不用让 CC 判断"这次该调哪个"）。
    from app.services import branch_diff_review

    # ① 有待决废弃请求 → 不审六维，改审「该不该废」。
    #    审一条正在申请废弃的用例的六维质量本身没有意义。
    if case.deprecate_status == "requested":
        return await branch_diff_review.review_deprecate(session, case, env_id=env_id)

    # ② 照抄堆（未被对账清单命中 + 内容与上一版逐字一致 + 上一版已审通过）
    #    → 四条件结算，不问 AI。清单命中的是「端点变了/字段变了/新增状态值」，
    #    没被命中就意味着它碰的接口和字段这一版全没动 —— 上一版的审核结论仍然成立，
    #    再审是拿同一份内容问同一个问题。
    hits = await branch_diff_review.hit_case_ids_of(session, case.branch_id)
    if hits is not None and case.review_status not in ("approved", "rejected"):
        why = await branch_diff_review.auto_approve_reason(session, case, hits)
        if why is not None and why[0]:
            await branch_diff_review.approve_as_system(session, case, why[1], why[2])
            await session.commit()
            return {
                "caseCode": case.case_code, "verdict": "approved",
                "decidedBy": "system", "照抄堆自动过审": True,
                "理由": why[1],
                "说明": ("这条没被对账清单命中、内容与上一版逐字一致、上一版已审通过，"
                         "所以不再走六维审。后续补交 changes 时若新命中，"
                         "这次自动过审会被撤回待审。"),
            }

    from app.models.project import Branch
    pid = (await session.execute(
        select(Branch.project_id).where(Branch.id == case.branch_id)
    )).scalars().first()
    cfg = await resolve_ai_config(pid, session, capability="tb-quality-review")
    if not cfg:
        return {"error": "这个项目还没配 AI 服务，评审跑不了"}
    out = await reviewer.review_case(session, cid, ai_config=cfg,
                                    persist=True, run_first=run_first, env_id=env_id)
    if out.get("error"):
        return out
    # 给 CC 的返回要短：它要的是"过没过 + 该改哪几条"，不是完整报告
    return {
        "caseCode": out["caseCode"], "verdict": out["verdict"], "total": out["total"],
        "verdictReason": out["verdictReason"],
        "dimensions": {k: v["score"] for k, v in out["dimensions"].items()},
        "mustFix": [f"[{f['severity']}] {f['where']}：{f['problem']}"
                    + (f" → {f['fix']}" if f.get("fix") else "")
                    for f in out["findings"] if f["severity"] in ("blocker", "major")][:10],
        "niceToFix": [f"{f['where']}：{f['problem']}" for f in out["findings"]
                      if f["severity"] == "minor"][:5],
        "coverageGaps": out["coverageGaps"],
        "summary": out["summary"],
        "ranBeforeReview": out.get("ranBeforeReview"),
        # 这次是**真跑过再评**还是静态看的，必须回给 CC —— 两者结论强度差一个量级
        # （实测同一条：静态 84 分通过、真跑 56 分打回）。不说的话它会拿一个
        # 静态 approved 当"这条过了"就交上去。
        "reviewMode": out.get("reviewMode"),
        "runAttribution": out.get("runAttribution"),
        "usage": ("rejected 的 blocker 一条都不许留着交上去。改完再调一次这个工具复核。"
                  + (" **这次是静态审的**（没真跑），"
                     "「接口场景验的端点页面到底调不调」这类问题它看不出来 —— "
                     "带 run_first=true 和 env_id 再审一次才算数。"
                     if out.get("reviewMode") != "run_first" else "")
                  + (" 这次结论是「无法审核」：既不算通过也不算打回，"
                     "把环境弄好再审一次。"
                     if out.get("verdict") == "inconclusive" else "")),
    }


async def review_batch(
    session: AsyncSession,
    branch_id: str,
    case_ids: str | None = None,
    module: str | None = None,
    env_id: str | None = None,
    scope: str = "all",
    with_checkup: bool = True,
) -> dict:
    """批量送审 —— **入平台的审核队列，别自己 for 循环调 tb_review_case**。

    为什么必须走队列（review-spec §5）：`tb_review_case` 是直调 `reviewer.review_case`，
    一条也不排队。你自己循环推一批的后果是**并发真跑打同一个环境**，而这条队列
    要防的两件事一件都吃不到：

    · **同环境串行** —— 两条脚本共用租户/账号，A 跑到一半 B 把 A 要用的数据删了，
      A 莫名报错，审核判 A「脚本有问题」。**这是假打回**，出几次这套审核就没人信了。
    · **熔断** —— 环境一挂，连续 3 条环境类失败就该暂停整批；逐条调的话 20 条会
      一条接一条全标「无法审核」，看起来像用例集体坏了。

    **这批一定是真跑**（`_run_batch` 写死 run_first=True）：静态审核查不出最贵的
    那一类 —— 接口场景验的端点页面根本不调。所以这里没有 run_first 参数。

    队列里**人发起的排在 CC 前面**（人在等结果，CC 不在等），所以你入队之后
    可能要等一会儿；用 tb_review_batch_status 轮询，别重复入队。
    同一条用例已经在这个环境的活跃批次里排着 → 自动合并，不会跑两遍。
    """
    import uuid as _uuid

    from sqlalchemy import select

    # 直接复用 REST 那两个解析器 —— **别写第二份**。这个库里"两份实现各改各的"
    # 已经栽过好几次（孪生 playwright.config、FailureTriagePanel 两处、
    # start_execution 和执行器两套判据）。
    from app.api.case_review import MAX_BATCH, _folder_scope, _resolve_env
    from app.core.exceptions import AppError
    from app.models.case import Case, CaseFolder
    from app.models.project import Branch

    from app.services.review import queue

    try:
        bid = _uuid.UUID(branch_id)
    except (ValueError, AttributeError):
        return {"error": f"branch_id 不是合法 UUID: {branch_id}"}

    pid = (await session.execute(
        select(Branch.project_id).where(Branch.id == bid))).scalars().first()
    if pid is None:
        return {"error": f"分支不存在: {branch_id}"}

    # module 名 → folder。和 tb_module_checkup 同一套查法（按名字，本分支内）
    folder = None
    if module:
        folder = (await session.execute(
            select(CaseFolder).where(CaseFolder.branch_id == bid,
                                     CaseFolder.name == module))).scalars().first()
        if folder is None:
            return {"error": f"这个分支下找不到模块「{module}」"}

    cids: list = []
    if case_ids:
        for raw in str(case_ids).split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                cids.append(_uuid.UUID(raw))
            except ValueError:
                return {"error": f"case_ids 里有不合法的 UUID: {raw}"}
        inferred = "single" if len(cids) == 1 else "sample"
    else:
        stmt = select(Case.id).where(Case.branch_id == bid, Case.deleted_at.is_(None))
        fscope = await _folder_scope(session, bid, folder.id if folder else None)
        if fscope:
            stmt = stmt.where(Case.folder_id.in_(fscope))
        if scope == "incremental":
            # 「只审没审过的和被打回的」。**无法审核的也算没审过** ——
            # 它上次是环境不行才没得出结论，正是这次该补的那批。
            stmt = stmt.where(
                (Case.review_status.is_(None))
                | (Case.review_status.in_(("pending", "rejected", "inconclusive"))))
        cids = [r[0] for r in (await session.execute(stmt.limit(MAX_BATCH + 1))).all()]
        inferred = "module_incremental" if scope == "incremental" else "module_full"

    if not cids:
        return {"error": "没有可评审的用例"
                         + ("（这个范围里的都审过了，要重审就显式传 case_ids）"
                            if scope == "incremental" else "")}
    truncated = len(cids) > MAX_BATCH
    cids = cids[:MAX_BATCH]

    try:
        env = await _resolve_env(session, pid, env_id)
    except AppError as e:
        return {"error": e.message}

    label = (f"{folder.name} {len(cids)} 条" if folder else f"{len(cids)} 条")
    if inferred == "single":
        one = await session.get(Case, cids[0])
        label = one.case_code if one else label

    actor = None
    try:
        from app.mcp.middleware import current_caller_user_id
        uid = await current_caller_user_id()
        if uid:
            from app.models.user import User
            actor = (await session.execute(
                select(User.username).where(User.id == uid))).scalar_one_or_none()
    except Exception:  # noqa: BLE001
        pass

    batch, merged = await queue.enqueue(
        session, project_id=pid, branch_id=bid, kind=inferred, case_ids=cids,
        folder_id=(folder.id if folder else None), scope_label=label,
        environment_id=env.id, environment_name=env.name,
        actor=actor or "cc", actor_kind="cc",
        with_checkup=with_checkup and inferred.startswith("module"))

    return {
        "batchId": str(batch.id),
        "kind": batch.kind,
        "total": batch.total,
        "scopeLabel": batch.scope_label,
        "environment": env.name,
        # 被合并掉的要说出来 —— 静默少审几条，和"审完了"长得一样
        "merged": merged or None,
        "truncated": truncated or None,
        "usage": ("已入队，**这批一定是真跑**。用 tb_review_batch_status(batch_id) 轮询，"
                  "别重复入队、也别再逐条调 tb_review_case。"
                  "人工发起的批次排在你前面，所以可能要等。"
                  + (f" 其中 {len(merged)} 条已经在别的批次里排着了，这次不重复跑。"
                     if merged else "")
                  + (f" 超过单批上限 {MAX_BATCH} 条，这次只排了前 {MAX_BATCH} 条，"
                     "剩下的分模块再来一批。" if truncated else "")),
    }


async def review_batch_status(session: AsyncSession, batch_id: str) -> dict:
    """看一个审核批次跑到哪了。`finished=true` 才算跑完，别拿 done==total 猜
    （total 为 0、或中途熔断时那个猜法不成立）。

    `status=paused` = **熔断了**（连续 3 条环境类失败）—— 那不是用例的问题，
    去把环境弄好，然后在页面上续跑；接着刷这批只会继续红。
    """
    import uuid as _uuid

    from sqlalchemy import select

    from app.api.case_review import _batch_dict
    from app.models.review_batch import ReviewBatch, ReviewBatchItem

    try:
        bid = _uuid.UUID(batch_id)
    except (ValueError, AttributeError):
        return {"error": f"batch_id 不是合法 UUID: {batch_id}"}
    b = await session.get(ReviewBatch, bid)
    if b is None:
        return {"error": f"找不到这个批次: {batch_id}"}
    items = (await session.execute(
        select(ReviewBatchItem).where(ReviewBatchItem.batch_id == bid)
        .order_by(ReviewBatchItem.case_code))).scalars().all()
    out = _batch_dict(b, items)
    if b.status == "paused":
        out["hint"] = ("熔断暂停：连续 3 条都是环境类失败。**这不是用例的问题** —— "
                       "先把环境弄好，再在审核报告页续跑。")
    return out

async def module_checkup(
    session: AsyncSession,
    branch_id: str,
    module: str | None = None,
    folder_id: str | None = None,
    observed_actions: list | None = None,
) -> dict:
    """**这个模块还缺什么** —— 写完一批用例自己问一句，别等人催（review-spec §8）。

    回两块：
    · `commonIssues` 共性问题 —— 这个模块的用例反复犯的同一个错（改一处能修一片）。
      纯汇总，不问模型。
    · `coverageGaps` 覆盖缺口 —— 该测没测的场景。

    **`observed_actions` 值得多花一步去凑**：把你在页面上探到的可操作项
    （按钮、菜单项、状态流转）传进来，缺口就是拿它跟现有用例对账出来的 ——
    "页面上有这个操作、用例里一条都没覆盖"是最硬的缺口。不传的话它只能
    凭用例标题猜，出来的东西会泛。

    **缺口是建议清单，不是门禁** —— 不参与任何一条用例过不过。
    不占队列、不用环境、不碰被测系统，随时可以问。
    """
    from app.services.review import checkup

    out = await checkup.run(session, branch_id, folder_id=folder_id, module=module,
                            observed_actions=observed_actions)
    if out.get("error"):
        return out
    return out
