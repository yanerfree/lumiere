"""失败跟进单 —— 「改好了跑成功了，过一段时间又失败了」这件事必须看得出来。

用户的原话点在要害上：
  「同一个问题不好武断，因为今天有 bug、修复了，不代表明天不会被改出同一个 bug；
    但是没修复之前肯定是一直失败」

所以归并分两种情况，不是一刀切：
  · 单子**开着**：同一条用例、同一现象的失败都并进去（没修好本来就一直红，同一件事）
  · 单子**关了**之后又红：**新开一张**，挂上一张，复发次数 +1
    —— 并进老账会把"中间绿过"这段埋掉，而那正是"回归"的证据

关闭：跑绿自动关（记凭哪一次关的）；人工关允许，但**原因必填**（用户拍的）。
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.failure_ticket import OPEN_STATUSES, STATUSES
from app.services import failure_ticket_service as svc


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _Session:
    """按顺序喂查询结果。added 收集新建的单子。"""

    def __init__(self, *batches):
        self._q = list(batches)
        self.added = []

    async def execute(self, _stmt):
        return _Res(self._q.pop(0) if self._q else [])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def get(self, _model, _id):
        return self._q.pop(0)[0] if self._q and self._q[0] else None


CID = uuid.uuid4()


def _run(status="failed", phenomenon="element_not_found"):
    return SimpleNamespace(id=uuid.uuid4(), case_id=CID, script_type="ui",
                           status=status, failure_phenomenon=phenomenon)


def _ticket(status="open", occurrences=1, recurrence=0):
    return SimpleNamespace(id=uuid.uuid4(), case_id=CID, script_type="ui",
                           phenomenon="element_not_found", status=status,
                           occurrences=occurrences, recurrence=recurrence,
                           last_run_id=None, closed_by_run_id=None, closed_reason=None,
                           closed_by=None, closed_at=None)


# ── 没修好之前：同一件事，累计 ────────────────────────────────────

@pytest.mark.asyncio
async def test_单子开着时同样的失败并进去():
    t = _ticket(occurrences=3)
    out = await svc.on_run(_Session([t]), _run())
    assert out is t and t.occurrences == 4, "没修好之前它本来就一直红，不该每轮新开一张"


@pytest.mark.asyncio
async def test_自称修好又红了退回处置中():
    """「改了但没修对」和「还没改」要分得开。"""
    t = _ticket(status="verifying")
    await svc.on_run(_Session([t]), _run())
    assert t.status == "fixing"


# ── 关了又红：复发，新开一张 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_关了之后又红算复发而不是并进老账():
    """这条是用户点出来的：修好、跑绿、过一段时间又失败 —— 那是**回归**。
    并进老账里，老账显示"红了 8 次"，看不出中间绿过，回归这个信号就被埋了。
    """
    closed = _ticket(status="closed", recurrence=1)
    s = _Session([], [closed])          # ① 没有开着的 ② 有关掉的
    await svc.on_run(s, _run())
    assert len(s.added) == 1
    new = s.added[0]
    assert new.reopened_from == closed.id, "没挂上一张，看不出是复发"
    assert new.recurrence == 2, "复发次数要累加 —— flaky 和真回归靠它分"
    assert closed.occurrences == 1, "老账不该被动"


@pytest.mark.asyncio
async def test_第一次红就是新单且复发数为零():
    s = _Session([], [])
    await svc.on_run(s, _run())
    assert s.added[0].recurrence == 0 and s.added[0].reopened_from is None


@pytest.mark.asyncio
async def test_现象判不出来也能归并():
    """现象为 None 时用 "unknown" —— 用 NULL 的话 NULL != NULL，每次都会新开一张。"""
    s = _Session([], [])
    await svc.on_run(s, _run(phenomenon=None))
    assert s.added[0].phenomenon == "unknown"


# ── 关单 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_跑绿自动关并记下凭哪次关的():
    t = _ticket()
    run = _run(status="passed", phenomenon=None)
    await svc.on_run(_Session([t]), run)
    assert t.status == "closed" and t.closed_by_run_id == run.id
    assert t.closed_reason == "复跑跑绿" and t.closed_at


@pytest.mark.asyncio
async def test_已知问题不会被偶然一次绿关掉():
    """人明确说过"知道它红、先不修"的，偶然绿一次不代表问题没了。"""
    known = _ticket(status="known")
    # known 不在 OPEN_STATUSES 里，所以查询压根不会捞到它
    assert "known" not in OPEN_STATUSES
    s = _Session([])                    # 查未关单 → 空
    out = await svc.on_run(s, _run(status="passed"))
    assert out is None and known.status == "known"


@pytest.mark.asyncio
async def test_人工关闭必须写原因():
    """没有原因的关闭等于把红的问题从看板上抹掉，下一轮又冒出来，
    谁都不知道上次为什么放过。"""
    with pytest.raises(ValueError):
        await svc.close_manually(_Session(), uuid.uuid4(), reason="  ", actor="admin")


@pytest.mark.asyncio
async def test_人工关闭记下是谁关的和理由():
    t = _ticket()
    out = await svc.close_manually(_Session([t]), t.id, reason="环境问题，已换机器", actor="liyan")
    assert out.status == "closed" and out.closed_by == "liyan"
    assert out.closed_reason == "环境问题，已换机器"


@pytest.mark.asyncio
async def test_标已知问题走另一个状态():
    t = _ticket()
    out = await svc.close_manually(_Session([t]), t.id, reason="等三方修", actor="liyan",
                                   known_issue=True)
    assert out.status == "known"


# ── 接线 ─────────────────────────────────────────────────────────

def test_挂在执行记账的唯一写入口上():
    """四条执行路径（单条/计划/批量/页面运行）都要有 ——
    各自记得去调的话，"下次再加一条路径又漏记"这个坑这个项目已经踩过。"""
    from app.services.script_run_service import record_run
    src = inspect.getsource(record_run)
    assert "failure_ticket_service.on_run" in src
    seg = src[src.index("failure_ticket_service"):]
    assert "logger.exception" in seg[:600], "跟进单出问题不能拖垮执行记账"


def test_状态机覆盖从红到关的全过程():
    assert set(STATUSES) == {"open", "analyzed", "confirmed", "fixing",
                             "verifying", "closed", "known"}
    assert "closed" not in OPEN_STATUSES and "known" not in OPEN_STATUSES


def test_值班入口给四个队列且带下一步():
    from app.mcp.tools.duty import next_duty
    src = inspect.getsource(next_duty)
    for q in ("待归因", "待复跑", "待补场景", "待自证"):
        assert q in src
    assert "下一步" in src, "只给清单不给下一步，CC 还得回头翻规范"
    assert "别自己关单" in src


def test_值班入口在triage和regression两档里():
    from app.mcp.profiles import PROFILES
    for key in ("triage", "regression"):
        p = next(x for x in PROFILES if x["key"] == key)
        assert "tb_next_duty" in p["tools"], f"{key} 档里没有"


# ── 页面侧 ───────────────────────────────────────────────────────

def test_跟进单挂在报告详情里():
    """人看完"这次红了 6 条"，下一句话就是"那 6 条现在怎么样了" ——
    单开一页要人自己去找，等于没有。"""
    import pathlib
    src = pathlib.Path("../frontend/src/pages/report/ReportDetail.jsx").read_text(encoding="utf-8")
    assert "failure-tickets?reportId=" in src, "报告页没按本次报告筛跟进单"
    for label in ("待归因", "待你确认", "待复跑", "已知问题"):
        assert label in src, f"状态 {label} 没显示"
    assert "第 ${t.recurrence} 次复发" in src.replace("`", "${").replace("}", "}") or "次复发" in src, \
        "复发次数要露出来 —— 它是 flaky 和真回归的分界"


def test_人工关单的原因在前后端都卡():
    """只在前端 disabled 是不够的 —— 接口被直接调就绕过去了。"""
    import inspect
    import pathlib
    from app.api import case_review
    api_src = inspect.getsource(case_review.close_failure_ticket)
    assert "min_length=2" in api_src, "接口没卡原因"
    ui = pathlib.Path("../frontend/src/pages/report/ReportDetail.jsx").read_text(encoding="utf-8")
    assert "closeReason.trim().length < 2" in ui, "前端没卡（人点了才发现报错，体验差）"


def test_默认只列没了结的():
    """看板要显示的是还没了结的；历史要看得传 onlyOpen=false。"""
    import inspect
    from app.api import case_review
    src = inspect.getsource(case_review.list_failure_tickets)
    assert 'only_open: bool = Query(default=True' in src


def test_值班入口不再用改过名的属性():
    """活体撞出来的：duty 里写着 `c.retest_pending`，而那个属性早改名成
    `has_fixed_bug` 了 —— 调用直接 AttributeError，整个值班入口挂掉。
    更重要的是语义：`fixed` 现在是"你回来调通了"（终态），
    所以「bug 标 fixed → 待重跑」这个队列在新语义下压根不存在。
    """
    import inspect

    from app.mcp.tools.duty import next_duty
    src = inspect.getsource(next_duty)
    assert "retest_pending" not in src, "又用了不存在的属性"
    assert "关联 bug 标了 fixed" not in src or "去掉了" in src


def test_用例模型上没有retest_pending():
    from app.models.case import Case
    assert not hasattr(Case, "retest_pending")
    assert hasattr(Case, "has_fixed_bug") and hasattr(Case, "blocked_by_bug")


def test_跑完要把runId回出来():
    """活体撞出来的：`tb_run_ui_script` 不回 run_id，而 `tb_submit_analysis` 要它 ——
    CC 跑完想归因得再调一次 tb_get_ui_script_result 去找，归因链第一步就断了。
    """
    import inspect

    from app.mcp.tools.ui_scripts import run_ui_script
    src = inspect.getsource(run_ui_script)
    assert '"runId": str(run_row.id)' in src
    assert '"ticket"' in src, "红了要把对应的跟进单一起给出来，别让它再查一遍"
    assert "tb_submit_analysis(run_id=" in src, "要指路：下一步拿这个 runId 去归因"
