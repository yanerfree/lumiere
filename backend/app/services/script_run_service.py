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
    elif run_mode == REGRESSION:
        setattr(case, dim_attr, "debugging")
    sync_review_status(case)


def sync_review_status(case) -> None:
    """三维按 target_level 全部完成 → 审核标签自动进「待审」。

    **自动，因为不该有人去点「提交审核」那一下** —— 那一步不产生任何信息
    （三维状态已经说明白了），只是给人加一次操作。

    往回也自动：任何一维被打回调试，标签退回 NULL（待提审）。但**人已经审过的
    （approved/rejected）不动** —— 那是人的结论，不能被一次重跑悄悄抹掉。
    """
    if case is None:
        return
    if case.review_status in ("approved", "rejected"):
        return
    target = getattr(case, "target_level", None) or "spec"
    dims = ["manual"] + (["api"] if target in ("spec_api", "full") else []) \
        + (["ui"] if target == "full" else [])
    all_done = all(getattr(case, f"{d}_status", None) == "completed" for d in dims)
    case.review_status = "pending" if all_done else None
