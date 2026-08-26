"""审过之后 CC 又改了内容 —— 时间线上必须留得下痕迹。

**事故现场**：TC-DYGL-00001 一天审了 8 轮（79→71→76→76→74→77→**89 通过**），
中间 6 次 `lum_update_case`。但 `cc_resubmit` 只在「被打回 + 回推接口场景」
那一条路上记（`sync._reflect_block`），改步骤/预期、换 UI 脚本一律不记 ——
于是库里只剩几个上下跳的分数，**"改了再审"和"原样再审"长得一模一样**。

这件事为什么要紧：模型给的分本来就抖（同一条 86 和 78 是常事，见
`score_and_verdict` 的注释）。分不开这两者，「改到过为止」和「刷到过为止」
就既无法证实也无法证伪 —— 而这正是"审核能不能替人工"的关键证据。

四条边界在这份文件里封样，其中两条是**"应该沉默"**的：
  ① 审过之后改内容 → 记一条 `cc_edit`；
  ② 连续几次改动合并成一行，不把时间线冲成流水账；
  ③ **审过之前一条都不记** —— 写用例的过程本来就是一路改；
  ④ **`cc_edit` 不许驱动「整改待复审」，也不许把 `reviewStale` 的 ⚠ 顶掉**。
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.services.review import rounds


class _RoundSession:
    """够 `record_edit` 用：一条"最新轮次"查询 + 一条 count + `record` 里的 max。

    按编译出来的 SQL 里有没有聚合函数分发 —— 三种形状都是查同一张表，
    用 `column_descriptions` 分不开（`select(func.count())` 的 entity 是 None）。
    """

    def __init__(self, latest=None, reviewed=0):
        self._latest = latest
        self._reviewed = reviewed
        self.added = []
        self.flushes = 0

    async def execute(self, stmt):
        sql = str(stmt).lower()
        if "count(" in sql:
            return SimpleNamespace(scalar_one=lambda: self._reviewed)
        if "max(" in sql:
            return SimpleNamespace(
                scalar_one_or_none=lambda: (self._latest.round if self._latest else None))
        rows = [self._latest] if self._latest is not None else []
        return SimpleNamespace(scalars=lambda: SimpleNamespace(
            all=lambda: rows, first=lambda: (rows[0] if rows else None)))

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushes += 1


def _round(kind="ai_review", rnd=1, changed=None, content_hash=None):
    return SimpleNamespace(round=rnd, kind=kind, changed=changed,
                           content_hash=content_hash)


def _edit(session, **kw):
    kw.setdefault("note", "改了用例内容")
    return asyncio.run(rounds.record_edit(session, uuid.uuid4(), **kw))


# ── ① 审过之后改内容要留痕 ────────────────────────────────────

def test_审过之后改内容记一条编辑轮次():
    s = _RoundSession(latest=_round("ai_review", 1, content_hash="abc"), reviewed=1)
    row = _edit(s, fields=["steps", "expectedResult"], step_count=12)
    assert row is not None and s.added, "审过之后的改动必须留下一行"
    assert row.kind == rounds.EDIT_KIND
    assert row.changed["fields"] == ["expectedResult", "steps"]
    assert row.changed["stepCount"] == 12


def test_编辑轮次不带verdict也不带分数():
    """它不是一次结论，别让它在时间线上看起来像审过了。"""
    s = _RoundSession(latest=_round("ai_review", 1, content_hash="abc"), reviewed=1)
    row = _edit(s, fields=["steps"])
    assert row.verdict is None and row.total is None


def test_编辑轮次不落签名():
    """`content_hash` 的含义是「这份 verdict 是对着哪一版内容算的」，
    只有带结论的轮次才有意义 —— 编辑轮次也落一个的话，`stale_map` 取的
    "最新一轮签名"就成了编辑时那份，⚠ 会当场失灵（见下面那条）。"""
    s = _RoundSession(latest=_round("ai_review", 1, content_hash="abc"), reviewed=1)
    row = _edit(s, fields=["steps"])
    assert getattr(row, "content_hash", None) is None


# ── ② 连续改动合并 ───────────────────────────────────────────

def test_连着改几次合并成一行():
    """一次整改常常是好几次调用（改标题、补一步、改预期）。拆成三行
    会把时间线冲垮 —— 合并成一行带一个次数。"""
    latest = _round(rounds.EDIT_KIND, 2, changed={"note": "改了用例内容",
                                                  "fields": ["title"], "edits": 1})
    s = _RoundSession(latest=latest, reviewed=1)
    row = _edit(s, fields=["steps"], step_count=9)
    assert not s.added, "已经有一条编辑轮次了，不该再开一行"
    assert row is latest
    assert row.changed["edits"] == 2
    assert row.changed["fields"] == ["steps", "title"], "改过的字段要并起来，不是覆盖"
    assert row.changed["stepCount"] == 9


def test_又审了一轮之后再改就是新的一行():
    """合并只在"最新一轮就是编辑"时发生。中间落过一轮 ai_review 的话，
    再改属于下一次整改 —— 并进上一次里会把两轮之间的边界抹掉。"""
    s = _RoundSession(latest=_round("ai_review", 3, content_hash="abc"), reviewed=2)
    row = _edit(s, fields=["steps"])
    assert s.added and row.round == 4


# ── ③ 应该沉默：审过之前不记 ──────────────────────────────────

def test_还没审过就一条都不记():
    """写用例的过程本来就是一路改，那时候没有"轮次之间"这回事。
    记下来只是把时间线填满噪音。"""
    s = _RoundSession(latest=None, reviewed=0)
    assert _edit(s, fields=["steps"]) is None
    assert not s.added and s.flushes == 0


def test_只有整改提交过没真审过也不记():
    """`cc_resubmit` 不算"审过" —— 判据是有没有一轮 `ai_review`。"""
    s = _RoundSession(latest=_round("cc_resubmit", 1), reviewed=0)
    assert _edit(s, fields=["steps"]) is None


# ── ④ 应该沉默：不许牵连既有的派生状态 ────────────────────────

def test_编辑轮次不会把用例跳成整改待复审():
    """「整改待复审」是 `cc_resubmit` 驱动的（`display_status` / 模块报告的
    resubmitted 桶）。复用那个 kind 的话，一条 approved 的用例改一下标题
    就会在页面上跳成"待复审" —— 而它的结论其实还在。"""
    case = SimpleNamespace(id=uuid.uuid4(), review_status="approved")
    s = _RoundSession(latest=_round(rounds.EDIT_KIND, 5))
    assert asyncio.run(rounds.display_status(s, case)) == "approved"


def test_编辑轮次不许把过期警告顶掉():
    """**这条是这一组里最要紧的。** `stale_map` 取的是"最新一轮的签名"，
    而编辑轮次不带签名 —— 不跳过它的话，最新签名会被顶成 None，这条用例
    整个漏出 stale_map，结果正好反了：**内容刚被改过的那些反而不再报 ⚠**。
    """
    import tests.test_review_staleness as st

    cid = uuid.uuid4()
    sig = st._sig(ui_version=1)
    # 审完（签名 = 当时那版）→ 换了 UI 脚本 → 记了一条编辑轮次
    cid, out = st._stale_map(
        [(cid, 1, "ai_review", sig), (cid, 2, rounds.EDIT_KIND, None)],
        ui_version=2, case_id=cid)
    assert out.get(cid) is True, "改过之后必须还报得出过期"


def test_人工覆盖仍然照旧顶掉过期判断():
    """跟上面那条相反 —— 人工那轮是**故意**顶掉的：人点通过是在改动之前
    还是之后，库里没有依据，不猜。这条防的是"顺手把 human_override 也跳过"。
    """
    import tests.test_review_staleness as st

    cid = uuid.uuid4()
    cid, out = st._stale_map(
        [(cid, 1, "ai_review", st._sig(ui_version=1)),
         (cid, 2, "human_override", None)],
        ui_version=2, case_id=cid)
    assert cid not in out
