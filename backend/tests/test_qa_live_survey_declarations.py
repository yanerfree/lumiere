"""页面渲染的那一份声明，得把**四路**都收进来。

四路里流量那一路（`merge_edges`）是 2026-09-04 才接上的：它一直在产声明，
但只落在 `ledger["traffic"]["declarations"]`，而页面渲染的是这里合出来的那一份 ——
**没人看得见的声明和没有声明是一回事**。它偏偏又是最该被看见的那一路：
登录没成的那一趟，分片、页数、边数全是绿的，只有流量这边看得出不对。
"""
from app.services.qa_live_survey import reconcile


def _q(decl=()):
    return {"scripts": [], "index": {"byDomain": {}, "unresolved": []},
            "claimedDomains": [], "helperLib": {}, "scenarios": [],
            "declarations": list(decl)}


def _run(*, traffic_decl=(), q_decl=(), plan_decl=()):
    return reconcile(
        plan={"routeTable": None, "buildFingerprint": "bf",
              "declarations": list(plan_decl)},
        ledger={"controlsClicked": 0,
                "traffic": {"declarations": list(traffic_decl)}},
        items=[], page_edges=[], q=_q(q_decl), page_survey_available=True)


class Test流量那一路的声明要能被看见:
    def test_流量的声明进得了页面渲染的那一份(self):
        out = _run(traffic_decl=["82/232 条 P 边是 401/403 —— 这一趟多半根本没登进去"])
        assert any("没登进去" in d for d in out["declarations"])

    def test_四路都在_而且按序(self):
        out = _run(plan_decl=["计划"], q_decl=["Q 侧"], traffic_decl=["流量"])
        head = [d for d in out["declarations"] if d in ("计划", "Q 侧", "流量")]
        assert head == ["计划", "Q 侧", "流量"]      # 对账那一路排在后面

    def test_同一句话在两路都出现只显示一次(self):
        dup = "本轮无路由表，G2 未验证"                # 对账那一路一定会说这句
        out = _run(traffic_decl=[dup])
        assert out["declarations"].count(dup) == 1

    def test_没有流量那一格也不炸(self):
        """老 survey 的账本里根本没有 `traffic` 键。"""
        out = reconcile(
            plan={"routeTable": None, "buildFingerprint": "bf", "declarations": []},
            ledger={"controlsClicked": 0}, items=[], page_edges=[],
            q=_q(), page_survey_available=True)
        assert out["declarations"]                    # 对账自己那几句还在
