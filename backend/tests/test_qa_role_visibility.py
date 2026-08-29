"""S7.7 角色维度：并集 → 可见性矩阵 → 越权候选。

这一份的哨兵几乎全指着**同一个方向**：把「没探到」算成「看不见」。
它是唯一会**凭空造出结论**的错 —— 其余的错顶多让一条候选消失，
而这个错会让 SEC 那边去查一个根本不存在的东西。查空两次，这份清单就没人看了。

所以每条降级规则都配一个**反向锚点**：证明真货照样报得出来。
只测「该消失的消失了」的话，一个 `return {"candidates": []}` 能让半份文件变绿。
"""
import inspect

from app.services import qa_role_visibility as rv
from app.services.qa_role_visibility import (
    BASIS,
    INVISIBLE,
    SEC_MARK,
    UNPROBED,
    VISIBLE,
    merge_shards,
    overprivilege_candidates,
    visibility_matrix,
)

MAIN = "qa-auditor"


def _it(key, page="/p1", label=""):
    return {"key": key, "page_path": page, "label": label or key}


def _shard(role, keys, page="/p1"):
    return {"role": role, "items": [_it(k, page) for k in keys]}


def _vis(key, roles, page="/p1"):
    """直接造一行并集后的账本行。"""
    return {"key": key, "page_path": page, "label": key, "roles_visible": list(roles)}


class Test并集:
    def test_主爬角色自己也在名单里(self):
        """只往里追加浅扫角色的话，一个只有主爬看得见的控件会落成
        `roles_visible == []` —— 和「一个角色都没探过它」在产物上一模一样，
        下游再也分不开，而那两件事的结论正好相反。"""
        rows = merge_shards([_shard(MAIN, ["a"])], main_role=MAIN)
        assert rows[0]["roles_visible"] == [MAIN]

    def test_只有浅扫看得见的也留下来(self):
        """拿主爬那份当底、其余只做"标注"的话，这些行整个消失。
        而「低权角色看得见、主爬这个只读账号看不见」**恰恰是**
        角色维度唯一有价值的那个信号。"""
        rows = merge_shards([_shard(MAIN, ["a"]), _shard("viewer", ["b"])], main_role=MAIN)
        assert {r["key"] for r in rows} == {"a", "b"}
        assert [r["roles_visible"] for r in rows if r["key"] == "b"] == [["viewer"]]

    def test_跨分片的同一个key合成一行(self):
        rows = merge_shards([_shard(MAIN, ["a"]), _shard("viewer", ["a"])], main_role=MAIN)
        assert len(rows) == 1
        assert rows[0]["roles_visible"] == [MAIN, "viewer"]

    def test_同一个分片里重复的key不许合并(self):
        """`(survey_id, key)` 撞库是**故意**的（见 `models/qa_page_survey.py`）：
        它是锚点推断塌掉的探测器。在这里顺手去个重，那个探测器就永远不会响，
        而 diff 会开始无缘无故地报「新增 40 项」。"""
        rows = merge_shards([_shard(MAIN, ["a", "a"])], main_role=MAIN)
        assert len(rows) == 2
        assert all(r["roles_visible"] == [MAIN] for r in rows)

    def test_浅扫先到也算主爬看得见(self):
        """分片是并发跑的，谁先返回不定。主爬那份晚到时不能只当成"又一个角色"。"""
        rows = merge_shards([_shard("viewer", ["a"]), _shard(MAIN, ["a"])], main_role=MAIN)
        assert rows[0]["roles_visible"] == [MAIN, "viewer"]

    def test_主爬那份的字段说了算(self):
        """同一个 key 的其余字段（标题、页面路径）以主爬那份为准 ——
        浅扫是掐着 40 页上限跑的，它那份本来就更浅。

        ⚠ 断 `roles_visible` 断不出这一条：并集是**集合**，谁先到结果都一样。
        主爬优先那行代码只决定「留下来的是哪一份的字段」。"""
        shallow = {"role": "viewer", "items": [dict(_it("k"), label="浅")]}
        main = {"role": MAIN, "items": [dict(_it("k"), label="深")]}
        assert merge_shards([shallow, main], main_role=MAIN)[0]["label"] == "深"

    def test_源行自带的角色不丢(self):
        s = {"role": "viewer", "items": [dict(_it("a"), roles_visible=["ops"])]}
        assert merge_shards([s], main_role=MAIN)[0]["roles_visible"] == ["ops", "viewer"]

    def test_角色名空着不塞进名单(self):
        """分片没带角色名时，往名单里塞个空串等于凭空多一个角色。"""
        rows = merge_shards([{"role": "", "items": [_it("a")]}], main_role=MAIN)
        assert rows[0]["roles_visible"] == []

    def test_没有分片时不炸(self):
        assert merge_shards([], main_role=MAIN) == []
        assert merge_shards(None, main_role=MAIN) == []

    def test_原始行不被就地改掉(self):
        """账本行还要落库。就地往入参里塞 `roles_visible`，
        重跑一次并集就会把上一趟的角色也算进来。"""
        src = _shard(MAIN, ["a"])
        merge_shards([src], main_role=MAIN)
        assert "roles_visible" not in src["items"][0]


class Test没探到不是看不见:
    def test_探过这一页又没看见才算看不见(self):
        m = visibility_matrix([_vis("a", [MAIN])],
                              roles=[MAIN, "viewer"],
                              probed_pages={MAIN: ["/p1"], "viewer": ["/p1"]})
        assert m["byKey"]["a"]["roles"]["viewer"] == INVISIBLE

    def test_没走到这一页是未探测(self):
        """浅扫只跑前 40 页，第 41 页往后**必然**什么都看不见。
        这不是边角情况，是常态。"""
        m = visibility_matrix([_vis("a", [MAIN], page="/p41")],
                              roles=[MAIN, "viewer"],
                              probed_pages={MAIN: ["/p41"], "viewer": ["/p1"]})
        assert m["byKey"]["a"]["roles"]["viewer"] == UNPROBED

    def test_账本里根本没这个角色是未探测(self):
        """没配凭证被跳过、登录没成、分片整个死了 —— 三种都长成
        「它一个控件都没看见」，而那和「它被禁掉了所有功能」是同一个 0。"""
        m = visibility_matrix([_vis("a", [MAIN])], roles=[MAIN, "viewer"],
                              probed_pages={MAIN: ["/p1"]})
        assert m["byKey"]["a"]["roles"]["viewer"] == UNPROBED

    def test_没有页面路径的项是未探测(self):
        m = visibility_matrix([_vis("a", [MAIN], page="")], roles=[MAIN, "viewer"],
                              probed_pages={MAIN: ["/p1"], "viewer": ["/p1"]})
        assert m["byKey"]["a"]["roles"]["viewer"] == UNPROBED

    def test_压根没传探测账本时全是未探测(self):
        """老数据没有 `pagesProbed` 这一列。默认成"都探过了"的话，
        一次升级就能把整个历史账本变成一堆假的「看不见」。"""
        m = visibility_matrix([_vis("a", [MAIN])], roles=[MAIN, "viewer"])
        assert m["byKey"]["a"]["roles"]["viewer"] == UNPROBED


class Test矩阵本身:
    def test_看见了压过一切(self):
        """同一个 key 出现在两行上（重复锚点），有一行看见了就是看见了。

        ⚠ **两个方向都得造。** 只造「先没看见、后看见」的话，后一行直接
        覆盖成 VISIBLE，那条守卫拆掉照样绿 —— 它管的是**反向**：
        已经看见过了，后来那行空的不许把它降级成「探过、看不见」。
        而降级出来的正是一格假的 INVISIBLE，越权候选就是拿它算的。
        """
        for rows in ([_vis("a", []), _vis("a", ["viewer"])],
                     [_vis("a", ["viewer"]), _vis("a", [])]):
            m = visibility_matrix(rows, roles=["viewer"],
                                  probed_pages={"viewer": ["/p1"]})
            assert m["byKey"]["a"]["roles"]["viewer"] == VISIBLE, rows

    def test_没传角色时从账本里认(self):
        m = visibility_matrix([_vis("a", [MAIN, "viewer"])])
        assert m["roles"] == sorted([MAIN, "viewer"])

    def test_一个控件都没看见的角色也要出现在矩阵里(self):
        """不然它连同它那一列的「未探测」一起消失，
        报告上看不出**这一趟少了个角色**。"""
        m = visibility_matrix([_vis("a", [MAIN])], roles=[MAIN, "ghost"])
        assert "ghost" in m["roles"]
        assert m["rolesUnprobed"] == ["ghost"]

    def test_探过但什么都没看见的不算未探测角色(self):
        """它是**探过了、确实看不见** —— 那是可比的格子。
        混进"没探到"里，它的越权候选就再也算不出来了。"""
        m = visibility_matrix([_vis("a", [MAIN])], roles=[MAIN, "viewer"],
                              probed_pages={"viewer": ["/p1"]})
        assert m["rolesUnprobed"] == []

    def test_三态计数0也渲染(self):
        m = visibility_matrix([], roles=[MAIN])
        assert m["counters"] == {VISIBLE: 0, INVISIBLE: 0, UNPROBED: 0}

    def test_三态各自计数(self):
        m = visibility_matrix([_vis("a", [MAIN]), _vis("b", [MAIN], page="/p9")],
                              roles=[MAIN, "viewer"],
                              probed_pages={MAIN: ["/p1", "/p9"], "viewer": ["/p1"]})
        assert m["counters"] == {VISIBLE: 2, INVISIBLE: 1, UNPROBED: 1}

    def test_空账本时没有未探测角色(self):
        """一项都没有的时候，「每一格都是未探测」在逻辑上成立 ——
        于是所有角色都进 `rolesUnprobed`，报告上冒出一句
        「这些角色一格都没探到」，而实际上是这一趟根本没爬到东西。"""
        assert visibility_matrix([], roles=[MAIN, "viewer"])["rolesUnprobed"] == []


def _big(low_extra=(), high_only=("h1", "h2", "h3", "h4", "h5")):
    """`high` 是 `low` 的超集（多看见 5 个），外加 `low` 独有的几个。"""
    items = [_vis("shared", ["low", "high"])]
    items += [_vis(k, ["high"]) for k in high_only]
    items += [_vis(k, ["low"]) for k in low_extra]
    return visibility_matrix(items, roles=["low", "high"],
                             probed_pages={"low": ["/p1"], "high": ["/p1"]})


class Test越权候选:
    def test_超集里的例外就是候选(self):
        got = overprivilege_candidates(_big(low_extra=("x",)))
        assert [c["key"] for c in got["candidates"]] == ["x"]
        c = got["candidates"][0]
        assert c["role"] == "low" and c["supersetRole"] == "high"
        assert c["mark"] == SEC_MARK and c["basis"] == BASIS
        # 自己跟自己比永远没有例外，混进来只会把「比了几对」这个数吹起来。
        assert got["counters"]["pairsCompared"] == 2

    def test_例外太多就不是越权只是菜单不同(self):
        """超过门槛就不是「超集带几个例外」，那两个角色只是各有各的菜单。
        硬报出来全是噪声，而噪声会让整份清单被忽略。"""
        assert overprivilege_candidates(_big(low_extra=("x", "y", "z", "w")))["candidates"] == []

    def test_支撑不够不算超集(self):
        """`high` 只多看见一两个，说不上「它本来就该看见 `low` 的全部」。
        这种形状两边互报，一趟能吐出成百条。"""
        assert overprivilege_candidates(_big(low_extra=("x",), high_only=("h1",)))[
            "candidates"] == []

    def test_未探测的格子整格不参与比较(self):
        """浅扫的 40 页上限会让每个浅扫角色在后面的页上"什么都看不见"。
        照那么算，每一趟都能吐出成百条假候选 —— 这是本模块最贵的那个错。"""
        items = [_vis("shared", ["low", "high"])]
        items += [_vis(k, ["high"]) for k in ("h1", "h2", "h3", "h4", "h5")]
        items += [_vis("x", ["low"], page="/p41")]      # high 没走到这一页
        m = visibility_matrix(items, roles=["low", "high"],
                              probed_pages={"low": ["/p1", "/p41"], "high": ["/p1"]})
        assert m["byKey"]["x"]["roles"]["high"] == UNPROBED
        assert overprivilege_candidates(m)["candidates"] == []

    def test_两边都探过的那一页照样报得出来(self):
        """上一条的反向锚点：**整格排除**不能顺手把真货一起排掉。"""
        items = [_vis("shared", ["low", "high"])]
        items += [_vis(k, ["high"]) for k in ("h1", "h2", "h3", "h4", "h5")]
        items += [_vis("x", ["low"], page="/p2")]
        m = visibility_matrix(items, roles=["low", "high"],
                              probed_pages={"low": ["/p1", "/p2"], "high": ["/p1", "/p2"]})
        assert [c["key"] for c in overprivilege_candidates(m)["candidates"]] == ["x"]

    def test_两个角色没有可比的格子就不比(self):
        m = visibility_matrix([_vis("a", ["low"])], roles=["low", "high"])
        got = overprivilege_candidates(m)
        assert got["candidates"] == [] and got["counters"]["pairsCompared"] == 0

    def test_候选顺序稳定(self):
        """顺序一变，同一批候选在页面上就成了"新的一批"。"""
        a = overprivilege_candidates(_big(low_extra=("x", "y")))["candidates"]
        b = overprivilege_candidates(_big(low_extra=("y", "x")))["candidates"]
        assert [c["key"] for c in a] == [c["key"] for c in b] == ["x", "y"]

    def test_候选按页面排(self):
        """人是**按页**去核实的：同一页的候选凑在一起，开一次页面就查完。

        ⚠ 断「两次跑出来一样」断不出这一条 —— 每对角色内部本来就是按 key 排好的，
        去掉排序照样稳定，只是**排错了序**。稳定和排对是两件事。"""
        items = [_vis("shared", ["low", "high"])]
        items += [_vis(k, ["high"]) for k in ("h1", "h2", "h3", "h4", "h5")]
        items += [_vis("a", ["low"], page="/p9"), _vis("b", ["low"], page="/p2")]
        pages = ["/p1", "/p2", "/p9"]
        m = visibility_matrix(items, roles=["low", "high"],
                              probed_pages={"low": pages, "high": pages})
        assert [c["key"] for c in overprivilege_candidates(m)["candidates"]] == ["b", "a"]

    def test_不改矩阵(self):
        m = _big(low_extra=("x",))
        overprivilege_candidates(m)
        assert set(m) == {"byKey", "roles", "counters", "rolesUnprobed"}


class Test没观测到不等于没有越权:
    def test_没有任何一处渲染成没有越权漏洞(self):
        """**这条是本 Story 的验收。** 这份东西看的是「哪些控件露给了谁」，
        它连一次越权请求都没发过。任何一个出口把 0 条候选说成"没有越权"，
        整个模块就从"一份线索"变成了一个假的绿勾。"""
        got = overprivilege_candidates(visibility_matrix([], roles=["low"]))
        blob = repr(got)
        for bad in ("没有越权漏洞", "无越权", "不存在越权", "越权：无", "安全"):
            assert bad not in blob, bad

    def test_没有任何一个布尔的放行位(self):
        """一个 `passed: True` / `clean: True` 会被下游直接当结论渲染，
        而它压根没有资格下这个结论。数据层就不给这个字段。"""
        got = overprivilege_candidates(_big())
        assert not [k for k, v in got.items() if isinstance(v, bool)]
        assert not [k for c in got["candidates"] for k, v in c.items() if isinstance(v, bool)]

    def test_结论和计数焊在同一句话里(self):
        """单独一个「0 条候选」被截出去就变成了绿勾。
        未探测的格数必须跟在同一句里，截不掉。

        ⚠ 断 `"未探测" in s` 断不出这一条 —— 后半句「未探测的格子不参与推导」
        本来就含这三个字，把**数字**整个删掉照样命中。要断的是数字。
        """
        m = visibility_matrix([_vis("a", ["low"])], roles=["low", "ghost"])
        n = m["counters"][UNPROBED]
        assert n == 1
        s = overprivilege_candidates(m)["summary"]
        assert f"未探测 {n} 格" in s and "ghost" in s
        assert "什么都没说" in s

    def test_每条候选都自带依据(self):
        """依据写在**每一条**上，不是页面上补一句 ——
        出口不止一个（页面、MCP、导出的文档），少带一个出口
        就会有人把候选当结论转出去。"""
        for c in overprivilege_candidates(_big(low_extra=("x",)))["candidates"]:
            assert c["basis"] == BASIS and "不是实测" in c["basis"]

    def test_没探到的角色带进候选清单(self):
        m = visibility_matrix([_vis("a", ["low"])], roles=["low", "ghost"])
        assert overprivilege_candidates(m)["rolesUnprobed"] == ["ghost"]

    def test_候选计数0也渲染(self):
        got = overprivilege_candidates(visibility_matrix([], roles=["low"]))
        assert got["counters"] == {"candidates": 0, "pairsCompared": 0,
                                   "cellsComparable": 0, "cellsUnprobed": 0}


class Test封样:
    def test_越权候选不进枚举产物(self):
        """候选是**推断**。混进 items 里就会被当成页面事实往下游传，
        然后进 diff、进对账、进清单 —— 一个推断变成一条"观测到的缺口"。"""
        src = inspect.getsource(rv)
        assert "roles_visible" not in inspect.getsource(rv.overprivilege_candidates)
        assert src.count("def merge_shards") == 1

    def test_全是纯函数不碰IO(self):
        """这个模块要在没有网络、没有数据库的单测里跑完整判定。
        一旦它开始自己去读账本，判定就只能靠端到端测，实际上就是不测了。"""
        src = inspect.getsource(rv)
        for bad in ("import requests", "httpx", "async def", "session", "await "):
            assert bad not in src, bad
