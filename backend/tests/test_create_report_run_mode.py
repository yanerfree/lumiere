"""页面「运行全部」的记账：模式该是「调试」，而且这条路径必须真的跑得起来。

**这份测试是补一个我自己造的事故。** 上一轮把 `_create_report` 里写死的
`run_mode=REGRESSION` 改成可配，`mode = run_mode or script_run_service.DEBUG`
放在了函数顶部 —— 而 `script_run_service` 在函数后面还有一处**函数内导入**，
Python 因此把它当局部变量，顶部引用直接 UnboundLocalError。

后果不是"模式标错了"，是**整条页面运行路径抛异常**：测试报告没了、script_runs
没了、用例状态也不推了，SSE 只回一句 error。而我当时只跑了单元测试就报告"修好了" ——
单元测试全绿，因为没有一条测试真的调过 `_create_report`。

所以这里必须**真调它**，不是测它里面某个纯函数。判据两条：
① 跑得起来（不抛） ② 记的是 debug 不是 regression。
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.services import api_test_runner, script_run_service


class FakeSession:
    def __init__(self, case=None):
        self._case = case
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def get(self, model, oid):
        return self._case


def _result(passed=True):
    return api_test_runner.ScenarioResult(
        scenario_id=str(uuid.uuid4()), scenario_title="发布上线",
        source_case_id=str(uuid.uuid4()),
        steps=[api_test_runner.StepResult(
            step_id="1", step_name="登录", method="POST", url="/login",
            status="pass" if passed else "fail", status_code=200, duration=10)],
    )


def _call(run_mode=None, passed=True):
    """真调 _create_report，把记账参数截下来。"""
    case = SimpleNamespace(id=uuid.uuid4(), case_code="TC-X-00001",
                           api_status="debugging")
    session = FakeSession(case=case)
    calls = {}

    async def fake_record_run(session, **kw):
        calls.update(kw)
        return None

    def fake_apply(c, t, st, mode):
        calls["applied_mode"] = mode
        calls["applied_type"] = t

    orig_r, orig_a = script_run_service.record_run, script_run_service.apply_case_status
    script_run_service.record_run = fake_record_run
    script_run_service.apply_case_status = fake_apply
    try:
        asyncio.run(api_test_runner._create_report(
            session, [_result(passed)], uuid.uuid4(), uuid.uuid4(),
            "报告名", None, uuid.uuid4(), run_mode))
    finally:
        script_run_service.record_run, script_run_service.apply_case_status = orig_r, orig_a
    return calls


def test_这条路径跑得起来():
    """最基本的一条 —— 它曾经整条抛 UnboundLocalError，而单元测试全绿。"""
    calls = _call()
    assert calls, "_create_report 跑完了却什么都没记 —— 说明中途出问题了"


def test_默认记成调试不是回归():
    """走到这里的只有"人在页面上点了运行"。计划回归走 adhoc_execution，不经过这里。

    记成 regression 的三个代价：① 执行历史里 UI 跑标「调试」、接口跑标「回归」，
    同一个详情页两个按钮说法不一致 ② 手动调试进回归通过率口径
    ③ REGRESSION 失败会把 api_status 打回 debugging，而断点续跑靠状态判待办 ——
    人手动试一次没成功，CC 下一轮就把这条已完成的用例捡回来重做。
    """
    calls = _call()
    assert calls["run_mode"] == script_run_service.DEBUG, calls["run_mode"]
    assert calls["applied_mode"] == script_run_service.DEBUG


def test_显式传回归时按回归记():
    calls = _call(run_mode=script_run_service.REGRESSION)
    assert calls["run_mode"] == script_run_service.REGRESSION
    assert calls["applied_mode"] == script_run_service.REGRESSION


def test_记的是接口维度():
    assert _call()["applied_type"] == "api"


def test_失败也照样记账():
    """失败证据挂在 script_runs 上，这条路不记就看不到。"""
    calls = _call(passed=False)
    assert calls["result"]["status"] == "failed"


def test_不再有函数内重复导入():
    """钉住根因本身 —— 函数内再出现一次 `from app.services import script_run_service`，
    Python 就又把它当局部变量，顶部那句引用会再次 UnboundLocalError。"""
    import inspect
    src = inspect.getsource(api_test_runner._create_report)
    assert src.count("import script_run_service") <= 1, \
        "_create_report 里有多处导入 script_run_service —— 会再次触发 UnboundLocalError"
