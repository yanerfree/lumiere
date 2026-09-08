"""QA 仓公共选择器表 → 活体命中报告。

这一份盯的**不是**「数得对不对」（那是 `querySelectorAll` 的事），而是四件
会让整份报告失去信用的事：

  1. **认不出的形状不许猜。** 参数化选择器拿前缀去探、`:visible` 摘掉再探 ——
     探的都是**另一个**选择器，而它的命中数在报告上跟真的长得一模一样。
  2. **「这一趟没见到」不等于「过期」。** 无向枚举一个控件都不点，弹窗里的控件
     结构上不可能出现。这一档必须带着构造性理由一起报，否则他会去改一批
     本来是对的选择器。
  3. **「压根没探」和「探了都没见到」必须分得开。** 前者是我们自己没跑成，
     后者才是关于他选择器的事实。
  4. **判重是他自己门禁的活。** 这里只认出 last-wins，不产出「该改哪一行」——
     两套判重迟早给出不一样的数，那时候没人知道该信哪个。
"""
import inspect

import pytest

from app.services.qa_selectors import (
    PROBE_JS,
    VERDICTS,
    merge_probe,
    parse_selectors,
    probe_payload,
    roll_up,
)

# 照抄他那份表的真实形状（1940 行里出现过的**全部**五种值写法）：
# 单行串、参数化箭头、Playwright 专有语法、`key:` 换行再接串、以及一条我们
# 认不出的。最后这条是**故意**的 —— fail-closed 的那一路必须有样本。
_TS = """
// 顶上的说明段：不是键
/* 块注释也不是 */
export const sel = {
  common: {
    header: '[data-testid="hdr"]',
    row: (id: string) => `[data-testid="row-${id}"]`,
    openOptions: '[role="option"]:visible',   // :visible 把 8 变成 4
    trendLegendActive:
      '[data-testid="trend-legend"] .active',
    fallbackList: ['a', 'b'],
    broken: '[data-testid=',
  },
  teams: {
    totalBadge: '[data-testid="teams-total"]',
  },
} as const

export const routes = {
  teams: '/teams',
  teamDetail: (id: string) => `/teams/${id}`,
} as const
"""


def _entry(parsed, key):
    return next(e for e in parsed["entries"] if e["key"] == key)


class Test五种值写法各归各档:
    def test_单行串可探(self):
        p = parse_selectors(_TS)
        e = _entry(p, "sel.common.header")
        assert e["selector"] == '[data-testid="hdr"]'
        assert e["probe"] is True and e["skipReason"] is None

    def test_参数化不探(self):
        """`[data-testid="row-${id}"]` 拿前缀去探，命中 0 到底是「改名了」
        还是「列表是空的」分不开 —— 两种结论一个要改选择器、一个要造数据。"""
        p = parse_selectors(_TS)
        e = _entry(p, "sel.common.row")
        assert e["probe"] is False and e["skipReason"] == "parameterized"
        assert e["selector"] is None

    def test_Playwright专有语法不探而且原文一个字不改(self):
        """**不许"顺手翻译"**：摘掉 `:visible` 之后探的是另一条选择器。
        他自己那条注释就记着 `:visible` 让命中数从 8 变 4。"""
        p = parse_selectors(_TS)
        e = _entry(p, "sel.common.openOptions")
        assert e["probe"] is False and e["skipReason"] == "playwrightOnly"
        assert e["selector"] == '[role="option"]:visible'      # 原文留着

    def test_换行接串的那条也要认(self):
        """他表里正好有一条（`dashboard.trendLegendActive`）是这个形状。
        不认的话它落进 `unparsed` —— 一条本来能探的选择器被算成"我们读不懂"。"""
        p = parse_selectors(_TS)
        e = _entry(p, "sel.common.trendLegendActive")
        assert e["selector"] == '[data-testid="trend-legend"] .active'
        assert e["probe"] is True

    def test_认不出的记进unparsed而不是猜(self):
        p = parse_selectors(_TS)
        assert p["unparsedKeys"] == ["sel.common.fallbackList"]
        assert p["counters"]["unparsed"] == 1
        assert not any(e["key"].endswith("fallbackList") for e in p["entries"])
        assert any("认不出就不猜" in d for d in p["declarations"])

    def test_语法坏了的串照样进探测清单(self):
        """`'[data-testid='` 是个合法的 JS 字符串、非法的 CSS。
        它必须**探**才能拿到 `invalid` —— 静态判「这条 CSS 合不合法」等于
        自己写一个 CSS 解析器，而浏览器手边就有一个。"""
        p = parse_selectors(_TS)
        assert _entry(p, "sel.common.broken")["probe"] is True
        assert "sel.common.broken" in [x["key"] for x in probe_payload(p)]

    def test_计数对得上(self):
        c = parse_selectors(_TS)["counters"]
        assert c == {"keys": 6, "probeable": 4, "parameterized": 1,
                     "playwrightOnly": 1, "routes": 1, "routeTemplates": 1,
                     "duplicateKeys": 0, "unparsed": 1}


class Test顶层命名空间也是一层:
    def test_sel_teams和routes_teams不是同一个键(self):
        """他那边第一版没把 `export const sel = {` 当一层，当场报出
        19 处「重复」而**一处都不是真的**。我们的键还要拿给人 grep,
        串了台就是指错行。"""
        p = parse_selectors(_TS)
        assert p["duplicateKeys"] == {}
        assert "sel.teams.totalBadge" in [e["key"] for e in p["entries"]]
        assert p["routes"] == {"teams": "/teams"}

    def test_routes进不了选择器清单(self):
        """`/teams` 是个 URL，不是 CSS。混进去就是 30 条必然「没见到」的噪声。"""
        p = parse_selectors(_TS)
        assert not any(e["key"].startswith("routes.") for e in p["entries"])
        assert p["routeTemplates"] == ["routes.teamDetail"]


class Test重复键按last_wins但不出结论:
    _DUP = """
export const sel = {
  t: {
    badge: '[data-testid="old"]',
    other: '.x',
    badge: '[data-testid="new"]',
  },
} as const
"""

    def test_探的是后面那个值(self):
        """`ui/` 没有 typecheck，重复键**静默 last-wins**。探前面那个等于
        探一条运行时根本没人用的选择器。"""
        p = parse_selectors(self._DUP)
        assert _entry(p, "sel.t.badge")["selector"] == '[data-testid="new"]'
        assert p["counters"]["keys"] == 2                  # 键只算一次

    def test_只声明不给整改建议(self):
        """判重是 `scripts/check-selectors-integrity.sh` ① 的活，而且它判得更细
        （还答「合并只是新增键还是改了既有键」）。这里再判一遍，两套数迟早不一样。"""
        p = parse_selectors(self._DUP)
        assert p["counters"]["duplicateKeys"] == 1
        d = " ".join(p["declarations"])
        assert "last-wins" in d and "check-selectors-integrity" in d


class Test探测清单是稳定的:
    def test_按键排死(self):
        """同一份表探两次必须给出同样的顺序 —— 不然两趟的产物没法 diff。"""
        p = parse_selectors(_TS)
        keys = [x["key"] for x in probe_payload(p)]
        assert keys == sorted(keys)

    def test_只带可探的(self):
        """参数化那 97 条和 Playwright 专有那条**一条都不许进** ——
        进去就得在浏览器里探一个我们没打算探的东西。"""
        p = parse_selectors(_TS)
        assert [x["key"] for x in probe_payload(p)] == [
            "sel.common.broken",
            "sel.common.header",
            "sel.common.trendLegendActive",
            "sel.teams.totalBadge",
        ]
        assert all(x["css"] for x in probe_payload(p))


class Test账本只留非0但每一页都要记:
    def test_0不入账(self):
        """445 × 40 页的 0 存下来是几万行，而它一个字的信息都没有 ——
        「这一页没见到」可以由「探过的页面清单」减出来。"""
        acc = {}
        merge_probe(acc, "/teams", {"a": 0, "b": 2})
        assert acc["hits"] == {"b": {"/teams": 2}}

    def test_一条都没命中的页也要记进pages(self):
        """少了这一笔，「探了、这页什么都没有」和「这页压根没探」在产物上
        一模一样 —— 而后者不是关于他选择器的事实，是关于我们这一趟的事实。"""
        acc = {}
        merge_probe(acc, "/empty", {"a": 0})
        assert acc["pages"] == ["/empty"] and acc["hits"] == {}

    def test_同一页探两次不会重复记(self):
        acc = {}
        merge_probe(acc, "/teams", {"a": 1})
        merge_probe(acc, "/teams", {"a": 1})
        assert acc["pages"] == ["/teams"]

    def test_数不出来的值跳过而不是当成0(self):
        """浏览器那头返回 `null`/字符串只可能是我们自己的协议坏了。
        当成 0 会把它并进「没见到」，那一档本来就不结论性，再掺进假数据
        就彻底没法用了。"""
        acc = {}
        merge_probe(acc, "/p", {"a": None, "b": "x", "c": 3})
        assert acc["hits"] == {"c": {"/p": 3}}

    def test_负一是语法坏了要留着(self):
        acc = {}
        merge_probe(acc, "/p", {"a": -1})
        assert acc["hits"] == {"a": {"/p": -1}}


def _parsed_simple():
    return parse_selectors("""
export const sel = {
  g: {
    one: '.one',
    many: '.many',
    bad: '.bad',
    unseen: '.unseen',
  },
} as const
""")


class Test四档的优先级:
    def _rep(self, hits_by_page):
        acc = {}
        for page, res in hits_by_page.items():
            merge_probe(acc, page, res)
        return roll_up(_parsed_simple(), acc)

    def test_命中1和命中多个(self):
        rep = self._rep({"/a": {"sel.g.one": 1, "sel.g.many": 3}})
        assert rep["buckets"]["hitOne"] == ["sel.g.one"]
        assert rep["buckets"]["hitMany"] == ["sel.g.many"]

    def test_命中多个压过命中1(self):
        """一个键在 A 页命中 1、在 B 页命中 3，要报的是 B 页那件事 ——
        `.first()` 抓哪个由 DOM 顺序说，不由脚本说。"""
        rep = self._rep({"/a": {"sel.g.many": 1}, "/b": {"sel.g.many": 3}})
        assert rep["buckets"]["hitMany"] == ["sel.g.many"]
        row = next(r for r in rep["rows"] if r["key"] == "sel.g.many")
        assert row["pages"] == {"/a": 1, "/b": 3} and row["maxCount"] == 3

    def test_语法坏了压过一切(self):
        """`invalid` 是反面里唯一**结论性**的那档：任何 spec 用到它都会当场炸。
        它要是被「在别的页命中过 1」盖住，就永远没人去修那一行。"""
        rep = self._rep({"/a": {"sel.g.bad": 1}, "/b": {"sel.g.bad": -1}})
        assert rep["buckets"]["invalid"] == ["sel.g.bad"]
        assert rep["buckets"]["hitOne"] == []

    def test_没命中的落没见到而不是过期(self):
        rep = self._rep({"/a": {"sel.g.one": 1}})
        assert "sel.g.unseen" in rep["buckets"]["notSeen"]
        assert set(rep["verdictNames"]) == set(VERDICTS)
        assert "过期" not in rep["verdictNames"]["notSeen"]

    def test_每条都带在哪一页命中几个(self):
        """不带这个他没法复查 —— 一份不能复查的报告只能被整份信或整份不信。"""
        rep = self._rep({"/teams": {"sel.g.one": 1}})
        row = next(r for r in rep["rows"] if r["key"] == "sel.g.one")
        assert row["pages"] == {"/teams": 1}
        assert row["line"] > 0 and row["selector"] == ".one"


class Test没见到必须带着构造性理由:
    def test_声明里写清为什么0不结论(self):
        acc = {}
        merge_probe(acc, "/teams", {"sel.g.one": 1})
        rep = roll_up(_parsed_simple(), acc)
        d = " ".join(rep["declarations"])
        assert "不等于" in d and "过期" in d
        assert "一个控件都不点" in d           # 构造性理由，不是"可能"
        assert "/teams" in d                   # 只探了哪些页，一起给

    def test_一条没见到都没有就不叠那条声明(self):
        """声明一多读的人就不读了。没有这一档时那句话是纯噪声。"""
        acc = {}
        for k in ("one", "many", "bad", "unseen"):
            merge_probe(acc, "/all", {"sel.g." + k: 1})
        rep = roll_up(_parsed_simple(), acc)
        assert rep["buckets"]["notSeen"] == []
        assert not any("不等于" in d for d in rep["declarations"])


class Test压根没探和探了没见到要分得开:
    def test_没探过整张表落探不了(self):
        """「探了、445 条全没见到」是 445 条待查，「压根没探」是我们自己没跑成。
        产物长一样的话等于让人去查一批不存在的问题。"""
        rep = roll_up(_parsed_simple(), None)
        assert rep["counters"]["notProbed"] == 4
        assert rep["counters"]["notSeen"] == 0
        assert any("一条都不能当成" in d for d in rep["declarations"])
        assert rep["pagesProbed"] == []

    def test_显式说没探就压过账本里的命中(self):
        """`probed=False` 是调用方说"这一趟没跑起来"。这时候账本里若还有命中，
        那是上一趟留下的 —— 按它出报告就是拿旧数据充当本趟结论。"""
        acc = {}
        merge_probe(acc, "/teams", {"sel.g.one": 1})
        rep = roll_up(_parsed_simple(), acc, probed=False)
        assert rep["counters"]["notProbed"] == 4
        assert rep["buckets"]["hitOne"] == []

    def test_探不了的两种原因各有各的话(self):
        rep = roll_up(parse_selectors(_TS), None, probed=True)
        why = {r["key"]: r.get("why", "") for r in rep["rows"]}
        assert "列表是空的" in why["sel.common.row"]            # 参数化
        assert "querySelectorAll" in why["sel.common.openOptions"]
        assert why["sel.common.row"] != why["sel.common.openOptions"]


class Test表和探测不同源要当场报出来:
    def test_探到的键在表里找不到就说话(self):
        """正常情况必须是 0 —— 清单和探测同源。不是 0 只有一种解释：
        **报告用的表比探的那趟新**（中间取过一次），于是这份报告说的是
        另一个版本的选择器表，里面每条「没见到」都可能只是键改了名。"""
        acc = {}
        merge_probe(acc, "/p", {"sel.g.one": 1, "sel.gone.away": 2})
        rep = roll_up(_parsed_simple(), acc)
        assert rep["counters"]["hitsForUnknownKeys"] == 1
        d = " ".join(rep["declarations"])
        assert "不是同一个版本" in d and "sel.gone.away" in d

    def test_同源时这条声明不出现(self):
        acc = {}
        merge_probe(acc, "/p", {"sel.g.one": 1})
        rep = roll_up(_parsed_simple(), acc)
        assert rep["counters"]["hitsForUnknownKeys"] == 0
        assert not any("不是同一个版本" in d for d in rep["declarations"])


class Test探测那段JS只查不点:
    def test_不含任何会改DOM的调用(self):
        """它是**唯一**在被测页面里执行的自研代码。一旦它点了什么，
        「无向枚举不点控件」那条设计就破了，而破在一个没人会去看的字符串里。"""
        for bad in (".click(", ".focus(", "dispatchEvent", "innerHTML",
                    "remove()", "setAttribute"):
            assert bad not in PROBE_JS
        assert "querySelectorAll" in PROBE_JS

    def test_坏选择器回负一而不是漏掉这条(self):
        """漏掉的话它和「没见到」混在一起 —— 而「语法坏了」是结论性的、
        「没见到」不是。"""
        assert "catch" in PROBE_JS and "-1" in PROBE_JS


class Test爬取那边探的清单和这边收的参数是一对:
    def test_爬取把selectorsProbed明写出来(self):
        """`0` 也要写：清单是空的（QA 仓没拉到 / 解析全军覆没）和"探了但一条都没
        命中"在报告上长得一模一样，而前者是我们自己没跑成。键名和参数是一对。"""
        from app.engine.surveys import qa_page_survey_crawl as crawl
        assert '"selectorsProbed"' in inspect.getsource(crawl.run_survey)
        assert "selector_probe" in inspect.signature(crawl.run_survey).parameters
        assert "selector_probe" in inspect.signature(crawl.crawl_role).parameters

    def test_爬取用的就是这里那段JS(self):
        """浏览器里跑的那段和这里封样的那段必须是同一个对象 ——
        各自维护一份的话，这些测试盯的是一段线上没在用的字符串。"""
        from app.engine.surveys import qa_page_survey_crawl as crawl
        assert crawl.PROBE_JS is PROBE_JS

    @pytest.mark.asyncio
    async def test_探测挂了只记账不抛(self):
        """抛出去会把整页（连带控件账和时窗）一起废掉 —— 拿一个附加产出
        换掉主产出。"""
        from app.engine.surveys import qa_page_survey_crawl as crawl

        class _Boom:
            async def evaluate(self, js, arg):
                raise RuntimeError("detached")

        ledger = {}
        await crawl._probe_selectors(_Boom(), "/teams", [{"key": "a", "css": ".a"}],
                                     ledger)
        assert ledger["selectorProbeFailed"] == [{"path": "/teams",
                                                  "error": "RuntimeError"}]
        assert "selectorProbe" not in ledger

    @pytest.mark.asyncio
    async def test_清单为空时一个evaluate都不发(self):
        """没拉到选择器表的那一趟，别在每一页上白发一次 evaluate。"""
        from app.engine.surveys import qa_page_survey_crawl as crawl

        class _Never:
            async def evaluate(self, js, arg):
                raise AssertionError("不该被调用")

        ledger = {}
        await crawl._probe_selectors(_Never(), "/teams", None, ledger)
        assert ledger == {}

    @pytest.mark.asyncio
    async def test_命中并进账本按页分格(self):
        from app.engine.surveys import qa_page_survey_crawl as crawl

        class _Page:
            async def evaluate(self, js, arg):
                return {it["key"]: 1 for it in arg}

        ledger = {}
        payload = [{"key": "sel.g.one", "css": ".one"}]
        await crawl._probe_selectors(_Page(), "/teams", payload, ledger)
        await crawl._probe_selectors(_Page(), "/users", payload, ledger)
        assert ledger["selectorProbe"]["pages"] == ["/teams", "/users"]
        assert ledger["selectorProbe"]["hits"] == {
            "sel.g.one": {"/teams": 1, "/users": 1}}


class Test值形状认得住而且不许猜:
    """`selectors.ts` 几乎每条分支都会被改（他自己实测四个 MR 里三个改了它），
    所以"今天的文件里没有这种写法"不构成不处理的理由 —— 处理的判据是
    **认错了会不会产出假事实**。"""

    def test_值后面跟行尾注释也认(self):
        """今天他表里一条都没有（实测 0 行）。哪天补上一句，这条本来能探的
        选择器会被记成"我们读不懂" —— 少一条覆盖，而且看着像他的表有问题。"""
        p = parse_selectors(
            "export const sel = {\n"
            "  g: {\n"
            "    a: '.a',   // 说明一句\n"
            "  },\n"
            "} as const\n")
        assert _entry(p, "sel.g.a")["selector"] == ".a"
        assert p["counters"]["unparsed"] == 0

    def test_键后面跟注释值在下一行也认(self):
        p = parse_selectors(
            "export const sel = {\n"
            "  g: {\n"
            "    a:   // 这里换行是因为下面那串太长\n"
            "      '.a',\n"
            "  },\n"
            "} as const\n")
        assert _entry(p, "sel.g.a")["selector"] == ".a"

    def test_拼起来的值不许读成一整串(self):
        """`'a' + 'b'` 用贪心正则会读成一整串 `a' + 'b`，然后被当成正常选择器
        拿去探、稳定命中 0 —— **一条假的「没见到」**。这个模块存在的全部理由
        就是不产出假事实，所以它必须落 `unparsed`（报出来，但不猜）。"""
        p = parse_selectors(
            "export const sel = {\n"
            "  g: {\n"
            "    a: '.a' + '.b',\n"
            "  },\n"
            "} as const\n")
        assert p["unparsedKeys"] == ["sel.g.a"]
        assert probe_payload(p) == []

    def test_自带模板占位的常量串也算参数化(self):
        """**按值的形状判，不按写法判。** 哪天有人把 `(id) => ...` 改写成一条
        带占位的常量串，写法变了、"要运行时的值才拼得出来"这件事没变。"""
        p = parse_selectors(
            "export const sel = {\n"
            "  g: {\n"
            "    a: `[data-testid=\"row-DOLLARid}\"`,\n"
            "  },\n"
            "} as const\n".replace("DOLLAR", "${"))
        e = _entry(p, "sel.g.a")
        assert e["probe"] is False and e["skipReason"] == "parameterized"
