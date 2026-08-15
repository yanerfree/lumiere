"""数据不变量体检的判据。

**为什么要有巡检这一层。** 这一轮最严重的 bug 是驼峰中间件把用户的 HTTP 请求体也改了
（库里 `upstream_id` → `upstreamId`，前端一保存就写回去）。它的形状是：
**没有一次请求失败、没有一行日志、页面上也看不出来** —— 库里存的和显示的都是被改过的
样子，只会让人以为用例本来就写错了。单元测试测不到（它测函数，数据是运行时攒的），
执行也测不到（执行只会挂在下游，指向错误的地方）。只有把库扫一遍才对得出来。

**误报比漏报更致命。** 报告里出现一条假的，人就不再逐条看了，真的那条跟着被忽略，
整个体检等于废掉。实测踩过一次：AT-0012 的 MCP initialize body 里 `clientInfo` /
`protocolVersion` 被标成污染，而那条场景 18/18 全绿 —— MCP 走 JSON-RPC，
协议字段本来就是驼峰。所以下面反例的份量不比正例轻。
"""
from __future__ import annotations

from app.services.data_health import (
    bool_as_string_assertions,
    camel_keys_in,
    check_step,
    dominant_style,
    is_protocol_envelope,
)

# 真实那条被改坏的（AT-0011 建服务），和它本来的样子
POLLUTED = {"name": "${svcName}", "enabled": True, "protocol": "http",
            "upstreamId": "${upstreamId}", "displayName": "${svcName}", "serviceType": "api",
            "config": {"routes": [{"path": "/v1/x", "forwardPath": "/",
                                   "preserveHost": False, "isolationRuleIds": ["${isoId}"]}]}}
CLEAN = {"name": "${svcName}", "enabled": True, "protocol": "http",
         "upstream_id": "${upstreamId}", "display_name": "${svcName}", "service_type": "api",
         "config": {"routes": [{"path": "/v1/x", "forward_path": "/",
                                "preserve_host": False, "isolation_rule_ids": ["${isoId}"]}]}}
MCP_INITIALIZE = {"id": 1, "jsonrpc": "2.0", "method": "initialize",
                  "params": {"clientInfo": {"name": "testbench", "version": "1"},
                             "capabilities": {}, "protocolVersion": "2024-11-05"}}


# ── 驼峰键识别 ──────────────────────────────────────────────────

def test_摊平后能找出嵌套里的驼峰键():
    keys = camel_keys_in(POLLUTED)
    assert "upstreamId" in keys and "displayName" in keys and "serviceType" in keys
    assert "forwardPath" in keys and "isolationRuleIds" in keys, "嵌套在 config.routes 里的也要找到"


def test_干净的蛇形一个都不报():
    assert camel_keys_in(CLEAN) == []


def test_单词和全小写不算驼峰():
    assert camel_keys_in({"name": 1, "enabled": True, "protocol": "http", "config": {}}) == []


# ── 项目主流风格 ────────────────────────────────────────────────

def test_蛇形为主的项目判成snake():
    assert dominant_style([CLEAN, CLEAN, CLEAN]) == "snake"


def test_驼峰为主的项目判成camel():
    """有的被测系统真用驼峰（实测「API自测项目」的 oldPassword/newPassword 就是对的）。"""
    camel = [{"oldPassword": "a", "newPassword": "b"}] * 3
    assert dominant_style(camel) == "camel"


def test_风格混用时不下结论():
    """势均力敌就不下结论 —— 免得把一个混用风格的项目整个标成污染。

    判据是"某一边票数达到另一边 2 倍"。所以 3:2 说不清、6:2 就算蛇形为主
    （我第一版拿 CLEAN 当"混用"样本，它自己有 6 个蛇形键、只配了 2 个驼峰，
    结果 6:2 正确判成 snake，是测试数据不够混用，不是实现错）。
    """
    assert dominant_style([{"a_b": 1, "c_d": 2, "e_f": 3, "gH": 4, "iJ": 5}]) is None
    assert dominant_style([]) is None
    # 6:2 是明确的蛇形为主，不该返回 None
    assert dominant_style([CLEAN, {"oldPassword": "a", "newPassword": "b"}]) == "snake"


# ── 协议信封豁免（这是那次误报的封样）────────────────────────────

def test_MCP信封不算污染():
    assert is_protocol_envelope(MCP_INITIALIZE)
    assert check_step(MCP_INITIALIZE, [], "snake") == [], \
        "MCP/JSON-RPC 的 clientInfo/protocolVersion 是协议规定的驼峰，报它就是误报"


def test_普通请求体不算协议信封():
    assert not is_protocol_envelope(CLEAN)
    assert not is_protocol_envelope(POLLUTED)
    assert not is_protocol_envelope(None)
    assert not is_protocol_envelope("raw")


def test_没有jsonrpc但有完整rpc三件套也认():
    assert is_protocol_envelope({"id": 1, "method": "tools/list", "params": {}})


# ── 组合判定 ────────────────────────────────────────────────────

def test_蛇形项目里的驼峰体报high():
    out = check_step(POLLUTED, [], "snake")
    assert len(out) == 1
    assert out[0]["kind"] == "body_camel_pollution" and out[0]["severity"] == "high"
    assert "422" in out[0]["why"], "要说清后果，否则人不知道这条为什么要紧"


def test_驼峰项目里的驼峰体不报():
    """这是关键的反例 —— 不然「API自测项目」会被整个标红。"""
    assert check_step({"oldPassword": "a", "newPassword": "b"}, [], "camel") == []


def test_风格判不出来时不报驼峰():
    assert [i for i in check_step(POLLUTED, [], None)
            if i["kind"] == "body_camel_pollution"] == []


# ── 断言类型 ────────────────────────────────────────────────────

def test_布尔写成字符串要报():
    bad = bool_as_string_assertions([
        {"type": "body_field", "field": "data.enabled", "expected": "true"}])
    assert bad == [{"field": "data.enabled", "wrote": "true"}]


def test_真布尔和变量引用不报():
    assert bool_as_string_assertions([
        {"type": "body_field", "field": "a", "expected": True},
        {"type": "body_field", "field": "b", "expected": "${flag}"},
        {"type": "body_field", "field": "c", "expected": "active"},
        {"type": "status", "value": 200},
    ]) == []


def test_断言问题和风格无关都要报():
    """断言类型是硬错，不看项目风格。"""
    for style in ("snake", "camel", None):
        out = check_step(CLEAN, [{"type": "body_field", "field": "x", "expected": "false"}], style)
        assert any(i["kind"] == "assertion_bool_as_string" for i in out), style


# ── 真实数据回放 ────────────────────────────────────────────────

def test_回放真实那批的结论():
    """网关项目主流是蛇形；被改坏的那几条要报，MCP 那条不能报。"""
    style = dominant_style([CLEAN, CLEAN, CLEAN, MCP_INITIALIZE])
    assert style == "snake"
    assert check_step(POLLUTED, [], style), "AT-0011 当初那份必须报出来"
    assert check_step(MCP_INITIALIZE, [], style) == [], "AT-0012 的 MCP 信封不能报"
    assert check_step(CLEAN, [], style) == [], "AT-0013 那份是干净的"
