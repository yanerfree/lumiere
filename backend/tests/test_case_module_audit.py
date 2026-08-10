"""用例管理模块走查修复的封样。

这一轮的问题有个共同形状：**页面说的和库里存的不是一回事**。
「无脚本」其实是状态没推、「场景列没有 API」其实是查错了存储、
「报告全是接口」其实是把入口通道当成了执行方式。
所以下面每条钉的都是同一件事：显示出来的那句话，得能对上真实数据。
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.api.plans import _exec_kind_by_report
from app.services.api_test_runner import (
    ScenarioResult,
    StepResult,
    _first_error,
    _readable_trace,
)
from app.services.script_run_service import DEBUG, REGRESSION, apply_case_status


class _Case:
    """只带状态字段的替身——apply_case_status 就只碰这几个。"""

    def __init__(self, **kw):
        self.ui_status = kw.get("ui_status", "not_started")
        self.api_status = kw.get("api_status", "not_started")
        self.ui_scenario_status = kw.get("ui_scenario_status", "draft")
        self.api_scenario_status = kw.get("api_scenario_status", "draft")


# ── 维度状态推进：接口这一维此前整个是死的 ──────────────────────────

def test_接口跑通也会推进状态():
    """原先这里写死 `script_type != "ui"` 直接 return。

    后果不是"少了个功能"，是**页面说假话**：接口场景跑通 69 次，api_status
    还停在 debugging，批量执行就报「0 个包含可执行脚本」——它有脚本。
    """
    case = _Case(api_status="debugging")
    apply_case_status(case, "api", "passed", REGRESSION)
    assert case.api_status == "pending_review"
    assert case.api_scenario_status == "completed"


def test_接口跑挂_回归才打回_调试不打回():
    """和 UI 同一条纪律：调试是"我正在试"，试挂了不代表用例坏了。"""
    debugging = _Case(api_status="executable")
    apply_case_status(debugging, "api", "failed", DEBUG)
    assert debugging.api_status == "executable", "调试失败不该把状态打回去"

    regression = _Case(api_status="executable")
    apply_case_status(regression, "api", "failed", REGRESSION)
    assert regression.api_status == "debugging"


def test_两个维度互不串台():
    """推 api 不能顺手改了 ui —— 用 f-string 拼属性名最容易出这种错。"""
    case = _Case(ui_status="executable", api_status="debugging")
    apply_case_status(case, "api", "passed", REGRESSION)
    assert case.ui_status == "executable"
    assert case.ui_scenario_status == "draft"


def test_不认识的类型直接跳过():
    case = _Case()
    apply_case_status(case, "manual", "passed", REGRESSION)
    assert case.ui_status == "not_started" and case.api_status == "not_started"


# ── 报告的「执行方式」：三级回退 ────────────────────────────────────

class _FakeSession:
    """execute() 只回一批 (report_id, script_type)。"""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _stmt):
        rows = self._rows

        class R:
            def all(self):
                return rows
        return R()


R1, R2, R3 = (uuid.uuid4() for _ in range(3))


@pytest.mark.asyncio
async def test_执行方式优先信执行痕迹():
    """script_runs 记的是**真跑了什么**，比任何声明都可信。"""
    got = await _exec_kind_by_report(
        _FakeSession([(R1, "ui")]), [R1], {R1: "api"}, {R1: "plan"}
    )
    assert got[R1] == "ui", "计划声明 api，实际跑的 ui —— 以实际为准"


@pytest.mark.asyncio
async def test_一份报告里两种脚本记成混合():
    got = await _exec_kind_by_report(
        _FakeSession([(R1, "ui"), (R1, "api")]), [R1], {}, {}
    )
    assert got[R1] == "mixed"


@pytest.mark.asyncio
async def test_没有执行痕迹时退回计划声明():
    """老计划报告在 record_run 接进来之前生成，没有第一级。"""
    got = await _exec_kind_by_report(
        _FakeSession([]), [R1, R2], {R1: "e2e", R2: "api"}, {}
    )
    assert got[R1] == "ui" and got[R2] == "api"


@pytest.mark.asyncio
async def test_接口测试通道按定义就是接口():
    """api_test 这条通道只跑接口场景，是定义使然，不是猜。"""
    got = await _exec_kind_by_report(_FakeSession([]), [R1], {}, {R1: "api_test"})
    assert got[R1] == "api"


@pytest.mark.asyncio
async def test_三条都不命中就留白_不许编():
    """显示「—」比显示一个猜出来的值好。猜错了没人知道它是猜的。"""
    got = await _exec_kind_by_report(_FakeSession([]), [R3], {}, {R3: "adhoc"})
    assert got[R3] is None


# ── 给人看的执行轨迹 ────────────────────────────────────────────────

def _step(name, status="pass", **kw):
    return StepResult(
        step_id=str(uuid.uuid4()), step_name=name, method=kw.get("method", "GET"),
        url=kw.get("url", "http://h/x"), status=status,
        status_code=kw.get("status_code", 200), duration=kw.get("duration", 5),
        assertions=kw.get("assertions", []), response_body=None,
        error=kw.get("error"), request_data=None,
    )


def test_轨迹把断言写成人话_而不是只印类型():
    """只印 `✓ status` 等于没说。人要看的是"断言了什么"。"""
    r = ScenarioResult(scenario_id="s", scenario_title="登录链路", steps=[
        _step("登录取 token", assertions=[
            {"type": "status", "operator": "==", "value": 200, "passed": True},
        ]),
    ])
    out = _readable_trace(r)
    assert "状态码 == 200" in out
    assert "type" not in out, "断言的内部字段名不该漏到界面上"


def test_失败步骤带出实际值():
    r = ScenarioResult(scenario_id="s", scenario_title="x", steps=[
        _step("建项目", status="fail", assertions=[
            {"type": "status", "operator": "==", "value": 201,
             "passed": False, "actual": 500},
        ], error="Internal Server Error"),
    ])
    out = _readable_trace(r)
    assert "✗" in out and "实际 500" in out
    assert "错误：Internal Server Error" in out


def test_错误摘要只取第一个挂掉的步骤():
    """列表里那一列只显示一行，给三条等于一条都看不清。"""
    r = ScenarioResult(scenario_id="s", scenario_title="x", steps=[
        _step("一", status="pass"),
        _step("二", status="fail", error="first boom"),
        _step("三", status="fail", error="second boom"),
    ])
    summary = _first_error(r)
    assert "二" in summary and "first boom" in summary
    assert "second boom" not in summary


def test_全通过时没有错误摘要():
    r = ScenarioResult(scenario_id="s", scenario_title="x", steps=[_step("一")])
    assert _first_error(r) is None


# ── 报告名的时区 ────────────────────────────────────────────────────

def test_报告名用本地时区而不是UTC():
    """名字给人看，时间就得是人所在时区的。

    用 UTC 拼名字、列表按本地渲染 createdAt，同一行会显示相差 8 小时的两个时间
    （实测「批量执行 · 08-10 08:17」配「2026/8/10 16:17:06」）。
    这里不假设具体时区，只钉住"两处命名都做了 astimezone 换算"。
    """
    import inspect

    from app.api import plans
    from app.services import api_test_runner

    adhoc = inspect.getsource(plans.execute_adhoc)
    assert "astimezone()" in adhoc, "批量执行报告名没做本地时区换算"

    api_src = inspect.getsource(api_test_runner._create_report)
    assert "astimezone()" in api_src, "接口测试报告名没做本地时区换算"

    now = datetime(2026, 8, 10, 8, 17, tzinfo=timezone.utc)
    assert now.astimezone().strftime("%m-%d %H:%M") != "08-10 08:17" or \
        datetime.now().astimezone().utcoffset().total_seconds() == 0


# ── 就地审核：打回必须带理由 ────────────────────────────────────────

def test_打回没带理由后端会拒():
    """后端这道校验一直在（review_reason.category 必填）。

    钉住它，是因为前端很容易只发 reviewStatus —— 我就这么干过一次：
    下拉里加了「打回」，点下去 400，而我当时没点过所以没发现。
    """
    import inspect

    from app.services import case_service

    src = inspect.getsource(case_service.update_case)
    assert 'data.review_status == "rejected"' in src
    assert "REASON_REQUIRED" in src


def test_前端打回走弹窗收理由_不直接提交():
    """对应上面那条后端校验。前端只发 reviewStatus 的话，用户点了就是个红叉。

    盯的是「打回」这条路必须把 reviewReason 一起发出去。
    """
    from pathlib import Path

    jsx = Path(__file__).resolve().parents[2] / "frontend/src/pages/cases/CaseManagement.jsx"
    src = jsx.read_text(encoding="utf-8")
    body = src[src.index("const rejectCase"):]
    body = body[:body.index("\n  }")]
    assert "reviewReason" in body, "打回没带 reviewReason，后端会 400"
    assert "category" in body, "reviewReason 里必须有 category"
    # 「通过」不需要理由，别顺手也要求填
    approve = src[src.index("const approveCase"):]
    approve = approve[:approve.index("\n  }")]
    assert "reviewReason" not in approve


def test_打回分类和生成向导保持同一套():
    """两处各写一套的话，同一个"场景重复"会落成两个不同的 category，
    质量归因统计直接分裂 —— 而且没人会发现，因为两边各自都是对的。"""
    import re
    from pathlib import Path

    base = Path(__file__).resolve().parents[2] / "frontend/src"

    def cats(path):
        src = (base / path).read_text(encoding="utf-8")
        block = src[src.index("REJECT_CATEGORIES = ["):]
        return set(re.findall(r"value: '([^']+)'", block[:block.index("]")]))

    assert cats("pages/cases/CaseManagement.jsx") == \
        cats("pages/scenario-gen/components/Stage5Review.jsx")
