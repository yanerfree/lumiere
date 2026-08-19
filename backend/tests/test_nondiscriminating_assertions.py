"""恒真断言 —— 「21 条全绿，但有多少条真能抓到问题，现在没人知道」。

外部 CC 反馈的第一条，也是它自己说"如果只能改一条就选这条"的那条。
平台判不了断言强弱（见 assertion_profile 里 Amelia 的技术否决），但**判得出**
一种确定无疑的恒真形状：**同一个请求读了两次、断言一模一样，而中间没有任何断言
证明它变过**。那几步动作坏掉，后一条照样绿。

范围说清楚：CC 举的 TC-DYGL-00002「驳回后打网关仍 401」，链子里只有那**一步**断 401
（前面没人建过基准），静态比对无从下手 —— 那一半只能靠纪律（规范里要求基准在动作
之前建）或者真跑一遍变异验证。这里做的是能证明的那一半。

判据落在**步骤名**上：声明了保持型就不提示。落款是自由文本，平台无从判断。
"""
from __future__ import annotations

from app.mcp.tools.sync import _nondiscriminating


def _s(name, url, assertions, method="GET", headers=None, body=None):
    return {"name": name, "method": method, "url": url, "assertions": assertions,
            "headers": headers, "body": body}


_401 = [{"type": "status", "operator": "==", "value": 401}]


def test_动作前后同一断言要提示():
    """真实那条的形状：申请前打网关 401 → 驳回后打网关仍 401。"""
    w = _nondiscriminating([
        _s("申请前打网关（应 401）", "${gatewayBase}/svc/echo", _401),
        _s("提交申请", "${BASE_URL}/api/v1/subscriptions", [
            {"type": "status", "operator": "==", "value": 200}], method="POST"),
        _s("驳回后打网关（应 401）", "${gatewayBase}/svc/echo", _401),
    ])
    assert len(w) == 1, w
    assert w[0]["step"] == 3
    assert "第 1 步" in w[0]["value"]
    assert "保持型" in w[0]["value"], "要告诉它怎么办，不然只是骂一句"


def test_步骤名声明了保持型就不啰嗦():
    """保持型断言是合法写法（「弃用后存量调用不中断」正该这么写）。
    平台要的不是它消失，是它别装成"验过了"。"""
    for word in ("仍应", "保持", "不变", "依旧", "始终", "不中断"):
        w = _nondiscriminating([
            _s("申请前打网关 401", "${gatewayBase}/x", _401),
            _s(f"驳回后{word} 401", "${gatewayBase}/x", _401),
        ])
        assert w == [], f"「{word}」应当算已声明：{w}"


def test_不同请求上的同一断言不算():
    """两个不同接口都断 200 是常态，报它就是滥报。"""
    assert _nondiscriminating([
        _s("建服务", "${BASE_URL}/a", [{"type": "status", "value": 200}], method="POST"),
        _s("查服务", "${BASE_URL}/b", [{"type": "status", "value": 200}]),
    ]) == []


def test_同一请求但断言不同不算():
    """同一个 GET 读两次、断的字段不一样 —— 那正是"动作生效了"的验法。"""
    assert _nondiscriminating([
        _s("改之前状态是 draft", "${BASE_URL}/s/${id}",
           [{"type": "body_field", "field": "data.status", "expected": "draft"}]),
        _s("改之后状态是 active", "${BASE_URL}/s/${id}",
           [{"type": "body_field", "field": "data.status", "expected": "active"}]),
    ]) == []


def test_中间证明过状态离开又回来的不算():
    """`基准 200 → 禁用后 404 → 启用后 200`：最后那个 200 是有效断言 ——
    启用没生效它就红在 404 上。这是真实那批里最多的形状（23 条场景里 20 多处），
    不排掉就等于滥报，人看两条假的就再也不看这个提示了。
    """
    gw = "${gatewayBase}/echo"
    assert _nondiscriminating([
        _s("基准：应可调通", gw, [{"type": "status", "value": 200}]),
        _s("禁用后应 404", gw, [{"type": "status", "value": 404}]),
        _s("启用后应恢复 200", gw, [{"type": "status", "value": 200}]),
    ]) == []


def test_列表里出现过又消失也算证明过():
    """contains / not_contains 是同一件事的两种结果，得算进"值变过"，
    否则「申请前没有 → 申请后有 → 驳回后又没有」会被误报。"""
    url = "${BASE_URL}/api/v1/subscriptions/provider"
    ok = [{"type": "status", "value": 200}]
    has = ok + [{"type": "body_contains", "value": "${appName}", "operator": "contains"}]
    hasnt = ok + [{"type": "body_contains", "value": "${appName}", "operator": "not_contains"}]
    assert _nondiscriminating([_s("申请前不含", url, hasnt),
                              _s("申请后含", url, has),
                              _s("驳回后不再含", url, hasnt)]) == []


def test_多角色读同一个列表不算():
    """同一个 URL 是不同的人在读（Authorization 不同）—— 那不是同一个请求。
    真实那条 15 步的待办场景里，管理员 A / B 各读一次 /todos，断言当然一样。"""
    url = "${BASE_URL}/api/v1/todos?status=pending"
    a = [{"type": "status", "value": 200},
         {"type": "body_contains", "value": "${svcName}", "operator": "contains"}]
    assert _nondiscriminating([
        _s("A 的待办里应有", url, a, headers={"Authorization": "Bearer ${tokenA}"}),
        _s("B 的待办里也应有", url, a, headers={"Authorization": "Bearer ${tokenB}"}),
    ]) == []


def test_写操作不比():
    """一级审批、二级审批都 POST /approve 都断 200 —— 各自验的是自己那次调用。"""
    url = "${BASE_URL}/api/v1/subs/${id}/approve"
    ok = [{"type": "status", "value": [200, 204], "operator": "in"}]
    assert _nondiscriminating([_s("一级通过", url, ok, method="POST"),
                              _s("二级通过", url, ok, method="POST")]) == []


def test_登录的body不同不算同一个请求():
    login = "${BASE_URL}${LOGIN_URL}"
    ok = [{"type": "status", "value": 200},
          {"type": "body_field", "field": "data.access_token", "operator": "not_empty"}]
    assert _nondiscriminating([
        _s("A 登录", login, ok, method="POST", body={"username": "${U1}"}),
        _s("B 登录", login, ok, method="POST", body={"username": "${U2}"}),
    ]) == []


def test_三次重复报两条():
    w = _nondiscriminating([
        _s("第一次", "${BASE_URL}/a", _401),
        _s("第二次", "${BASE_URL}/a", _401),
        _s("第三次", "${BASE_URL}/a", _401),
    ])
    assert len(w) == 2 and [x["step"] for x in w] == [2, 3]


def test_断言不是对象不炸():
    assert _nondiscriminating([_s("x", "${BASE_URL}/a", ["乱写的"])]) == []


def test_检查真的接在回推入口上():
    """只测函数的话，把它从 sync_orchestrated_scenario 里摘掉也不会红 ——
    「忘了接线」是这类检查最常见的失效方式。"""
    import inspect

    from app.mcp.tools import sync
    src = inspect.getsource(sync.sync_orchestrated_scenario)
    assert "_nondiscriminating" in src, "恒真断言检查没接进回推入口"


def test_规范里前置了这条纪律():
    """回推后才说太晚 —— 那时脚本已经写完跑完了。判据要写在 CC 动手前看的那份规范里。"""
    from app.mcp.tools.sync import _SPEC_API_SCENARIO as spec
    assert "先让它红一次" in spec, "没写「新断言先让它红一次」"
    assert "动作前后断同一件事" in spec
    assert "别缩小断言作用域" in spec, "页面级→行级的退化判据没前置"


def test_真实那批只报出该报的():
    """在真实 23 条场景 / 460 步上标定：按 method+url 硬比会报 42 条，几乎全是假的。
    三条 narrowing（只看读操作 / headers+body 进签名 / 中间证明过状态变化的不算）
    之后剩 2 条，都是同一个形状 ——
    `GET /push-status 断 success` 在制备阶段断过一次，改动之后又断一次，
    而中间没有任何断言证明它离开过 success：**推送压根没重跑，这条也是绿的。**
    这条测试钉的是"别退回滥报"，不是钉具体数字。
    """
    push = "${BASE_URL}/api/v1/services/${sid}/push-status"
    ok = [{"type": "status", "value": 200},
          {"type": "body_field", "field": "data.status", "expected": "success"}]
    w = _nondiscriminating([
        _s("制备：等推送收敛", push, ok),
        _s("改配置", "${BASE_URL}/api/v1/services/${sid}", [{"type": "status", "value": 200}],
           method="PUT"),
        _s("改动应重新推送并收敛", push, ok),
    ])
    assert len(w) == 1 and w[0]["step"] == 3, w
    assert "证明它变过" in w[0]["value"]
