"""计划/批量跑接口场景，报告里必须留下**步骤明细**，不能只留一段文字轨迹。

事故长这样：一份 9 条全跑完的接口回归报告，展开任何一条都只有
「✅ 1. xxx [POST http://… → 201] 14ms」这样的纯文本，**看不到发了什么 body、
回了什么、哪条断言挂的**。查下来是 `_run_orchestrated_scenario` 只回
{status, duration_ms, error_summary, stdout}，而两个调用方
（execution.py 的计划执行、adhoc_execution 的批量回归）都是拿
`case_result.get("steps", [])` 去建 TestReportStep —— 取不到，于是一条不写。

最难发现的地方在于**同一条场景在页面上点「运行」是能下钻的**
（那条走 api_test_runner._create_report，它自己写了步骤），
所以现象像"报告页时好时坏"，而不是"少了一个字段"。

判据：真调 `_run_orchestrated_scenario`，返回里 steps 得能还原出
请求体、响应体、断言、状态码 —— 报告详情下钻要的就是这四样。
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.engine.tasks import adhoc_execution
from app.services import api_test_runner


class _Steps:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *a, **kw):
        return _Steps(self._rows)


def _step(name, method, url, status, code, body, req, asserts, err=None):
    return SimpleNamespace(
        id=uuid.uuid4(), name=name, method=method, url=url,
        last_status=status,
        last_response={
            "statusCode": code, "duration": 7, "body": body,
            "request": req, "assertions": asserts, "error": err,
        },
    )


def _run(rows, passed):
    scenario = SimpleNamespace(
        id=uuid.uuid4(), title="示例场景",
        project_id=uuid.uuid4(), branch_id=uuid.uuid4(),
    )

    async def fake_run_batch(**kw):
        yield api_test_runner.RunEvent(
            type="scenario_done",
            data={"scenarioId": str(scenario.id), "passed": passed},
        )

    orig = api_test_runner.run_batch
    api_test_runner.run_batch = fake_run_batch
    try:
        return asyncio.run(adhoc_execution._run_orchestrated_scenario(
            FakeSession(rows), scenario, env_id=None,
        ))
    finally:
        api_test_runner.run_batch = orig


def test_orchestrated_result_carries_step_details():
    rows = [
        _step("前置: 建凭证", "POST", "${BASE_URL}/api/v1/creds", "pass", 201,
              {"id": "c-1"}, {"url": "http://h/api/v1/creds", "body": {"name": "t"}},
              [{"type": "status", "passed": True}]),
        _step("操作: 授权高危工具", "POST", "${BASE_URL}/api/v1/perms", "fail", 422,
              {"detail": "unprocessable"}, {"url": "http://h/api/v1/perms", "body": {"tool": "x"}},
              [{"type": "status", "passed": False, "expected": 201, "actual": 422}],
              err="断言不通过"),
    ]
    res = _run(rows, passed=False)

    steps = res.get("steps")
    assert steps, "接口场景跑完必须回步骤明细，否则报告详情下钻是空的"
    assert len(steps) == 2

    # 报告下钻要的四样：请求、响应、断言、状态码 —— 一样都不能丢
    ok, bad = steps[0], steps[1]
    assert ok["request_data"]["body"] == {"name": "t"}
    assert ok["response_data"] == {"id": "c-1"}
    assert ok["assertions"] == [{"type": "status", "passed": True}]
    assert ok["status_code"] == 201

    assert bad["status"] == "fail"
    assert bad["status_code"] == 422
    assert bad["response_data"] == {"detail": "unprocessable"}
    assert bad["error_summary"] == "断言不通过"

    # URL 用**实际发出去**的那个，不是 ${BASE_URL} 模板
    assert ok["url"] == "http://h/api/v1/creds"
    assert "${" not in bad["url"]

    # 原来那段文字轨迹和汇总不能被弄丢
    assert res["status"] == "failed"
    assert res["error_summary"].startswith("步骤「操作: 授权高危工具」")
    assert "示例场景" in res["stdout"]


def test_step_shape_matches_report_step_writer():
    """键名必须和两个调用方 `step.get(...)` 的那串对得上，错一个就是静默丢字段。"""
    import io
    import re

    consumed = set()
    for p in ("app/engine/tasks/execution.py", "app/engine/tasks/adhoc_execution.py"):
        src = io.open(p, encoding="utf-8").read()
        block = src.split('for j, step in enumerate(case_result.get("steps", []))')[1][:900]
        consumed |= set(re.findall(r'step\.get\("([a-z_]+)"', block))
    assert consumed, "没找到 TestReportStep 的写入块，测试锚点过期了"

    produced = set(_run([
        _step("s", "GET", "u", "pass", 200, {}, {"url": "u"}, []),
    ], passed=True)["steps"][0])

    # step_label / step_phase 是 pytest 步骤日志那条路才有的，接口场景没有，
    # 允许缺；其余的必须齐。
    missing = consumed - produced - {"step_label", "step_phase"}
    assert not missing, f"接口场景没产出这些键，报告里会静默变空：{sorted(missing)}"
