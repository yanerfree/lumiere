"""脚本执行记账（A0）——script_runs 的唯一写入口。

在此之前，script_runs 只有单条即席跑会写；计划执行、adhoc 批量、页面「运行验证」
三条路都只写 test_report_scenarios。失败证据（HAR / 截图 / 现象分类）挂在
script_runs 上，于是覆盖不到任何回归失败。

把写入收成一个函数，是为了避免"下次再加一条执行路径又漏记"——现在漏没漏，
grep 一下 record_run 的调用方就知道。

口径：
- script_runs        = 自动化执行的事实与证据（唯一，覆盖全部执行路径）
- test_report_*      = 一次计划的汇总快照 + 手动录入结果（不动，通过率仍从它算）
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.script import ScriptRun
from app.models.user import User

logger = logging.getLogger(__name__)

DEBUG = "debug"
REGRESSION = "regression"

# ── 流量回收 ────────────────────────────────────────────────────────
# 一次 UI 执行会录下浏览器发的全部请求（含响应体），实测 96~98 条、约 34KB。
# 它是**失败时唯一的网络证据**——平台的请求拦截器拦的是 httpx，浏览器发的请求
# 根本不经过它；不录 HAR，UI 脚本挂了就只剩一句「元素找不到」，看不到那一刻
# 后端返了什么。
#
# 但通过那次的流量几乎没人回头看，而这张表只涨不落。所以按结果分开留：
KEEP_PASSED = 1   # 通过的只留最新一次
KEEP_FAILED = 5   # 失败的留最近 5 次
#
# 为什么失败的要留好几次而不是也只留一次：挂了之后重跑一次想复现，没复现出来
# （flaky）——这一重跑就会把挂掉那次的流量冲掉，而 flaky 恰恰是最需要拿两次
# 流量对比的场景。项目里 flaky 是正式的归因类别、还有 flakyEvidence 字段。


async def prune_captured_requests(session: AsyncSession, case_id, script_type: str) -> int:
    """回收同一条用例（同一脚本类型）下的老流量，返回回收了几行。

    规则：通过的只留最新 KEEP_PASSED 次，失败的留最近 KEEP_FAILED 次。
    只清 `captured_requests` 这一个字段 —— 步骤、错误、截图、stdout、失败现象
    全部原样保留，历史行数不变。**回收不是删记录，是丢掉那一坨流量。**

    按结果分两档，不是一刀切"留最近 N 次"：一条用例挂了之后往往连着重跑好几遍，
    一刀切的话那几次重跑会把挂掉那次挤出窗口 —— 而那次才是要看的。
    """
    cid = case_id if isinstance(case_id, uuid.UUID) else uuid.UUID(str(case_id))
    pruned = 0
    for statuses, keep in (({"passed"}, KEEP_PASSED), (None, KEEP_FAILED)):
        stmt = select(ScriptRun).where(
            ScriptRun.case_id == cid,
            ScriptRun.script_type == script_type,
            ScriptRun.captured_requests.isnot(None),
            # 已经回收过的绝不再碰。**双保险**：真正的根因是 JSONB 把 None 存成
            # JSON null（见模型里那条注释），已经在列上修了；但万一哪天又有别的
            # 写法让空值漏进上面那个 isnot(None)，重复回收会把原条数抹成 0 ——
            # 那是不可逆的信息丢失，值得多加这一条。
            ScriptRun.captured_pruned_count.is_(None),
        )
        stmt = (stmt.where(ScriptRun.status == "passed") if statuses
                else stmt.where(ScriptRun.status != "passed"))
        rows = (await session.execute(
            stmt.order_by(ScriptRun.created_at.desc())
        )).scalars().all()
        for r in rows[keep:]:
            # 先记条数再置空 —— 反过来就永远是 0，界面上「没抓到」和「已回收」
            # 又分不出来了。
            r.captured_pruned_count = len(r.captured_requests or [])
            r.captured_requests = None
            pruned += 1
    if pruned:
        await session.flush()
    return pruned


async def fallback_user_id(session: AsyncSession) -> uuid.UUID | None:
    """executed_by 是 NOT NULL FK，拿不到真实执行人时的兜底。

    只在后台任务确实没带 user_id 时用。正常路径都应该把真实执行人传进来——
    记账记成别人，比不记还糟。
    """
    # MCP 调用先认调用方自己的 Key 身份 —— 记成别人比不记还糟，而且事后补不回来
    try:
        from app.mcp.middleware import current_caller_user_id
        caller = await current_caller_user_id()
        if caller:
            uid = uuid.UUID(caller)
            hit = (await session.execute(
                select(User.id).where(User.id == uid, User.is_active.is_(True))
            )).scalar_one_or_none()
            if hit:
                return hit
    except Exception:  # noqa: BLE001
        pass
    return (
        await session.execute(
            select(User.id)
            .where(User.is_active.is_(True))
            .order_by(User.role.asc(), User.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def record_run(
    session: AsyncSession,
    *,
    case_id,
    script_type: str,
    result: dict,
    executed_by,
    script_id=None,
    run_mode: str = DEBUG,
    attempt: int = 1,
    report_scenario_id=None,
    base_url: str | None = None,
    commit: bool = False,
) -> ScriptRun | None:
    """把一次脚本执行落进 script_runs。

    result 就是 executor.execute_single_case / ts_runner 的返回值。
    记账失败绝不能拖垮执行本身——所以这里吞异常只记日志，调用方不用包 try。
    """
    try:
        uid = executed_by or await fallback_user_id(session)
        if not uid:
            logger.warning("script_runs 记账跳过：没有可用的 executed_by（case=%s）", case_id)
            return None

        # 失败现象分类（A4）。只判「是什么」不判「为什么」——归因归 CC。
        # 放在这个唯一写入口，四条执行路径自动都有；判不出来会老实标 unknown。
        phenomenon = None
        if (result.get("status") or "") != "passed":
            try:
                from app.services import failure_triage
                phenomenon = failure_triage.classify(
                    status=result.get("status"),
                    error_summary=result.get("error_summary"),
                    stdout=result.get("stdout"),
                    captured_requests=result.get("captured_requests"),
                    base_url=base_url,
                    # 记账就发生在执行刚结束时，所以"现在"就是失败时刻。
                    # 窗口锚在它身上，别锚在最后一条抓包上。
                    failed_at=datetime.now(timezone.utc),
                )["phenomenon"]
            except Exception:  # noqa: BLE001
                logger.exception("失败分类异常（不影响记账）")

        run = ScriptRun(
            case_id=case_id if isinstance(case_id, uuid.UUID) else uuid.UUID(str(case_id)),
            script_id=script_id,
            script_type=script_type,
            status=result.get("status", "error"),
            duration_ms=result.get("duration_ms"),
            error_summary=result.get("error_summary"),
            stdout=result.get("stdout"),
            screenshots=result.get("screenshots") or None,
            captured_requests=result.get("captured_requests") or None,
            # 步骤要存下来 —— 执行历史展开读的是这一行，不存就只剩 pytest 那一坨。
            steps=result.get("steps") or None,
            executed_by=uid if isinstance(uid, uuid.UUID) else uuid.UUID(str(uid)),
            run_mode=run_mode,
            attempt=attempt,
            report_scenario_id=report_scenario_id,
            failure_phenomenon=phenomenon,
        )
        session.add(run)
        await session.flush()

        # 回收老流量。挂在这个唯一写入点上 —— 不需要定时任务，也就不会有
        # 「清理任务挂了没人发现」这种二次故障。
        try:
            await prune_captured_requests(session, case_id, script_type)
        except Exception:  # noqa: BLE001
            logger.exception("流量回收失败（不影响这次执行记账）")

        # 开/并/关失败跟进单。挂在这个唯一写入点上 —— 四条执行路径自动都有。
        # 红了开单或累计（没修好之前它一直红，是同一件事）；
        # 绿了关单并记下凭哪一次跑绿关的。**关了又红算复发，新开一张**。
        try:
            from app.services import failure_ticket_service
            await failure_ticket_service.on_run(session, run)
        except Exception:  # noqa: BLE001
            logger.exception("失败跟进单处理失败（不影响这次执行记账）")

        # 记完账立刻判一次 flaky —— 挂在这个唯一写入点上，任何执行路径
        # （单条调试 / 计划回归 / 批量）都自动过一遍，不用各自记得去调。
        # 判定只看同一脚本版本，见 flaky_service 的说明。
        try:
            from app.services import flaky_service
            await flaky_service.evaluate(session, case_id, script_id)
        except Exception:  # noqa: BLE001
            logger.exception("flaky 判定失败（不影响这次执行记账）")

        await (session.commit() if commit else session.flush())
        return run
    except Exception:  # noqa: BLE001
        logger.exception("script_runs 记账失败（case=%s, mode=%s）", case_id, run_mode)
        return None


def apply_case_status(case, script_type: str, status: str, run_mode: str = DEBUG) -> None:
    """按执行结果推进用例的维度状态，并顺带算一次审核标签。

    **跑绿就置「完成」—— 放权 CC，不再等人。** 原来跑绿只到「待人发布」那一态，
    再要人在列表上勾选点「发布到回归」才变 executable，而回归门禁看的就是 executable。
    代价是回归池永远是空的（实测 257 条里只有 1 条）。现在 CC 跑绿自己置 completed，
    「要不要人审」拆到 review_status 那个独立标签上，**而且审核不挡回归**。

    **debug 跑只许向前推进，不许打回**：调试是"我正在试"，试挂了不代表这条用例坏了。
    而断点续跑的判据是维度还在 draft/debugging —— 调试失败一打回，
    CC 下一轮就会把已经做完的用例又捡回来重做一遍。
    只有 regression（计划/批量回归）失败才是真信号，才允许打回 debugging。

    **UI 和接口两维一视同仁**：此前这里写死 `script_type != "ui"` 直接 return，
    于是接口场景跑通多少次 `api_status` 都停在 debugging，页面报「0 个包含可执行脚本」——
    它明明有脚本、还跑通了 69 次。
    """
    if case is None or script_type not in ("ui", "api"):
        return
    dim_attr = f"{script_type}_status"
    passed = status == "passed"
    if passed:
        if getattr(case, dim_attr) in ("debugging", "draft"):
            setattr(case, dim_attr, "completed")
        # **关联 bug 这里一个字都不动。** 曾经想过"跑绿就自动摘掉已修的关联"，
        # 但那是在执行路径上偷偷改另一份数据：关联凭什么消失、什么时候消失，
        # 页面上看不出来，出问题也查不回去。谁调通的谁清 ——
        # CC 验完传 lum_update_case(bug_refs=[]) 一行就完事，一次显式动作。
        # 忘了清也不会错到哪里去：列表上就是一条橙色「待重跑」挂着，看得见。
    elif run_mode == REGRESSION:
        setattr(case, dim_attr, "debugging")
    sync_review_status(case)


def copied_unverified(case) -> bool:
    """这条用例是**从别的分支复制来的、内容还逐字一致、而且在这个版本上一维都没跑过**。

    三个条件缺一不可，各自挡的是不同的东西：

    · `content_fingerprint` 非空 —— 它只在分支复制那一刻写下，而**任何一次内容
      改动都会清掉它**（见 case_service 的 _diverge）。所以非空就等于"我还是那份
      拷贝，一个字都没动"。用它而不用 `source_case_id` 是因为后者是永久出处，
      改完内容也还在，拿它当判据会把已经改过的用例也一起锁在草稿里。

    · api/ui 两维都还在 draft —— 跑过一次就说明它在这个版本上被验过了，
      该按正常规则推进。**照抄堆就是靠这一条解锁的**：内容没变也要在新版本上
      真跑一遍（"接口签名没变、底层行为变了"只有这一跑抓得到），跑完维度一动，
      这个守卫就自动让路。

    只承诺手工步骤（target_level=spec）的用例永远跑不到任何一维，所以它一直被
    锁在草稿 —— 直到分支对账确认它未被清单命中、内容逐字一致、源用例已审通过，
    自动过审直接给它 approved（那条路走 review_status，上面已经提前 return 了）。
    """
    return (getattr(case, "content_fingerprint", None) is not None
            and getattr(case, "api_status", "draft") == "draft"
            and getattr(case, "ui_status", "draft") == "draft")


def plan_satisfied(case) -> bool:
    """**这条用例的覆盖计划兑现了没有** —— 只算 target_level 点名要做的那几维。

    spec = 只要手工步骤 / spec_api = 步骤+接口 / full = 三件套。
    不在计划里的那一维永远停在 draft（页面显示「无」），**不能拿它拖住整体状态**。
    """
    target = getattr(case, "target_level", None) or "spec"
    dims = ["manual"] + (["api"] if target in ("spec_api", "full") else []) \
        + (["ui"] if target == "full" else [])
    return all(getattr(case, f"{d}_status", None) == "completed" for d in dims)


def sync_after_plan_change(case) -> None:
    """**改完 target_level 必须调这个**，`sync_review_status` 一个人不够。

    改计划和跑一次用例是两件事：
    · 跑一次 —— `sync_review_status` 对人已审过的（approved/rejected/inconclusive）
      提前 return，护的是「别让一次重跑悄悄抹掉人的结论」，对的。
    · 改计划 —— 是有人明确说「这条还要多做一维」。多出来的那一维摆在那儿没做，
      整条就**不再是「完成」**，跟谁审过没关系。审核结论仍然不动（那是人的判断，
      审的是当时那几维），只把「状态」列拨回实情。

    不加这一步的症状（2026-08-31 用户截图指出来的就是它）：一条 spec_api 的用例
    三维齐了 → 自动「完成 + 待审」；后来发现 UI 那边也有问题，把计划提到 full，
    `target_level` 是在 `update_case` **之后**单独赋的、没人重算 ——
    于是列表上一行同时写着「UI·草稿」和「状态·完成」「审核·通过」，
    三个信号互相打架。实测库里 4 条这样（TC-FWGL-00017/00033/00035、TC-JKQQRZ-00001）。
    """
    if case is None:
        return
    sync_review_status(case)
    # 人已审过的上面那步整个跳过了，状态列得单独拨回来。「废弃」是人的决定，不碰。
    if (case.review_status in ("approved", "rejected", "inconclusive")
            and getattr(case, "lifecycle_status", None) != "deprecated"):
        case.lifecycle_status = "done" if plan_satisfied(case) else "draft"


def sync_review_status(case) -> None:
    """三维按 target_level 全部完成 → 审核标签自动进「待审」。

    **自动，因为不该有人去点「提交审核」那一下** —— 那一步不产生任何信息
    （三维状态已经说明白了），只是给人加一次操作。

    往回也自动：任何一维被打回调试，标签退回 NULL（待提审）。但**人已经审过的
    （approved/rejected）不动** —— 那是人的结论，不能被一次重跑悄悄抹掉。

    ⚠ 那条提前 return 把**整体状态也一起冻住了**，这是它的代价。跑用例时无所谓
    （维度没变、计划没变，状态本来就该维持），但**改 target_level 时不行** ——
    计划变了状态必须跟着变。所以改计划走 `sync_after_plan_change`，别直接调这个。

    `inconclusive`（无法审核）一样不动。它是**审过了、但这次没能得出结论**
    （缺环境/环境挂了/没得跑），不是"还没审"。被这里冲回 pending 的话，
    「有 4 条没跑成」这个事实就在下一次执行时静默消失了 —— 而报告页正是靠它
    才能说清"这批通过的含金量"。
    """
    if case is None:
        return
    if case.review_status in ("approved", "rejected", "inconclusive"):
        return
    if copied_unverified(case):
        # **复制过来还没在这个版本上验过的，不许自动进「待审/完成」。**
        #
        # 不加这条，只承诺手工步骤的用例（target_level=spec）一从旧分支复制过来
        # 就显示「完成 + 待审」：dims 只有 manual 一维，而 sync_manual_status
        # 看见步骤有内容就置 completed，于是 all_done 立刻成立。
        # 它在新版本上一次都没验过 —— 这正是「没跑过也说通过了」，
        # 只是不从 review_status 那个门进来。
        #
        # 复制时把 lifecycle/review 强行置回草稿是不够的：任何一次后续调用
        # （改一次标题、跑一次别的维度）都会重新走到这里再把它推回「待审」。
        # 判据必须是**状态无关**的，所以看 content_fingerprint —— 见那个函数。
        case.review_status = None
        if getattr(case, "lifecycle_status", None) != "deprecated":
            case.lifecycle_status = "draft"
        return
    all_done = plan_satisfied(case)
    case.review_status = "pending" if all_done else None

    # 整体状态跟着一起走。**否则同一行三个信号自相矛盾**：列表页「状态」列写着
    # 「草稿」，右边三件套全绿、审核写着「待审」—— 实测被问「为什么状态没全部
    # 转成完成」。人看列表第一眼看的就是状态列，它说草稿就等于说这条没做完。
    #
    # 「废弃」是人的决定，任何自动推进都不许碰它。
    if getattr(case, "lifecycle_status", None) != "deprecated":
        case.lifecycle_status = "done" if all_done else "draft"
