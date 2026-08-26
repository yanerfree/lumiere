"""压测断言判定封样。

踩到的：用接口场景那边的写法（`expected`）配一条压测断言，**每一发都判失败**，
而错误分布里显示的是 `{"200": 4}` —— 读起来像"服务器返回 200 也算错"。
真实原因（断言拿空串去比）被状态码顶包掉了，人根本查不出来。

所以钉三件事：两种写法都认、配坏了要说是配坏了、失败原因能直接读懂。
"""
from app.services.load_test_runner import LoadTestRunner


def _r() -> LoadTestRunner:
    return LoadTestRunner.__new__(LoadTestRunner)   # 只用纯判定方法，不起执行器


# ── 两种写法都要认 ──

def test_压测页面的写法value():
    ok, why = _r()._check_assertion({"type": "status", "value": "200"}, 200, "")
    assert ok and why is None


def test_接口场景那边的写法expected():
    """lum_sync_orchestrated_scenario 的断言用 expected。从那边搬过来的场景
    不能因为字段名不同就每一发都失败。"""
    ok, why = _r()._check_assertion({"type": "status", "operator": "eq", "expected": 200}, 200, "")
    assert ok and why is None


def test_状态码不匹配时说清期望和实际():
    ok, why = _r()._check_assertion({"type": "status", "value": "200"}, 500, "")
    assert not ok
    assert "200" in why and "500" in why


# ── 配置坏了要说是配置坏了 ──

def test_断言没给值算配置错而不是被测系统错():
    """以前拿空串去比，结果每一发都失败，人以为是被测系统挂了。"""
    ok, why = _r()._check_assertion({"type": "status"}, 200, "")
    assert not ok
    assert "配置" in why


def test_正则写错了也算配置错():
    ok, why = _r()._check_assertion({"type": "body_regex", "value": "([unclosed"}, 200, "abc")
    assert not ok
    assert "配置" in why


# ── 其余类型 ──

def test_body包含():
    ok, _ = _r()._check_assertion({"type": "body_contains", "value": "hello"}, 200, "say hello world")
    assert ok
    ok, why = _r()._check_assertion({"type": "body_contains", "value": "nope"}, 200, "say hello")
    assert not ok and "没有" in why


def test_不认识的断言类型放过但不报错():
    """放过比误判好 —— 误判会让整轮压测的数字全废。"""
    ok, why = _r()._check_assertion({"type": "未来新增的类型", "value": "x"}, 200, "")
    assert ok and why is None


def test_失败原因能直接当错误分布的分类键():
    """它会被原样塞进 errorBreakdown，得是人话，不能是状态码。"""
    _, why = _r()._check_assertion({"type": "status", "value": "200"}, 404, "")
    assert why and not why.isdigit() and len(why) < 80
