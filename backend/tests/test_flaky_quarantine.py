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


def test_一直坏和刚坏都不该被隔离():
    """隔离是为了挡住"时好时坏"的噪音；真坏了必须继续报警，不能被隔离藏起来。"""
    assert _flips(["failed", "failed", "failed"]) < flaky_service.FLIPS
    assert _flips(["passed", "passed", "failed"]) < flaky_service.FLIPS
