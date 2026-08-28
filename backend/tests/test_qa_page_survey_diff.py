"""两趟页面枚举的 diff 语义（S6.4）。

Test ID: qa-page-survey-diff-UT-001

这一整个文件在守同一条：**「这次没走到那个页面」不许写成「这个功能没了」。**
`removed` 是要给人看的缺口，多报一条就得有人去查一个不存在的东西 ——
查两次之后这份结论就没人信了。所以每条降级规则都配一条**反向锚点**：
真的删掉了必须照样报 `removed`，否则「一律 unknown」也能让全部测试变绿。
"""
import pytest

from app.services import qa_page_survey as d


def _item(page: str, anchor: str, **kw) -> dict:
    return {"key": f"{page}::{anchor}", "page_path": page, "anchor": anchor,
            "anchor_kind": "testid", "label": anchor, "control_type": "read",
            "state": "enabled", **kw}


class Test没看清的页面:
    def test_打不开的页面进不了_removed(self):
        before = [_item("/svc", "a"), _item("/env", "b")]
        after = [_item("/env", "b")]
        r = d.diff_items(before, after,
                         after_ledger={"pagesFailed": [{"path": "/svc", "error": "TimeoutError"}]})
        assert r["removed"] == []
        assert [x["key"] for x in r["unknown"]] == ["/svc::a"]
        assert r["unknown"][0]["reason"]

    def test_真删了照样报_removed(self):
        """反向锚点：降级规则不许把所有消失都吞成 unknown。"""
        before = [_item("/svc", "a"), _item("/svc", "b")]
        after = [_item("/svc", "a")]
        r = d.diff_items(before, after, after_ledger={"pagesVisited": 1})
        assert [x["key"] for x in r["removed"]] == ["/svc::b"]
        assert r["unknown"] == []

    def test_空状态页也算没看清(self):
        """列表页没数据时按钮成片消失 —— 那是「这会儿没数据」，不是「功能删了」。"""
        r = d.diff_items([_item("/svc", "a")], [],
                         after_ledger={"pagesEmptyState": ["/svc"]})
        assert r["removed"] == []
        assert len(r["unknown"]) == 1

    def test_上一趟没看清那页就不算新增(self):
        """上次没看到 ≠ 当时没有。反过来那一半同样会造假缺口 ——
        它会把一批老功能报成「这版新加的」，然后有人去给它们补用例。"""
        r = d.diff_items([], [_item("/svc", "a")],
                         before_ledger={"pagesFailed": [{"path": "/svc", "error": "Error"}]})
        assert r["added"] == []
        assert len(r["unknown"]) == 1

    def test_真新增照样报_added(self):
        r = d.diff_items([], [_item("/svc", "a")], before_ledger={"pagesVisited": 3})
        assert [x["key"] for x in r["added"]] == ["/svc::a"]

    def test_路径里带冒号空格也认得出来(self):
        """账本里的失败页记的是结构化的一条，不是 `f"{path}: {err}"`。

        拼成串再反解析的话，路径里本来就可能带 `": "`（查询串、带冒号的模块名），
        切一刀就切歪 —— 那一页于是不算失败页，它的 item 立刻变成 `removed`。
        **这条就是拿一个带冒号的路径去撞那个反解析。**
        """
        page = "/svc: 详情"
        r = d.diff_items([_item(page, "a")], [],
                         after_ledger={"pagesFailed": [{"path": page, "error": "TimeoutError"}]})
        assert r["removed"] == []
        assert len(r["unknown"]) == 1


class Test整趟不可比:
    def test_有分片死了就一条_removed_都不许报(self):
        """分片死掉时账本里**一页都没记**，逐页那条规则完全失效：
        上一趟的每一行都会变成 removed —— 一次网络抖动报出「整个域的功能全没了」。"""
        before = [_item("/svc", "a"), _item("/env", "b")]
        r = d.diff_items(before, [], after_ledger={"shardsFailed": ["TimeoutError"]})
        assert r["removed"] == []
        assert len(r["unknown"]) == 2

    def test_上一趟分片死了也不许报_added(self):
        after = [_item("/svc", "a")]
        r = d.diff_items([], after, before_ledger={"shardsFailed": ["Error"]})
        assert r["added"] == []
        assert len(r["unknown"]) == 1

    def test_账本没给也不敢乱报(self):
        """两个账本都没传时不许假装看清了 —— 但也不能一律 unknown，
        否则这个函数就没有输出了。缺账本 = 按看清了算，账本是调用方的责任。"""
        r = d.diff_items([_item("/svc", "a")], [])
        assert [x["key"] for x in r["removed"]] == ["/svc::a"]


class Test两趟稳定:
    """S6.4 的验收判据：同一个构建跑两趟，added / removed 都必须是空的。"""

    def test_同一批控件跑两趟没有增删(self):
        rows = [_item("/svc", "a"), _item("/svc", "b"), _item("/env", "c")]
        r = d.diff_items(rows, list(rows), after_ledger={}, before_ledger={})
        assert r["added"] == [] and r["removed"] == [] and r["unknown"] == []
        assert r["stable"] == 3

    def test_dom_顺序变了也不算增删(self):
        """锚点是 testid/id/文案，不是序号 —— 顺序变了 diff 必须一个字都不动。"""
        rows = [_item("/svc", "a"), _item("/svc", "b"), _item("/env", "c")]
        r = d.diff_items(rows, list(reversed(rows)))
        assert r["added"] == [] and r["removed"] == []
        assert r["stable"] == 3

    def test_同一个控件换了页面算两条(self):
        """key 带页面路径，所以「挪到另一页」是一删一增，不是没变 ——
        对账那边确实要看见它挪走了。"""
        r = d.diff_items([_item("/svc", "a")], [_item("/env", "a")])
        assert [x["key"] for x in r["removed"]] == ["/svc::a"]
        assert [x["key"] for x in r["added"]] == ["/env::a"]


class Test模块纪律:
    def test_diff_不许变成_async(self):
        """判定是纯函数、零 IO。

        一旦有人在 diff 里读一次库，它就得变成 `async def` —— 而这个文件里
        每一条规则测试都是同步直调的，那时候它们要么集体改写、要么被删掉。
        **这条不是形式检查**：上面那十几条降级规则是这个模块的全部价值，
        它们必须留在"不起 session 就能测"的那一边。
        （本文件其余每一条都在实证同一件事：全是同步调用、没有任何 fixture。）
        """
        import inspect
        assert not inspect.iscoroutinefunction(d.diff_items)
        assert not inspect.iscoroutinefunction(d.undiffable_pages)
        assert not inspect.iscoroutinefunction(d.is_wholly_unreliable)

    @pytest.mark.parametrize("led", [None, {}, {"pagesFailed": []}])
    def test_账本缺项不炸(self, led):
        assert d.undiffable_pages(led) == set()
        assert d.is_wholly_unreliable(led) is False

    # 「落库不许静默去重」不在这里测：扫源码找 `on_conflict` 是**假封样** ——
    # 这段文字里出现这个词就能把它满足。真判据是"撞了要抛"，那要真库，
    # 在根级 tests/integration/services/test_qa_page_survey_store.py。
