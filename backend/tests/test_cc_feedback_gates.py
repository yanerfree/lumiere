"""CC 反馈通道的三道闸 + 指纹 + 状态机不变量。

这一份咬的方向只有一个：**这条通道的价值全在「回音」上**。
没有回音，CC 下一轮照原样再撞一次 —— 而那正是这个需求要消掉的东西
（此前靠人肉收集、转述、再转述）。所以下面每条都在防同一类退化：

  · 闸门松掉 → 表变成一个没人愿意打开的地方 → 没人回音
  · 回音可选 → 赶时间时第一个被跳过的就是它
  · 指纹不稳 → 归并失效 → 同一件事 N 行 → 处理方放弃
  · wont_fix 不短路 → 「回复原因」只是客套，下一轮照样再来

带 DB 的那一半（归并、短路、复发、ack）在根目录 tests/api/cc_feedback/ 里，
那边打的是真接口。
"""
import pytest

from app.models.cc_feedback import (
    CATEGORIES,
    CATEGORY_LABEL,
    OPEN_STATUSES,
    PENDING_STATUSES,
    SEVERITIES,
    STATUS_LABEL,
    STATUSES,
)
from app.services.cc_feedback_service import (
    MAX_BODY,
    MIN_BODY,
    QUOTA_PER_DAY,
    fingerprint_of,
    validate,
)

BODY = "调 lum_get_case 想读回 bugRefs，返回里没有这个字段；工具描述写的是「读一条用例的全部内容」。"
EV = {"expected": "描述说读全部内容", "actual": "返回里没有 bugRefs"}


def _ok(**kw):
    d = {"title": "t" * 10, "body": BODY, "category": "improvement", "evidence": None}
    d.update(kw)
    return validate(d["title"], d["body"], d["category"], d["evidence"])


# ── 闸一：正文下限 ────────────────────────────────────────────────

def test_正文太短被拒且拒绝里带出路():
    bad = _ok(body="这个工具不好用")
    assert bad is not None
    # 只报「太短」是不够的 —— 那会让人凑字数了事。必须说清写什么。
    assert "howTo" in bad and "期望" in bad["howTo"]
    assert "why" in bad


def test_正文长度按去空白后算():
    """前后一堆空白凑不出长度 —— 否则闸一用一个回车就能绕过。"""
    assert _ok(body="太短" + " " * 200) is not None
    assert _ok(body="x" * MIN_BODY) is None


def test_正文上限也拦():
    assert _ok(body="x" * (MAX_BODY + 1)) is not None


# ── 闸二：bug 必须写清 expected / actual ──────────────────────────

@pytest.mark.parametrize("ev", [
    None, {}, {"expected": "a"}, {"actual": "b"},
    {"expected": "  ", "actual": "b"},          # 空白不算填了
])
def test_bug缺证据被拒(ev):
    bad = _ok(category="bug", evidence=ev)
    assert bad is not None
    assert "expected" in bad["error"] or "actual" in bad["error"]


def test_bug证据齐了就放行():
    assert _ok(category="bug", evidence=EV) is None


def test_非bug不强制证据():
    """improvement / requirement 常常没有「说好的 vs 实际」—— 强制它们填，
    只会逼出编造的证据，比没有更坏。"""
    for c in ("improvement", "requirement"):
        assert _ok(category=c, evidence=None) is None


# ── 分类 ──────────────────────────────────────────────────────────

def test_类别只认三个且拒绝里写明判据():
    bad = _ok(category="feature")
    assert bad is not None
    for c in CATEGORIES:
        assert c in bad["error"]
    # 判据必须给出来，否则 CC 只能猜；且要说明**报错类不扣分**，
    # 不然它会倾向于全报 bug（听起来最急）
    assert "静默失败" in bad["howTo"]
    assert "不扣分" in bad["howTo"]


def test_标题不能空且有长度上限():
    assert _ok(title="") is not None
    assert _ok(title="  ") is not None
    assert _ok(title="x" * 201) is not None
    assert _ok(title="x" * 200) is None


# ── 闸三：配额 ────────────────────────────────────────────────────

def test_配额必须高过一轮的真实量级():
    """2026-09-01 那份汇总一次 31 条，是认真写满一轮的量级。

    配额低于它，这道闸挡掉的**正好是它声称要放行的那种人** —— 一度写成 20，
    第 21 条起被弹回来。这条测试就是钉住这个数不许再往下调。
    """
    assert QUOTA_PER_DAY > 31


# ── 指纹：归并的地基 ──────────────────────────────────────────────

def test_指纹不含正文():
    """同一个坑第二次报，措辞几乎一定不一样。掺正文进去就永远并不上，
    归并等于没做 —— 而归并失效的表现是「同一件事 N 行」，处理方会直接放弃。"""
    a = fingerprint_of("lum_get_case", "读不回 bugRefs")
    assert a == fingerprint_of("lum_get_case", "读不回 bugRefs")
    # 正文不参与，所以这里根本没有正文参数可传 —— 签名本身就是这条约束的封样


@pytest.mark.parametrize("t", [
    "读不回 bugRefs", "读不回bugRefs", "读不回 BugRefs",
    "读不回 bugRefs。", "  读不回 bugRefs  ", "读不回-bugRefs",
])
def test_指纹对大小写标点空白不敏感(t):
    assert fingerprint_of("lum_get_case", t) == fingerprint_of("lum_get_case", "读不回 bugRefs")


def test_同工具的两个毛病不并成一条():
    assert fingerprint_of("lum_get_case", "读不回 bugRefs") != \
        fingerprint_of("lum_get_case", "读不回 tags")


def test_不同工具的同名毛病不并():
    """「静默失败」这种标题在好几个工具上都成立，只按标题并会把它们糊成一条。"""
    assert fingerprint_of("lum_get_case", "静默失败") != \
        fingerprint_of("lum_update_case", "静默失败")


def test_工具名缺失也能算出指纹():
    assert len(fingerprint_of(None, "某个毛病")) == 32


# ── 状态机不变量 ──────────────────────────────────────────────────

def test_待处理是还开着的子集():
    """页面默认筛 PENDING，归并往 OPEN 上并。前者若不是后者的子集，
    就会出现「页面上看不到、但新上报会并进去」的行 —— 它永远不会被处理。"""
    assert set(PENDING_STATUSES) <= set(OPEN_STATUSES) <= set(STATUSES)


def test_了结态和还开着的态不重叠():
    assert set(OPEN_STATUSES).isdisjoint({"done", "wont_fix", "duplicate"})


def test_每个状态和类别都有中文标签():
    """漏一个，页面上就会露出一个英文枚举值 —— 而这一页是给人读的。"""
    assert set(STATUS_LABEL) == set(STATUSES)
    assert set(CATEGORY_LABEL) == set(CATEGORIES)


def test_严重度只有平台填得了():
    """CC 自评会单调通胀（每个报的人都觉得自己那条最急），一轮后这列没区分度。
    所以上报工具根本不收这个参数 —— 这里钉住它不许被「顺手补上」。"""
    from app.mcp.tools import feedback as fb
    import inspect
    sig = inspect.signature(fb.report_feedback)
    assert "severity" not in sig.parameters
    assert set(SEVERITIES) == {"high", "medium", "low"}
