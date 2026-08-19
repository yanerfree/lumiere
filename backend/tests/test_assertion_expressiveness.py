"""断言表达力 —— 缺的三样把人逼向规范自己禁止的写法。

外部 CC 写完 21 条订阅用例后反馈的第二条：断言表达不了「数组为空」「按字段值过滤
后再断言」「最后一条」，于是只能拿 `body_contains not_contains "app_name"` 在整个
响应体里搜字符串绕，用 `data[0]` 定位业务对象 —— 而 `data[N]` 正是规范自己点名的
反模式（「下标是另一种写死」）。**规范禁止的写法，如果是唯一能写出来的写法，
那是平台的问题。**

顺带一个纯假绿：`not_empty` 在 `[]` 上通过 —— 「查出来应该有数据」这条断言
恰好在没数据时绿。
"""
from __future__ import annotations

from app.services.api_test_runner import _check_assertions, _extract_value


def _one(a: dict, body, code: int = 200) -> dict:
    return _check_assertions([a], code, body)[0]


# ── 取值：负下标 / 按字段值过滤 ──────────────────────────────────

_BODY = {"data": {"items": [
    {"id": "a1", "name": "svc-old", "seq": 1},
    {"id": "b2", "name": "svc-新建", "seq": 2},
    {"id": "c3", "name": "svc-last", "seq": 3},
]}}


def test_负下标取最后一条():
    assert _extract_value(_BODY, "data.items[-1].id") == "c3"
    assert _extract_value(_BODY, "data.items[-2].name") == "svc-新建"


def test_负下标越界返回None():
    assert _extract_value(_BODY, "data.items[-9].id") is None


def test_按字段值过滤():
    """这条是重点：不用知道它排第几，按业务标识找。"""
    assert _extract_value(_BODY, "data.items[name=svc-新建].id") == "b2"


def test_过滤没命中返回None不退回第一条():
    """退回第一条就是假绿 —— 断言会验到别的对象上去。"""
    assert _extract_value(_BODY, "data.items[name=不存在].id") is None


def test_过滤值里带点也认():
    """按 . 切段时方括号里的点不能算分隔符。"""
    body = {"data": [{"host": "a.b.c", "id": "x"}]}
    assert _extract_value(body, "data[host=a.b.c].id") == "x"


def test_过滤按数值比较():
    """seq 是数字，过滤值从路径里来必然是字符串。"""
    assert _extract_value(_BODY, "data.items[seq=2].id") == "b2"


def test_过滤后再接下标():
    body = {"data": [{"k": "x", "v": 1}, {"k": "x", "v": 2}]}
    assert _extract_value(body, "data[k=x].v") == 1


def test_正下标照旧():
    assert _extract_value(_BODY, "data.items[0].id") == "a1"
    assert _extract_value(_BODY, "$.data.items[1].name") == "svc-新建"


# ── not_empty：空数组必须算空 ────────────────────────────────────

def test_空数组不算非空():
    """原来是绿的。「查出来应该有数据」在没数据时通过 = 纯假绿。"""
    r = _one({"type": "body_field", "field": "data.items", "operator": "not_empty"},
             {"data": {"items": []}})
    assert r["passed"] is False


def test_空对象和空串也不算非空():
    assert _one({"type": "body_field", "field": "data", "operator": "not_empty"},
                {"data": {}})["passed"] is False
    assert _one({"type": "body_field", "field": "data", "operator": "not_empty"},
                {"data": "  "})["passed"] is False


def test_数字0算非空():
    """0 是有意义的值，不是"空"。把它判成空会让「总数应为 0」这类断言没法写。"""
    assert _one({"type": "body_field", "field": "data.total", "operator": "not_empty"},
                {"data": {"total": 0}})["passed"] is True


def test_有元素才算非空():
    assert _one({"type": "body_field", "field": "data.items", "operator": "not_empty"},
                _BODY)["passed"] is True


# ── is_empty：这是原来写不出来的那条 ─────────────────────────────

def test_空数组is_empty通过():
    assert _one({"type": "body_field", "field": "data.items", "operator": "is_empty"},
                {"data": {"items": []}})["passed"] is True


def test_有元素is_empty不通过():
    assert _one({"type": "body_field", "field": "data.items", "operator": "is_empty"},
                _BODY["data"])["passed"] is False


def test_字段不存在不算空():
    """字段不存在是"接口改了字段名"，跟"列表为空"是两件事。
    混成一件会把改名假绿掉。"""
    r = _one({"type": "body_field", "field": "data.items", "operator": "is_empty"},
             {"data": {}})
    assert r["passed"] is False


# ── length：过滤之后断"恰好一条" ─────────────────────────────────

def test_length断条数():
    a = {"type": "body_field", "field": "data.items", "operator": "length", "expected": 3}
    r = _one(a, _BODY)
    assert r["passed"] is True and r["actual"] == 3


def test_length对不上():
    assert _one({"type": "body_field", "field": "data.items",
                 "operator": "length", "expected": 1}, _BODY)["passed"] is False


def test_length不能用在对象上():
    """`data[name=x]` 过滤出来是一个对象，len 是它的**键数** ——
    拿它断「恰好一条」会因为"这条刚好有 1 个字段"而通过，是纯假绿。
    断条数只能对列表用（URL 上带查询条件让服务端过滤，再对列表断 length）。
    """
    body = {"data": [{"name": "本次-abc"}, {"name": "别人的"}]}
    r = _one({"type": "body_field", "field": "data[name=本次-abc]",
              "operator": "length", "expected": 1}, body)
    assert r["passed"] is False and "键数" in r["error"]


def test_length用在数字上要说清楚():
    r = _one({"type": "body_field", "field": "data.total", "operator": "length",
              "expected": 3}, {"data": {"total": 3}})
    assert r["passed"] is False and "length" in r["error"]


# ── 大小比较：编辑器下拉里本来就有，执行器以前不认 ────────────────

def test_大于小于能用():
    assert _one({"type": "body_field", "field": "data.total", "operator": ">",
                 "expected": 2}, {"data": {"total": 3}})["passed"] is True
    assert _one({"type": "body_field", "field": "data.total", "operator": "<=",
                 "expected": 3}, {"data": {"total": 3}})["passed"] is True
    assert _one({"type": "body_field", "field": "data.total", "operator": ">=",
                 "expected": 4}, {"data": {"total": 3}})["passed"] is False


def test_比较两边不是数字要说清楚而不是静默失败():
    r = _one({"type": "body_field", "field": "data.name", "operator": ">",
              "expected": 2}, {"data": {"name": "abc"}})
    assert r["passed"] is False and "数字" in r["error"]


def test_操作符仍然严进():
    """不认识的操作符必须当场说出来 —— 静默判失败查不出原因。"""
    r = _one({"type": "body_field", "field": "a", "operator": "eq", "expected": 1}, {"a": 1})
    assert r["passed"] is False and "不认识的操作符" in r["error"]


# ── 前后端一致：下拉里的每一项执行器都得认 ────────────────────────

def test_编辑器下拉的操作符执行器全认():
    """下拉里多一个执行器不认的，人选了就得到「不认识的操作符」，那一步永远失败。
    gt/lt 就这么错了很久：界面上有「大于」，_VALID_OPS 里没有。
    """
    import re
    from pathlib import Path

    from app.services.api_test_runner import _VALID_OPS

    root = Path(__file__).resolve().parents[2] / "frontend/src"
    jsx = (root / "components/ApiStepList.jsx").read_text(encoding="utf-8")
    adapter = (root / "pages/cases/apiStepAdapter.js").read_text(encoding="utf-8")

    line = next(l for l in jsx.splitlines() if l.startswith("const assertOps"))
    ui_ops = re.findall(r"value: '([^']+)'", line)
    out_map = dict(re.findall(r"(\w+): '([^']+)'",
                              next(l for l in adapter.splitlines() if l.startswith("const OP_OUT"))))

    backend = set(_VALID_OPS["body_field"]) | set(_VALID_OPS["status"])
    for op in ui_ops:
        assert op in out_map, f"编辑器有「{op}」但适配器不映射 —— 存一次就被兜成 eq"
        assert out_map[op] in backend, f"编辑器的「{op}」映射成 {out_map[op]}，执行器不认"


def test_适配器双向映射不丢操作符():
    """只做单向的话，往返一次就把断言改成别的意思（in → == 那次就是这么坏的）。"""
    import re
    from pathlib import Path

    adapter = (Path(__file__).resolve().parents[2]
               / "frontend/src/pages/cases/apiStepAdapter.js").read_text(encoding="utf-8")
    lines = {k: next(l for l in adapter.splitlines() if l.startswith(f"const OP_{k}"))
             for k in ("IN", "OUT")}
    into = dict(re.findall(r"'?([\w=!<>]+)'?: '([^']+)'", lines["IN"]))
    back = dict(re.findall(r"(\w+): '([^']+)'", lines["OUT"]))
    for be, fe in into.items():
        assert back.get(fe) == be, f"{be} → {fe} 回不来（回到 {back.get(fe)}）"


# ── 提取路径也要能用变量 + 过滤器 ─────────────────────────────────

def test_提取路径里的变量会解析():
    """`data[description=${svcName}].id` 才是"拿到本次那条的 id"的正解。
    不解析的话只能退回 `data[0].id` —— 那是规范自己禁止的写法，
    而外部 CC 正是因为这个只能"先断 data[0] 含本次服务名、再取它的 id"打补丁。
    """
    import inspect

    from app.services import api_test_runner as r

    src = inspect.getsource(r.run_single_step)
    i_extract = src.index("step.variables_extract")
    seg = src[i_extract:i_extract + 600]
    assert "_resolve_variables(str(path)" in seg, "提取路径没解析 ${var}"


def test_提取用的是同一套路径引擎():
    """断言认过滤器、提取不认，是最难查的那种不一致。"""
    import inspect

    from app.services import api_test_runner as r
    src = inspect.getsource(r.run_single_step)
    assert src.count("_extract_value") >= 1


# ── 编辑器往返不许改坏断言（实测：一次空保存改坏了 9 步）────────────

def _adapter() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[2]
            / "frontend/src/pages/cases/apiStepAdapter.js").read_text(encoding="utf-8")


def test_布尔期望值往返不变成字符串():
    """实测：打开一条步骤、什么都不改点保存，库里 `expected: false` 变成 `"false"` ——
    而平台**故意不做布尔兜底**（兜了「期望 true、实际 1」就算相等，那是假绿），
    于是这条断言从此必挂，报错还长得像平台在说胡话（期望 false｜实际 False）。
    编辑器一次保存回写**所有**步骤，所以一次空保存改坏了那条场景 18 步里的 9 步。
    """
    src = _adapter()
    assert "=== 'false' ? false" in src, "布尔没还原，编辑器一保存就把断言改坏"
    assert "=== 'true' ? true" in src


def test_非空为空不带期望值入库():
    """输入框是隐藏的，但 state 里还留着上次选别的操作符时的值 ——
    带进库报告里会印「响应字段 data.enabled 为空 false」这种胡话。"""
    src = _adapter()
    assert "delete a.expected" in src


def test_没有提取物写null不写空对象():
    """回推进来的是 null，保存一次全变 {} —— 18 步里 12 步"改动了"，实际什么都没变。
    对比改动时这就是纯噪音，真改动混在里面看不见。"""
    src = _adapter()
    assert "Object.keys(variablesExtract).length ? variablesExtract : null" in src


# ── [*k=v] 取全部命中：验唯一性唯一的写法 ─────────────────────────

def test_星号过滤取全部命中():
    body = {"data": [{"name": "x", "id": 1}, {"name": "x", "id": 2}, {"name": "y", "id": 3}]}
    assert _extract_value(body, "data[*name=x]") == [{"name": "x", "id": 1}, {"name": "x", "id": 2}]
    assert _extract_value(body, "data[*name=zz]") == []


def test_唯一性只能用星号过滤加length():
    """`[k=v]` 只取第一条 —— 被测系统真收下了第二条同名，断言照样绿。
    活体跑回推链路时就是这么被 tb_check_assertion_bite 抓出来的（still_green）：
    「同名再建应被拒」那一步跳掉之后，「有且只有一条」还是绿的。
    而 length 对整个列表用也不行：被测系统的 `?search=` 不是严格过滤（实测 9 条全回来）。
    """
    dup = {"data": [{"name": "x", "id": 1}, {"name": "x", "id": 2}]}
    one = {"data": [{"name": "x", "id": 1}, {"name": "other", "id": 9}]}
    a = {"type": "body_field", "field": "data[*name=x]", "operator": "length", "expected": 1}
    assert _one(a, dup)["passed"] is False, "重复了必须红"
    assert _one(a, one)["passed"] is True, "只有一条时要绿（列表里有别人不算)"
    # 老写法在重复时是绿的 —— 这就是它抓不到唯一性的原因
    old = {"type": "body_field", "field": "data[name=x].id", "operator": "==", "expected": 1}
    assert _one(old, dup)["passed"] is True


def test_断空断不存在都要有基准():
    """not_exists / is_empty / length==0 三种写法都有同一个坑：
    **字段名写错也是空的** → 一路恒真。判据是结构性的：同一条路径前面有没有断过非空。
    """
    from app.mcp.tools.sync import _missing_path_baseline

    def step(name, field, op, expected=None):
        a = {"type": "body_field", "field": field, "operator": op}
        if expected is not None:
            a["expected"] = expected
        return {"name": name, "method": "GET", "url": "/x", "assertions": [a]}

    for op, exp in (("not_exists", None), ("is_empty", None), ("length", 0)):
        w = _missing_path_baseline([step("删后查", "data[*name=x]", op, exp)])
        assert len(w) == 1 and "没有任何一步证明过" in w[0]["value"], op
        ok = _missing_path_baseline([
            step("删之前查得到", "data[*name=x]", "length", 1),
            step("删后查", "data[*name=x]", op, exp)])
        assert ok == [], f"{op}：前面建过基准就不该再报"
