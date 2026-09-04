"""§14 功能地图 + §15 广度/深度的封样。

这份测试盯的是**判据的通用性**和**「没量到」不许被洗成「没有」**。会红的典型改动：
给 `classify_hint` 塞产品名词、把三种「没看到」合成一个「未测」、
在返回里加一个「综合完成度」、`readable_paths=None` 被顺手写成 `set()`、
两边丢的读动作不是同一档（页面上会凭空多一批缺口）。
"""
import pytest

from app.services.qa_business_actions import verb_inventory
from app.services.qa_domain_map import (
    HINT_KINDS,
    UNSEEN_KINDS,
    WHERE_KINDS,
    absorb_reading,
    attach_control_endpoints,
    breadth_depth,
    chain_map,
    classify_hint,
    map_declarations,
    merge_surface,
    new_unseen_book,
    note_unseen,
    pair_actions,
    pick_row_state,
    read_rules,
    read_structure,
    script_verbs_of,
    snapshot_actions,
    state_candidates,
    state_path,
    states_not_walked,
    summarize_maps,
)


class Test提示归类:
    def test_状态码压过文案(self):
        # 「你没权限改这一项」出现在一条 200 的业务提示里也很常见，
        # 反过来判会记出一条假的权限边界。
        assert classify_hint("随便一句话", status=403) == "permission"

    def test_三类各认得出来(self):
        assert classify_hint("名称不能为空") == "constraint"
        assert classify_hint("当前状态不允许此操作") == "state_edge"
        assert classify_hint("权限不足") == "permission"

    def test_归不了类留原文不猜(self):
        assert classify_hint("处理中，请稍候") == "unknown"

    def test_页面什么都没说不算一类提示(self):
        # 空 = G4 的料（点了没反应），不是「一条规则」。
        assert classify_hint("   ") == ""

    def test_英文认词边界不认子串(self):
        # 裸 `in` 会让 invalid 命中 invalidate —— 多认一句提示，
        # 就把一条断点洗成了一条"规则"。
        assert classify_hint("token invalidated by rotation") == "unknown"
        assert classify_hint("invalid email") == "constraint"

    def test_每一类都写了为什么(self):
        for kind, meta in HINT_KINDS.items():
            assert meta["label"] and meta["why"], kind


class Test规则那一格:
    def test_提示原文不压成通过失败(self):
        got = read_rules([], [{"text": "名称已存在", "where": "create"}])
        assert got["hints"][0]["text"] == "名称已存在"
        assert got["hintKinds"]["constraint"] == 1

    def test_一个必填标记都没读到要能看出来(self):
        # 0 不等于「这个表单没有必填」，antd 是用样式类标的。
        got = read_rules([{"label": "名称"}], [])
        assert got["requiredMarksSeen"] is False

    def test_每一类都渲染包括0(self):
        got = read_rules([], [])
        assert set(got["hintKinds"]) == set(HINT_KINDS)


class Test动作面归类:
    def test_位置打错当场拒(self):
        with pytest.raises(ValueError):
            snapshot_actions([{"label": "删除"}], where="rows")

    def test_六个位置一个不少(self):
        assert set(WHERE_KINDS) == {"page", "row", "batch",
                                    "layer", "detail", "tab"}

    def test_灰没灰读不到不当成亮的(self):
        rows = snapshot_actions([{"label": "删除"}], where="row")
        assert rows[0]["enabled"] is None

    def test_行内和批量的同名按钮是两个动作(self):
        a = snapshot_actions([{"label": "删除"}], where="row")
        b = snapshot_actions([{"label": "删除"}], where="batch")
        assert merge_surface([a, b])["actionsTotal"] == 2

    def test_同一按钮两种状态一亮一灰就是状态机一条边(self):
        a = snapshot_actions([{"label": "审批", "disabled": False}],
                             where="row", state="待审核")
        b = snapshot_actions([{"label": "审批", "disabled": True}],
                             where="row", state="已通过")
        edges = merge_surface([a, b])["stateEdges"]
        assert edges[0]["enabledIn"] == ["待审核"]
        assert edges[0]["disabledIn"] == ["已通过"]

    def test_每个位置都渲染0也渲染(self):
        # batch 常年 0 是欠账（一次没勾过行），不是「这个产品没有批量」。
        assert set(merge_surface([])["actionsByWhere"]) == set(WHERE_KINDS)

    def test_读不到灰没灰的处数单独数(self):
        a = snapshot_actions([{"label": "导出"}], where="page")
        assert merge_surface([a])["enabledUnknown"] == 1


class Test两边对账用端点不用名字:
    def _surface(self, method, path):
        return {"actions": [{"label": "导出", "where": "page",
                             "endpoints": [{"method": method, "path": path}]}]}

    def test_页面发过脚本没打过就是一条缺口(self):
        got = pair_actions(self._surface("GET", "/api/services/export"),
                           [{"method": "POST", "path": "/api/services"}])
        assert [x["verb"] for x in got["actionsUntested"]] == ["export"]

    def test_脚本打过页面上没有哪个按钮发过(self):
        got = pair_actions(self._surface("GET", "/api/services/export"),
                           [{"method": "POST",
                             "path": "/api/services/1/approve"}])
        assert [x["verb"] for x in got["verbsNotOnPage"]] == ["approve"]

    def test_两边都空不算对齐(self):
        # 控件级那一列全是 NULL 时这两个清单恒空，读起来像「完全一致」。
        got = pair_actions({"actions": []}, [])
        assert got["paired"] is False
        assert got["actionsUntested"] == [] and got["verbsNotOnPage"] == []

    def test_列表页自己的GET两边都丢掉(self):
        # 打开页面就会发 —— 拿它连两边等于把「他打开过这页」当成「他测过」。
        got = pair_actions(self._surface("GET", "/services"),
                           [{"method": "GET", "path": "/services"}])
        assert got["pageVerbs"] == [] and got["scriptVerbs"] == []

    def test_没查过能不能GET时不许当成查过了(self):
        # `readable_paths=None` 被顺手写成 `set()` 的话，空集合读作
        # 「查过了，这些路径都不能 GET」，于是每条深路径都被判成动作 ——
        # 页面上凭空多出一个叫 settings 的"业务动作"。
        surface = self._surface("POST", "/api/services/settings")
        assert pair_actions(surface, [])["pageVerbs"] == ["create"]
        got = pair_actions(surface, [], readable_paths=set())
        assert got["pageVerbs"] == ["settings"]


class Test脚本那一半直接拍平不重算:
    def test_三档都收(self):
        inv = verb_inventory([
            {"method": "POST", "path": "/api/services/1/approve"},
            {"method": "POST", "path": "/api/services"},
            {"method": "GET", "path": "/api/services/export"},
        ])
        got = script_verbs_of({"SVC": inv})
        assert set(got) >= {"approve", "create", "export"}

    def test_只丢read那一个词(self):
        inv = verb_inventory([{"method": "GET", "path": "/api/services/1"}])
        assert inv["crud"] == {"read": ["GET /api/services/{}"]}
        assert script_verbs_of({"SVC": inv}) == {}

    def test_没对账过给空的不炸(self):
        assert script_verbs_of(None) == {}

    def test_拍平之后能和页面那一半连上(self):
        inv = verb_inventory([{"method": "POST",
                               "path": "/api/services/1/approve"}])
        surface = {"actions": [{"label": "审批", "where": "row", "endpoints": [
            {"method": "POST", "path": "/api/services/9/approve"}]}]}
        got = pair_actions(surface, None,
                           script_verbs=script_verbs_of({"SVC": inv}))
        assert got["paired"] is True
        assert got["actionsUntested"] == [] and got["verbsNotOnPage"] == []


class Test控件级那一列接得上:
    """§14.2 的页面那一半：动作面是链路枚举的，「这个按钮发了什么」在
    item 的 `endpoints` 那一列上。**不接起来的话 `pageVerbs` 恒为空** ——
    两个清单一起空，读起来像「页面和脚本完全一致」。
    """

    def _sf(self, anchor="btn-del", label="删除"):
        return {"actions": [{"label": label, "where": "row",
                             "anchor": anchor}]}

    def test_按anchor接上之后能对账(self):
        sf = self._sf()
        attach_control_endpoints(sf, [
            {"anchor": "btn-del", "page_path": "/services",
             "endpoints": [{"method": "delete", "path": "/api/services/1"}]}])
        assert sf["actions"][0]["endpoints"] == [
            {"method": "DELETE", "path": "/api/services/1"}]
        got = pair_actions(sf, [{"method": "DELETE",
                                 "path": "/api/services/2"}])
        assert got["paired"] is True

    def test_没点过的那一列一个字都不写(self):
        # `None` 写成 `[]` 等于替这一行宣布「点了没发请求」。
        sf = self._sf()
        attach_control_endpoints(sf, [
            {"anchor": "btn-del", "endpoints": None}])
        assert "endpoints" not in sf["actions"][0]

    def test_点了确实没发也不写(self):
        # 那是 G4 那一列的料，写进来会让这个按钮看着像「连上了、只是没端点」。
        sf = self._sf()
        attach_control_endpoints(sf, [
            {"anchor": "btn-del", "endpoints": []}])
        assert "endpoints" not in sf["actions"][0]

    def test_页面路径不进键(self):
        # 同一个「删除」在列表页和详情页上是同一个动作词；掺进 page_path
        # 只会让详情页那个删除凭空多出一条缺口。
        sf = self._sf()
        attach_control_endpoints(sf, [
            {"anchor": "btn-del", "page_path": "/services/1",
             "endpoints": [{"method": "DELETE", "path": "/api/services/1"}]}])
        assert sf["actions"][0]["endpoints"]

    def test_没anchor就退回文案(self):
        sf = self._sf(anchor="导出", label="导出")
        attach_control_endpoints(sf, [
            {"label": "导出",
             "endpoints": [{"method": "GET", "path": "/api/svc/export"}]}])
        assert sf["actions"][0]["endpoints"]

    def test_同一个控件的边去重(self):
        sf = self._sf()
        ep = {"method": "DELETE", "path": "/api/services/1"}
        attach_control_endpoints(sf, [
            {"anchor": "btn-del", "endpoints": [ep, dict(ep)]}])
        assert len(sf["actions"][0]["endpoints"]) == 1

    def test_一条item都没有时不炸也不编(self):
        sf = self._sf()
        attach_control_endpoints(sf, None)
        assert pair_actions(sf, None)["paired"] is False


class Test状态是数出来的:
    def test_低基数短词那一列(self):
        cells = [["订单A", "待审核", "2026-09-01"],
                 ["订单B", "已通过", "2026-09-02"],
                 ["订单C", "待审核", "2026-09-03"]]
        cand = state_candidates(cells)["candidates"]
        assert [c["column"] for c in cand] == [1]
        assert cand[0]["values"] == ["已通过", "待审核"]

    def test_每行都不一样的列不是状态(self):
        cells = [["订单A", "2026-09-01"], ["订单B", "2026-09-02"]]
        assert state_candidates(cells)["candidates"] == []

    def test_行太少就说数不出来不猜(self):
        got = state_candidates([["订单A", "待审核"]])
        assert got["candidates"] == [] and got["why"]

    def test_连续相同的状态合成一格(self):
        got = state_path(["待审核", "待审核", "已通过"])
        assert got["path"] == ["待审核", "已通过"]
        assert got["edgesWalked"] == [{"from": "待审核", "to": "已通过"}]

    def test_读不到的那一格不打断序列(self):
        assert state_path(["待审核", "", "已通过"])["path"] == ["待审核", "已通过"]

    def test_没走到的分支不等于这个状态不存在(self):
        cells = [["a", "待审核"], ["b", "已驳回"], ["c", "待审核"]]
        cand = state_candidates(cells)
        assert states_not_walked(cand, state_path(["待审核"])) == ["已驳回"]

    def test_我们那一行的状态取候选列同一格(self):
        cells = [["a", "待审核"], ["b", "已通过"], ["c", "待审核"]]
        assert pick_row_state(cells, ["c", "待审核"]) == "待审核"

    def test_数不出来就返回空串不猜(self):
        assert pick_row_state([["a", "x"]], ["a", "x"]) == ""


class Test结构差集:
    def test_建完之后多出来的区块(self):
        got = read_structure(["基本信息"], ["基本信息", "审批记录"])
        assert got["appeared"] == ["审批记录"]

    def test_只报差集不解释它是什么(self):
        got = read_structure([], ["操作日志"])
        assert set(got) == {"appeared", "disappeared", "before", "after"}


class Test三种没看到必须分开:
    def test_三本账各一本(self):
        assert set(new_unseen_book()) == set(UNSEEN_KINDS)

    def test_类型打错当场拒(self):
        with pytest.raises(ValueError):
            note_unseen(new_unseen_book(), "untested")

    def test_遗漏算我们的欠账故意没点不算(self):
        assert UNSEEN_KINDS["unreached"]["ours"] is True
        assert UNSEEN_KINDS["seen_not_run"]["ours"] is False
        assert UNSEEN_KINDS["blocked"]["ours"] is False


class Test广度深度两个数:
    def _surface(self):
        return merge_surface([snapshot_actions(
            [{"label": "新建", "disabled": False}], where="page")])

    def test_不许有综合完成度(self):
        got = summarize_maps([])
        assert "score" not in got and "completeness" not in got
        assert set(got["breadth"]) & set(got["depth"]) == set()

    def test_一趟都没跑广度不算满(self):
        # `unreached` 天然是空的，那时「广度满」是最响的一句假话。
        got = breadth_depth(merge_surface([]), new_unseen_book(), {})
        assert got["breadth"]["full"] is False

    def test_有前置没做到就不满(self):
        book = note_unseen(new_unseen_book(), "unreached", where="detail")
        got = breadth_depth(self._surface(), book, {})
        assert got["breadth"]["full"] is False

    def test_故意没点不扣广度(self):
        book = note_unseen(new_unseen_book(), "seen_not_run", where="page")
        got = breadth_depth(self._surface(), book, {})
        assert got["breadth"]["full"] is True
        assert got["depth"]["notRun"] == 1

    def test_灰没灰读不到的处数单独摆着(self):
        s = merge_surface([snapshot_actions([{"label": "导出"}], where="page")])
        assert breadth_depth(s, new_unseen_book(), {})["breadth"]["roleUnknown"] == 1

    def test_三本账的条数各自渲染(self):
        got = breadth_depth(self._surface(), new_unseen_book(), {})
        assert set(got["breadth"]["declaredNotSeen"]) == set(UNSEEN_KINDS)

    def test_深度看主链走通了没有(self):
        got = breadth_depth(self._surface(), new_unseen_book(),
                            {"chainsAttempted": 1, "chainsCompleted": 1})
        assert got["depth"]["mainChainDone"] is True


class Test一条链折成地图:
    def _chain(self):
        from app.services.qa_directed_chain import new_chain
        return new_chain("/services", "qa-probe-x")

    def test_四样一格都不省(self):
        got = chain_map(self._chain())
        assert set(got) == {"page", "rules", "probed", "state",
                            "surface", "structure"}

    def test_点一步四样都收进来(self):
        ch = self._chain()
        absorb_reading(ch, step="create", where="layer",
                       read={"hints": ["名称不能为空"],
                             "sections": ["基本信息"],
                             "cells": [["a", "待审核"], ["b", "已通过"],
                                       ["c", "待审核"]],
                             "ourRow": ["a", "待审核"]},
                       items=[{"label": "确定", "disabled": False}])
        got = chain_map(ch)
        assert got["rules"]["hints"][0]["kind"] == "constraint"
        assert got["state"]["path"]["path"] == ["待审核"]
        assert got["surface"]["actionsTotal"] == 1
        assert got["probed"] == ["layer"]

    def test_探过这一层和探到几个是两件事(self):
        ch = self._chain()
        absorb_reading(ch, step="detail", where="detail", read={}, items=[])
        # 探过、一个都没有 = 产品的事实；压根没探才是我们的欠账。
        assert chain_map(ch)["probed"] == ["detail"]


class Test声明说的是没量到:
    def test_一条都没连上时不许下结论(self):
        lines = map_declarations({"paired": False}, {}, {})
        assert any("一条都没连上" in x for x in lines)
        assert any("空的不等于对齐" in x for x in lines)

    def test_没勾过行就说批量那一层没看到(self):
        lines = map_declarations({"paired": True}, {},
                                 merge_surface([]))
        assert any("批量条" in x and "不是「这个产品没有批量操作」" in x
                   for x in lines)

    def test_没进详情页就说清楚别读成只有列表页(self):
        lines = map_declarations({"paired": True}, {}, merge_surface([]))
        assert any("只有列表页" in x for x in lines)

    def test_读不到灰没灰要出声(self):
        s = merge_surface([snapshot_actions([{"label": "导出"}], where="page")])
        lines = map_declarations({"paired": True}, {}, s)
        assert any("灰没灰" in x for x in lines)

    def test_一趟都没跑也有声明(self):
        got = summarize_maps([])
        assert got["declarations"]


class Test多条链合起来:
    def _chain(self, page, states):
        from app.services.qa_directed_chain import new_chain
        ch = new_chain(page, "qa-probe-" + page)
        ch["states"] = list(states)
        return ch

    def test_两条链的状态序列不许首尾相接(self):
        # A 链末尾 → B 链开头，会造出一条谁都没走过的边。
        a = self._chain("/a", ["待审核", "已通过"])
        b = self._chain("/b", ["草稿", "待审核"])
        edges = summarize_maps([a, b])["surface"]["statePath"]["edgesWalked"]
        assert {"from": "已通过", "to": "草稿"} not in edges
        assert len(edges) == 2

    def test_没探到的层记成我们的欠账(self):
        got = summarize_maps([self._chain("/a", [])])
        wheres = [x["where"] for x in got["unseen"]["unreached"]]
        assert set(wheres) == {"detail", "batch", "tab"}
