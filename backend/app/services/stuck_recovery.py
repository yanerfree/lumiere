"""卡住的执行怎么放出来。

执行是**进程内**的后台任务（BackgroundTasks + asyncio）。它有三种死法，
前两种能在原地兜住，第三种不能：

1. 抛异常  → `except Exception` 里调 `release_execution`
2. 超时    → `asyncio.TimeoutError` 里调 `release_execution`
3. 进程没了（kill -9 / OOM / 机器重启）→ **一行 except 都不会跑**

第 3 种留下的现场是：计划停在 `executing`、用例行停在 `running`、报告没有
`completed_at`。而 `start_execution` 只接受 draft / completed / paused ——
**这个计划再也触发不了**。人工出口只有一个「终止」按钮（abort 收 executing），
但那会把没跑的用例全记成「跳过」，等于拿一次假结果换回一个能用的计划。
所以除了 1、2 的原地兜底，还要 `sweep_orphaned` 在启动时和之后每隔几分钟扫一遍。

扫的判据只有一条：`test_report_scenarios.status == 'running'` 且 `started_at`
老过 STUCK_AFTER。这个值只有两个执行器（execution.py / adhoc_execution.py）在
真跑某条用例的那几秒里会写，写的同时一定会写 `started_at`；整个执行又有 600s
上限，所以扫到的行**不可能**属于一个还活着的执行。这条门槛也顺带保证了：
万一有人另起一个进程连同一个库，也不会把人家正在跑的执行给收了。

**不按"计划停在 executing 多久"来扫**——`reopen_plan`（重新打开已完成的计划）和
`resume_plan`（恢复暂停的计划）也会把状态置成 executing 且不跑任何后台任务，
它们都不更新 `executed_at`。按计划年龄扫的话，用户前脚点「恢复」，看门狗后脚
就把它又收回 completed，而且悄无声息。宁可漏掉"崩在第一条用例开始之前"那几秒
的窄窗口（那时还有「终止」兜底），也不能去撤用户刚做的动作。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.plan import Plan
from app.models.report import TestReport, TestReportScenario

# 下面这几个不直接用，但**必须 import**：写 test_report_scenarios 时 SQLAlchemy 要
# 按外键给表排序，`cases`/`projects`/`users` 这些目标表不在同一个 MetaData 里就会抛
# NoReferencedTableError。跑整个 app 时它们由别处顺带注册了，所以这个坑只在单独
# 用这个模块时炸（恢复脚本、测试）——而"单独用"恰恰是恢复模块最该能用的场合。
from app.models.case import Case  # noqa: F401
from app.models.environment import Environment  # noqa: F401
from app.models.project import Branch, Project  # noqa: F401
from app.models.user import User  # noqa: F401

logger = logging.getLogger(__name__)

# 执行本身的上限是 600s（两个执行器的 _EXECUTION_TIMEOUT）。留足余量，
# 超过这个岁数还挂在 running 的，一定是没人管的孤儿。
STUCK_AFTER = timedelta(seconds=900)
SWEEP_INTERVAL_SECONDS = 300


async def close_report(session: AsyncSession, report_id: uuid.UUID, why: str,
                       unstarted_reason: str | None = None) -> int:
    """把一份报告收口：running 的行记成 error、重算汇总、补 completed_at。

    `unstarted_reason` 给"根本没开跑"的情况用（比如沙箱建不起来）：那时候
    自动化行还都停在 pending，把它们记成 skipped 并写明原因。
    **只动 execution_type='automated' 的行** —— 手动行的 pending 是"等人来录"，
    是合法状态，收掉它等于把人家没做完的活判死。

    返回被收掉的行数。
    """
    stuck_rows = (await session.execute(
        select(TestReportScenario).where(
            TestReportScenario.report_id == report_id,
            TestReportScenario.status == "running",
        )
    )).scalars().all()

    now = datetime.now(timezone.utc)
    for row in stuck_rows:
        row.status = "error"
        # 写清楚是"中断"而不是"断言失败"——否则看报告的人会去查用例哪里写错了。
        row.error_summary = f"执行中断：{why}"[:500]
        row.completed_at = now

    if unstarted_reason:
        never_ran = (await session.execute(
            select(TestReportScenario).where(
                TestReportScenario.report_id == report_id,
                TestReportScenario.status == "pending",
                TestReportScenario.execution_type == "automated",
            )
        )).scalars().all()
        for row in never_ran:
            # skipped 不进通过率分母：它确实没跑，算成失败是冤枉用例、
            # 算成通过是骗人。
            row.status = "skipped"
            row.error_summary = f"执行未开始：{unstarted_reason}"[:500]
        stuck_rows = list(stuck_rows) + list(never_ran)

    report = (await session.execute(
        select(TestReport).where(TestReport.id == report_id)
    )).scalar_one_or_none()
    if report is not None:
        from app.services.execution_service import recompute_report_stats
        await recompute_report_stats(session, report)
        if report.completed_at is None:
            report.completed_at = now
    await session.flush()
    return len(stuck_rows)


async def release_plan(session: AsyncSession, plan_id: uuid.UUID) -> None:
    """把计划从 `executing` 放回去，好让它还能再被触发。

    还有**待人工录入**的用例就按 `pending_manual`，和正常收尾
    （complete_execution）同一口径 —— 崩一次不该把人家没录完的活判死。

    只数 execution_type='manual' 的 pending 行：自动化行停在 pending 是"没轮到
    就崩了"，不是"等人来录"。把它算进去会让计划挂上 pending_manual，页面提示
    用户去录入一批他根本没打算手动做的用例。
    """
    plan = (await session.execute(select(Plan).where(Plan.id == plan_id))).scalar_one_or_none()
    if plan is None or plan.status != "executing":
        return

    report = (await session.execute(
        select(TestReport).where(TestReport.plan_id == plan_id)
        .order_by(TestReport.created_at.desc())
    )).scalars().first()
    pending = 0
    if report is not None:
        pending = len((await session.execute(
            select(TestReportScenario.id).where(
                TestReportScenario.report_id == report.id,
                TestReportScenario.status == "pending",
                TestReportScenario.execution_type == "manual",
            )
        )).scalars().all())

    if pending > 0 and plan.plan_type == "automated":
        plan.status = "pending_manual"
    else:
        plan.status = "completed"
        plan.completed_at = datetime.now(timezone.utc)
    await session.flush()


async def release_execution(report_id: str | uuid.UUID, why: str,
                            plan_id: str | uuid.UUID | None = None) -> None:
    """崩溃/超时现场调这个。**自带独立连接**。

    不复用崩溃现场那个 session：flush 失败之后事务是脏的，再拿它写只会
    连恢复动作一起回滚掉。整个函数吞异常 —— 恢复失败不该盖住原始错误。
    """
    engine = create_async_engine(settings.database_url, echo=False)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await close_report(session, _as_uuid(report_id), why)
            if plan_id is not None:
                await release_plan(session, _as_uuid(plan_id))
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("释放卡住的执行失败 report=%s plan=%s", report_id, plan_id)
    finally:
        await engine.dispose()


async def ensure_finalized(report_id: str | uuid.UUID, plan_id: str | uuid.UUID | None,
                           why: str) -> bool:
    """执行函数**正常返回**之后兜一道：计划要是还停在 executing，就说明它是从
    某条早退路径出来的，收口。返回是否真的收了。

    为什么不逐条去补那些 `return`：`_execute` 里现在有两条早退发生在
    `complete_execution` 之前 —— 「无用例可执行」和「创建沙箱失败」。两条都会让
    计划永远停在 executing。实测跑一个现成计划就撞上了第二条（项目上留着一个
    过期的 script_base_path，报「目标路径不是有效的 Git 仓库」）。
    **而且这类现场看门狗抓不到**：一行都没进过 running，扫描判据够不着。

    逐条补的话，下一个人加第三条 return 时照样会漏。所以判据放在出口上：
    出来了、计划还在 executing，就一定是漏了。

    批量执行（adhoc）没有计划，传 plan_id=None，判据换成"报告还没有
    completed_at"——同一条沙箱失败的 return 在那边也有一份。
    """
    engine = create_async_engine(settings.database_url, echo=False)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            if plan_id is not None:
                plan = (await session.execute(
                    select(Plan).where(Plan.id == _as_uuid(plan_id))
                )).scalar_one_or_none()
                if plan is None or plan.status != "executing":
                    return False  # 正常收尾过了，什么都不用做
            else:
                done = (await session.execute(
                    select(TestReport.completed_at).where(TestReport.id == _as_uuid(report_id))
                )).scalar_one_or_none()
                if done is not None:
                    return False
            await close_report(session, _as_uuid(report_id), why, unstarted_reason=why)
            if plan_id is not None:
                await release_plan(session, _as_uuid(plan_id))
            await session.commit()
            logger.warning("执行提前返回但没收尾，已补收口 plan=%s report=%s：%s",
                           plan_id, report_id, why)
            return True
    except Exception:  # noqa: BLE001
        logger.exception("补收口失败 plan=%s", plan_id)
        return False
    finally:
        await engine.dispose()


async def sweep_orphaned(session: AsyncSession) -> list[dict]:
    """扫掉进程被杀留下的孤儿执行。返回收掉的报告列表（给日志和测试看）。"""
    cutoff = datetime.now(timezone.utc) - STUCK_AFTER
    rows = (await session.execute(
        select(TestReportScenario.report_id, TestReport.plan_id)
        .join(TestReport, TestReport.id == TestReportScenario.report_id)
        .where(
            TestReportScenario.status == "running",
            TestReportScenario.started_at.isnot(None),
            TestReportScenario.started_at < cutoff,
        )
        .distinct()
    )).all()

    closed: list[dict] = []
    for report_id, plan_id in rows:
        n = await close_report(session, report_id, "进程重启，这次执行的结果已丢失")
        if plan_id is not None:
            await release_plan(session, plan_id)
        closed.append({"reportId": str(report_id), "planId": str(plan_id) if plan_id else None,
                       "rows": n})

    if closed:
        await session.commit()
        logger.warning("放出卡住的执行 %d 个：%s", len(closed), closed)
    return closed


async def start_watchdog():
    """启动看门狗（返回 asyncio.Task，由 lifespan 持有并在关闭时取消）。

    第一轮立刻跑：进程刚起来的时候，正是上一次被杀留下的现场最需要收拾的时刻。
    """
    import asyncio

    async def _loop():
        while True:
            engine = create_async_engine(settings.database_url, echo=False)
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    await sweep_orphaned(session)
            except Exception:  # noqa: BLE001
                logger.exception("孤儿执行清扫失败（不影响服务）")
            finally:
                await engine.dispose()
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)

    return asyncio.create_task(_loop())


def _as_uuid(v) -> uuid.UUID:
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))
