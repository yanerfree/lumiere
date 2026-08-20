"""回推入库门禁：把 CC 反复犯的两类定型错误拦在写入时，别等跑起来才发现。

背景是一个模式问题：**这两类错误每一轮都会重犯，而每一轮都靠人事后审出来再改。**
- 断言期望值写成字符串："2"（实际数字 2，TC-FWGL-00006）、"true"（实际布尔 true，
  TC-FWGL-00001）。跑起来必挂，报错还长得像平台在说胡话。
- 异步下发之后立刻断言，不开 retry_timeout_ms：6 条场景 23 个数据面/收敛断言只有
  4 个开了重试，其余 19 个裸奔 —— 3 个当场挂、**4 个侥幸跑赢时间窗**。
  侥幸过的最危险：看着绿，换台机器就红。

分工：布尔硬拦（平台故意不兜布尔，兜了会假绿），缺 retry 软警告（有些接口确实同步，
判不准）。软警告必须带上建议值，否则 CC 会退回插「等待」占位步骤 —— 那正是
retry_timeout_ms 要消灭的东西。
"""
from __future__ import annotations

from app.mcp.tools.sync import _needs_retry, _typo_assertions


def _step(**kw):
    base = {"name": "某一步", "method": "GET", "url": "${BASE_URL}/x", "assertions": []}
    base.update(kw)
    return base


# ── 一、断言期望值的类型 ────────────────────────────────────────

def test_布尔写成字符串要拦():
    bad = _typo_assertions(3, _step(assertions=[
        {"type": "body_field", "field": "data.enabled", "operator": "==", "expected": "true"}]))
    assert len(bad) == 1, bad
    assert bad[0]["field"] == "data.enabled"
    assert bad[0]["wrote"] == '"true"' and bad[0]["shouldBe"] == "true"


def test_false也要拦():
    assert len(_typo_assertions(1, _step(assertions=[
        {"type": "body_field", "field": "data.enabled", "expected": "false"}]))) == 1


def test_首字母大写的也要拦():
    """Python 的 repr 是 True/False，照抄进 JSON 就成了这个样子。"""
    assert len(_typo_assertions(1, _step(assertions=[
        {"type": "body_field", "field": "a", "expected": "True"}]))) == 1


def test_真布尔不拦():
    assert _typo_assertions(1, _step(assertions=[
        {"type": "body_field", "field": "data.enabled", "expected": True},
        {"type": "body_field", "field": "data.x", "expected": False}])) == []


def test_变量引用不拦():
    """${var} 插值出来本来就是字符串，拦它是错的。"""
    assert _typo_assertions(1, _step(assertions=[
        {"type": "body_field", "field": "a", "expected": "${flag}"}])) == []


def test_普通字符串不拦():
    assert _typo_assertions(1, _step(assertions=[
        {"type": "body_field", "field": "data.lifecycle_status", "expected": "active"}])) == []


def test_数字字符串不拦():
    """"2" vs 2 已经由 _scalar_eq 兜住（插值必然是字符串），再拦就是误报。"""
    assert _typo_assertions(1, _step(assertions=[
        {"type": "body_field", "field": "data.version", "expected": "2"}])) == []


def test_value字段写法也认():
    """status 类断言用的是 value 不是 expected。"""
    assert len(_typo_assertions(1, _step(assertions=[
        {"type": "body_field", "field": "a", "operator": "==", "value": "true"}]))) == 1


# ── 二、异步下发缺重试 ──────────────────────────────────────────

def test_打数据面没开重试要警告():
    w = _needs_retry(8, _step(url="${gatewayBase}${isoPrefix}/v1/svc/echo",
                              assertions=[{"type": "status", "value": 200}]))
    assert w is not None
    assert w["field"] == "retry_timeout_ms"
    assert "10000" in w["value"], "必须给出建议值，否则会退回插等待步骤"
    assert "等待" in w["value"], "要明确说别插占位步骤"


def test_断推送状态没开重试要警告():
    w = _needs_retry(7, _step(url="${BASE_URL}/api/v1/services/${sid}/push-status",
                              assertions=[{"type": "body_field", "field": "data.status",
                                           "expected": "success"}]))
    assert w is not None


def test_断言里提到收敛字段也算():
    """URL 看不出来，但断言里断的是 data.status/synced_count 这类。"""
    assert _needs_retry(1, _step(url="${BASE_URL}/api/v1/services/${sid}",
                                 assertions=[{"type": "body_field", "field": "data.synced_count",
                                              "expected": 2}])) is not None


def test_已经开了重试就不啰嗦():
    assert _needs_retry(1, _step(url="${gatewayBase}/x", retry_timeout_ms=10000)) is None


def test_普通控制面读取不警告():
    """别滥报 —— 读用例详情、列表这类同步接口不该被提示。"""
    assert _needs_retry(1, _step(url="${BASE_URL}/api/v1/services/${sid}",
                                 assertions=[{"type": "body_field", "field": "data.name",
                                              "expected": "${svcName}"}])) is None
    assert _needs_retry(1, _step(url="${BASE_URL}/api/v1/services?keyword=x",
                                 assertions=[{"type": "status", "value": 200}])) is None


def test_登录清理这类不警告():
    assert _needs_retry(1, _step(method="POST", url="${BASE_URL}${LOGIN_URL}",
                                 assertions=[{"type": "status", "value": 200}])) is None
    assert _needs_retry(1, _step(method="DELETE", url="${BASE_URL}/api/v1/services/${sid}",
                                 assertions=[{"type": "status", "value": 204}])) is None


# ── 三、拿这一批真实数据回放：该拦的拦、该警告的警告 ──────────────

def test_真实那批的布尔断言会被拦下():
    """TC-FWGL-00001 AT-0011 step9 的原始写法。"""
    real = _step(name="确认服务已转 active 且为启用态",
                 url="${BASE_URL}/api/v1/services/${serviceId}",
                 assertions=[
                     {"type": "status", "value": 200, "operator": "=="},
                     {"type": "body_field", "field": "data.lifecycle_status",
                      "expected": "active", "operator": "=="},
                     {"type": "body_field", "field": "data.enabled",
                      "expected": "true", "operator": "=="}])
    bad = _typo_assertions(9, real)
    assert len(bad) == 1 and bad[0]["field"] == "data.enabled", bad


def test_真实那批侥幸过的收敛断言会被警告():
    """AT-0011 step16 —— 它当时是绿的，纯靠跑赢时间窗。"""
    real = _step(name="推送应已收敛到全部网关节点",
                 url="${BASE_URL}/api/v1/services/${serviceId}/push-status",
                 assertions=[{"type": "status", "value": 200},
                             {"type": "body_field", "field": "data.status",
                              "expected": "success"}])
    assert _needs_retry(16, real) is not None, "侥幸过的也必须提示，它随时会翻"


# ── 四、钉住调用点：门禁必须真的接在写入路径上 ──────────────────

def test_门禁真的接在回推入口上():
    """只测两个函数的话，把它们从 sync_orchestrated_scenario 里摘掉也不会红 ——
    而"忘了接线"正是这类检查最常见的失效方式（bug B 就是这么漏的：
    apply_case_status 写好了，唯一的调用点被一个 if 挡住）。"""
    import inspect

    from app.mcp.tools import sync
    src = inspect.getsource(sync.sync_orchestrated_scenario)
    assert "_typo_assertions" in src, "断言类型检查没接进回推入口"
    assert "_needs_retry" in src, "缺重试检查没接进回推入口"
    assert "bool_as_string" in src, "拦下来之后没把明细回给调用方，CC 不知道改哪一步"
