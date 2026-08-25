"""CC 自审通道 —— 回推完自己先过一遍评审，别等人。

为什么给 CC 开这个口子：评审用的六个维度里，五个都是它自己能改的
（场景合不合理、验证点够不够、接口有没有多余、UI 脚本对不对、纪律）。
让它回推完自己评一次、按 findings 改完再评，人看到的就是已经过审的东西。
这也是"AI 评审替掉人工待审"的前半段 —— 后半段是平台在页面上按同一套判据出结论。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# 单条审核挂了这么久还没落终态 → 当成僵尸清掉，这次重审。
# 15 分钟的来历：`run_first=True` 实测最长到分钟级（真跑 UI + LLM 出结论），
# 15 分钟已经是它的几倍；再往长会让"进程真的崩过"之后这条用例卡着审不了。
_INFLIGHT_STALE_MIN = 15


def _now():
    return datetime.now(timezone.utc)


def _fix_lists(findings: list[dict]) -> dict:
    """mustFix / niceToFix —— CC 要的是"该改哪几条"，两条路径（现审、查旧结论）
    必须长得一样，否则它得为两种形状各写一套解析。"""
    return {
        "mustFix": [f"[{f.get('severity')}] {f.get('where')}：{f.get('problem')}"
                    + (f" → {f['fix']}" if f.get("fix") else "")
                    + (f"（步骤 {f['stepRef']}）" if f.get("stepRef") else "")
                    for f in findings if f.get("severity") in ("blocker", "major")][:10],
        "niceToFix": [f"{f.get('where')}：{f.get('problem')}" for f in findings
                      if f.get("severity") == "minor"][:5],
    }


def _usage_note(review_mode: str | None, verdict: str | None) -> str:
    return ("rejected 的 blocker 一条都不许留着交上去。改完再调一次 tb_review_case 复核。"
            + (" **这次是静态审的**（没真跑），"
               "「接口场景验的端点页面到底调不调」这类问题它看不出来 —— "
               "带 run_first=true 和 env_id 再审一次才算数。"
               if review_mode != "run_first" else "")
            + (" 这次结论是「无法审核」：既不算通过也不算打回，"
               "把环境弄好再审一次。" if verdict == "inconclusive" else ""))


def _cc_view(out: dict) -> dict:
    """给 CC 的返回要短：它要的是"过没过 + 该改哪几条"，不是完整报告。

    抽成函数是因为现在有两条路径要产出**同一个形状**：现审一次（`review_case`）、
    和只查已经落库的结论（`review_status`，超时之后 CC 走这条）。
    两边各拼一份的话，CC 那边就得判断"这次拿到的是哪种形状"。
    """
    findings = out.get("findings") or []
    return {
        "caseCode": out["caseCode"], "verdict": out["verdict"], "total": out["total"],
        "verdictReason": out["verdictReason"],
        "dimensions": {k: v["score"] for k, v in (out.get("dimensions") or {}).items()},
        **_fix_lists(findings),
        "coverageGaps": out.get("coverageGaps") or [],
        "summary": out.get("summary"),
        "ranBeforeReview": out.get("ranBeforeReview"),
        # 这次是**真跑过再评**还是静态看的，必须回给 CC —— 两者结论强度差一个量级
        # （实测同一条：静态 84 分通过、真跑 56 分打回）。不说的话它会拿一个
        # 静态 approved 当"这条过了"就交上去。
        "reviewMode": out.get("reviewMode"),
        "runAttribution": out.get("runAttribution"),
        "usage": _usage_note(out.get("reviewMode"), out.get("verdict")),
    }


async def _inflight(session: AsyncSession, case_id: uuid.UUID):
    """这条用例**现在是不是已经有人在审**。返回 `(item, batch, 已经跑了几分钟)` 或 None。

    为什么要有这一步（review-spec 反馈 §7）：单条审核是一次不间断的同步调用，
    `run_first=True` 时跑到分钟级。CC 那边超时中止之后，它看不见"其实已经落库了"，
    照惯性会再调一次 —— 于是同一条用例被真跑两遍，第二遍还可能撞上第一遍留下的
    数据，跑出一个和第一遍矛盾的结论。所以在账本上留个在跑标记。

    顺带也挡住另一种撞车：人在页面上点了模块批量审核（走 `queue`），CC 同时跳进来
    审同一条 —— 这两个原来互相看不见。所以这里**不限 kind**，任何活跃批次里挂着
    pending/running 的都算在跑。

    僵尸只清自己那种（`INLINE_KIND`）：队列自己的批次有 `recover_orphans()` 收尾，
    这里不许碰 —— 一个排在队里等了 16 分钟的 pending item 是正常的，不是僵尸。
    **`kind="single"` 也是队列的**（详情页点"审这一条"），别顺手把它当自己人。
    """
    from app.models.review_batch import (ACTIVE_STATUSES, INLINE_KIND, ReviewBatch,
                                         ReviewBatchItem)

    rows = (await session.execute(
        select(ReviewBatchItem, ReviewBatch)
        .join(ReviewBatch, ReviewBatch.id == ReviewBatchItem.batch_id)
        .where(ReviewBatchItem.case_id == case_id,
               ReviewBatchItem.status.in_(("pending", "running")),
               ReviewBatch.status.in_(ACTIVE_STATUSES))
        .order_by(ReviewBatch.created_at.desc()))).all()
    dirty = False
    for item, batch in rows:
        started = batch.started_at or batch.created_at
        mins = ((_now() - started).total_seconds() / 60) if started else 0.0
        if batch.kind == INLINE_KIND and mins > _INFLIGHT_STALE_MIN:
            note = (f"上一次单条审核起于 {int(mins)} 分钟前，一直没落终态"
                    f"（进程大概崩在中间了）—— 当成僵尸清掉，不挡后面的重审。")
            item.status, item.error, item.finished_at = "failed", note, _now()
            batch.status, batch.note, batch.finished_at = "partial", note, _now()
            batch.done, batch.failed = batch.total, batch.total
            dirty = True
            continue
        if dirty:
            await session.commit()
        return item, batch, mins
    if dirty:
        await session.commit()
    return None


async def _open_single_batch(session: AsyncSession, case, project_id,
                             env_id: str | None, run_first: bool):
    """给这一次单条审核开一行账（`kind=INLINE_KIND`）。

    不新建表也不加迁移：`review_batches` 本来就是"一次审核这件事"的账本，
    只是单条审核此前完全绕开了它 —— 于是"这条正在审"这件事在库里没有任何痕迹。

    ⚠ **`status` 直接落 `running`，绝不能落 `queued`**：`queue._claim_next` 捡的就是
    `queued`，落成 queued 会被队列 worker 捡走**再真跑一遍**（而且它固定
    `run_first=True`）。同理 `queue.recover_orphans()` 里对 `INLINE_KIND` 做了排除，
    否则重启一次就会把这条内联批次退回 queued，等于凭空多一次真跑。

    用的是**专门的 kind**，不是 `single` —— `single` 是详情页发起、**走队列**的那种，
    拿来当"内联标记"会让重启收尾把人在页面上发起的单条审核判死。
    """
    from app.models.review_batch import INLINE_KIND, ReviewBatch, ReviewBatchItem

    batch = ReviewBatch(
        project_id=uuid.UUID(str(project_id)), branch_id=case.branch_id,
        kind=INLINE_KIND, scope_label=f"CC 自审 {case.case_code}",
        folder_id=case.folder_id,
        environment_id=uuid.UUID(str(env_id)) if env_id else None,
        case_ids=[str(case.id)], actor="cc", actor_kind="ai",
        status="running", total=1, started_at=_now(),
        current_case_code=case.case_code,
        note=("CC 自审（MCP 内联跑，不经队列）"
              + ("·先真跑一遍再评" if run_first else "·静态审")),
    )
    session.add(batch)
    await session.flush()
    item = ReviewBatchItem(batch_id=batch.id, case_id=case.id,
                           case_code=case.case_code, status="running")
    session.add(item)
    await session.commit()
    return batch, item


async def _close_single_batch(session: AsyncSession, batch_id, item_id,
                              out: dict | None, error: str | None) -> None:
    """把账落成终态。**跑挂了也要落** —— 停在 running 的话，这条用例接下来
    15 分钟都会被 `_inflight` 判成"正在审"，谁也审不了它。
    """
    from app.models.review_batch import ReviewBatch, ReviewBatchItem
    try:
        batch = await session.get(ReviewBatch, batch_id)
        item = await session.get(ReviewBatchItem, item_id)
        verdict = (out or {}).get("verdict")
        err = error or (out or {}).get("error")
        if item is not None:
            item.status = "failed" if err else "done"
            item.verdict = verdict
            item.run_state = ((out or {}).get("runAttribution") or {}).get("kind")
            item.error = (err or None) and str(err)[:300]
            item.finished_at = _now()
        if batch is not None:
            batch.status = "partial" if err else "done"
            batch.done = 1
            batch.current_case_code = None
            batch.finished_at = _now()
            if err:
                batch.failed = 1
            elif verdict == "approved":
                batch.approved = 1
            elif verdict == "inconclusive":
                batch.inconclusive = 1
            else:
                batch.rejected = 1
        await session.commit()
    except Exception:  # noqa: BLE001
        # 落账失败不能吃掉评审结论本身 —— 结论已经在 cases/case_review_rounds 上了。
        logger.exception("单条审核落账失败 case_batch=%s", batch_id)


async def review_case(
    session: AsyncSession,
    case_id: str,
    run_first: bool = False,
    env_id: str | None = None,
) -> dict:
    """按六个维度评审一条用例，回结论 + 每条 finding 指到具体位置。

    维度：场景合理性 / 验证点到位 / 接口必要性 / UI 脚本正确性 / 覆盖遗漏 / 可执行与纪律。
    不适用的维度自动摊掉权重（没写 UI 脚本就不评 UI 那一维）。

    **判定不由 AI 说**：有 blocker 一律不过、加权低于 80 不过 —— 规则写在平台代码里。
    blocker 的定义是"放进回归就是假绿或根本跑不了"：断言恒真、只断控制面状态就当生效、
    预期照着实现抄、UI 脚本必挂。

    `run_first=True` 会先真跑一遍这条的接口场景再评（debug 模式，不进通过率口径）。
    断言咬不咬得住静态看不出来 —— "改完读回来还是 200" 长得完全正常。

    结论会落库：审核标签（approved/rejected）、评分、findings。
    评完照着 findings 改，改完再评一次；rejected 的 blocker 一条都不许留着交上去。

    ⚠ **这是一次不间断的同步调用，`run_first=True` 时可能跑到分钟级**（先真跑一遍
    接口场景/UI 脚本，再等 LLM 出结论）——中途没有心跳。如果调用方那边先超时中止了，
    **不代表这条没跑完**：评审是跑完就落库，超时只是"没等到响应"，不是"没产出结果"。
    看不到返回值时**调 `tb_review_check`**（只读、秒回）：它会告诉你这条是还在审
    （连同已经跑了几分钟），还是已经出结论了、结论是什么。

    **别在超时之后直接重调这个工具**：平台会挡下来（返回 `status="in_progress"`），
    因为重跑一遍不只是白烧一次真跑 —— 第二遍可能撞上第一遍留下的数据，
    跑出一个和第一遍互相矛盾的结论，页面上就出现两条打架的轮次。
    """
    from app.services.ai_config_resolver import resolve_ai_config
    from app.services.review import reviewer
    from app.models.case import Case

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

    # ── 已经有人在审这条了 → 不再跑一遍 ──────────────────────
    busy = await _inflight(session, cid)
    if busy is not None:
        item, batch, mins = busy
        from app.models.review_batch import INLINE_KIND
        mine = batch.kind == INLINE_KIND
        return {
            "caseCode": case.case_code, "status": "in_progress",
            "startedMinutesAgo": round(mins, 1),
            "reviewKind": batch.kind, "actorKind": batch.actor_kind,
            "说明": (f"这条用例{'已经在审了' if mine else '正排在一次「%s」审核里' % batch.kind}"
                     f"（{round(mins, 1)} 分钟前开始），**这次没有重新审**。"
                     + ("上一次调用可能只是在你那边超时了 —— 评审是跑完就落库。"
                        if mine else "人在页面上发起的批量审核会审到它。")),
            "usage": (f"等一会调 tb_review_check（case_id 同上）看结论，"
                      f"别重复调 tb_review_case —— 重跑一遍除了白烧一次真跑，"
                      f"还可能撞上上一遍留下的数据、跑出互相矛盾的结论。"
                      f"要是超过 {_INFLIGHT_STALE_MIN} 分钟还是这个状态，"
                      f"平台会把它当僵尸清掉，那时再调 tb_review_case 就会真的重审。"),
        }

    batch, item = await _open_single_batch(session, case, pid, env_id, run_first)
    out: dict = {}
    try:
        out = await reviewer.review_case(session, cid, ai_config=cfg,
                                        persist=True, run_first=run_first, env_id=env_id)
    except Exception as e:  # noqa: BLE001
        # **异常也要落账**：不落的话这条会停在 running，接下来 15 分钟没人审得了它。
        await _close_single_batch(session, batch.id, item.id, None, str(e)[:300])
        raise
    await _close_single_batch(session, batch.id, item.id, out, out.get("error"))
    if out.get("error"):
        return out
    return _cc_view(out)


async def review_status(session: AsyncSession, case_id: str) -> dict:
    """**这条用例审到哪了 / 上次审出了什么** —— 只读，不触发评审、不碰被测系统。

    专治一件事：`tb_review_case` 那次调用在你那边超时中止了，你不知道它到底跑完没有。
    评审是**跑完就落库**的，超时只是"没等到响应"。这个工具秒回，三种结果：

    · `status="in_progress"` —— 还在审（连同已经跑了几分钟）。**接着等，别重调
      `tb_review_case`**：重跑一遍会撞上上一遍留下的数据，出两条打架的结论。
    · `status="reviewed"` —— 已经有结论了，返回的形状跟 `tb_review_case` 成功时**一样**
      （verdict / mustFix / niceToFix / coverageGaps / reviewMode / runAttribution），
      照着 mustFix 改就行，不用再审一次。
      带 `stale=true` 的话，这个结论是对着**已经被改过的内容**算出来的
      （审完之后场景/脚本又被 sync 覆盖过）—— 那就得重审一次才算数。
    · `status="not_reviewed"` —— 从来没审过，去调 `tb_review_case`。

    参数: case_id(用例UUID)
    """
    from app.models.case import Case
    from app.services.review import rounds

    cid = uuid.UUID(case_id)
    case = (await session.execute(select(Case).where(Case.id == cid))).scalars().first()
    if case is None:
        return {"error": f"用例 {case_id} 不存在"}

    busy = await _inflight(session, cid)
    if busy is not None:
        item, batch, mins = busy
        return {
            "caseCode": case.case_code, "status": "in_progress",
            "startedMinutesAgo": round(mins, 1),
            "reviewKind": batch.kind, "actorKind": batch.actor_kind,
            "usage": (f"还在审，已经跑了 {round(mins, 1)} 分钟。"
                      f"过一会再调一次这个工具；**别调 tb_review_case** —— "
                      f"那会被挡下来，或者（超过 {_INFLIGHT_STALE_MIN} 分钟后）真的重跑一遍。"),
        }

    all_rounds = await rounds.list_rounds(session, cid)
    latest = next((r for r in all_rounds if r.get("kind") == "ai_review"), None)
    if latest is None:
        return {
            "caseCode": case.case_code, "status": "not_reviewed",
            "reviewStatus": case.review_status,
            "usage": "这条从来没被 AI 审过（或者只有人工/系统的结论）。调 tb_review_case 审一次。",
        }

    findings = latest.get("findings") or []
    return {
        "caseCode": case.case_code, "status": "reviewed",
        "round": latest.get("round"),
        "verdict": latest.get("verdict"), "total": latest.get("total"),
        # 轮次里存的是分数字典，跟 `_cc_view` 的 dimensions 同形
        "dimensions": latest.get("dimensions") or {},
        "verdictReason": (case.review_reason or {}).get("text"),
        **_fix_lists(findings),
        "coverageGaps": latest.get("coverageGaps") or [],
        "summary": latest.get("summary"),
        "reviewMode": latest.get("reviewMode"),
        "trafficSeen": latest.get("trafficSeen"),
        "runAttribution": (case.review_reason or {}).get("runAttribution"),
        "reviewedAt": latest.get("at"),
        # **过期要说出来**：这条结论算的是当时那份场景/脚本，之后被 sync 覆盖过的话
        # findings 文本还原样留着，照着改就是去改一份已经不存在的内容
        # （老轮次没存签名，那时是 None —— 不猜，不等于"没过期"）。
        "stale": latest.get("stale"),
        # 列表上那个字段现在是什么（可能已经被后续的人工/系统操作改过）
        "reviewStatus": case.review_status,
        "usage": (_usage_note(latest.get("reviewMode"), latest.get("verdict"))
                  + (" ⚠ **这个结论已经过期**：审完之后这条的接口场景/UI 脚本又被改过，"
                     "findings 说的可能是已经不存在的内容 —— 重新调 tb_review_case 审一遍。"
                     if latest.get("stale") else "")),
    }


async def module_checkup(
    session: AsyncSession,
    branch_id: str,
    module: str | None = None,
    folder_id: str | None = None,
    observed_actions: list | None = None,
) -> dict:
    """**这个模块还缺什么** —— 写完一批用例自己问一句，别等人催（review-spec §8）。

    回三块：
    · `commonIssues` 共性问题 —— 这个模块的用例反复犯的同一个错（改一处能修一片）。
      纯汇总，不问模型。
    · `coverageGaps` 覆盖缺口 —— 该测没测的场景（模型看内容）。
    · `coverageSkew` 覆盖分布 —— P0 占比、六类操作（创建/查询/修改/删除/异常/权限）
      各几条、缺哪一类（代码数个数）。**模型不会替你算比例** —— 它读 60 条标题时
      不会去数"18/22 都是创建类"，而这个比例往往比任何一条缺口都刺眼。

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
