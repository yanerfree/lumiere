"""审核队列：**一个环境一条队列，串行**（review-spec §5）。

## 为什么不能并行

并行换来的不是快，是**假打回**。两条实测过的踩法：

1. 两条脚本用同一个租户、同一个账号，A 跑到一半 B 把数据删了 →
   A 莫名报错 → 审核判 A「脚本有问题」，其实是被踩的。
2. 网关那边「平台关闭本租户审批开关」是个**全局开关**。一条脚本把它关了，
   另一条正好在测开关开着时的行为 —— 两边结果都不可信。

假打回出过几次，这套审核就没人信了。所以同环境串行；不同环境可以同时跑
（它们是两套被测系统，互相踩不到）。

## 四件事，每件都有一个「静默会怎样」

| 机制 | 不做会怎样 |
|---|---|
| **熔断** | 环境一挂，16 条一条接一条全失败、全标「无法审核」，看起来像用例集体坏了 |
| **取消** | 排队里那批发现发错了，只能等它跑完（30 条五分钟） |
| **合并** | 同一条用例被点两次就跑两遍，白烧一遍 AI 调用和一次真跑 |
| **重启收尾** | 进程被 kill，正在跑的那批永远停在 running，页面上转到天荒地老 |

## 进程内队列的边界

worker 是进程内的 asyncio task，**串行保证只在单进程内成立**。
本项目 uvicorn 是单进程跑的（见 CLAUDE.md 的端口硬规则），所以够用。
真要多 worker 部署，这里得换成数据库咨询锁 —— 那时候
`_claim_next` 的 `UPDATE ... WHERE status='queued'` 已经是乐观锁的形状，
加一句 `FOR UPDATE SKIP LOCKED` 就能升级，不用重写。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update

from app.deps.db import async_session_factory
from app.models.case import Case
from app.models.review_batch import (ACTIVE_STATUSES, INLINE_KIND, ReviewBatch,
                                     ReviewBatchItem)
from app.services.review import run_outcome

logger = logging.getLogger(__name__)

# 连续这么多条都是环境类失败 → 整条队列暂停（§5「环境挂了要熔断」）。
# 3 不是拍的：1 会被单条用例的偶发网络抖动误触发，5 的话 16 条里已经白跑三分之一。
BREAKER_LIMIT = 3

_workers: dict[str, asyncio.Task] = {}
_spawn_lock: asyncio.Lock | None = None


def _lock() -> asyncio.Lock:
    # 不能在模块导入时建 —— 那会绑到导入时的 event loop 上，
    # 而 uvicorn 的 loop 是之后才起的（测试里更明显：每个用例一个新 loop）。
    global _spawn_lock
    if _spawn_lock is None:
        _spawn_lock = asyncio.Lock()
    return _spawn_lock


def _env_key(environment_id) -> str:
    """没有环境的批次（体检）归到一条独立队列 —— 它不碰被测系统，
    跟真跑的那些互不影响，不该排在它们后面等。"""
    return str(environment_id) if environment_id else "-"


def _now():
    return datetime.now(timezone.utc)


# ── 入队 ─────────────────────────────────────────────────────────

async def enqueue(session, *, project_id, branch_id, kind: str, case_ids: list,
                  folder_id=None, scope_label: str | None = None,
                  environment_id=None, environment_name: str | None = None,
                  actor: str | None = None, actor_kind: str = "human",
                  with_checkup: bool = False) -> tuple[ReviewBatch, list[str]]:
    """建一个批次并排上队。返回 (批次, 被合并掉的用例编号)。

    **合并**：同一条用例已经在这个环境的活跃批次里排着了，就不再排一遍（§5）。
    重复跑一遍不只是浪费 —— 两次真跑的结论可能不一样（第二次撞上第一次留的数据），
    而页面上会显示两条互相矛盾的轮次。
    """
    cids = [uuid.UUID(str(c)) for c in (case_ids or [])]
    merged: list[str] = []

    if cids:
        busy = set((await session.execute(
            select(ReviewBatchItem.case_id)
            .join(ReviewBatch, ReviewBatch.id == ReviewBatchItem.batch_id)
            .where(ReviewBatchItem.case_id.in_(cids),
                   ReviewBatchItem.status.in_(("pending", "running")),
                   ReviewBatch.status.in_(ACTIVE_STATUSES),
                   ReviewBatch.environment_id == environment_id)
        )).scalars().all())
        if busy:
            codes = dict((await session.execute(
                select(Case.id, Case.case_code).where(Case.id.in_(busy)))).all())
            merged = [codes.get(c) or str(c) for c in busy]
            cids = [c for c in cids if c not in busy]

    batch = ReviewBatch(
        project_id=uuid.UUID(str(project_id)), branch_id=uuid.UUID(str(branch_id)),
        kind=kind, scope_label=scope_label,
        folder_id=uuid.UUID(str(folder_id)) if folder_id else None,
        environment_id=uuid.UUID(str(environment_id)) if environment_id else None,
        environment_name=environment_name,
        case_ids=[str(c) for c in cids], actor=actor, actor_kind=actor_kind,
        status="queued", total=len(cids), with_checkup=with_checkup,
    )
    session.add(batch)
    await session.flush()

    if cids:
        codes = dict((await session.execute(
            select(Case.id, Case.case_code).where(Case.id.in_(cids)))).all())
        for cid in cids:
            session.add(ReviewBatchItem(batch_id=batch.id, case_id=cid,
                                        case_code=codes.get(cid), status="pending"))
    await session.commit()

    await ensure_worker(_env_key(environment_id))
    return batch, merged


async def cancel(session, batch_id) -> bool:
    """取消。排队中的直接置 cancelled；正在跑的等它做完手上这条再停 ——
    **不硬杀**：跑到一半被掐断会留下半截数据，下一批撞上它又是一轮假打回。
    """
    b = await session.get(ReviewBatch, uuid.UUID(str(batch_id)))
    if b is None or b.status not in ACTIVE_STATUSES:
        return False
    b.status = "cancelled"
    b.note = "已取消"
    b.finished_at = _now() if b.status != "running" else None
    await session.execute(
        update(ReviewBatchItem).where(ReviewBatchItem.batch_id == b.id,
                                      ReviewBatchItem.status == "pending")
        .values(status="skipped", error="批次已取消"))
    await session.commit()
    return True


async def resume(session, batch_id) -> bool:
    """熔断暂停之后，人确认环境好了，接着跑剩下的。"""
    b = await session.get(ReviewBatch, uuid.UUID(str(batch_id)))
    if b is None or b.status != "paused":
        return False
    b.status = "queued"
    b.note = None
    await session.commit()
    await ensure_worker(_env_key(b.environment_id))
    return True


# ── worker ───────────────────────────────────────────────────────

async def ensure_worker(env_key: str) -> None:
    async with _lock():
        t = _workers.get(env_key)
        if t is not None and not t.done():
            return
        _workers[env_key] = asyncio.create_task(_worker(env_key),
                                                name=f"review-queue:{env_key}")


async def _worker(env_key: str) -> None:
    """一条队列一个 worker。取不到活就退出 —— 下次入队时会重新拉起来，
    不留空转的常驻任务（这个项目已经有三个看门狗了，不再加第四个）。"""
    try:
        while True:
            batch_id = await _claim_next(env_key)
            if batch_id is None:
                return
            try:
                await _run_batch(batch_id)
            except Exception:  # noqa: BLE001
                logger.exception("审核批次跑挂了 batch=%s", batch_id)
                await _finalize(batch_id, status="partial", note="批次执行异常，见日志")
    except asyncio.CancelledError:
        raise
    finally:
        _workers.pop(env_key, None)


async def _claim_next(env_key: str) -> str | None:
    """挑下一个。**人发起的插到 CC 自审前面** —— 人在等结果，CC 不在等（§5）。"""
    async with async_session_factory() as s:
        # `INLINE_KIND` 是 MCP 在进程内自己跑的，账本上那行只是"在跑"的标记 ——
        # 它建出来就是 running、永远不会是 queued，这一句是**结构上的兜底**：
        # 万一哪天有人手滑把它建成 queued，worker 捡走就是凭空多一次真跑
        # （`_run_batch` 固定 `run_first=True`），而且跟已落库的结论打架。
        cond = [ReviewBatch.status == "queued", ReviewBatch.kind != INLINE_KIND]
        if env_key == "-":
            cond.append(ReviewBatch.environment_id.is_(None))
        else:
            cond.append(ReviewBatch.environment_id == uuid.UUID(env_key))
        row = (await s.execute(
            select(ReviewBatch).where(*cond)
            .order_by((ReviewBatch.actor_kind != "human"),   # False(人) 排在 True(CC) 前
                      ReviewBatch.created_at)
            .limit(1))).scalars().first()
        if row is None:
            return None
        row.status = "running"
        row.started_at = row.started_at or _now()
        await s.commit()
        return str(row.id)


async def _run_batch(batch_id: str) -> None:
    from app.services.ai_config_resolver import resolve_ai_config
    from app.services.review import reviewer

    async with async_session_factory() as s:
        b = await s.get(ReviewBatch, uuid.UUID(batch_id))
        if b is None:
            return
        project_id, env_id = b.project_id, b.environment_id
        cfg = await resolve_ai_config(project_id, s, capability="tb-quality-review")
        if cfg is None:
            await _finalize(batch_id, status="partial", note="AI 服务未配置，这批没跑")
            return
        items = (await s.execute(
            select(ReviewBatchItem).where(ReviewBatchItem.batch_id == b.id,
                                          ReviewBatchItem.status == "pending")
            .order_by(ReviewBatchItem.case_code))).scalars().all()
        item_ids = [(str(i.id), str(i.case_id), i.case_code) for i in items]

    consecutive_env_fail = 0

    for item_id, case_id, case_code in item_ids:
        async with async_session_factory() as s:
            b = await s.get(ReviewBatch, uuid.UUID(batch_id))
            if b is None or b.status == "cancelled":
                return
            if b.status == "paused":
                return
            b.current_case_code = case_code
            it = await s.get(ReviewBatchItem, uuid.UUID(item_id))
            if it is not None:
                it.status = "running"
            await s.commit()

        verdict = err = run_state = None
        async with async_session_factory() as s:
            try:
                # **批量也必须真跑**（§1）。静态审核查不出最贵的那一类：
                # 接口场景验的端点页面根本不调 —— 实测一条 83 分静态通过的用例
                # 就指着一个页面从来不调的接口。这种通过比不审更坏。
                out = await reviewer.review_case(s, uuid.UUID(case_id), ai_config=cfg,
                                                 persist=True, run_first=True,
                                                 env_id=str(env_id) if env_id else None)
                verdict = out.get("verdict")
                run_state = (out.get("runAttribution") or {}).get("kind")
                err = out.get("error")
            except Exception as e:  # noqa: BLE001
                logger.exception("评审失败 case=%s", case_id)
                err = str(e)[:300]

        # 熔断计数：**只数环境类**。脚本挂了、系统有 bug 都不该触发熔断 ——
        # 那是这条用例自己的事，后面 15 条照样该审。
        if run_state in (run_outcome.ENV_DOWN, run_outcome.NO_ENV):
            consecutive_env_fail += 1
        elif err:
            consecutive_env_fail += 1 if run_outcome.is_env_error(err) else 0
        else:
            consecutive_env_fail = 0

        async with async_session_factory() as s:
            b = await s.get(ReviewBatch, uuid.UUID(batch_id))
            it = await s.get(ReviewBatchItem, uuid.UUID(item_id))
            if b is None:
                return
            if it is not None:
                it.status = "failed" if err else "done"
                it.verdict = verdict
                it.run_state = run_state
                it.error = err
                it.finished_at = _now()
            b.done += 1
            if err:
                b.failed += 1
            elif verdict == "approved":
                b.approved += 1
            elif verdict == "inconclusive":
                b.inconclusive += 1
            else:
                b.rejected += 1
            b.current_case_code = None

            if consecutive_env_fail >= BREAKER_LIMIT:
                # **熔断**：不白跑，也不污染一整批结论。剩下的留在 pending，
                # 人确认环境好了点「继续」就接着跑（resume）。
                left = b.total - b.done
                b.status = "paused"
                b.note = (f"连续 {consecutive_env_fail} 条都是环境类失败，"
                          f"环境好像挂了 —— 剩下 {left} 条先停着。"
                          f"确认环境正常后点「继续」，不用重新发起。")
                await s.commit()
                logger.warning("审核队列熔断 batch=%s 剩余=%d", batch_id, left)
                return
            await s.commit()

    await _finalize(batch_id)


async def _finalize(batch_id: str, status: str | None = None, note: str | None = None) -> None:
    async with async_session_factory() as s:
        b = await s.get(ReviewBatch, uuid.UUID(str(batch_id)))
        if b is None or b.status in ("cancelled", "paused"):
            return
        b.status = status or ("partial" if b.failed else "done")
        if note:
            b.note = note
        b.current_case_code = None
        b.finished_at = _now()
        await s.commit()
    if not status:
        try:
            await _build_report(batch_id)
        except Exception:  # noqa: BLE001
            logger.exception("模块报告生成失败（不影响逐条结论）batch=%s", batch_id)


async def _build_report(batch_id: str) -> None:
    """模块报告的两块内容（§7）：共性问题 + 覆盖缺口。

    **价值在这两块**：一条一条看只知道"这条不行"；看模块才知道
    "这一整片都犯同一个错"和"这个模块压根没测到的地方"。
    审完算一次存下来 —— 每次打开重算的话，LLM 每轮措辞不同，
    同一份报告两次打开长得不一样。
    """
    from app.services.review import checkup

    async with async_session_factory() as s:
        b = await s.get(ReviewBatch, uuid.UUID(str(batch_id)))
        if b is None or not b.with_checkup or not b.folder_id:
            return
        out = await checkup.run(s, b.branch_id, folder_id=b.folder_id)
        if out.get("error"):
            return
        b.report = {k: out[k] for k in
                    ("commonIssues", "coverageGaps", "coverageGapsTotal", "total", "reviewed")
                    if k in out}
        await s.commit()


# ── 重启收尾 ─────────────────────────────────────────────────────

async def recover_orphans() -> int:
    """进程重启后收拾现场。

    **不做会怎样**：进程被 kill 时一行 except 都不会跑，正在跑的那批
    永远停在 running —— 页面上进度条转到天荒地老，人也不知道该不该重发。
    这个项目已经在「执行」上栽过同一个坑（见 `stuck_recovery`）。

    收拾方式是**退回 queued 而不是标失败**：已经审完的那几条结论都在库里，
    item 的 status 记着谁审完了，重新排队只会跑剩下的。

    ⚠ **`INLINE_KIND` 的不能退回 queued**。那是 `tb_review_case` 在账本上留的
    在跑标记（MCP 内联跑，不经队列，见 `mcp/tools/review._open_single_batch`）——
    退回 queued 的话，worker 会把它当成一个待跑批次捡走**再真跑一遍**
    （`_run_batch` 固定 `run_first=True`），等于重启一次就凭空多一轮真跑，
    还会跟已经落库的结论打架。它们直接标成终态：进程都崩了，那次审核确实没跑完。

    排除的是 `INLINE_KIND`，**不是 `single`** —— `single` 是详情页点"审这一条"
    发起的，那种**走队列**（`api/case_review.py` 里 `len(case_ids)==1` 就推断成
    single），排除它等于服务一重启，人在页面上发起的单条审核就被判死。
    """
    async with async_session_factory() as s:
        rows = (await s.execute(
            select(ReviewBatch).where(ReviewBatch.status == "running"))).scalars().all()
        inline = [b for b in rows if b.kind == INLINE_KIND]
        owned = [b for b in rows if b.kind != INLINE_KIND]
        for b in owned:
            b.status = "queued"
            b.current_case_code = None
            b.note = "服务重启过，这批从没审完的那条接着跑"
        for b in inline:
            b.status = "partial"
            b.current_case_code = None
            b.failed = b.failed + max(0, b.total - b.done)
            b.done = b.total
            b.finished_at = _now()
            b.note = "服务重启过，这次单条审核没跑完（内联跑的，不排队重跑）"
        if inline:
            await s.execute(
                update(ReviewBatchItem)
                .where(ReviewBatchItem.batch_id.in_([b.id for b in inline]),
                       ReviewBatchItem.status.in_(("pending", "running")))
                .values(status="failed", error="服务重启过，这次单条审核没跑完",
                        finished_at=_now()))
        if owned:
            await s.execute(
                update(ReviewBatchItem)
                .where(ReviewBatchItem.status == "running",
                       ReviewBatchItem.batch_id.in_([b.id for b in owned]))
                .values(status="pending"))
        await s.commit()
        n, n_inline = len(owned), len(inline)

    if n:
        logger.warning("重启收尾：%d 个审核批次退回排队", n)
    if n_inline:
        logger.warning("重启收尾：%d 次单条审核标为没跑完（内联跑的，不重排）", n_inline)
    # 把队列重新拉起来 —— 只标状态不拉 worker 的话，它们会一直排着没人跑。
    async with async_session_factory() as s:
        keys = (await s.execute(
            select(ReviewBatch.environment_id).where(ReviewBatch.status == "queued")
            .group_by(ReviewBatch.environment_id))).scalars().all()
    for k in keys:
        await ensure_worker(_env_key(k))
    return n


async def queue_view(session, branch_id) -> dict:
    """这条分支上还有多少在排队/在跑 —— 报告页标题那行字。"""
    rows = (await session.execute(
        select(ReviewBatch.status, func.count(ReviewBatch.id))
        .where(ReviewBatch.branch_id == uuid.UUID(str(branch_id)),
               ReviewBatch.status.in_(ACTIVE_STATUSES))
        .group_by(ReviewBatch.status))).all()
    return {status: n for status, n in rows}
