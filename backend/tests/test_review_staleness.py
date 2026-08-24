"""评审结论不该在场景/脚本被覆盖之后原样留着。

**事故现场**：TC-DYGL-00017 有一份 49 分打回的记录，写着「UI 脚本没有 def test_
入口」。脚本早被 `tb_sync_ui_script` 换过一版，取出当前内容一看那个入口就在里面。
原地复评直接 83 分 approved，那两条问题一个字没再出现——旧记录是对着已经不存在
的内容算的，却没有任何标记说这件事，险些被引导去重写一个本来就能跑的脚本。

判据：`content_signature` 摊平场景步骤（url/method/assertions/body）+ UI 脚本
版本号；`ai_review` 落库时存一份，`list_rounds` 读的时候拿当前签名重新算一遍
比对，标 `stale`。**只摊定义、不摊执行结果**——重新跑一遍不该让上一轮"过期"，
只有内容真的被改过才算。
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.models.api_test import ApiTestScenario, ApiTestStep
from app.models.review_round import CaseReviewRound
from app.models.script import Script
from app.services.review import rounds


class FakeSession:
    """execute() 按 select 的目标模型分发预置行，够 content_signature/list_rounds 用。"""

    def __init__(self, by_model=None):
        self._by_model = by_model or {}

    async def execute(self, stmt):
        model = stmt.column_descriptions[0]["entity"]
        rows = self._by_model.get(model, [])
        return SimpleNamespace(scalars=lambda: SimpleNamespace(
            first=lambda: (rows[0] if rows else None), all=lambda: rows))


def _step(method="GET", url="/api/x", assertions=None, body=None):
    return SimpleNamespace(sort_order=0, method=method, url=url,
                           assertions=assertions or [], body=body)


def _sig(scenario_present=True, steps=None, ui_version=1):
    by_model = {}
    if scenario_present:
        by_model[ApiTestScenario] = [uuid.uuid4()]
    by_model[ApiTestStep] = steps or [_step()]
    if ui_version is not None:
        by_model[Script] = [ui_version]
    return asyncio.run(rounds.content_signature(FakeSession(by_model), uuid.uuid4()))


def test_内容不变签名不变():
    assert _sig() == _sig()


def test_断言改了签名跟着变():
    a = _sig(steps=[_step(assertions=[{"field": "data.enabled", "operator": "=="}])])
    b = _sig(steps=[_step(assertions=[{"field": "data.enabled", "operator": "!="}])])
    assert a != b


def test_脚本版本号变了签名跟着变():
    """场景一字没动，只是 UI 脚本被 sync 覆盖出了新版本——这正是事故现场那种：
    问题出在 UI 脚本，接口场景没碰。"""
    assert _sig(ui_version=1) != _sig(ui_version=2)


def test_只是重新跑一遍不该改变签名():
    """`content_signature` 只摊场景定义，不摊执行结果 —— run_first 重跑
    不该把上一轮 approved 标成过期。"""
    import inspect
    src = inspect.getsource(rounds.content_signature)
    assert "st.last_status" not in src and "st.last_response" not in src


def test_列表按当前内容标过期():
    case_id = uuid.uuid4()
    same_sig = _sig()
    row_fresh = SimpleNamespace(
        round=2, kind="ai_review", verdict="approved", total=83, dimensions={}, findings=[],
        coverage_gaps=[], summary="", changed=None, actor="ai", model="m",
        review_mode="static", traffic_seen=None, content_hash=same_sig, created_at=None)
    row_stale = SimpleNamespace(
        round=1, kind="ai_review", verdict="rejected", total=49, dimensions={}, findings=[],
        coverage_gaps=[], summary="", changed=None, actor="ai", model="m",
        review_mode="static", traffic_seen=None, content_hash="deadbeef" * 4, created_at=None)
    row_legacy = SimpleNamespace(
        round=0, kind="ai_review", verdict="approved", total=80, dimensions={}, findings=[],
        coverage_gaps=[], summary="", changed=None, actor="ai", model="m",
        review_mode=None, traffic_seen=None, content_hash=None, created_at=None)

    by_model = {
        CaseReviewRound: [row_fresh, row_stale, row_legacy],
        ApiTestScenario: [uuid.uuid4()], ApiTestStep: [_step()], Script: [1],
    }
    out = asyncio.run(rounds.list_rounds(FakeSession(by_model), case_id))
    by_round = {r["round"]: r for r in out}
    assert by_round[2]["stale"] is False, "内容没变的最新一轮不该被标过期"
    assert by_round[1]["stale"] is True, "签名对不上当前内容，得说出来"
    assert by_round[0]["stale"] is None, "存量轮次没存过签名，不能瞎猜说它过期"
