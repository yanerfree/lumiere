"""单条审核（MCP 内联跑）不许被重复触发、也不许被队列捡走重跑。

**事故现场**（review-spec 反馈 §7）：`tb_review_case` 是一次不间断的同步调用，
`run_first=True` 时跑到分钟级、中途没有心跳。CC 那边一超时就中止，它看不见
"其实已经落库了"，照惯性再调一次 —— 同一条用例被真跑两遍，第二遍还会撞上
第一遍留下的数据，出两条互相矛盾的轮次。

判据：这一次审核在 `review_batches` 上留一行 `kind=INLINE_KIND`（`"cc_inline"`）
的账，既是"正在审"的标记，也是防重复的锁。**这个 kind 是专门开的，不是复用
`"single"`** —— `single` 是人在详情页点"审这一条"发起的，那种**走队列**
（`api/case_review.py` 里 `len(case_ids)==1` 就推断成 single），
把它当内联标记的话，服务一重启就会把人发起的单条审核判死。
（库里现成就有 4 条 actor=admin 的 single 批次，摸库时才发现的。）

四条边界都在这份文件里封样：

1. 有在跑的 → 直接回 `in_progress`，**不重跑**；
2. `INLINE_KIND` 挂过 15 分钟没落终态 → 当僵尸清掉，不永久卡住这条用例；
   但**队列自己的批次不许在这里清** —— 排在队里等 16 分钟是正常的；
3. 重启收尾 `recover_orphans()` 把队列批次退回 `queued` 接着跑，
   `INLINE_KIND` 却**不能**退回 —— 退回就会被 worker 捡走再真跑一遍
   （`_run_batch` 固定 `run_first=True`），等于重启一次凭空多一轮真跑。
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy.sql import Select

from app.models.review_batch import INLINE_KIND
from app.mcp.tools import review as review_tools
from app.services.review import queue


# ── ① / ② 在跑标记与僵尸清理 ──────────────────────────────────

class _InflightSession:
    """`_inflight` 只做一次 join 查询 + 可能一次 commit，够用。"""

    def __init__(self, rows):
        self._rows = rows
        self.commits = 0

    async def execute(self, stmt):
        return SimpleNamespace(all=lambda: list(self._rows))

    async def commit(self):
        self.commits += 1


def _pair(kind=INLINE_KIND, mins_ago=1.0, status="running"):
    started = review_tools._now() - timedelta(minutes=mins_ago)
    batch = SimpleNamespace(id=uuid.uuid4(), kind=kind, status="running",
                            actor_kind="ai", started_at=started, created_at=started,
                            total=1, done=0, failed=0, note=None,
                            current_case_code="TC-X-00001", finished_at=None)
    item = SimpleNamespace(id=uuid.uuid4(), batch_id=batch.id, status=status,
                           error=None, finished_at=None)
    return item, batch


def _inflight(rows):
    s = _InflightSession(rows)
    return asyncio.run(review_tools._inflight(s, uuid.uuid4())), s


def test_有在跑的单条审核就不再跑一遍():
    item, batch = _pair(mins_ago=2.0)
    busy, _ = _inflight([(item, batch)])
    assert busy is not None
    got_item, got_batch, mins = busy
    assert got_item is item and got_batch is batch
    assert 1.9 < mins < 2.2


def test_模块批量在审同一条也算在跑():
    """人在页面上点了模块批量、CC 同时跳进来审同一条 —— 这两个原来互相看不见。"""
    item, batch = _pair(kind="module_full", mins_ago=3.0, status="pending")
    busy, _ = _inflight([(item, batch)])
    assert busy is not None and busy[1].kind == "module_full"


def test_挂了太久的单条审核当僵尸清掉():
    item, batch = _pair(mins_ago=review_tools._INFLIGHT_STALE_MIN + 1)
    busy, s = _inflight([(item, batch)])
    assert busy is None, "僵尸不该继续挡着这条用例"
    assert item.status == "failed" and item.finished_at is not None
    assert batch.status == "partial" and batch.finished_at is not None
    assert batch.done == 1 and batch.failed == 1
    assert s.commits == 1, "清完要落库，否则下次还是僵尸"


def test_队列自己的批次等再久也不许在这里清():
    """排在队里等 16 分钟是正常的。队列有自己的 recover_orphans 收尾，
    在这儿清掉会把一批还没轮到的用例判死。"""
    item, batch = _pair(kind="module_full",
                        mins_ago=review_tools._INFLIGHT_STALE_MIN + 30,
                        status="pending")
    busy, s = _inflight([(item, batch)])
    assert busy is not None, "队列批次不归这里清"
    assert item.status == "pending" and batch.status == "running"
    assert s.commits == 0


def test_详情页发起的单条排久了也不许在这里清():
    """`kind="single"` 是人在详情页点"审这一条"发起的，**走队列**。
    它和内联的 `cc_inline` 只差一个字，清错了就是把人在等的那次审核判死。"""
    item, batch = _pair(kind="single",
                        mins_ago=review_tools._INFLIGHT_STALE_MIN + 30,
                        status="pending")
    busy, s = _inflight([(item, batch)])
    assert busy is not None, "single 是队列的，不归这里清"
    assert item.status == "pending" and batch.status == "running"
    assert s.commits == 0


def test_没人在审就是没人在审():
    busy, _ = _inflight([])
    assert busy is None


# ── ③ 重启收尾不许把内联批次退回排队 ────────────────────────

class _RecoverSession:
    def __init__(self, batches):
        self.batches = batches
        self.updates = []

    async def execute(self, stmt):
        if isinstance(stmt, Select):
            name = stmt.column_descriptions[0]["name"]
            if name == "environment_id":       # 第二段：把队列重新拉起来
                return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
            return SimpleNamespace(scalars=lambda: SimpleNamespace(
                all=lambda: list(self.batches)))
        self.updates.append(stmt)              # item 的批量 update
        return SimpleNamespace()

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _batch(kind, total=1, done=0, failed=0):
    return SimpleNamespace(id=uuid.uuid4(), kind=kind, status="running",
                           total=total, done=done, failed=failed,
                           current_case_code="TC-X-00001", note=None, finished_at=None)


def test_重启收尾把队列批次退回排队():
    b = _batch("module_full", total=10, done=4)
    s = _RecoverSession([b])
    n = asyncio.run(_recover(s))
    assert n == 1
    assert b.status == "queued" and b.current_case_code is None
    assert b.done == 4, "已经审完的那几条不该被改动"


def test_重启收尾不把单条审核退回排队():
    """退回 queued 的话 worker 会把它当待跑批次捡走**再真跑一遍**。"""
    single = _batch(INLINE_KIND)
    s = _RecoverSession([single])
    n = asyncio.run(_recover(s))
    assert n == 0, "内联批次不算「退回排队」的那一类"
    assert single.status != "queued"
    assert single.status == "partial" and single.finished_at is not None
    assert single.failed == 1 and single.done == 1
    assert "没跑完" in (single.note or "")


def test_两种批次混在一起各走各的():
    single, owned = _batch(INLINE_KIND), _batch("module_incremental", total=5, done=2)
    s = _RecoverSession([single, owned])
    n = asyncio.run(_recover(s))
    assert n == 1
    assert owned.status == "queued" and single.status == "partial"


def test_详情页发起的单条重启后照样接着跑():
    """同上：`single` 走队列，重启收尾必须把它退回 queued 接着跑，
    不能跟着 `cc_inline` 一起被判死。"""
    b = _batch("single")
    s = _RecoverSession([b])
    n = asyncio.run(_recover(s))
    assert n == 1
    assert b.status == "queued" and b.finished_at is None


def _recover(session):
    async def run():
        orig_factory, orig_worker = queue.async_session_factory, queue.ensure_worker
        queue.async_session_factory = lambda: session

        async def _fake_worker(*a, **k):
            raise AssertionError("没有 queued 批次时不该去拉 worker")

        queue.ensure_worker = _fake_worker
        try:
            return await queue.recover_orphans()
        finally:
            queue.async_session_factory = orig_factory
            queue.ensure_worker = orig_worker
    return run()
