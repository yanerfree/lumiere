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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.script import ScriptRun
from app.models.user import User

logger = logging.getLogger(__name__)

DEBUG = "debug"
REGRESSION = "regression"


async def fallback_user_id(session: AsyncSession) -> uuid.UUID | None:
    """executed_by 是 NOT NULL FK，拿不到真实执行人时的兜底。

    只在后台任务确实没带 user_id 时用。正常路径都应该把真实执行人传进来——
    记账记成别人，比不记还糟。
    """
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
            executed_by=uid if isinstance(uid, uuid.UUID) else uuid.UUID(str(uid)),
            run_mode=run_mode,
            attempt=attempt,
            report_scenario_id=report_scenario_id,
            failure_phenomenon=phenomenon,
        )
        session.add(run)
        await (session.commit() if commit else session.flush())
        return run
    except Exception:  # noqa: BLE001
        logger.exception("script_runs 记账失败（case=%s, mode=%s）", case_id, run_mode)
        return None


def apply_case_status(case, script_type: str, status: str, run_mode: str = DEBUG) -> None:
    """按执行结果推进用例的维度状态。

    **debug 跑只许向前推进，不许打回**：调试是"我正在试"，试挂了不代表这条用例坏了。
    而断点续跑的判据是 `ui_status != executable`——调试失败一打回 debugging，
    CC 下一轮就会把已经做完的用例又捡回来重做一遍。
    只有 regression（计划/批量回归）失败才是真信号，才允许把状态打回 debugging。
    """
    if case is None or script_type != "ui":
        return
    passed = status == "passed"
    if passed:
        case.ui_scenario_status = "completed"
        if case.ui_status in ("debugging", "not_started", "draft", "needs_fix"):
            case.ui_status = "pending_review"
    elif run_mode == REGRESSION:
        case.ui_scenario_status = "debugging"
        case.ui_status = "debugging"
