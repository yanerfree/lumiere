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
