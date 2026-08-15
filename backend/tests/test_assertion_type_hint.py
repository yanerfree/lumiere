"""值一样类型不一样时，报错必须把类型说出来。

实测卡点：AT-0011 有一步断言 `data.enabled == "true"`（字符串），响应里是布尔
`true`。页面和 MCP 返回都写成

    期望 data.enabled == true，实际 True

`true` 和 `True` 只差一个大小写，看到的人第一反应是"平台在说胡话"，然后跑去
怀疑判定逻辑，而真正要改的是断言里那对引号。

判定本身**不放松**：`_scalar_eq` 故意不让 "true" 等于 True（放过去的话
「期望 true、实际 1」也会算相等，那是假绿，见 8690b1d）。这里只改措辞。
数字那一类已经由 _scalar_eq 兜住，所以不该再出现在这个提示里。
"""
from __future__ import annotations

from app.services.api_test_runner import _type_hint, failure_detail


def _a(expected, actual, passed=False):
    return {"type": "body_field", "field": "data.enabled", "operator": "==",
            "expected": expected, "actual": actual, "passed": passed}


def test_字符串true对布尔true要点出类型():
    h = _type_hint(_a("true", True))
    assert "类型不同" in h, h
    assert "期望是字符串" in h and "实际是布尔" in h, h


def test_还要告诉人该怎么改():
    """只说"类型不同"仍要人自己猜写法。直接给出该写什么。"""
    h = _type_hint(_a("true", True))
    assert "断言里应写成 true（不加引号）" in h, h
    assert "断言里应写成 false（不加引号）" in _type_hint(_a("false", False))


def test_类型相同不啰嗦():
    assert _type_hint(_a("active", "inactive")) == ""
    assert _type_hint(_a(True, False)) == ""


def test_值本身就不同时不提类型():
    """期望 true 实际 false —— 类型不是重点，别用括号把真正的差异挤掉。"""
    assert _type_hint(_a("true", False)) == ""


def test_数字类那一类不该再出现提示():
    """"2" vs 2 已经被 _scalar_eq 判为相等、根本不会进失败分支；
    真进来了也不该说类型 —— 那会把注意力引到不需要改的地方。"""
    h = _type_hint(_a("2", 2))
    assert "类型不同" in h, "值一样类型不同仍应说明，但……"
    assert "不加引号" not in h, "只对布尔给改法建议，数字不给"


def test_缺值时不炸():
    assert _type_hint({"expected": None, "actual": True}) == ""
    assert _type_hint({"expected": "true", "actual": None}) == ""
    assert _type_hint({}) == ""


def test_提示真的出现在失败原文里():
    """钉住调用点 —— 只测 _type_hint 的话，把它从 failure_detail 摘掉也不会红。"""
    out = failure_detail([_a("true", True)], None)
    assert "类型不同" in out["why"], out["why"]
    assert "期望是字符串" in out["why"]


def test_正常失败不带类型噪声():
    out = failure_detail([_a("active", "draft")], None)
    assert "类型不同" not in out["why"], out["why"]
    assert "draft" in out["why"]
