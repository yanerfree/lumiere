"""运行时步骤勾选（只作用于「运行全部」）+ 「一步没跑」不许报通过。

来由：接口页面加了 Apifox 那种勾选框，取消勾选的步骤本次不执行。做这个功能时
撞到一条**先于它就存在**的假绿 ——

    passed = all(s.status == "pass" or s.status == "skip" for s in steps)

一步都没跑（步骤全禁用，或者新功能里一个都没勾）时，`all()` 对空集/全 skip 返回
True，页面显示「全通过 0/0 步」，再经 apply_case_status 把用例的接口维度推成
pending_review。没有任何红色、计数是 0、结论却是通过 —— 这种假绿比假红危险得多。

勾选刻意**不复用步骤上的 `enabled`**：那是持久禁用（写库、影响之后每一次回归），
而勾选是"这一次先只跑这几步"。两者语义不同，混用会让人以为自己只是临时试一下，
实际把回归内容改了。
"""
from __future__ import annotations

from app.services.api_test_runner import ScenarioResult, StepResult


def _steps(*statuses):
    return [StepResult(step_id=str(i), step_name=f"s{i}", method="GET", url="/x", status=st)
            for i, st in enumerate(statuses)]


def _result(*statuses):
    return ScenarioResult(scenario_id="s", scenario_title="t", steps=_steps(*statuses))


# ── 一步都没真跑过，不许报通过 ──────────────────────────────────

def test_全是skip不算通过():
    """步骤全禁用 / 运行时一个都没勾 —— 这是"没测"，不是"测过了都对"。"""
    r = _result("skip", "skip", "skip")
    assert r.passed is False
    assert r.pass_count == 0


def test_一步都没有不算通过():
    assert ScenarioResult(scenario_id="s", scenario_title="t", steps=[]).passed is False


# ── 正常语义不能被上面那条改坏 ──────────────────────────────────

def test_全通过仍算通过():
    assert _result("pass", "pass").passed is True


def test_跑了的都过了夹杂skip仍算通过():
    """勾掉几步之后剩下的全过 —— 这是真的通过，不能连坐。"""
    r = _result("pass", "skip", "pass")
    assert r.passed is True
    assert r.pass_count == 2


def test_有失败就不通过():
    assert _result("pass", "fail", "skip").passed is False
    assert _result("fail").passed is False


def test_只有一步通过其余全skip也算通过():
    """边界：至少有一步真跑过且过了。"""
    r = _result("pass", "skip", "skip")
    assert r.passed is True
    assert r.pass_count == 1


# ── 请求体契约：stepIds 是可选的，不传等于全跑 ────────────────────

def test_请求体默认不带stepIds():
    from app.api.api_test import RunBatchRequest
    body = RunBatchRequest(scenario_ids=["a"])
    assert body.step_ids is None, "不传时必须是 None（= 全跑），不能是空列表（= 一步都不跑）"


def test_请求体能收下stepIds():
    from app.api.api_test import RunBatchRequest
    body = RunBatchRequest(scenario_ids=["a"], step_ids=["s1", "s2"])
    assert body.step_ids == ["s1", "s2"]


def test_空列表和不传必须区分开():
    """[] 是"一个都没勾"，None 是"没用这个功能"。混成一样的话，
    没勾任何步骤会被当成全跑，正好跟用户的意图相反。"""
    from app.api.api_test import RunBatchRequest
    assert RunBatchRequest(scenario_ids=["a"], step_ids=[]).step_ids == []
    assert RunBatchRequest(scenario_ids=["a"]).step_ids is None
