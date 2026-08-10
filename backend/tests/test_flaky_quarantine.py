"""flaky 自动隔离的判定逻辑封样。

最容易错的一条是**换脚本版本不算翻转** —— 实测库里 TC-XMGL-00001 有两个脚本版本、
各自有失败，不按 script_id 分组的话，一次成功的修复会被判成 flaky。
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import flaky_service


class _Case:
    """够用的假用例 —— 只关心这三个字段。"""

    def __init__(self, is_flaky=False, quarantined_until=None):
        self.is_flaky = is_flaky
        self.quarantined_until = quarantined_until
        self.flaky_evidence = None


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


# ── is_quarantined / should_skip ──

def test_没标没隔离就不跳过():
    assert flaky_service.should_skip(_Case(), NOW) is False


def test_人工标记的仍然跳过():
    assert flaky_service.should_skip(_Case(is_flaky=True), NOW) is True


def test_检测到不稳定不跳过():
    """这是这次改的核心：检测 ≠ 隔离。

    时好时坏本身就是信息（时序/脏数据/并发/环境抖动），自动把它藏起来
    等于自动让人不去查这个问题。检测只标记 + 给"该往哪儿看"，跳不跳由人定。
    """
    c = _Case()
    c.flaky_evidence = {"note": "翻转了 2 次", "runs": []}     # 检测到了
    assert c.quarantined_until is None                         # 但没被隔离
    assert flaky_service.should_skip(c, NOW) is False           # 所以照常执行


def test_隔离期内跳过():
    c = _Case(quarantined_until=NOW + timedelta(days=3))
    assert flaky_service.is_quarantined(c, NOW) is True
    assert flaky_service.should_skip(c, NOW) is True


def test_隔离到期自动回来():
    """到期即失效，不需要谁去清理 —— 这是这次改动的核心：得有回来的路。"""
    c = _Case(quarantined_until=NOW - timedelta(seconds=1))
    assert flaky_service.is_quarantined(c, NOW) is False
    assert flaky_service.should_skip(c, NOW) is False


def test_到期边界不提前放行():
    c = _Case(quarantined_until=NOW + timedelta(seconds=1))
    assert flaky_service.is_quarantined(c, NOW) is True


# ── 翻转计数：evaluate 里那段判据 ──

def _flips(seq):
    return sum(1 for a, b in zip(seq, seq[1:]) if a != b)


@pytest.mark.parametrize("seq,expected", [
    (["passed", "passed", "passed"], 0),
    (["passed", "failed", "passed"], 2),      # 典型 flaky
    (["failed", "passed", "failed"], 2),
    (["passed", "passed", "failed"], 1),      # 刚坏，不是 flaky —— 该报警不该隐藏
    (["failed", "failed", "passed"], 1),      # 修好了，也不是 flaky
    (["failed", "failed", "failed"], 0),      # 一直坏，是真 bug
])
def test_翻转计数(seq, expected):
    assert _flips(seq) == expected


def test_阈值是实测校准过的():
    """最近至多 7 次里 2 次翻转 —— 造样本真跑 24 轮校准出来的，见 flaky_service 的说明。

    原来是固定 3 窗口，实测漏掉"成片挂"的脚本：50% 失败率的要 23 轮才抓到，
    而 21% 的 8 轮就抓到，严重程度和检出速度反相关。改窗口后 23 → 9 轮。
    """
    assert flaky_service.WINDOW == 7
    assert flaky_service.FLIPS == 2
    assert flaky_service.MIN_RUNS == 3
    assert flaky_service.QUARANTINE_DAYS == 14


def _hit(seq, window=None, need=None):
    """按线上同一套口径判：最近至多 window 次里翻转 >= need。"""
    window = window or flaky_service.WINDOW
    need = need or flaky_service.FLIPS
    w = seq[-window:]
    if len(w) < flaky_service.MIN_RUNS:
        return False
    return _flips(w) >= need


def test_成片挂的脚本也能抓到():
    """这是改窗口的直接原因：连续失败不产生翻转，FFFF 的翻转数和 PPPP 一样是 0。

    实测序列（40% 设定、实际 50% 失败）在固定 3 窗口下要到第 23 轮才触发。
    """
    burst = list("FFFFPPPPF")           # 挂一片 → 好一片 → 又挂
    assert _hit(burst) is True
    assert _hit(burst, window=3) is False, "3 窗口就是漏在这里"


def test_修好了不能被判成flaky():
    """FFFF→PPPP 是"改好了"，只有 1 次翻转。

    这条是"别把判据换成『窗口里既有 P 又有 F』"的原因 —— 那个判据实测在第 5 轮
    就把修好的用例关起来了。
    """
    assert _hit(list("FFFFPPPP")) is False
    assert _hit(list("FFPPPPPP")) is False


def test_强交替仍然三轮就抓到():
    """窗口是"最近**至多** 7 次"，不是"攒满 7 次才判" —— 否则要白等 4 轮。"""
    assert _hit(list("PFP")) is True


def test_诊断给的是往哪儿看不是结论():
    """平台看不见根因，但能把失败之间的共性/差异摆出来 —— 这是查的起点。"""
    same = flaky_service._diagnose([
        {"status": "failed", "error": "Timeout", "phenomenon": "timeout", "runId": "1"},
        {"status": "passed", "error": None, "phenomenon": None, "runId": "2"},
        {"status": "failed", "error": "Timeout", "phenomenon": "timeout", "runId": "3"},
    ])
    assert same["distinctErrors"] == 1
    assert "被测系统" in same["hint"]        # 错误都一样 → 指向业务

    diff = flaky_service._diagnose([
        {"status": "failed", "error": "Timeout", "phenomenon": "timeout", "runId": "1"},
        {"status": "passed", "error": None, "phenomenon": None, "runId": "2"},
        {"status": "failed", "error": "Connection refused", "phenomenon": "http_5xx", "runId": "3"},
    ])
    assert diff["distinctErrors"] == 2
    assert "环境" in diff["hint"]            # 错误各不同 → 指向环境/时序


def test_诊断给出可对比的两次执行():
    """最近一次成功 vs 最近一次失败，截图和流量摆一起最快看出差别。"""
    d = flaky_service._diagnose([
        {"status": "passed", "error": None, "phenomenon": None, "runId": "p1"},
        {"status": "failed", "error": "boom", "phenomenon": "assertion_mismatch", "runId": "f1"},
    ])
    assert d["compare"] == {"lastPassed": "p1", "lastFailed": "f1"}


def test_一直坏和刚坏都不该被隔离():
    """隔离是为了挡住"时好时坏"的噪音；真坏了必须继续报警，不能被隔离藏起来。"""
    assert _flips(["failed", "failed", "failed"]) < flaky_service.FLIPS
    assert _flips(["passed", "passed", "failed"]) < flaky_service.FLIPS


def test_错误摘要归一化后同一个错不被数成多种():
    """实测踩过：pytest 把随机值和对象地址写进摘要，同一个断言失败被数成 4 种错，
    诊断于是给出**完全相反**的结论（说"更像环境问题"，实际是稳定的同一个失败）。"""
    from app.services.flaky_service import _diagnose, _err_shape

    same = [
        "AssertionError: 校准用的随机失败\nassert 0.2721101623515365 >= 0.4\n"
        " +  where 0.2721101623515365 = <built-in method random of Random object at 0xcf91c50>()",
        "AssertionError: 校准用的随机失败\nassert 0.15617404998147644 >= 0.4\n"
        " +  where 0.15617404998147644 = <built-in method random of Random object at 0x3f314c50>()",
        "AssertionError: 校准用的随机失败\nassert 0.3848953127147271 >= 0.4\n"
        " +  where 0.3848953127147271 = <built-in method random of Random object at 0x133b8c50>()",
    ]
    assert len(set(same)) == 3, "前提：这三条原始字符串确实各不相同"
    assert len({_err_shape(x) for x in same}) == 1, "归一化后应收敛成同一个错"

    runs = [{"status": "failed", "error": e, "phenomenon": "assertion_mismatch", "runId": str(i)}
            for i, e in enumerate(same)]
    runs.insert(1, {"status": "passed", "error": None, "phenomenon": None, "runId": "p"})
    d = _diagnose(runs)
    assert d["distinctErrors"] == 1
    assert "被测系统真有问题" in d["hint"], "同一个错应指向业务，不该说'更像环境'"


def test_归一化不会把真不同的错抹成一种():
    """反向兜底：归一化删掉的只能是噪声。真不同的错必须仍然分得开，
    否则诊断会永远说"同一个错"，一样是错的。"""
    from app.services.flaky_service import _err_shape

    diff = ["TimeoutError: 等 #save 超时", "ConnectionRefused: 连不上 db",
            "AssertionError: 名字不对", "net::ERR_ABORTED"]
    assert len({_err_shape(x) for x in diff}) == 4
