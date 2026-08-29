"""S7.5 每个域声明「页面维度适不适用」。

这条漏掉，新维度上线**第一天就废** —— 会系统性地报「这个域缺口巨大」，
其实只是那个域的功能在页面上本来就看不到。

但反方向更毒：`notApplicable` 是个**消音器**。判宽一格，那个域的真缺口
从此永远不出现，**而且永远不会红** —— 没有任何测试会因为"少报了一个缺口"而失败，
除非专门写。这一份大半在写那个"专门"。
"""
from app.services.qa_coverage_reconcile import (
    NOT_APPLICABLE,
    build_group_index,
    compute_gaps,
    page_applicability,
)

# 域码是**别人维护的**清单里的编码。判据长在「层」列上，不写死域码 ——
# 他加一个新的非 UI 域，我们这边要自动跟得上，而不是等它被误报成"缺口巨大"。
_SCENARIOS = [
    {"id": "GW-01", "domain": "GW", "tier": "gateway", "state": "gap"},
    {"id": "NFR-01", "domain": "NFR", "tier": "nfr", "state": "covered"},
    {"id": "PUB-01", "domain": "PUB", "tier": "api", "state": "covered"},
    {"id": "PUB-02", "domain": "PUB", "tier": "contract", "state": "gap"},
    {"id": "SEC-01", "domain": "SEC", "tier": "sec", "state": "gap"},
    {"id": "POL-01", "domain": "POL", "tier": "ui", "state": "gap"},
    {"id": "TEM-01", "domain": "TEM", "tier": "api", "state": "covered"},
    {"id": "TEM-02", "domain": "TEM", "tier": "e2e", "state": "gap"},
]


def _app(**kw):
    args = dict(scenarios=_SCENARIOS, page_domains=set(), page_survey_available=True)
    args.update(kw)
    return page_applicability(**args)


class Test不适用的四个域:
    def test_非UI层的域不产生假缺口(self):
        """AC 原文点名 `GW` / `NFR` / `PUB` / `SEC`。它们的共同点不是域码，
        是**清单自己的「层」列说了这些场景不在页面上**。"""
        by = _app()["byDomain"]
        assert by["GW"]["state"] == NOT_APPLICABLE
        assert by["NFR"]["state"] == NOT_APPLICABLE
        assert by["PUB"]["state"] == NOT_APPLICABLE
        assert by["SEC"]["state"] == NOT_APPLICABLE

    def test_理由要写清楚是凭什么判的(self):
        """「不适用」是个会让缺口消失的结论，它必须自带凭据 ——
        否则下次有人怀疑"这个域是不是被误消音了"，无从查起。"""
        assert "非 UI 层" in _app()["byDomain"]["GW"]["reason"]
        assert _app()["byDomain"]["PUB"]["tiers"] == ["api", "contract"]

    def test_有UI层场景的域是适用的(self):
        by = _app()["byDomain"]
        assert by["POL"]["state"] == "applicable"

    def test_混着写的域按适用算(self):
        """`TEM` 既有 `api` 又有 `e2e`。有一个 UI 层场景就说明它在页面上有面 ——
        **消音要全票，放行只要一票**，方向和「宁可漏报不可误报」是同一条。"""
        assert _app()["byDomain"]["TEM"]["state"] == "applicable"


class Test消音只认正面声明:
    def test_没观测到不等于不适用(self):
        """「这一轮没在页面上见到它」既可能是它真没有面、也可能是爬虫没跑到那几页 ——
        **数字上是同一个 0**。这条要是判成 `notApplicable`，
        一次爬得浅一点就能永久消掉一个域的所有缺口。"""
        got = page_applicability(
            scenarios=[{"id": "X-01", "domain": "X", "tier": "", "state": "gap"}],
            page_domains=set(), page_survey_available=True)
        assert got["byDomain"]["X"]["state"] == "unknown"

    def test_页面枚举没跑起来时全世界都是unknown(self):
        """一次爬虫失败就把所有域标成"不适用"，报告上一片「无缺口」——
        最毒的那种假绿。**没有页面枚举时，谁都不许被消音。**"""
        by = _app(page_survey_available=False)["byDomain"]
        assert {v["state"] for v in by.values()} == {"unknown"}

    def test_认不出来的层不消音(self):
        """清单是**别人维护**的。他加一个新层名 `chaos`，我们这边不认识 ——
        这时候必须是 `unknown`。判成 `notApplicable` 的话，
        他每加一个层名就悄悄静音一批域，而且没人会发现。"""
        got = page_applicability(
            scenarios=[{"id": "Y-01", "domain": "Y", "tier": "chaos", "state": "gap"}],
            page_domains=set(), page_survey_available=True)
        assert got["byDomain"]["Y"]["state"] == "unknown"

    def test_页面上真见到过就压过层列(self):
        """层列说 `api`，但爬虫在页面上真打到了这个域的端点 ——
        **观测到的事实压过清单的声明**。反过来不成立（见上面两条）：
        观测到 ⇒ 适用，是正面证据；没观测到 ⇒ 什么都推不出来。"""
        by = _app(page_domains={"PUB"})["byDomain"]
        assert by["PUB"]["state"] == "applicable"
        assert "真见到过" in by["PUB"]["reason"]

    def test_废弃的场景不参与判断(self):
        """`Z` 的 UI 场景**已经废弃**了，现在活着的只有 `api` —— 那个页面没了。
        把废弃行也算进来的话，一堆早就不做的 UI 场景会一直证明"这个域还有面"，
        于是它每一轮都被报一堆假缺口，正是「新维度第一天就废」那个死法。

        ⚠ 桩要写成**废弃的那条恰好会改变结论**，否则这条测试的名字承诺了什么，
        它的桩根本没走到（第一版就是 `api` 废弃 + `ui` 活着，两种实现同一个答案）。"""
        got = page_applicability(
            scenarios=[{"id": "Z-01", "domain": "Z", "tier": "ui", "state": "deprecated"},
                       {"id": "Z-02", "domain": "Z", "tier": "api", "state": "gap"}],
            page_domains=set(), page_survey_available=True)
        assert got["byDomain"]["Z"]["state"] == NOT_APPLICABLE


class Test不进分母也不给0分:
    def test_不适用的不进rollup分母(self):
        r = _app()["rollup"]
        assert sorted(r["notApplicable"]) == ["GW", "NFR", "PUB", "SEC"]
        assert r["denominator"] == 2          # POL + TEM

    def test_不适用的域一个个列出来而不是只给个数(self):
        """只给「4 个域不适用」这个数字的话，没法回答"是哪四个、凭什么" ——
        而这四个域的缺口从此不再出现在任何地方。"""
        assert isinstance(_app()["rollup"]["notApplicable"], list)
        assert "GW" in _app()["rollup"]["notApplicable"]

    def test_unknown既不消音也不算适用(self):
        """`unknown` 是**独立第三态**：不进 `notApplicable`（所以缺口照报），
        也要单独列出来（所以有人能去把清单的层列补上）。"""
        got = page_applicability(
            scenarios=[{"id": "X-01", "domain": "X", "tier": "", "state": "gap"}],
            page_domains=set(), page_survey_available=True)
        assert got["rollup"]["unknown"] == ["X"]
        assert got["rollup"]["notApplicable"] == []
        assert got["rollup"]["denominator"] == 1

    def test_一个域都没有时三个数照样渲染(self):
        """只在非 0 时出现的计数，跟「没算过」长得一模一样。"""
        r = page_applicability(scenarios=[], page_domains=set())["rollup"]
        assert r == {"denominator": 0, "notApplicable": [], "unknown": []}


class Test跟compute_gaps接上:
    def test_页面上见过的域带得出来(self):
        idx = build_group_index({"POL": {"name": "策略", "groups": ["Policies"],
                                         "groupsRaw": "Policies"}})
        g = compute_gaps(
            page_items=[{"page_path": "/p", "anchor": "a", "label": "驳回",
                         "control_type": "button", "state": "enabled",
                         "endpoints": [{"source": "observed", "method": "POST", "path": "/api/policies/1/reject"}]}],
            routes=[{"group": "Policies", "method": "POST",
                     "path": "/api/policies/{}/reject"}],
            scripts=[], index=idx, claimed_domains=set())
        assert g["pageDomains"] == ["POL"]

    def test_测到了的域也算在页面上见过(self):
        """脚本已经测过 ⇒ 它不是缺口，但「这个域在页面上有面」照样成立，
        而且是**最有力的正面证据**。漏掉这一支的话，
        一个测得最好的域反而会被消音 —— 它的新缺口从此不报。"""
        idx = build_group_index({"POL": {"name": "策略", "groups": ["Policies"],
                                         "groupsRaw": "Policies"}})
        g = compute_gaps(
            page_items=[{"page_path": "/p", "anchor": "a", "label": "驳回",
                         "control_type": "button", "state": "enabled",
                         "endpoints": [{"source": "observed", "method": "POST", "path": "/api/policies/1/reject"}]}],
            routes=[{"group": "Policies", "method": "POST",
                     "path": "/api/policies/{}/reject"}],
            scripts=[{"domain": "POL", "scenarioId": "POL-01",
                      "text": 'curl -X POST "$API/api/policies/1/reject"'}],
            index=idx, claimed_domains={"POL"})
        assert g["g1"] == [] and g["g3"] == []
        assert g["pageDomains"] == ["POL"]
