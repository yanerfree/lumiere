"""让**以后**回推就是对的，而不是每轮回头改数据。

这批判据都来自一次真实审计里查出来、但当时只能靠人指出来的问题。
逐条改成机制 —— 数据是改不完的，改了也只对那一条有效。
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.engine.har import MAX_ENTRIES
from app.mcp.tools import deliverable
from tests.test_check_deliverable import FakeSession, _case, _scenario, _step


def _run(session):
    return asyncio.run(deliverable.check_deliverable(session, str(session._case.id)))


def _kinds(r, bucket="notes"):
    return [x["kind"] for x in r.get(bucket, [])]


# ── ① 写操作只断状态码 ──────────────────────────────────────────

def test_改状态的写操作只断状态码要提示():
    """实测 105 步里 20 步只断 status，其中「禁用服务」「重新启用服务」这类
    **改状态的写操作**只断了 200 —— 接口什么都没做、只要回 200 也判绿，
    而这类步骤恰恰是用例的核心动作。"""
    steps = [_step(0, "禁用服务", method="POST",
                   assertions=[{"type": "status", "value": 200}])]
    r = _run(FakeSession(case=_case(), scenario=_scenario(), steps=steps))
    assert "write_only_status_assert" in _kinds(r), r.get("notes")


def test_登录制备清理只断状态码不提示():
    """它们的目的就是"别报错"，断状态码是对的。滥报会把真的那条淹掉。"""
    steps = [_step(0, "制备：发布上线", method="POST", group="制备",
                   assertions=[{"type": "status", "value": 200}])]
    r = _run(FakeSession(case=_case(), scenario=_scenario(), steps=steps))
    assert "write_only_status_assert" not in _kinds(r), r.get("notes")


def test_读操作只断状态码不提示():
    """GET 不改状态，断状态码没毛病。"""
    steps = [_step(0, "查询服务列表", method="GET",
                   assertions=[{"type": "status", "value": 200}])]
    r = _run(FakeSession(case=_case(), scenario=_scenario(), steps=steps))
    assert "write_only_status_assert" not in _kinds(r)


def test_写操作断到字段上就不提示():
    steps = [_step(0, "禁用服务", method="POST", assertions=[
        {"type": "status", "value": 200},
        {"type": "body_field", "field": "data.enabled", "expected": False}])]
    r = _run(FakeSession(case=_case(), scenario=_scenario(), steps=steps))
    assert "write_only_status_assert" not in _kinds(r)


# ── ② 少做一维却没说为什么 ────────────────────────────────────────

def test_没写不做某维的理由要提示():
    """建用例时只提醒不拦，实测 6 条全空 —— 提醒发生在写入那一刻，过了就没人再提。
    挪到交付门禁：CC 每次自证都要看这份结论。"""
    r = _run(FakeSession(case=_case(target_level="spec_api", target_level_reason=None),
                         scenario=_scenario(), steps=[_step(0, "x")]))
    assert "target_level_reason_missing" in _kinds(r), r.get("notes")


def test_写了理由就不提示():
    r = _run(FakeSession(case=_case(target_level="spec_api",
                                    target_level_reason="纯接口链路，页面回显由 00001 覆盖"),
                         scenario=_scenario(), steps=[_step(0, "x")]))
    assert "target_level_reason_missing" not in _kinds(r)


def test_三件套齐的不提示():
    """full 没有"少做的维度"，不该问理由。"""
    r = _run(FakeSession(case=_case(target_level="full", target_level_reason=None),
                         scenario=_scenario(), steps=[_step(0, "x")],
                         script=SimpleNamespace(id=uuid.uuid4()),
                         runs=[SimpleNamespace(status="passed", captured_requests=[])]))
    assert "target_level_reason_missing" not in _kinds(r)


# ── ③ 越界提示不能被理解成「删掉这一步」 ──────────────────────────

def test_越界提示要说清不是删掉这一步():
    """实测 CC 打算删掉两条『应记入操作日志』，理由是『别的用例已覆盖』——
    而那两条断的 action（改路由 / 回滚）没有任何其它用例验过。删掉就是丢覆盖。"""
    steps = [_step(0, "跨租户目录应可见该服务的暴露级别与订阅入口")]
    r = _run(FakeSession(case=_case(), scenario=_scenario(), steps=steps))
    n = [x for x in r.get("notes", []) if x["kind"] == "possible_out_of_scope"]
    assert n, r.get("notes")
    assert "不是删掉这一步" in n[0]["detail"], n[0]["detail"]


# ── ④ 流量截断按重要性留，不是按先来后到 ────────────────────────

def _har(entries):
    import json
    import tempfile
    from pathlib import Path
    p = Path(tempfile.mkdtemp()) / "n.har"
    p.write_text(json.dumps({"log": {"entries": entries}}), encoding="utf-8")
    return str(p)


def _entry(i, method="GET", status=200, path="/api/v1/x"):
    return {"startedDateTime": f"2026-08-16T00:00:{i:02d}.000Z", "time": 1,
            "request": {"method": method, "url": f"http://h{path}?i={i}", "headers": []},
            "response": {"status": status, "content": {"mimeType": "application/json"}}}


def test_写操作不会被页面轮询挤掉():
    """原来是「取前 N 条然后 break」。实测一次执行 2960 条，配额全被页面自身的
    轮询 GET 吃光，**真正的写操作一条都没进来** —— 而拿这份流量编排接口场景
    恰恰只需要那些写操作。"""
    from app.engine.har import parse_har
    entries = [_entry(i) for i in range(MAX_ENTRIES + 200)]          # 海量 GET 在前
    entries += [_entry(9000 + i, method="POST") for i in range(5)]   # 写操作在最后
    out = parse_har(_har(entries))
    posts = [e for e in out if e.get("method") == "POST"]
    assert len(posts) == 5, f"写操作被挤掉了，只剩 {len(posts)} 条"


def test_非2xx也要留下():
    from app.engine.har import parse_har
    entries = [_entry(i) for i in range(MAX_ENTRIES + 200)]
    entries += [_entry(9000, status=500)]
    out = parse_har(_har(entries))
    assert [e for e in out if e.get("status") == 500], "失败响应被挤掉了 —— 那是排错唯一的证据"


def test_截断仍然留痕且说清丢了什么():
    from app.engine.har import parse_har
    out = parse_har(_har([_entry(i) for i in range(MAX_ENTRIES + 50)]))
    mark = [e for e in out if e.get("truncated")]
    assert mark, "静默截断 —— 面板会把上限数读成实际数"
    assert mark[0]["totalSeen"] > MAX_ENTRIES
    assert "droppedOrdinary" in mark[0], "没说清丢掉的是哪一类"


def test_不超额时不截断也不改顺序():
    from app.engine.har import parse_har
    out = parse_har(_har([_entry(i) for i in range(10)]))
    assert len(out) == 10 and not any(e.get("truncated") for e in out)
    assert [e["startedAt"] for e in out] == sorted(e["startedAt"] for e in out)


def test_截断后仍按时间排序():
    """按重要性分组挑完要排回时间序，否则面板上时间乱跳。"""
    from app.engine.har import parse_har
    entries = [_entry(i, method="POST" if i % 20 == 0 else "GET")
               for i in range(MAX_ENTRIES + 100)]
    out = [e for e in parse_har(_har(entries)) if not e.get("truncated")]
    stamps = [e["startedAt"] for e in out]
    assert stamps == sorted(stamps), "截断后时间乱序了"


# ── ⑤ 写操作不许被催重试（两个门禁必须同一口径） ──────────────────

def test_写操作不催加重试():
    """**平台自己在打架。** 一边催「加 retry_timeout_ms」，另一边警告
    「写操作上开重试会造出多份数据」—— 同一个 POST 步骤同时收到两条相反建议。

    实测 CC 指出来：6 条场景报了 19 处，全是 申请/驳回/审批/撤销 这类 POST，
    它们的 data.status 是**同步响应直接回传的**，没有异步可等；
    真异步的是数据面下发，那些步骤本来就开着重试。
    照建议加 = 重发写请求造出多条数据，比不加更糟。
    """
    steps = [_step(0, "消费方发起订阅申请", method="POST", retry=0,
                   url="${BASE_URL}/api/v1/subscriptions",
                   assertions=[{"type": "body_field", "field": "data.status",
                                "expected": "pending"}])]
    r = _run(FakeSession(case=_case(), scenario=_scenario(), steps=steps))
    assert "async_assertion_no_retry" not in [x["kind"] for x in r["risks"]], r["risks"]


def test_读回来确认那种步骤照旧要催():
    """豁免只给写操作。真正等收敛的 GET 必须还报，否则等于把这条判据废了。"""
    steps = [_step(0, "确认推送已收敛", method="GET", retry=0,
                   url="${BASE_URL}/api/v1/services/${sid}/push-status",
                   assertions=[{"type": "body_field", "field": "data.status",
                                "expected": "success"}])]
    r = _run(FakeSession(case=_case(), scenario=_scenario(), steps=steps))
    assert "async_assertion_no_retry" in [x["kind"] for x in r["risks"]], r["risks"]


def test_回推门禁和交付门禁口径一致():
    """两处判据分头写，很容易只改一处 —— 那就又变成"两个地方说法不同"。"""
    from app.mcp.tools.sync import _needs_retry
    post = {"method": "POST", "url": "${BASE_URL}/api/v1/subscriptions",
            "retry_timeout_ms": 0,
            "assertions": [{"type": "body_field", "field": "data.status", "expected": "pending"}]}
    assert _needs_retry(1, post) is None, "回推门禁还在催写操作加重试"
    get = dict(post, method="GET", url="${BASE_URL}/api/v1/services/x/push-status")
    assert _needs_retry(1, get) is not None, "读回来确认那种不催了 —— 判据被改废了"


# ── ⑥ 预期到底跟谁确认的 ────────────────────────────────────────

def test_没确认过要提示():
    r = _run(FakeSession(case=_case(expected_confirmed_at=None, expected_confirmed_actor=None),
                         scenario=_scenario(), steps=[_step(0, "x")]))
    assert "expected_not_confirmed" in _kinds(r), r.get("notes")


def test_落款看不出是人时要摆出来():
    """实测有一条落款写的是「实测（本轮探索）」—— 那是 CC 自己跑了一遍，
    不是任何人确认过。三份产物同源，互相一致但一起错的时候，只有外部确认能挡住。
    **装作有确认，比没有确认更危险。**"""
    import datetime
    r = _run(FakeSession(
        case=_case(expected_confirmed_at=datetime.datetime(2026, 8, 16),
                   expected_confirmed_actor="实测（本轮探索）"),
        scenario=_scenario(), steps=[_step(0, "x")]))
    n = [x for x in r.get("notes", []) if x["kind"] == "expected_confirmed_by_self"]
    assert n, r.get("notes")
    assert "实测（本轮探索）" in n[0]["detail"], "没把落款原样摆出来，人看不出问题在哪"


def test_跟人确认过的不提示():
    import datetime
    r = _run(FakeSession(
        case=_case(expected_confirmed_at=datetime.datetime(2026, 8, 16),
                   expected_confirmed_actor="用户（候选清单评审）"),
        scenario=_scenario(), steps=[_step(0, "x")]))
    ks = _kinds(r)
    assert "expected_confirmed_by_self" not in ks and "expected_not_confirmed" not in ks, ks


# ── ⑦ 模块级 UI 覆盖空洞 ────────────────────────────────────────

def test_整个模块没有UI维度要报出来():
    """**这个空洞单条看不出来。** 逐条问「这条要不要做 UI」，每次回答都合理：
    「判定点在数据面」「UI 只能验按钮不存在」…… 六条各有各的道理，合起来
    就是整块界面没有任何自动化盯着。实测订阅管理 6 条全 spec_api、0 条做 UI。
    """
    import asyncio as _a
    from app.mcp.tools.deliverable import _module_ui_gaps
    fid = uuid.uuid4()
    cases = [SimpleNamespace(folder_id=fid, target_level="spec_api") for _ in range(6)]

    class S:
        async def get(self, model, oid):
            return SimpleNamespace(name="订阅管理")

    gaps = _a.run(_module_ui_gaps(S(), cases))
    assert gaps and gaps[0]["module"] == "订阅管理" and gaps[0]["caseCount"] == 6, gaps
    assert "整块界面裸奔" in gaps[0]["detail"]


def test_模块里有一条做UI就不报():
    """判据是「这个模块有没有人做」，不是「每条都要做」。"""
    import asyncio as _a
    from app.mcp.tools.deliverable import _module_ui_gaps
    fid = uuid.uuid4()
    cases = [SimpleNamespace(folder_id=fid, target_level="spec_api") for _ in range(5)]
    cases.append(SimpleNamespace(folder_id=fid, target_level="full"))

    class S:
        async def get(self, model, oid):
            return SimpleNamespace(name="订阅管理")

    assert _a.run(_module_ui_gaps(S(), cases)) == []


def test_刚起步的小模块不报():
    """才两三条可能只是刚开始写，这时候催 UI 是噪音。"""
    import asyncio as _a
    from app.mcp.tools.deliverable import _module_ui_gaps
    fid = uuid.uuid4()
    cases = [SimpleNamespace(folder_id=fid, target_level="spec") for _ in range(3)]

    class S:
        async def get(self, model, oid):
            return SimpleNamespace(name="刚开工")

    assert _a.run(_module_ui_gaps(S(), cases)) == []
