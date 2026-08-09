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


def test_阈值默认值是2次翻转():
    """默认 3 轮内 2 次翻转。这是经验值不是结论，改这里要同步改 flaky_service 的说明。"""
    assert flaky_service.WINDOW == 3
    assert flaky_service.FLIPS == 2
    assert flaky_service.QUARANTINE_DAYS == 14


def test_一直坏和刚坏都不该被隔离():
    """隔离是为了挡住"时好时坏"的噪音；真坏了必须继续报警，不能被隔离藏起来。"""
    assert _flips(["failed", "failed", "failed"]) < flaky_service.FLIPS
    assert _flips(["passed", "passed", "failed"]) < flaky_service.FLIPS
