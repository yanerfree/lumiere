"""报告列表的两列：环境、状态。

## 环境列半张表是「-」

库里 24/24 条接口报告的 `environment_id` 是 NULL —— 值一直在调用方手上
（页面上选的那个环境，`body.env_id`），只是从来没往 `_create_report` 传。
一列有一半是「-」时，人的第一反应是"这功能坏了"，而不是"这条通道没记"。

## 状态列：「执行中」执行了 12 天

原来 `isCompleted = !!completedAt`，没有 completed_at 就一律显示「执行中」。
库里那条 8-12 的报告因此在页面上"跑了 12 天"，而它其实是
**手动计划在等人录结果**（plan.status=pending_manual，两条场景都是 manual/pending）。

「在跑」只能等，「等人录」是**待办**。混成一个词的代价是这条待办永远不会被认领。
"""
import inspect

from app.api import plans
from app.services import api_test_runner


def test_接口执行把环境写进报告():
    src = inspect.getsource(api_test_runner._create_report)
    assert "environment_id=env_id" in src, "报告没记环境，列表那一列只能是「-」"
    assert "env_id" in inspect.signature(api_test_runner._create_report).parameters
    assert "env_id" in inspect.signature(api_test_runner.run_batch).parameters, \
        "run_batch 不收 env_id，端点上那个值就传不下来"
    # 端点 → run_batch 这一段也要真的传，光有参数不算
    from app.api import api_test
    assert "env_id=uuid.UUID(body.env_id)" in inspect.getsource(api_test.run_batch_scenarios)


def test_报告状态四种态而不是完了没完():
    src = inspect.getsource(plans._report_status_map)
    for state in ("pending_manual", "running", "stalled", "done_unsealed"):
        assert state in src, f"少了 {state} 这一态"


def test_等人录不算在跑():
    """手动场景还挂着、自动化没有待办 → 待录入，不是执行中。"""
    src = inspect.getsource(plans._report_status_map)
    assert 'a["manual"] and not a["auto"]' in src, "没区分「等人录」和「在跑」"


def test_久了没动静算中断而不是一直在跑():
    src = inspect.getsource(plans._report_status_map)
    assert "_STALE_AFTER_MIN" in src or "timedelta" in src, "没有时间上界，一份报告可以永远「执行中」"
    assert plans._STALE_AFTER_MIN > 0


def test_列表把算好的状态给前端():
    src = inspect.getsource(plans.list_reports)
    assert "_report_status_map" in src, "列表没调状态计算"
    assert '"status": "completed" if report.completed_at' in src
    assert '"pendingManual"' in src and '"pendingAuto"' in src, \
        "待办数量要给前端 —— 「还差几条」是人决定重跑还是去录的依据"


def test_前端按后端给的状态渲染_不再自己猜():
    from pathlib import Path
    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/report/ReportList.jsx").read_text(encoding="utf-8")
    assert "STATUS_META" in jsx
    for label in ("待录入", "已中断"):
        assert label in jsx, f"页面上没有「{label}」这一态"
    assert "STATUS_META[r.status]" in jsx, "前端没用后端算好的状态"
    # 环境为空要说清为什么空，不能只留一个「-」
    assert "没记下环境" in jsx
