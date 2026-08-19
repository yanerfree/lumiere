"""状态码断言的期望值键名：`expected` 和 `value` 必须**两种都认**。

外部 CC 新建的 23 步编排场景全红，逐字重跑两次结果相同；而 8/16 建的老场景 15/15 全绿。
差别只有一个键名 —— 老的写 `{"type":"status","value":200}`，新的写 `"expected":200`。
现象长成这样，几乎不可能自己查出来：

    期望 200，实际 200        → 判失败
    期望 in [403,404]，实际 404 → 判失败

因为**判定和显示各挑了一个键名**：status 分支只读 `value`（拿到 None），
报错那一行读 `expected`（打印出 200）。两个键名一个概念，就是这个 bug 的形状。

连锁伤害不止假红：清理步骤实际回 204 却被判 fail，`tb_check_env_hygiene` 于是推断
「没跑到清理」，报出 3 条"残留"——那 3 个 id 去查全是 404，早删干净了。
一个键名错，最后是让人去被测系统里找不存在的垃圾。
"""
from __future__ import annotations

from app.services.api_test_runner import (
    _check_assertions, _expects_status, describe_assertion, expected_of, failure_detail, field_of,
)


def _one(a: dict, body=None, code: int = 200) -> dict:
    return _check_assertions([a], code, body)[0]


# ── CC 实测那两条 ────────────────────────────────────────────────

def test_status用expected也要认():
    """`{"type":"status","expected":200}` —— 15 步都是这个形状，全被判失败。"""
    assert _one({"type": "status", "expected": 200, "operator": "=="}, code=200)["passed"]
    assert not _one({"type": "status", "expected": 200, "operator": "=="}, code=500)["passed"]


def test_status用value照旧():
    """老场景全靠这个形状跑绿，别修一个坏一个。"""
    assert _one({"type": "status", "value": 201, "operator": "=="}, code=201)["passed"]


def test_in加expected也要认():
    """清理步骤那 8 条 `in [200,204]`。"""
    a = {"type": "status", "expected": [200, 204], "operator": "in"}
    assert _one(a, code=204)["passed"]
    assert _one(a, code=200)["passed"]
    assert not _one(a, code=500)["passed"]
    assert _one({"type": "status", "expected": [403, 404], "operator": "in"}, code=404)["passed"]


def test_状态码是字符串也按数字比():
    """`${var}` 插值出来永远是字符串，不转必然假红 —— 数组里的元素同样。"""
    assert _one({"type": "status", "expected": "200", "operator": "=="}, code=200)["passed"]
    assert _one({"type": "status", "expected": ["200", "204"], "operator": "in"}, code=204)["passed"]


def test_取期望值只有一个口径():
    """两个键都给时 `expected` 优先 —— 那是历史形态 `{value: 字段路径, expected: 期望值}`。"""
    assert expected_of({"value": 200}) == 200
    assert expected_of({"expected": 200}) == 200
    assert expected_of({"value": "data.x", "expected": 3}) == 3
    assert expected_of({}) is None
    assert field_of({"value": "data.x", "expected": 3}, "==") == "data.x"
    assert field_of({"field": "data.y", "expected": 3}, "==") == "data.y"
    assert field_of({"value": "data.z"}, "not_empty") == "data.z"


def test_显示和判定不许再分叉():
    """这个 bug 之所以查不出来，就是因为报错打印的期望值和判定用的不是同一个。"""
    a = {"type": "status", "expected": 200, "operator": "=="}
    r = _one(a, code=500)
    assert describe_assertion(r) == "状态码 == 200"
    assert failure_detail([r], None)["failedAssertions"][0]["expected"] == 200
    assert r["actual"] == 500


def test_expects_status认in和字符串():
    """401 重试判断用它。`in [401,403]` 原来会抛 TypeError 落到 False ——
    "这一步本来就该 401"被当成 token 过期，白重试一轮。"""
    assert _expects_status([{"type": "status", "expected": [401, 403], "operator": "in"}], 401)
    assert _expects_status([{"type": "status", "expected": 401, "operator": "=="}], 401)
    assert _expects_status([{"type": "status", "value": "401", "operator": "=="}], 401)
    assert not _expects_status([{"type": "status", "expected": 200, "operator": "=="}], 401)


# ── 观测缺口：只列失败的断言，等于看不出其余的求值了没有 ──────────────

def test_失败步骤要把每条断言都列出来():
    """CC 的原话：failedAssertions 永远只列状态码那一条，所以新的 `data[*key=val]`
    过滤语法到底有没有被求值、取到了什么，返回里一个字都没有 ——
    "改对了没有"这件事就无从判断。"""
    asserts = [
        {"type": "status", "expected": 200, "operator": "=="},
        {"type": "body_field", "field": "data[*name=echo]", "expected": 1, "operator": "length"},
        {"type": "body_field", "field": "data.status", "expected": "pending", "operator": "=="},
    ]
    res = _check_assertions(asserts, 500, {"data": [{"name": "echo"}], "status": "pending"})
    d = failure_detail(res, None)
    checked = {c["desc"]: c for c in d["checked"]}
    assert len(checked) == 3, "通过的也要列，否则看不出它们求值了没有"
    assert checked["响应字段 data[*name=echo] length 1"]["passed"] is True
    assert checked["响应字段 data[*name=echo] length 1"]["actual"] == 1, "实际值得带上"
    assert checked["状态码 == 200"]["passed"] is False


# ── 入库门禁：真的什么都没给，拦在门口 ────────────────────────────

def test_期望值压根没给要硬拦():
    """键名那半在执行器里收口了；这半是"真的一个都没给"——必然红，且报错看不懂。"""
    from app.mcp.tools.sync import _unevaluatable_assertions

    dead = _unevaluatable_assertions(1, {"name": "x", "assertions": [
        {"type": "status", "operator": "=="},
        {"type": "body_field", "operator": "==", "expected": 1},   # 没给字段路径
        {"type": "status", "expected": 200, "operator": "=="},     # 这条是好的
        {"type": "body_field", "field": "data.x", "operator": "not_empty"},  # 不需要期望值
    ]})
    assert len(dead) == 2, dead
    assert "期望值" in dead[0]["why"]
    assert "字段路径" in dead[1]["why"]


def test_入库时键名归一到前端口径():
    """前端编辑器存的是 status→value、body_field→field+expected。库里长出两种形状的话，
    页面上打开再保存就会规回一种，diff 里多出一堆无意义变更。"""
    from app.mcp.tools.sync import _canon_assertion

    assert _canon_assertion({"type": "status", "expected": 200, "operator": "=="}) == \
        {"type": "status", "value": 200, "operator": "=="}
    assert _canon_assertion({"type": "status", "expected": [200, 204], "operator": "in"}) == \
        {"type": "status", "value": [200, 204], "operator": "in"}
    assert _canon_assertion({"type": "body_field", "field": "data.x", "expected": 3,
                             "operator": "=="}) == \
        {"type": "body_field", "field": "data.x", "expected": 3, "operator": "=="}
    # 历史形态：value 是字段路径
    assert _canon_assertion({"type": "body_field", "value": "data.x", "operator": "not_empty"}) == \
        {"type": "body_field", "field": "data.x", "operator": "not_empty"}


def test_归一化真的接在入库路径上():
    import inspect

    from app.mcp.tools import sync
    src = inspect.getsource(sync.sync_orchestrated_scenario)
    assert "_canon_assertion(a)" in src, "归一化没接上，库里照旧长两种形状"
    assert "_unevaluatable_assertions" in src, "门禁没接上"


# ── 清理步骤：判 fail ≠ 请求没成功 ──────────────────────────────

def test_清理请求成功了就别报残留():
    """CC 报的连锁伤害：清理步骤实际 204，被状态码断言的键名 bug 判成 fail，
    工具于是推断"没跑到清理"，报出 3 条残留 —— 那 3 个 id 全是 404。
    响应码本来就躺在 last_response 里，带上它这件事就不用猜。"""
    import inspect

    from app.services import env_hygiene
    src = inspect.getsource(env_hygiene.check_env_hygiene)
    assert "_status_code" in src and "requestsSucceeded" in src
    assert "cleanupStatusCode" in src, "要把真实响应码回出去，否则人只能猜"

    class _St:
        def __init__(self, code):
            self.last_response = {"statusCode": code}
    assert env_hygiene._status_code(_St(204)) == 204
    assert env_hygiene._status_code(_St(None)) is None
