"""tb_run_api_test 跑完之后，用例的接口维度必须真的动 —— 且只有编排场景能动它。

两个实测缺陷的封样：

**一、接口维度永不收敛。** 落用例状态那段原来只存在于 `_create_report()` 里，
而它被 `if all_results and user_id:` 挡着；MCP 通道调 `run_batch` 不传 user_id，
整段跳过。于是步骤级 last_status 存了，用例级一个字段都不动，`api_status`
永远停在 debugging，`_owes()` 判它「还欠着」，断点续跑每一轮都把已经跑绿的
场景当待办重做。实测：AT-0013 跑到 19/19 全绿，用例的 owes 仍返回 ["api"]。

注意 `test_owes_convergence.py` 当时是**绿的** —— 它测 `_owes` 这个纯函数，
给定 api_status=pending_review 就收敛。坏的是没人把 api_status 推到那儿的接线。
所以这份测的是接线，不是判据。

**二、两个模块不能互相串。** 「接口测试模块」的单接口场景和「用例的接口维度」
是两回事：前者凭接口文档造、不属于任何用例。它跑成什么样都不该改动用例状态 ——
串过去的后果是用例的接口维度被一条根本不测它的场景推着走，页面上显示
「这条用例的接口测好了」。孤儿场景（曾经绑过、用例已删）同样拦在外面。
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.mcp.tools import api_tests
from app.models.api_test import ApiTestScenario
from app.models.case import Case
from app.services import script_run_service


class FakeSession:
    """只认 session.get(Model, id) 和 commit()，并记下查过谁。

    `lookups` 是边界测试的关键：判「有没有串到用例侧」不能只看最终结果 ——
    真 SQLAlchemy 里 `session.get(Case, None)` 只是告警并返回 None，
    于是"没查到用例"和"压根没去查"在结果上长得一模一样，
    拆掉边界守卫测试照样绿（实测：突变测试 9 条全过，一条没红）。
    所以要盯的不变量是**没去查**，不是"查了没查到"。
    """

    def __init__(self, objects: dict):
        self._objects = objects
        self.commits = 0
        self.lookups: list[tuple] = []

    async def get(self, model, oid):
        self.lookups.append((model, oid))
        return self._objects.get((model, oid))

    async def commit(self):
        self.commits += 1

    def looked_up_cases(self):
        return [oid for model, oid in self.lookups if model is Case]


def _scenario(source_case_id):
    return SimpleNamespace(id=uuid.uuid4(), source_case_id=source_case_id)


def _case(api_status="debugging", code="TC-X-00001"):
    # review_status / target_level / 其余两维：sync_review_status 要读它们
    return SimpleNamespace(id=uuid.uuid4(), case_code=code, api_status=api_status,
                           review_status=None,
                           target_level="spec_api", manual_status="completed",
                           ui_status="draft", steps=[{"seq": 1}])


def _run(session, finished):
    """跑 _apply_case_dimension，并把 record_run 的调用记下来。"""
    calls = []

    async def fake_record_run(session, **kw):
        calls.append(kw)
        return None

    orig = script_run_service.record_run
    script_run_service.record_run = fake_record_run
    try:
        applied = asyncio.run(api_tests._apply_case_dimension(session, finished))
    finally:
        script_run_service.record_run = orig
    return applied, calls


def _fin(scenario, passed=True, duration_ms=100, error_summary=None):
    return api_tests._Finished(scenario_id=scenario.id, passed=passed,
                               duration_ms=duration_ms, error_summary=error_summary)


# ── 一、接线：跑通之后状态真的往前走 ──────────────────────────────

def test_编排场景跑通把api维度置完成():
    """这就是当初断掉的那一环 —— 全绿了 api_status 却纹丝不动。

    2026-08 放权 CC：跑绿直接置 completed，不再是 pending_review 等人发布。
    """
    case = _case(api_status="debugging")
    sc = _scenario(case.id)
    session = FakeSession({(ApiTestScenario, sc.id): sc, (Case, case.id): case})

    applied, calls = _run(session, [_fin(sc, passed=True)])

    assert case.api_status == "completed", case.api_status
    assert applied == [{"caseCode": case.case_code, "apiStatus": "completed",
                        "changed": True}]
    assert len(calls) == 1 and calls[0]["script_type"] == "api"


def test_跑通之后审核标签自动进待审():
    """三维按 target_level 全完成 → 自动「待审」，没有「提交审核」那一下。"""
    case = _case(api_status="debugging")   # manual 已 completed、target=spec_api
    sc = _scenario(case.id)
    session = FakeSession({(ApiTestScenario, sc.id): sc, (Case, case.id): case})
    _run(session, [_fin(sc, passed=True)])
    assert case.review_status == "pending", case.review_status


def test_跑通之后owes不再挂着接口维度():
    """接线 + 判据合起来必须收敛 —— 这才是 dogfood 里真正要的那个结果。"""
    from app.mcp.tools.test_cases import _owes

    case = _case(api_status="debugging")
    case.steps = [{"seq": 1}]
    case.target_level = "spec_api"
    case.ui_status = "not_started"
    case.manual_status = "not_started"
    sc = _scenario(case.id)
    session = FakeSession({(ApiTestScenario, sc.id): sc, (Case, case.id): case})

    assert "api" in _owes(case)          # 跑之前：欠着
    _run(session, [_fin(sc, passed=True)])
    assert _owes(case) == []             # 跑通之后：收敛


def test_执行历史带上错误摘要():
    """不带的话 failure_triage 判不出现象，用例历史里只剩一个红点。"""
    case = _case()
    sc = _scenario(case.id)
    session = FakeSession({(ApiTestScenario, sc.id): sc, (Case, case.id): case})

    _, calls = _run(session, [_fin(sc, passed=False, error_summary="确认推送已收敛：期望 success，实际 'pushing'")])

    assert calls[0]["result"]["status"] == "failed"
    assert "pushing" in calls[0]["result"]["error_summary"]


def test_调试跑挂不把已完成的用例打回():
    """MCP 手动跑是「我正在调」。用 REGRESSION 会把 executable 打回 debugging，
    而断点续跑正是靠状态判待办 —— 一次调试失败就能让做完的用例被捡回来重做。"""
    case = _case(api_status="completed")
    sc = _scenario(case.id)
    session = FakeSession({(ApiTestScenario, sc.id): sc, (Case, case.id): case})

    _run(session, [_fin(sc, passed=False)])

    assert case.api_status == "completed", case.api_status


# ── 二、边界：接口测试模块永远碰不到用例 ──────────────────────────

def test_单接口场景跑通不碰任何用例():
    """接口测试模块的本职产物，不属于任何用例 —— 连查都不该去查用例表。

    只断言 `applied == []` 是假过的：没有 source_case_id 时用例本来就查不到，
    守卫拆了结果也一样。真正要钉住的是它**在边界上就停下了**。
    """
    sc = _scenario(None)
    session = FakeSession({(ApiTestScenario, sc.id): sc})

    applied, calls = _run(session, [_fin(sc, passed=True)])

    assert applied == []
    assert calls == []
    assert session.looked_up_cases() == [], session.looked_up_cases()


def test_孤儿场景跑通不碰任何用例():
    """曾经绑过、用例已删 —— source_case_id 还在但取不到用例，必须跳过而不是炸。"""
    dead_case_id = uuid.uuid4()
    sc = _scenario(dead_case_id)
    session = FakeSession({(ApiTestScenario, sc.id): sc})   # 故意不放 Case

    applied, calls = _run(session, [_fin(sc, passed=True)])

    assert applied == []
    assert calls == []


def test_混跑时只有编排那条落到用例():
    """一次跑多条、单接口和编排混在一起，边界不能漏。"""
    case = _case()
    bound = _scenario(case.id)
    standalone = _scenario(None)
    session = FakeSession({(ApiTestScenario, bound.id): bound,
                           (ApiTestScenario, standalone.id): standalone,
                           (Case, case.id): case})

    applied, calls = _run(session, [_fin(standalone), _fin(bound)])

    assert [a["caseCode"] for a in applied] == [case.case_code]
    assert len(calls) == 1
    # 查用例表只该为编排那条发生一次 —— 单接口那条连查都没查。
    assert session.looked_up_cases() == [case.id], session.looked_up_cases()


def test_场景不存在也不炸():
    session = FakeSession({})
    applied, calls = _run(session, [_fin(_scenario(None))])
    assert applied == [] and calls == []


# ── 三、批量跑的耗时不能串场景 ────────────────────────────────────

def test_多场景耗时各算各的():
    """原来 sum(全部 results) 会把前面场景的步骤也算进去，越靠后越离谱。"""
    a, b = _scenario(None), _scenario(None)

    async def fake_run_batch(ids, session, **kw):
        yield SimpleNamespace(type="step_result",
                              data={"stepName": "a1", "status": "pass", "duration": 10})
        yield SimpleNamespace(type="step_result",
                              data={"stepName": "a2", "status": "pass", "duration": 20})
        yield SimpleNamespace(type="scenario_done",
                              data={"scenarioId": str(a.id), "title": "A", "passed": True,
                                    "passCount": 2, "failCount": 0})
        yield SimpleNamespace(type="step_result",
                              data={"stepName": "b1", "status": "pass", "duration": 5})
        yield SimpleNamespace(type="scenario_done",
                              data={"scenarioId": str(b.id), "title": "B", "passed": True,
                                    "passCount": 1, "failCount": 0})

    seen = []

    async def fake_apply(session, finished):
        seen.extend(finished)
        return []

    import app.services.api_test_runner as runner
    orig_batch, orig_apply = runner.run_batch, api_tests._apply_case_dimension
    runner.run_batch = fake_run_batch
    api_tests._apply_case_dimension = fake_apply
    try:
        asyncio.run(api_tests.run_api_test(FakeSession({}), f"{a.id},{b.id}"))
    finally:
        runner.run_batch, api_tests._apply_case_dimension = orig_batch, orig_apply

    assert [f.duration_ms for f in seen] == [30, 5], [f.duration_ms for f in seen]
