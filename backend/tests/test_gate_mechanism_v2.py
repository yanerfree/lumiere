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


# ── ⑧ 跑红不一定是问题，也可能正是它在干活 ──────────────────────

def _fs(case, scenario, steps, runs):
    """带 script_runs 的 FakeSession（_defect_verdict 要读最近一次执行）。"""
    return FakeSession(case=case, scenario=scenario, steps=steps, runs=runs)


def test_人已确认是产品缺陷时跑红不算阻塞():
    """**这条决定了「按需求写预期」到底可不可行。**

    门禁原来一律阻塞：按需求写预期而实现不符时用例就红，于是这条永远不可交付 ——
    唯一能交付的路是把预期改成实测值，也就是把被测系统的 bug 洗成「预期」。
    门禁只认绿，就是在奖励这件事。实测那 12 条预期全是实测倒推的，这是原因之一。
    """
    run = SimpleNamespace(status="failed", captured_requests=[],
                          confirmed_cause="product_defect",
                          confirmed_note="确认：禁用期应 403，实现返回 401",
                          cc_analysis=None)
    steps = [_step(0, "禁用后网关应返回 403", status="fail", method="GET")]
    r = _run(_fs(_case(manual_status="completed", api_status="completed"),
                 _scenario(), steps, [run]))
    assert r["deliverable"] is True, r["blockers"]
    assert "failing_on_known_defect" in _kinds(r), r.get("notes")


def test_只有CC自己提的归因不解锁交付():
    """自证不能解锁 —— 否则等于给了一条「跑不过就说是产品的锅」的后门。"""
    run = SimpleNamespace(status="failed", captured_requests=[], confirmed_cause=None,
                          cc_analysis={"cause": "product_defect", "confidence": "high"})
    steps = [_step(0, "禁用后网关应返回 403", status="fail", method="GET")]
    r = _run(_fs(_case(), _scenario(), steps, [run]))
    assert r["deliverable"] is False
    assert "api_steps_failed_pending_triage" in [b["kind"] for b in r["blockers"]], r["blockers"]


def test_没归因的失败照旧是硬阻塞():
    run = SimpleNamespace(status="failed", captured_requests=[],
                          confirmed_cause=None, cc_analysis=None)
    steps = [_step(0, "发布服务", status="fail", method="POST")]
    r = _run(_fs(_case(), _scenario(), steps, [run]))
    assert r["deliverable"] is False
    assert "api_steps_failed" in [b["kind"] for b in r["blockers"]], r["blockers"]


def test_人确认是脚本自己错的不解锁():
    """只有 product_defect 才解锁。test_defect 是脚本写错了，那就是该修。"""
    run = SimpleNamespace(status="failed", captured_requests=[],
                          confirmed_cause="test_defect", confirmed_note="断言字段名写错",
                          cc_analysis=None)
    steps = [_step(0, "发布服务", status="fail", method="POST")]
    r = _run(_fs(_case(), _scenario(), steps, [run]))
    assert r["deliverable"] is False


def test_方法论要求读需求加读实现再判断():
    """措辞是这套机制唯一的入口 —— CC 只会照它写的做。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/mcp/__init__.py").read_text(encoding="utf-8")
    seg = src[src.index("①-A"):src.index("①-0")]
    for k in ("读需求", "读实现", "自己比对", "按需求写", "product_defect"):
        assert k in seg, f"方法论里缺「{k}」"
    # 既要挡住"事事找人确认"，也要挡住"卡住了闷头硬试"。
    # 写得太死（只准问两件事）的后果是它遇到环境问题、越权、依赖造不出来时
    # 一直瞎试；写得太松又退回"把一堆没判断过的清单丢给人"。
    assert "默认自己判断" in seg, "没说清默认自己判 —— 会退回事事找人确认"
    for k in ("卡住了", "这条用例之外", "影响别人"):
        assert k in seg, f"兜底缺「{k}」—— 遇到这类情况它会闷头硬试"
    assert "带着你的判断和依据去问" in seg, "没说清怎么问 —— 甩清单那道确认会废掉"


def test_场景清单也来自需求加实现():
    """**源头**：场景来源原来只写「从页面盘」= 纯实现视角。
    需求里写了、实现漏做的功能，页面上没有入口，永远盘不到 —— 漏测的恰恰是这一类。
    预期那一环上一轮修了，场景这一环才是更早的源头。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/mcp/__init__.py").read_text(encoding="utf-8")
    seg = src[src.index("①-1 【怎么挑场景】"):src.index("①-2")]
    assert "先读需求" in seg, "场景来源没写需求 —— 又变成从实现倒推"
    assert "需求有、实现没有" in seg, "没说清功能缺失该怎么处理"
    assert "product_defect" in seg, "功能缺失没导向缺陷归因，会被当成跳过"
    assert "实现有、需求没有" in seg, "多出来的行为没人看"


def test_探索中发现场景不对要当场改():
    """场景清单是动手前拍的，动手时一定会发现它不准。
    没有这条，CC 会为了"跟一开始报的一致"硬把发现塞回原框，或者攒到下一轮。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/mcp/__init__.py").read_text(encoding="utf-8")
    seg = src[src.index("①-1-B"):src.index("①-2")]
    for k in ("拆成两条", "补一条", "tb_update_case", "重写它"):
        assert k in seg, f"缺「{k}」—— 发现了也不知道该怎么办"
    assert "不用等谁批准" in seg, "没授权就会攒着等确认"
    assert "删掉已有用例" in seg, "没划出真正需要先说的那条边界"
