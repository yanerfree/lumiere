"""交付门禁的判据。

**为什么要有这个工具**：之前每一轮都是 CC 说"这两条可以交付"，人再逐条查库才发现
不行（请求体被改坏、断言类型写错、异步断言裸奔、状态其实没到位）。人肉门禁的问题
不是慢，是**不可复现** —— 查的人换了、心气松了就漏过去。

**为什么这份测试必须严**：门禁判错比没有门禁更糟。说"可交付"而实际不行，
等于给了一个盖过章的错误结论，下游没人会再查一遍。所以这里正反都要钉。
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.mcp.tools import deliverable
from app.models.api_test import ApiTestScenario, ApiTestStep
from app.models.case import Case
from app.models.script import Script, ScriptRun


class FakeSession:
    """只认 session.get(Model, id) 和 execute(select(...))。

    execute 按 select 的目标模型返回预置好的行，够这个只读工具用。
    """

    def __init__(self, case=None, scenario=None, steps=None, script=None, runs=None):
        self._case = case
        self._scenario = scenario
        self._steps = steps or []
        self._script = script
        self._runs = runs or []

    async def get(self, model, oid):
        return self._case if model is Case else None

    async def execute(self, stmt):
        target = stmt.column_descriptions[0]["entity"]
        rows = {
            ApiTestScenario: [self._scenario] if self._scenario else [],
            ApiTestStep: self._steps,
            Script: [self._script] if self._script else [],
            ScriptRun: self._runs,
        }.get(target, [])
        return SimpleNamespace(scalars=lambda: SimpleNamespace(
            first=lambda: (rows[0] if rows else None), all=lambda: rows))


def _case(**kw):
    base = dict(id=uuid.uuid4(), case_code="TC-X-00001", title="服务发布后网关可调通",
                target_level="spec_api", lifecycle_status="draft",
                steps=[{"seq": 1, "action": "a", "expected": "b"}],
                expected_result="发布后网关返回 200",
                manual_status="not_started", ui_status="not_started",
                api_status="pending_review")
    base.update(kw)
    return SimpleNamespace(**base)


def _scenario():
    return SimpleNamespace(id=uuid.uuid4(), code="AT-0011")


def _step(order, name, status="pass", assertions=None, retry=0, url="${BASE_URL}/api/v1/x",
          body=None, group=None):
    return SimpleNamespace(sort_order=order, name=name, last_status=status,
                           assertions=assertions or [{"type": "status", "value": 200}],
                           retry_timeout_ms=retry, url=url, body=body, group_name=group)


def _run(session):
    return asyncio.run(deliverable.check_deliverable(session, str(session._case.id)))


# ── 正例：全绿且干净 → 可交付 ────────────────────────────────────

def test_全绿且无脆弱点则可交付():
    steps = [_step(0, "登录"), _step(1, "发布上线"),
             _step(2, "推送应已收敛", retry=10000,
                   url="${BASE_URL}/api/v1/services/${sid}/push-status",
                   assertions=[{"type": "body_field", "field": "data.status", "expected": "success"}]),
             _step(3, "打网关应 200", retry=10000, url="${gatewayBase}/v1/x/echo")]
    r = _run(FakeSession(case=_case(), scenario=_scenario(), steps=steps))
    assert r["deliverable"] is True, r["blockers"]
    assert r["blockers"] == []
    assert r["risks"] == [], r["risks"]
    assert "可交付" in r["verdict"]


# ── 硬阻塞 ──────────────────────────────────────────────────────

def test_一步都没跑过不算可交付():
    """「写完了」和「跑通了」是两件事 —— 这是最容易被当成"做完了"的一种。"""
    steps = [_step(0, "登录", status=None), _step(1, "发布", status=None)]
    r = _run(FakeSession(case=_case(), scenario=_scenario(), steps=steps))
    assert r["deliverable"] is False
    assert any(b["kind"] == "api_never_run" for b in r["blockers"]), r["blockers"]


def test_有步骤挂着不算可交付():
    steps = [_step(0, "登录"), _step(1, "发布", status="fail")]
    r = _run(FakeSession(case=_case(), scenario=_scenario(), steps=steps))
    assert r["deliverable"] is False
    assert any(b["kind"] == "api_steps_failed" for b in r["blockers"])


def test_布尔断言写成字符串不算可交付():
    """必然假红。这是 TC-FWGL-00001 当初被卡住的那条。"""
    steps = [_step(0, "确认已启用", assertions=[
        {"type": "body_field", "field": "data.enabled", "expected": "true"}])]
    r = _run(FakeSession(case=_case(), scenario=_scenario(), steps=steps))
    assert r["deliverable"] is False
    b = [x for x in r["blockers"] if x["kind"] == "assertion_bool_as_string"]
    assert b and "data.enabled" in b[0]["detail"] and "不加引号" in b[0]["detail"]


def test_欠接口维度不算可交付():
    r = _run(FakeSession(case=_case(target_level="spec_api"), scenario=None))
    assert r["deliverable"] is False
    assert any(b["kind"] == "api_scenario_missing" for b in r["blockers"])


def test_欠UI维度不算可交付():
    steps = [_step(0, "登录")]
    r = _run(FakeSession(case=_case(target_level="full"), scenario=_scenario(),
                         steps=steps, script=None))
    assert r["deliverable"] is False
    assert any(b["kind"] == "ui_script_missing" for b in r["blockers"])


def test_没有手工步骤不算可交付():
    r = _run(FakeSession(case=_case(steps=[]), scenario=_scenario(), steps=[_step(0, "x")]))
    assert r["deliverable"] is False
    assert any(b["kind"] == "manual_missing" for b in r["blockers"])


def test_spec级别不要求接口和UI():
    """target_level=spec 只要手工步骤 —— 别把它判成欠维度。"""
    r = _run(FakeSession(case=_case(target_level="spec"), scenario=None))
    assert r["deliverable"] is True, r["blockers"]
    assert r["dimStatus"] == {"manual": "not_started"}


# ── 脆弱点：交得了但会偶发红 ──────────────────────────────────────

def test_异步断言裸奔算脆弱点而不是阻塞():
    """跑绿了也是侥幸跑赢时间窗 —— 拦它太狠（有些接口确实同步），但必须说出来。"""
    steps = [_step(0, "推送应已收敛", retry=0,
                   url="${BASE_URL}/api/v1/services/${sid}/push-status",
                   assertions=[{"type": "body_field", "field": "data.status", "expected": "success"}])]
    r = _run(FakeSession(case=_case(), scenario=_scenario(), steps=steps))
    assert r["deliverable"] is True, r["blockers"]
    risk = [x for x in r["risks"] if x["kind"] == "async_assertion_no_retry"]
    assert risk and "10000" in risk[0]["detail"], r["risks"]


def test_只跑了一部分算脆弱点():
    """勾选运行之后整条链没被完整验证过一次。"""
    steps = [_step(0, "登录"), _step(1, "发布"), _step(2, "清理", status=None)]
    r = _run(FakeSession(case=_case(), scenario=_scenario(), steps=steps))
    assert r["deliverable"] is True
    assert any(x["kind"] == "api_partial_run" for x in r["risks"]), r["risks"]


def test_流量被截断算脆弱点():
    """靠后的写操作可能不在证据里，拿它编排场景会漏关键请求。"""
    run = SimpleNamespace(status="passed", captured_requests=[
        {"url": "/a"}, {"truncated": True, "kept": 150, "totalSeen": 252}])
    r = _run(FakeSession(case=_case(target_level="full"), scenario=_scenario(),
                         steps=[_step(0, "x")],
                         script=SimpleNamespace(id=uuid.uuid4()), runs=[run]))
    t = [x for x in r["risks"] if x["kind"] == "traffic_truncated"]
    assert t and "252" in t[0]["detail"], r["risks"]


# ── 提示：要人判断的，不拦 ────────────────────────────────────────

def test_弱断言给提示():
    """承诺「应产生版本记录」却只用 body_contains —— 字符串出现不等于那件事发生了。"""
    steps = [_step(0, "回滚应生成新版本且保留历史",
                   assertions=[{"type": "status", "value": 200},
                               {"type": "body_contains", "value": "rollback"}])]
    r = _run(FakeSession(case=_case(), scenario=_scenario(), steps=steps))
    assert r["deliverable"] is True
    assert any(x["kind"] == "weak_assertion" for x in r["notes"]), r["notes"]


def test_越界测试点给提示():
    """步骤关键词在标题和预期里都找不到 —— 疑似别的用例的测试点混进来了。"""
    steps = [_step(0, "私密服务不应出现在跨租户服务目录")]
    r = _run(FakeSession(case=_case(title="服务发布后网关可调通",
                                    expected_result="发布后网关返回 200"),
                         scenario=_scenario(), steps=steps))
    assert any(x["kind"] == "possible_out_of_scope" for x in r["notes"]), r["notes"]


def test_前置制备清理不算越界():
    """这三类天然不在标题里，报它们就是噪音。"""
    steps = [_step(0, "租户管理员登录", group="前置"),
             _step(1, "制备：建服务", group="制备"),
             _step(2, "清理：删除本次服务", group="清理")]
    r = _run(FakeSession(case=_case(), scenario=_scenario(), steps=steps))
    assert not any(x["kind"] == "possible_out_of_scope" for x in r["notes"]), r["notes"]


def test_请求体驼峰键给提示():
    """被响应层污染过的痕迹（历史 bug）。也可能那个接口本来就用驼峰，所以只提示。"""
    steps = [_step(0, "建服务", body={"upstreamId": "x", "config": {"forwardPath": "/"}})]
    r = _run(FakeSession(case=_case(), scenario=_scenario(), steps=steps))
    n = [x for x in r["notes"] if x["kind"] == "body_camel_keys"]
    assert n and "upstreamId" in str(n[0]["detail"])


def test_蛇形请求体不报():
    steps = [_step(0, "建服务", body={"upstream_id": "x", "config": {"forward_path": "/"}})]
    r = _run(FakeSession(case=_case(), scenario=_scenario(), steps=steps))
    assert not any(x["kind"] == "body_camel_keys" for x in r["notes"])


def test_spec_api却在预期里写页面落点给提示():
    r = _run(FakeSession(case=_case(target_level="spec_api",
                                    expected_result="详情页路由配置同步回显新路径"),
                         scenario=_scenario(), steps=[_step(0, "x")]))
    assert any(x["kind"] == "ui_wording_in_spec_api" for x in r["notes"]), r["notes"]


# ── 结论措辞：要说清"轮到谁了" ────────────────────────────────────

def test_待发布要点明还需人工发布():
    """CC 改不了 executable，这是红线。结论里必须说清下一步是谁的。"""
    r = _run(FakeSession(case=_case(api_status="pending_review"),
                         scenario=_scenario(), steps=[_step(0, "x")]))
    assert r["waitingHuman"] == ["api"]
    assert "发布到回归" in r["verdict"], r["verdict"]


def test_状态还没到位时说清是记账问题不是内容问题():
    r = _run(FakeSession(case=_case(api_status="debugging"),
                         scenario=_scenario(), steps=[_step(0, "x")]))
    s = [x for x in r["risks"] if x["kind"] == "status_behind"]
    assert s and "跑一遍" in s[0]["detail"], r["risks"]


def test_不改任何状态():
    """这是只读工具。它一旦能改状态，就变成了另一个"自己给自己盖章"的入口。"""
    import inspect
    import re
    src = inspect.getsource(deliverable)
    for forbidden in ("session.commit", "session.add", "session.flush",
                      "apply_case_status", "record_run"):
        assert forbidden not in src, f"交付门禁不该有 {forbidden}"
    # 不许给用例的任何状态字段赋值。`!= "executable"` 这类**读取**是允许的 ——
    # 判据本来就要读状态，所以只查赋值形状（第一版我把读取也拦了，自己误报一次）。
    assert not re.search(r"\.(?:api|ui|manual|lifecycle)_status\s*=[^=]", src), \
        "交付门禁在给状态字段赋值 —— 它一旦能改状态，就变成另一个「自己给自己盖章」的入口"


def test_用例不存在时不炸():
    class Empty(FakeSession):
        async def get(self, model, oid):
            return None
    out = asyncio.run(deliverable.check_deliverable(Empty(), str(uuid.uuid4())))
    assert "error" in out


# ── 越界判定的两个坑（都是我自己踩过的）────────────────────────

def test_分词不能整串当一个词():
    """第一版用 `[一-龥]{2,}`，它贪婪匹配，把「发布应产生版本记录」整串当成一个词，
    于是在标题里当然找不到 —— 一条正常步骤被报成越界，一条用例误报 12 次。"""
    from app.mcp.tools.deliverable import _bigrams
    assert _bigrams("发布应产生版本记录") >= {"发布", "版本", "记录"}
    assert "发布应产生版本记录" not in _bigrams("发布应产生版本记录")


def test_用例范围必须算上手工步骤():
    """标题和预期各一句话，覆盖不了一个完整流程 —— 只拿那两句当范围会大面积误报。"""
    from app.mcp.tools.deliverable import _case_scope_text
    c = _case(title="发布", expected_result="可调通",
              steps=[{"seq": 1, "action": "查看版本历史", "expected": "新增一条版本记录"}])
    scope = _case_scope_text(c)
    assert "版本历史" in scope and "版本记录" in scope


def test_范围里提过的步骤不报越界():
    """真实回归：这几条原来全被误报。"""
    steps = [_step(0, "发布应产生版本记录"), _step(1, "确认服务已转 active 且为启用态")]
    case = _case(title="API 类型服务发布上线后，网关由 404 转为 200",
                 expected_result="发布后服务转为运行中，产生版本记录与操作日志",
                 steps=[{"seq": 1, "action": "查看版本历史", "expected": "新增一条版本记录并带当前标记"},
                        {"seq": 2, "action": "观察状态", "expected": "服务转为运行中、处于启用态"}])
    r = _run(FakeSession(case=case, scenario=_scenario(), steps=steps))
    assert not [n for n in r["notes"] if n["kind"] == "possible_out_of_scope"], r["notes"]


def test_真越界的还是要报():
    """用例通篇没提「暴露级别」「跨租户目录」，这两条是别的测试点混进来的。"""
    steps = [_step(0, "新建服务默认暴露级别为私密"),
             _step(1, "私密服务不应出现在跨租户服务目录")]
    case = _case(title="API 类型服务发布上线后，网关由 404 转为 200",
                 expected_result="发布后网关可调通并回传上游响应",
                 steps=[{"seq": 1, "action": "点击发布上线", "expected": "状态转为运行中"}])
    r = _run(FakeSession(case=case, scenario=_scenario(), steps=steps))
    assert len([n for n in r["notes"] if n["kind"] == "possible_out_of_scope"]) == 2, r["notes"]


def test_步骤名太短不判():
    """「发布上线」只有 3 个二元组，样本太小，判什么都是噪音。"""
    from app.mcp.tools.deliverable import _bigram_overlap
    assert _bigram_overlap("发布", "任意范围") is None


def test_MCP协议信封不报驼峰():
    """AT-0012 的 clientInfo/protocolVersion 是 JSON-RPC 规范规定的，它 18/18 全绿。"""
    steps = [_step(0, "发网关 MCP initialize", body={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"clientInfo": {"name": "t"}, "protocolVersion": "2024-11-05"}})]
    r = _run(FakeSession(case=_case(), scenario=_scenario(), steps=steps))
    assert not [n for n in r["notes"] if n["kind"] == "body_camel_keys"], r["notes"]
