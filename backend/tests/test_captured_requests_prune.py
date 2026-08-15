"""流量回收：通过的只留最新一次，失败的留最近 5 次。

一次 UI 执行录下浏览器发的全部请求（实测 96~98 条、约 34KB），是**失败时唯一的
网络证据**——平台的拦截器拦的是 httpx，浏览器发的请求根本不经过它。
但这张表只涨不落，而通过那次的流量几乎没人回头看。

这份测试盯的是回收**别把该留的清掉**，比"清没清干净"重要得多：
清多了丢的是排错现场，事后补不回来；清少了只是多占几 MB。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services import script_run_service as svc


class FakeRun:
    def __init__(self, status, created_at, n=97, script_type="ui", case_id=None):
        self.id = uuid.uuid4()
        self.case_id = case_id
        self.script_type = script_type
        self.status = status
        self.created_at = created_at
        self.captured_requests = [{"url": f"/x/{i}"} for i in range(n)] if n else None
        self.captured_pruned_count = None
        self.steps = [{"action": "点击"}]
        self.error_summary = "元素找不到" if status != "passed" else None
        self.screenshots = [{"name": "fail.png"}]


class FakeSession:
    """按 record_run 里那句 select 的形状返回行 —— 只认 status 和排序。"""

    def __init__(self, rows):
        self.rows = rows
        self.flushed = 0

    async def execute(self, stmt):
        want_passed = "status = " in str(stmt.compile()) and "!=" not in str(stmt.compile())
        rows = [r for r in self.rows
                if r.captured_requests is not None
                and ((r.status == "passed") if want_passed else (r.status != "passed"))]
        rows.sort(key=lambda r: r.created_at, reverse=True)

        class R:
            def scalars(self_inner):
                class S:
                    def all(s2):
                        return rows
                return S()
        return R()

    async def flush(self):
        self.flushed += 1


CID = uuid.uuid4()
T0 = datetime(2026, 8, 15, 6, 0, tzinfo=timezone.utc)


def _rows(spec):
    """spec 形如 [('passed', 0), ('failed', 1), ...]，数字是第几分钟（越大越新）。"""
    return [FakeRun(st, T0 + timedelta(minutes=m), case_id=CID) for st, m in spec]


async def _prune(rows):
    s = FakeSession(rows)
    n = await svc.prune_captured_requests(s, CID, "ui")
    return n, rows


@pytest.mark.asyncio
async def test_通过的只留最新一次():
    rows = _rows([("passed", 1), ("passed", 2), ("passed", 3)])
    await _prune(rows)
    kept = [r for r in rows if r.captured_requests is not None]
    assert len(kept) == 1
    assert kept[0].created_at == T0 + timedelta(minutes=3), "留的不是最新那次"


@pytest.mark.asyncio
async def test_失败的留最近五次():
    rows = _rows([("failed", i) for i in range(1, 9)])   # 8 次失败
    await _prune(rows)
    kept = sorted((r for r in rows if r.captured_requests is not None),
                  key=lambda r: r.created_at)
    assert len(kept) == 5, f"失败留了 {len(kept)} 次"
    assert kept[0].created_at == T0 + timedelta(minutes=4), "留的不是最近 5 次"


@pytest.mark.asyncio
async def test_重跑通过不会冲掉挂掉那次的流量():
    """**这条是整套规则存在的理由。**

    典型场景：挂了 → 重跑一次想复现 → 没复现出来（flaky）。
    如果一刀切"只留最新一次"，这一重跑就把挂掉那次的流量冲掉了 ——
    而 flaky 恰恰是最需要拿两次流量对比的场景。
    """
    rows = _rows([("failed", 1), ("passed", 2), ("passed", 3)])
    await _prune(rows)
    fail_row = next(r for r in rows if r.status == "failed")
    assert fail_row.captured_requests is not None, \
        "挂掉那次的流量被后来的重跑冲掉了 —— 排错现场没了，事后补不回来"


@pytest.mark.asyncio
async def test_回收只丢流量不丢别的证据():
    rows = _rows([("failed", i) for i in range(1, 9)])
    await _prune(rows)
    gone = [r for r in rows if r.captured_requests is None]
    assert gone, "这个用例没回收到任何行，下面的断言等于没跑"
    for r in gone:
        assert r.steps, "步骤被清掉了"
        assert r.error_summary, "错误摘要被清掉了"
        assert r.screenshots, "截图被清掉了"


@pytest.mark.asyncio
async def test_回收要记下原来有多少条():
    """只置空的话，界面上「本来就没抓到」和「抓了 97 条被回收了」长得一样，
    人会当成 bug 报。"""
    rows = _rows([("passed", 1), ("passed", 2)])
    await _prune(rows)
    gone = next(r for r in rows if r.captured_requests is None)
    assert gone.captured_pruned_count == 97, gone.captured_pruned_count


def test_置空必须落成SQL_NULL不是JSON_null():
    """**真库上抓到的 bug，单元测试全绿也照样发生。**

    JSONB 列默认把 Python None 存成字面量 'null'，不是 SQL NULL —— `IS NOT NULL`
    仍为真。于是被回收过的行每次执行都被重新选出来清一遍，`len(None or [])` = 0，
    **原来记的「97 条」被抹成 0**。信息不可逆地没了，而且一声不吭。

    发现方式是回收上线后对真库查了一眼：「还带流量 61 行、已回收 34 行」——
    数字对不上。单元测试用的是假 session，永远碰不到这个。
    """
    from app.models.script import ScriptRun
    # 三列都是 `result.get(...) or None` 写进来的，都会踩同一个坑。
    # 只修 captured_requests 的时候，steps 存量 25 行、screenshots 112 行都还是坏的 ——
    # 表现是任何 jsonb_array_length 查询直接报「cannot get array length of a scalar」。
    for name in ("captured_requests", "steps", "screenshots"):
        col = ScriptRun.__table__.c[name]
        assert getattr(col.type, "none_as_null", False) is True, \
            f"{name} 没开 none_as_null —— 置空会存成 JSON null，IS NOT NULL 照样为真"


@pytest.mark.asyncio
async def test_已回收过的不再碰():
    """双保险：重复回收会把原条数抹成 0，不可逆。"""
    import inspect
    src = inspect.getsource(svc.prune_captured_requests)
    assert "captured_pruned_count.is_(None)" in src, \
        "回收没有排除「已经回收过的行」—— 重复回收会把原条数抹成 0"


@pytest.mark.asyncio
async def test_不够数就一条都不动():
    rows = _rows([("passed", 1), ("failed", 2), ("failed", 3)])
    n, rows = await _prune(rows)
    assert n == 0
    assert all(r.captured_requests is not None for r in rows)


@pytest.mark.asyncio
async def test_回收失败不能拖垮记账():
    """流量回收是省空间的，执行记账是记事实的。前者挂了绝不能连累后者 ——
    否则一个清理 bug 会让所有执行都不记账，那比多占几 MB 严重得多。"""
    import inspect
    src = inspect.getsource(svc.record_run)
    i = src.index("prune_captured_requests")
    assert "except Exception" in src[i:i + 300], "回收没有被 try 包住"


def test_保留档位是两档不是一刀切():
    assert svc.KEEP_PASSED == 1
    assert svc.KEEP_FAILED == 5
    assert svc.KEEP_FAILED > svc.KEEP_PASSED, \
        "失败留得不比通过多的话，重跑就会把挂掉那次挤出窗口"


def test_接口把回收条数送出去():
    from pathlib import Path
    api = (Path(__file__).resolve().parents[1] / "app/api/scripts.py").read_text(encoding="utf-8")
    assert '"captured_pruned_count"' in api, "执行历史接口没送回收条数，界面说不出「已回收」"


def test_执行历史展开是页签不是一路摊开():
    """一条记录 37 步 + 98 条流量，上下摞着同时展开要占三四屏，往下翻第二条得滚很久。
    而且每条历史都长一样，摊开不会让人"一眼看全"，只会让人"怎么翻都翻不完"。

    失败归因不进页签 —— 那是失败时唯一要人动手的东西，藏进页签等于藏起来。
    """
    from pathlib import Path
    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/cases/CaseDetail.jsx").read_text(encoding="utf-8")
    assert "function RunDetail" in jsx
    i = jsx.index("function RunDetail")
    seg = jsx[i:i + 4200]
    assert "<Tabs" in seg, "执行历史展开没用页签，又摊开了"
    for k in ("'steps'", "'traffic'", "'raw'"):
        assert k in seg, f"页签缺了 {k}"
    tabs_at = seg.index("<Tabs")
    assert seg.index("FailureTriagePanel") < tabs_at, "失败归因被塞进页签了 —— 那是唯一要人动手的东西"
    assert "expandedRowRender: r => (\n                      <RunDetail" in jsx, \
        "执行历史没用 RunDetail —— 组件写了但没接上"


def test_前端三种情况分得开():
    """有流量 / 已回收 / 没抓到 —— 后两种都渲染成空白的话，回收会被当成 bug 报。"""
    from pathlib import Path
    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/cases/CaseDetail.jsx").read_text(encoding="utf-8")
    assert "function RunTraffic" in jsx, "执行历史里没有流量渲染"
    i = jsx.index("function RunTraffic")
    seg = jsx[i:i + 1800]
    assert "已回收" in seg, "回收后没有提示，会显示成空白"
    assert "没有抓到流量" in seg, "没抓到流量的情况没区分开"
    assert "<RunTraffic" in jsx, "组件写了但没挂到执行历史里"
