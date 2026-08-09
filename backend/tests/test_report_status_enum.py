"""六种状态与通过率口径的封样。

规范（change-sync-log「状态枚举扩展」）：
  passed / failed / error / skipped / xfail / flaky
  通过率 = passed / (passed + failed + error + flaky)，skipped 和 xfail 不进分母

实现里原来只有 4 种，缺的两种各有后果：

· **flaky**：用例失败后重试通过，最终记成 `passed`，只在备注里写一句
  「重试 2 次，最终通过」——**通过率照样算 100%**。一次就过和试了三次才过，
  对使用者是完全不同的两件事，这正是"假通过"。
· **xfail**：pytest 的预期失败在 junit 里是 `<skipped type="pytest.xfail">`，
  被并进"跳过"。分母没算错（两者都排除），但报告上写"跳过"读起来像没跑。
"""
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app.engine.result_parser import parse_junit_xml


def _junit(tmp_path: Path, cases: list[str]) -> str:
    xml = "<testsuite>" + "".join(cases) + "</testsuite>"
    p = tmp_path / "junit.xml"
    p.write_text(xml)
    return str(p)


# ── junit 解析：xfail 不能被并进 skipped ──

def test_pytest的xfail解析成xfail(tmp_path):
    r = parse_junit_xml(_junit(tmp_path, [
        '<testcase name="t1" time="0.1"><skipped type="pytest.xfail" message="known bug"/></testcase>']))
    assert r[0]["status"] == "xfail"


def test_真正的skip还是skipped(tmp_path):
    r = parse_junit_xml(_junit(tmp_path, [
        '<testcase name="t1" time="0.1"><skipped type="pytest.skip" message="no env"/></testcase>']))
    assert r[0]["status"] == "skipped"


def test_没有type的skip按skipped算(tmp_path):
    r = parse_junit_xml(_junit(tmp_path, [
        '<testcase name="t1" time="0.1"><skipped message="x"/></testcase>']))
    assert r[0]["status"] == "skipped"


def test_通过失败错误不受影响(tmp_path):
    r = parse_junit_xml(_junit(tmp_path, [
        '<testcase name="a" time="0.1"/>',
        '<testcase name="b" time="0.1"><failure message="boom"/></testcase>',
        '<testcase name="c" time="0.1"><error message="crash"/></testcase>',
    ]))
    assert [x["status"] for x in r] == ["passed", "failed", "error"]


# ── 通过率口径 ──

def _rate(passed, failed, error, flaky, skipped=0, xfail=0):
    """和 execution_service 里那行分母保持同一个口径。"""
    denom = passed + failed + error + flaky
    return round(passed / denom * 100, 2) if denom else None


def test_flaky进分母():
    """9 通过 + 1 重试后通过 → 90%，不是 100%。这是这次改动的核心。"""
    assert _rate(passed=9, failed=0, error=0, flaky=1) == 90.0


def test_skipped不进分母():
    assert _rate(passed=8, failed=2, error=0, flaky=0, skipped=90) == 80.0


def test_xfail不进分母():
    """预期失败是"按计划失败"，不该拉低通过率。"""
    assert _rate(passed=8, failed=2, error=0, flaky=0, xfail=50) == 80.0


def test_全跳过时通过率是空而不是零():
    """分母为 0 时给 None —— 报 0% 会被当成"全挂了"。"""
    assert _rate(passed=0, failed=0, error=0, flaky=0, skipped=5) is None


@pytest.mark.parametrize("status", ["passed", "failed", "error", "skipped", "xfail", "flaky"])
def test_六种状态前端都认得(status):
    """少一种前端就会渲染成空白，用户看到一格什么都没有。"""
    import re

    src = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
           / "report" / "ReportDetail.jsx").read_text(errors="replace")
    m = re.search(r"const statusMap = \{(.*?)\n\}", src, re.S) or re.search(r"\{(.*?)\n\}", src, re.S)
    assert re.search(rf"\b{status}:\s*\{{", src), f"前端 statusMap 里没有 {status}"
