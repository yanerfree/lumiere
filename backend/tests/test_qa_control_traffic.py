"""控件级 P 边：HAR 按**点击时窗**归控件（§13.2b）。

页面级那份在 `test_qa_page_traffic.py`。**两份分开写是故意的** ——
这一粒度的三个错和页面级不一样，代价也不一样：

① **把关层/跳转后的流量算成"这个按钮调的"**（延长尾巴、或忘了 `effect`）——
   错的边在报告上和对的长得一模一样，还会让一条真缺口凭空消失（P 账里有了）。
② **把「没量到」当成「量了是 0」** —— 那正好是 G4 的全部内容，
   于是白得一批「这按钮点下去什么都不发生」，一条测试都不会红。
③ **把边挂到错的控件头上**（归属键用 `(页, anchor)`：弹层里的「保存」和
   页面上的「保存」是同一个 anchor）—— JSON 列上没有外键，挂错了没人知道。

Test ID: qa-control-traffic-UT-001
Priority: P0
"""
from app.engine.surveys.qa_page_survey_crawl import item_key
from app.services import qa_page_traffic as t
from app.services.qa_coverage_reconcile import (
    EDGE_SOURCES,
    build_group_index,
    compute_gaps,
    edge_ok,
)

PAGE = "/services"


def _click(anchor, start, end, *, page=PAGE, label="", effect="", scope="", key=None):
    w = {"page": page, "anchor": anchor, "label": label or anchor,
         "key": key if key is not None else item_key(page, anchor, scope)}
    if start is not None:
        w["startedAt"] = start
    if end is not None:
        w["endedAt"] = end
    if effect:
        w["effect"] = effect
    return w


def _entry(url, at, method="GET", rtype="xhr", status=200):
    e = {"startedDateTime": at, "_resourceType": rtype,
         "request": {"method": method, "url": url}}
    if status is not None:
        e["response"] = {"status": status}
    return e


def _har(*entries):
    return {"log": {"version": "1.2", "entries": list(entries)}}


def _at(sec, ms=0):
    return "2026-09-03T10:00:%02d.%03dZ" % (sec, ms)


def _item(anchor, *, page=PAGE, scope="", clicked=True, state="enabled",
          effect="", ctype="button"):
    return {"key": item_key(page, anchor, scope), "page_path": page,
            "anchor": anchor, "label": anchor, "control_type": ctype,
            "state": state, "clicked": clicked, "effect": effect,
            "endpoints": None}


# ── 归属 ─────────────────────────────────────────────────────────────────


class Test按点击归属:
    def test_窗内的请求归这个控件(self):
        out = t.bucket_clicks(_har(_entry("http://h/api/v1/services", _at(2))),
                              [_click("new-btn", _at(1), _at(4))])
        assert [(e["key"], e["method"], e["path"]) for e in out["edges"]] == [
            (item_key(PAGE, "new-btn"), "GET", "/api/v1/services")]
        assert out["counters"]["controlsWithEdges"] == 1
        assert out["counters"]["controlsSilent"] == 0

    def test_窗外的请求不记账也不报未归属(self):
        """落在所有点击窗之外的就是**页面自己加载**发的 —— 它有自己的主人
        （页面级那本账）。在这儿记一格 `unwindowed` 会得到几百条毫无意义的
        "归不了属"，把真正要看的 `clickWindowsUnclosed` 淹掉。"""
        out = t.bucket_clicks(_har(_entry("http://h/api/v1/me", _at(9))),
                              [_click("new-btn", _at(1), _at(4))])
        assert out["edges"] == []
        assert out["counters"]["controlsSilent"] == 1
        assert "edgesUnwindowed" not in out["counters"]

    def test_一律不延长尾巴(self):
        """页面级会把窗尾延长（轮询请求还该算这一页）。**控件级不许** ——
        点完紧接着是关层 / goto 回来，延长就把那些算成了这个按钮发的。"""
        out = t.bucket_clicks(_har(_entry("http://h/api/v1/x", _at(5))),
                              [_click("new-btn", _at(1), _at(4))])
        assert out["edges"] == []

    def test_点开始那一刻的请求算在内(self):
        out = t.bucket_clicks(_har(_entry("http://h/api/v1/x", _at(1))),
                              [_click("new-btn", _at(1), _at(4))])
        assert len(out["edges"]) == 1

    def test_两个窗重叠就不猜(self):
        """猜一个的话，边会挂到错的按钮上，而那种错查不出来。"""
        out = t.bucket_clicks(_har(_entry("http://h/api/v1/x", _at(3))),
                              [_click("a", _at(1), _at(5)),
                               _click("b", _at(2), _at(6))])
        assert out["edges"] == []
        assert out["counters"]["controlEdgesAmbiguous"] == 1
        assert out["samples"]["clickAmbiguous"][0]["controls"] == [
            item_key(PAGE, "a"), item_key(PAGE, "b")]

    def test_窗乱序传进来照样归得对(self):
        out = t.bucket_clicks(_har(_entry("http://h/api/v1/x", _at(2))),
                              [_click("b", _at(6), _at(8)),
                               _click("a", _at(1), _at(4))])
        assert out["edges"][0]["anchor"] == "a"


class Test没有右边界的窗:
    def test_整条弃掉并记数(self):
        out = t.bucket_clicks(_har(_entry("http://h/api/v1/x", _at(2))),
                              [_click("a", _at(1), None)])
        assert out["edges"] == []
        assert out["counters"]["clickWindows"] == 0
        assert out["counters"]["clickWindowsUnclosed"] == 1

    def test_不许算成点了没发请求(self):
        """**这是本粒度最贵的一个错。**「没量到」和「量了是 0」合起来，
        G4 就白得一条「这按钮点下去什么都不发生」。"""
        out = t.bucket_clicks(_har(), [_click("a", _at(1), None)])
        assert out["counters"]["controlsSilent"] == 0
        assert any("别读成" in d for d in out["declarations"])

    def test_右边界比左边界早也算废(self):
        out = t.bucket_clicks(_har(), [_click("a", _at(5), _at(1))])
        assert out["counters"]["clickWindowsUnclosed"] == 1

    def test_读不出时间戳也算废(self):
        out = t.bucket_clicks(_har(), [_click("a", "不是时间", _at(4))])
        assert out["counters"]["clickWindowsUnclosed"] == 1


class Test点了什么都没发:
    def test_有窗没边就是G4的料(self):
        out = t.bucket_clicks(_har(_entry("http://h/api/v1/x", _at(9))),
                              [_click("a", _at(1), _at(4))])
        assert out["counters"]["controlsSilent"] == 1
        assert out["attempted"] == [{"key": item_key(PAGE, "a"), "pagePath": PAGE,
                                     "anchor": "a", "effect": ""}]

    def test_静态资源不算发了请求(self):
        out = t.bucket_clicks(
            _har(_entry("http://h/assets/index-a1b2.js", _at(2), rtype="script")),
            [_click("a", _at(1), _at(4))])
        assert out["edges"] == []
        assert out["counters"]["controlsSilent"] == 1

    def test_预检不算(self):
        out = t.bucket_clicks(
            _har(_entry("http://h/api/v1/x", _at(2), method="OPTIONS",
                        rtype="preflight")),
            [_click("a", _at(1), _at(4))])
        assert out["counters"]["controlsSilent"] == 1


class Test出处和拦截:
    def test_拦下来的写请求也是观测到的事实(self):
        e = _entry("http://h/api/v1/services", _at(2), method="DELETE", status=0)
        out = t.bucket_clicks(_har(e), [_click("del", _at(1), _at(4))])
        assert out["edges"][0]["source"] == "aborted"
        assert out["counters"]["controlEdgesAborted"] == 1

    def test_读请求挂了不算拦截(self):
        e = _entry("http://h/api/v1/services", _at(2), status=0)
        out = t.bucket_clicks(_har(e), [_click("a", _at(1), _at(4))])
        assert out["edges"][0]["source"] == "observed"

    def test_出处必须过得了对账那道闸(self):
        """`source` 不在 `EDGE_SOURCES` 里的话，`edge_ok` 会把控件级边**全部**
        拒掉 —— 而 P 账变空在页面上长得像「没缺口」。"""
        out = t.bucket_clicks(
            _har(_entry("http://h/api/v1/a", _at(2)),
                 _entry("http://h/api/v1/b", _at(2), method="POST", status=0)),
            [_click("a", _at(1), _at(4))])
        for e in out["edges"]:
            assert e["source"] in EDGE_SOURCES
            assert edge_ok(e)

    def test_没权限的单独数(self):
        e = _entry("http://h/api/v1/x", _at(2), status=403)
        out = t.bucket_clicks(_har(e), [_click("a", _at(1), _at(4))])
        assert out["counters"]["controlEdgesUnauthorized"] == 1


class Test跳转那一档要留把手:
    def test_effect跟着边走(self):
        """`navigate` 那一格里混着**目标页自己的加载流量** —— 丢了 `effect`，
        「点这个按钮会调这条端点」就成了一句错话。"""
        out = t.bucket_clicks(_har(_entry("http://h/api/v1/detail", _at(2))),
                              [_click("row", _at(1), _at(4), effect="navigate")])
        assert out["edges"][0]["effect"] == "navigate"
        assert out["counters"]["controlEdgesAfterNavigate"] == 1


class Test合并多角色:
    def _bucket(self, role, status=200):
        return t.bucket_clicks(
            _har(_entry("http://h/api/v1/x", _at(2), method="DELETE", status=status)),
            [_click("del", _at(1), _at(4))], role=role)

    def test_角色取并集(self):
        out = t.merge_control_edges([self._bucket("admin"), self._bucket("member")])
        assert out["edges"][0]["roles"] == ["admin", "member"]
        assert "role" not in out["edges"][0]

    def test_拦截压过观测(self):
        out = t.merge_control_edges([self._bucket("admin"),
                                     self._bucket("member", status=0)])
        assert out["edges"][0]["source"] == "aborted"

    def test_点过的证据跨角色合并(self):
        """A 角色点不着、B 角色点成了 —— 合完必须算"点过"，
        否则 B 那一趟的 G4 会整片消失。"""
        a = t.bucket_clicks(_har(), [_click("x", _at(1), None)], role="a")
        b = t.bucket_clicks(_har(), [_click("x", _at(1), _at(4))], role="b")
        out = t.merge_control_edges([a, b])
        assert [x["key"] for x in out["attempted"]] == [item_key(PAGE, "x")]

    def test_计数相加(self):
        out = t.merge_control_edges([self._bucket("a"), self._bucket("b")])
        assert out["counters"]["clickWindows"] == 2
        assert out["counters"]["controlEdgesMerged"] == 1


# ── 挂到 item 上 ─────────────────────────────────────────────────────────


class Test三态一个都不许合:
    def test_点过有边(self):
        items = [_item("a")]
        out = t.attach_control_edges(
            items, [{"key": item_key(PAGE, "a"), "pagePath": PAGE, "anchor": "a",
                     "method": "GET", "path": "/api/v1/x", "source": "observed"}],
            [{"key": item_key(PAGE, "a")}])
        assert items[0]["endpoints"][0]["path"] == "/api/v1/x"
        assert out["controlEdgesAttached"] == 1

    def test_点过没边是空表不是NULL(self):
        items = [_item("a")]
        out = t.attach_control_edges(items, [], [{"key": item_key(PAGE, "a")}])
        assert items[0]["endpoints"] == []
        assert out["controlEdgesSilentRows"] == 1

    def test_没点过是NULL不是空表(self):
        """**这一条错了最贵**：一千多个没碰过的控件会集体宣布
        「点了什么都没发」，G4 从个位数涨到四位数，全是假的。"""
        items = [_item("a", clicked=False), _item("b", clicked=False)]
        t.attach_control_edges(items, [], [])
        assert [x["endpoints"] for x in items] == [None, None]

    def test_只挂真点过的那几行(self):
        items = [_item("a"), _item("b", clicked=False)]
        t.attach_control_edges(items, [], [{"key": item_key(PAGE, "a")}])
        assert items[0]["endpoints"] == []
        assert items[1]["endpoints"] is None


class Test归属键是item的key不是anchor:
    def test_弹层里同名控件拿不到外层的边(self):
        """页面上有个「保存」，弹层 `[新建]` 里也有个「保存」，anchor 一样。
        按 `(页, anchor)` 归属会把外层点出来的边挂到弹层那一行上 ——
        而弹层那个我们**根本没点**。"""
        outer, inner = _item("save"), _item("save", scope="[新建]", clicked=False)
        edge = {"key": item_key(PAGE, "save"), "pagePath": PAGE, "anchor": "save",
                "method": "POST", "path": "/api/v1/x", "source": "observed"}
        t.attach_control_edges([outer, inner], [edge],
                               [{"key": item_key(PAGE, "save")}])
        assert len(outer["endpoints"]) == 1
        assert inner["endpoints"] is None

    def test_同key两行都拿到同样的边(self):
        """`merge_shards` 只跨分片合，分片内的重复它故意不动 ——
        弹出式的写法会让第二行白得一条「点了什么都没发」。"""
        a, b = _item("dup"), _item("dup")
        edge = {"key": item_key(PAGE, "dup"), "pagePath": PAGE, "anchor": "dup",
                "method": "GET", "path": "/api/v1/x", "source": "observed"}
        out = t.attach_control_edges([a, b], [edge], [{"key": item_key(PAGE, "dup")}])
        assert len(a["endpoints"]) == len(b["endpoints"]) == 1
        assert out["controlEdgesUnmatched"] == 0

    def test_找不着对应行的边只记数不硬塞(self):
        """塞给一个凑近的 anchor 就是把边归给了错的按钮，那种错不报错。"""
        items = [_item("a")]
        out = t.attach_control_edges(
            items, [{"key": "/other::ghost", "method": "GET", "path": "/api/v1/z",
                     "source": "observed"}], [{"key": item_key(PAGE, "a")}])
        assert items[0]["endpoints"] == []
        assert out["controlEdgesUnmatched"] == 1

    def test_边上不留冗余的页和anchor(self):
        items = [_item("a")]
        t.attach_control_edges(
            items, [{"key": item_key(PAGE, "a"), "pagePath": PAGE, "anchor": "a",
                     "method": "GET", "path": "/api/v1/x", "source": "observed"}],
            [{"key": item_key(PAGE, "a")}])
        assert set(items[0]["endpoints"][0]) == {"method", "path", "source"}


# ── 跟对账接上 ───────────────────────────────────────────────────────────


class Test跟对账接上:
    def _gaps(self, items, **kw):
        kw.setdefault("controls_clicked", 1)
        return compute_gaps(page_items=items, routes=[], scripts=[],
                            index=build_group_index({}), claimed_domains=set(), **kw)

    def test_量了是0才是G4(self):
        it = _item("a")
        it["endpoints"] = []
        g = self._gaps([it])
        assert [x["kind"] for x in g["g4"]] == ["G4"]
        assert g["counters"]["controlsUnmeasured"] == 0

    def test_没量到不进G4(self):
        """点是点了，可那次点击的时窗没收到右边界 —— `endpoints` 留 NULL。
        算成 G4 就是拿"没算过"冒充"算过是 0"。"""
        g = self._gaps([_item("a")])          # endpoints=None、clicked=True
        assert g["g4"] == []
        assert g["counters"]["controlsUnmeasured"] == 1
        assert any("没算过" in d for d in g["declarations"])

    def test_没点过既不进G4也不算没量到(self):
        g = self._gaps([_item("a", clicked=False)])
        assert g["g4"] == []
        assert g["counters"]["controlsUnclicked"] == 1
        assert g["counters"]["controlsUnmeasured"] == 0

    def test_两个数分开渲染(self):
        """「没碰过」补预算就能解决，「碰了没量到」是采集有洞 ——
        合成一个数就分不出该修哪个。"""
        g = self._gaps([_item("m"), _item("u", clicked=False)])
        assert (g["counters"]["controlsUnmeasured"],
                g["counters"]["controlsUnclicked"]) == (1, 1)

    def test_控件级的边进P账(self):
        it = _item("a")
        it["endpoints"] = [{"method": "POST", "path": "/api/v1/services",
                            "source": "observed"}]
        g = self._gaps([it])
        assert g["counters"]["pageEndpoints"] == 1
        assert g["g4"] == []
