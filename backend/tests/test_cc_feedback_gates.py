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


# ── AI 自己落裁定：判据从哪来、翻案怎么翻 ─────────────────────────
#
# 2026-09-01 口径反转（原来是「AI 只出建议，处置只有人能落」）。反转的前提是
# **把不可逆性拆掉**，不是把守卫拆掉 —— 下面这几条钉的就是那个前提。

def test_四个裁定里没有已处理():
    """AI 落不了 done —— done 的含义是**代码改完了**，而它没改过代码。
    这不是「不给它权限」，是它在事实上判不了这件事。"""
    from app.services.cc_feedback_service import _HANDLE_PROMPT
    assert "needs_human" in _HANDLE_PROMPT
    assert "不要输出 `done`" in _HANDLE_PROMPT


def test_判据里必须有工具的契约和实现():
    """「AI 判得了的自己判」只有在它真看得见判据时才成立。

    这一类反馈的判据恰好只有三样：工具**说**它做什么（描述）、它**实际**做什么
    （源码）、以及平台**有没有别的工具**能干这件事（全表）—— 最后那样是判
    「他没找对方法」这一类的唯一依据。少任何一样，AI 就只能凭反馈正文本身猜，
    而正文正是报的人自己的理解。
    """
    from app.services.cc_feedback_service import _platform_facts

    facts = _platform_facts("lum_report_feedback")
    assert "lum_report_feedback" in facts
    assert "def " in facts                       # 源码进去了
    assert ".py:" in facts                       # 而且指到了文件:行
    assert facts.count("lum_") > 30              # 全表在（60+ 个工具）


def test_不认识的工具名要说出来而不是装作没这回事():
    """CC 可能写错工具名，也可能报的是页面上的毛病（没有工具名）。
    这时候 AI 该知道「查不到这个工具」，否则它会拿全表去硬凑一个结论。"""
    from app.services.cc_feedback_service import _platform_facts

    facts = _platform_facts("lum_不存在的工具")
    assert "lum_不存在的工具" in facts
    assert "没有" in facts or "查不到" in facts or "不在" in facts


def test_翻案的判据是有新东西不是又说了一遍():
    """AI 判的 wont_fix 能翻案，靠的是「正文变了」。这个归一化函数就是那道判据：
    松了（比如只比长度）→ 复读也能翻案，短路等于没有；
    紧了（比如全字节相等）→ 多一个空格就算新证据，同样等于没有。
    """
    from app.services.cc_feedback_service import _body_key

    a = "调 lum_get_case 想读回 bugRefs，返回里没有这个字段。"
    assert _body_key(a) == _body_key("  调 lum_get_case 想读回 bugRefs； 返回里没有这个字段  ")
    assert _body_key(a) != _body_key(a + "补一句：批量场景下也一样。")


def test_抽检是取模不是掷骰子():
    """掷骰子会出现连续二十条一条没抽到的走运区间，而抽检的全部意义就是
    **稳定的覆盖率** —— 用来校准 AI 判得准不准，样本时有时无就校准不了。"""
    import inspect

    from app.services.cc_feedback_service import _sample_this_wont_fix
    from app.models.cc_feedback import WONT_FIX_SAMPLE_EVERY

    src = inspect.getsource(_sample_this_wont_fix)
    assert "%" in src
    assert "random" not in src
    assert WONT_FIX_SAMPLE_EVERY >= 2


def test_谁判的只有三种而且默认是ai():
    """这一列不是装饰：它决定这条 wont_fix 还能不能被翻案。
    人判的终局、AI 判的可翻 —— 存不下「谁判的」，这个区别就无处安放。"""
    from app.models.cc_feedback import DECIDERS

    assert set(DECIDERS) == {"ai", "human", "system"}


def test_自动分诊默认开着():
    """默认关掉的话，这套东西退化成「人还是得一条条点」—— 而这次改的就是那件事。
    这个开关只为测试和批量导入留（真打模型会在没网关的机器上偶发红、
    也会在 asyncio.run() 收尾时把判到一半的后台任务全毁掉）。

    ⚠ 不用 importlib.reload 来验默认值：重载会把模块里的 `_BATCH` 和 `_BG`
    换成新对象，同一进程里别的测试拿着旧引用，就变成「批量跑了但状态查不到」。
    这里直接看它读的那个环境变量的默认值。
    """
    import inspect

    import app.services.cc_feedback_service as svc

    src = inspect.getsource(svc)
    line = next(ln for ln in src.splitlines() if ln.startswith("AUTO_TRIAGE"))
    assert "CC_FEEDBACK_AUTO_TRIAGE" in line
    assert '"1"' in line or "'1'" in line          # 默认值是 1


# ── 范围（area = 坏掉的是哪一块子系统）─────────────────────────────
#
# 这一列的三个坑全属于「写错了不报错」那一类：指纹掺了它 → 归并和 wont_fix
# 短路一起失效，而表现是「反馈变多了」，看着完全正常；NULL 塞成 other → AI
# 那一层再也不会碰它们（它只填空的），一次性回填把数据钉死在错值上。

def test_域不进指纹():
    """指纹只有 (tool_name, 归一化标题) 两样。掺 area 的后果有两个，
    而且都不报错：① 同一件事改了域之后变成两行（归并失效）；
    ② **wont_fix 短路失效** —— 那是这条通道最要紧的行为。

    签名本身就是封样：fingerprint_of 根本没有可以传域的位置。
    """
    import inspect

    from app.services.cc_feedback_service import fingerprint_of

    assert set(inspect.signature(fingerprint_of).parameters) == {"tool_name", "title"}
    src = inspect.getsource(fingerprint_of)
    assert "area" not in src


def test_域是可空的而且NULL不等于其它():
    """NULL = 还没判过；other = 判过了、确实归不进任何一档。
    合成一个值，「没判」就永久伪装成「判过了没归属」，而这一列的价值全在能筛。"""
    from app.models.cc_feedback import AREAS, CCFeedback

    assert "other" in AREAS
    col = CCFeedback.__table__.c.area
    assert col.nullable is True
    assert col.default is None and col.server_default is None   # 没有默认值可落
    assert col.index is True                                    # 页面要按它筛 + 出计数


def test_每个域都有中文标签():
    """漏一个，页面上就露出一个英文枚举值 —— 而这一页是给人读的。"""
    from app.models.cc_feedback import AREA_LABEL, AREAS

    assert set(AREA_LABEL) == set(AREAS)
    assert all(AREA_LABEL[a] for a in AREAS)


def test_默认域只认注册工具名不做关键词猜测():
    """「AI 评审规则文案」靠关键词猜得中，「执行结果状态」猜不中 ——
    而猜错的那半没有任何地方会报错。所以这一层只做精确查表。"""
    import inspect

    from app.services.cc_feedback_service import _TOOL_AREA, area_for_tool

    assert area_for_tool("lum_review_case") == "ai_review"
    assert area_for_tool("lum_不存在的工具") is None
    assert area_for_tool("AI 评审规则文案") is None      # 自由文本一律不猜
    assert area_for_tool(None) is None
    assert area_for_tool("  lum_review_case  ") == "ai_review"

    src = inspect.getsource(area_for_tool)
    assert "in " not in src.split("return")[-1]          # 没有子串匹配
    assert len(_TOOL_AREA) > 40


def test_默认域那张表里全是真工具名和合法域():
    """表里写错一个工具名，那个工具报上来的反馈就永远落不到域上 ——
    而「落不到域」和「还没判域」在库里长得一模一样，查不出来。"""
    from app.mcp import TOOL_CATALOG
    from app.models.cc_feedback import AREAS
    from app.services.cc_feedback_service import _TOOL_AREA

    names = {t["name"] for t in TOOL_CATALOG}
    assert names, "TOOL_CATALOG 空了，这条测试就变成恒真"
    assert not (set(_TOOL_AREA) - names), set(_TOOL_AREA) - names
    assert not (set(_TOOL_AREA.values()) - set(AREAS))


def test_上报工具收域但不强制():
    """填了当默认、不填交给后面两层。**收了这个参数不等于要求它填** ——
    要求填的话，CC 就得先学会这 14 档才能报第一条反馈。"""
    import inspect

    from app.mcp.tools import feedback as fb

    prm = inspect.signature(fb.report_feedback).parameters
    assert "area" in prm
    assert prm["area"].default is None


def test_域出现在提示词和回音里():
    """AI 那一层要判它，就得先在 schema 里有它；CC 那边要按块看自己报了些什么，
    回音里就得带上。少任何一头，这一列都只有页面自己看得见。"""
    from app.services.cc_feedback_service import _HANDLE_PROMPT, _echo_of
    import inspect

    assert '"area"' in _HANDLE_PROMPT
    assert "{areas}" in _HANDLE_PROMPT
    assert "area" in inspect.getsource(_echo_of)


def test_AI只填空的不盖人判过的():
    """人在抽屉里改过域之后，下一次点「AI 处理」不许把它悄悄改回去 ——
    这也是「回填留 NULL 别塞 other」那条规则的另一半：塞了 other，
    这个 `is None` 就永远不成立，AI 这一层等于没接上。"""
    import inspect

    from app.services.cc_feedback_service import ai_handle

    src = inspect.getsource(ai_handle)
    assert "row.area is None" in src


def test_人改域时非法值当场拒而不是悄悄修():
    """页面上是个下拉，出不了非法值 —— 出了就说明是脚本在调，
    而悄悄纠正会让那个脚本一直错下去（下一个版本加了新档它照样填错）。"""
    import inspect

    from app.services.cc_feedback_service import triage

    src = inspect.getsource(triage)
    assert "area not in AREAS" in src
