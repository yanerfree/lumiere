"""S7.6 G1/G2 渲染成可直接粘贴的清单表行。

两个方向的错都不会自己报出来：

- **粘进去认不出来**：行的形状差一点（首列带反引号、标题里有竖线），
  清单渲染出来照样是张好看的表，但 `parse_catalog` 不认 ——
  **归档了，却不在任何统计里**。所以验收不是比对字符串，是**喂回解析器**。
- **编号复用**：一个 ID 在不同时间指两个东西。要到有人翻仓库历史、
  或者脚本头 `@scenario` 对不上时才发现，那时已经查不清了。
"""
from app.services.qa_catalog import parse_catalog
from app.services.qa_coverage_reconcile import propose_rows

# 注意 `POL-09` 是**废弃**的，而且它是最大号；03/04 是中间的空洞
_SCENARIOS = [
    {"id": "POL-01", "domain": "POL", "state": "covered"},
    {"id": "POL-02", "domain": "POL", "state": "gap"},
    {"id": "POL-09", "domain": "POL", "state": "deprecated"},
    {"id": "TEM-01", "domain": "TEM", "state": "covered"},
]


def _g1(domain="POL", anchor="POST /api/policies/{}/reject", **kw):
    row = {"kind": "G1", "domain": domain, "blame": "catalog", "severity": "high",
           "method": "POST", "path": "/api/policies/{}/reject", "anchor": anchor,
           "pagePath": "/policies", "label": "批量驳回", "controlAnchor": "btn-reject"}
    row.update(kw)
    return row


def _g2(domain="POL", anchor="GET /api/policies/export", **kw):
    row = {"kind": "G2", "domain": domain, "blame": "catalog", "severity": "medium",
           "method": "GET", "path": "/api/policies/export", "anchor": anchor,
           "group": "Policies"}
    row.update(kw)
    return row


def _p(g1=None, g2=None, scenarios=_SCENARIOS, **kw):
    gaps = {"g1": list(g1 or []), "g2": list(g2 or [])}
    gaps.update(kw)
    return propose_rows(gaps=gaps, scenarios=scenarios)


class Test编号一经分配永不复用:
    def test_取该域最大号加一(self):
        assert _p(g1=[_g1()])["rows"][0]["id"] == "POL-10"

    def test_废弃的号也占着(self):
        """`POL-09` 已经废弃，但它的号**没还回来** —— 脚本头的 `@scenario`、
        仓库历史、过往报告全都指着它。只算活着的场景就会吐出 `POL-03`。"""
        got = _p(g1=[_g1()])["rows"][0]["id"]
        assert got == "POL-10"
        assert got != "POL-09"

    def test_不填中间的空洞(self):
        """03/04 是别人退役掉的号，不是空位。"""
        assert _p(g1=[_g1()])["rows"][0]["id"] not in {"POL-03", "POL-04"}

    def test_同一批里的多条各拿各的号(self):
        """全给 max+1 的话，粘进清单后 `parse_catalog` 只留第一条、
        其余进 `duplicateIds` —— **缺口看着归档了，其实凭空消失**。"""
        rows = _p(g1=[_g1(anchor="a1"), _g1(anchor="a2")], g2=[_g2(anchor="a3")])["rows"]
        assert [r["id"] for r in rows] == ["POL-10", "POL-11", "POL-12"]
        assert len({r["id"] for r in rows}) == 3

    def test_没有历史场景的域从01开始(self):
        assert _p(g1=[_g1(domain="NEW")])["rows"][0]["id"] == "NEW-01"

    def test_位宽跟着清单走(self):
        """清单里写的是 `POL-005`（三位），下一号就得是 `POL-006`，不是 `POL-6`。

        ⚠ 第一版这条桩用的是 `POL-100` → `POL-101` —— 位宽写死 2 还是 3
        输出**一模一样**，这条测试根本没走到它名字承诺的那一关。"""
        assert _p(g1=[_g1()], scenarios=[{"id": "POL-005"}])["rows"][0]["id"] == "POL-006"
        assert _p(g1=[_g1()], scenarios=[{"id": "POL-100"}])["rows"][0]["id"] == "POL-101"

    def test_号超出三位就提不出行而不是提个假的(self):
        """清单 ID 的形状是三位封顶。硬吐 `POL-1000` 出去 = 粘进去不被认，
        又回到「归档了但不在统计里」。宁可提不出来。"""
        got = _p(g1=[_g1()], scenarios=[{"id": "POL-999"}])
        assert got["rows"] == []
        assert got["counters"]["blocked"] == 1
        assert "三位" in got["blocked"][0]["reason"]

    def test_两次跑出来的号一样(self):
        """同一批缺口换个进来的顺序，**同一个缺口得拿到同一个号**。否则上周
        归档成 `POL-06` 的那条，这周提案里成了 `POL-07` —— 两个号指同一件事，
        谁都对不上，只能整批当新的重报一遍。

        ⚠ 只比 ID 列表比不出来：不排序时列表照样是 `[POL-10, POL-11]`，
        变的是**哪个缺口拿到哪个号**。要比的是「路径 → 号」这个映射。"""
        def _m(rows):
            return {r["evidence"]["path"]: r["id"] for r in rows}

        x = _g1(anchor="POST /api/a", path="/api/a")
        y = _g1(anchor="POST /api/b", path="/api/b")
        assert _m(_p(g1=[x, y])["rows"]) == _m(_p(g1=[y, x])["rows"])


class Test真粘回去认不认:
    def test_生成的行喂回解析器能认出来(self):
        """**这条是本 Story 的验收**。断言字符串形状只是在核对我自己编的东西；
        只有 `parse_catalog` 说认，「可直接粘贴」才成立。"""
        row = _p(g1=[_g1()])["rows"][0]
        scen, _, meta = parse_catalog(row["markdown"])
        assert len(scen) == 1
        assert scen[0]["id"] == "POL-10"
        assert scen[0]["title"] == row["title"]
        assert scen[0]["tier"] == "ui"
        assert scen[0]["state"] == "gap"
        assert meta["unparsedRows"] == []

    def test_G2的行也认(self):
        row = _p(g2=[_g2()])["rows"][0]
        scen, _, meta = parse_catalog(row["markdown"])
        assert len(scen) == 1 and meta["unparsedRows"] == []

    def test_首列不许带反引号(self):
        """`_ROW_RE` 的首列不允许反引号。带上的话整行掉进 `unparsedRows` ——
        表格渲染出来还是好看的，但清单的统计里没有它。"""
        assert "`" not in _p(g1=[_g1()])["rows"][0]["markdown"]

    def test_标题里的竖线不许漏出去(self):
        """一根竖线把一行劈成两列，后面所有格子整体左移 ——
        `⬜` 落到「执行层」那一格，状态列变空 ⇒ 解析出来还是 gap，
        但层列被污染成 `⬜`，谁都不会注意。"""
        row = _p(g1=[_g1(label="导出 | 批量")])["rows"][0]
        assert "|" not in row["title"]
        scen, _, _ = parse_catalog(row["markdown"])
        assert scen[0]["tier"] == "ui"

    def test_标题里的换行也不许漏出去(self):
        row = _p(g1=[_g1(label="导出\n批量")])["rows"][0]
        assert "\n" not in row["markdown"]
        assert len(parse_catalog(row["markdown"])[0]) == 1

    def test_标题空了退回方法和路径(self):
        """页面控件没有可读文案（图标按钮）时标题会是空的。
        空标题的行照样解析，但清单上是一行没有描述的场景 —— 没人认领得了。"""
        row = _p(g1=[_g1(label="", pagePath="")])["rows"][0]
        assert row["title"] == "POST /api/policies/{}/reject"


class Test不知道的格子不许猜:
    def test_优先级和风险写成问号(self):
        """猜一个 `P2` 上去，一个本该 P0 的缺口就被我们自己埋到队尾了，
        而且再没人会重新问一遍 —— 它看起来已经有人定过级了。"""
        row = _p(g1=[_g1()])["rows"][0]
        assert row["priority"] == "P?" and row["risk"] == "?"
        assert "优先级 P" in row["todo"] and "风险 R" in row["todo"]

    def test_G1的层是观测到的ui(self):
        """G1 的前提就是「在页面上点得到」—— 层是 `ui` 是观测事实，不是猜。"""
        row = _p(g1=[_g1()])["rows"][0]
        assert row["tier"] == "ui" and "执行层" not in row["todo"]

    def test_G2的层是问号(self):
        """G2 只知道路由表里有这条端点，页面上有没有面**没观测过**。
        顺手填 `api` 的话，一个其实有页面的功能就被永久归到非 UI 层，
        下一轮 S7.5 拿层列一判，整个域被消音。"""
        row = _p(g2=[_g2()])["rows"][0]
        assert row["tier"] == "?" and "执行层" in row["todo"]


class Test提不出行的也要记一笔:
    def test_归不了属的端点单独记账(self):
        got = _p(g1=[_g1()], endpointsUnextracted=[],
                 endpointsUnattributed=[{"anchor": "GET /api/x"}, {"anchor": "GET /api/y"}])
        assert got["counters"]["unattributed"] == 2

    def test_没有域码的行提不出来但不丢(self):
        got = _p(g1=[_g1(domain="")])
        assert got["rows"] == []
        assert got["blocked"][0]["anchor"] == "POST /api/policies/{}/reject"

    def test_计数0也渲染(self):
        """只在非 0 时出现的计数，跟「没算过」长得一模一样。"""
        assert _p()["counters"] == {"proposed": 0, "blocked": 0, "unattributed": 0}


class Test一个端点归两个域:
    def test_两条都提并且标出歧义(self):
        """组 → 域码的映射是**集合**（S7.1）。悄悄挑一个，等于替别人的清单
        做归属决定；两条都不提，缺口消失。两条都提 + 标出来让人挑。"""
        rows = _p(g1=[_g1(domain="POL", anchor="k"), _g1(domain="TEM", anchor="k")])["rows"]
        assert {r["domain"] for r in rows} == {"POL", "TEM"}
        assert rows[0]["ambiguousDomains"] == ["TEM"]
        assert rows[1]["ambiguousDomains"] == ["POL"]

    def test_不歧义的时候是空的(self):
        """常驻的歧义标记等于没有标记。"""
        assert _p(g1=[_g1()])["rows"][0]["ambiguousDomains"] == []


class Test证据带得出来:
    def test_G1带页面和控件锚点(self):
        """提案要能被核实。没有锚点的话，人只能凭一句标题决定要不要粘。"""
        ev = _p(g1=[_g1()])["rows"][0]["evidence"]
        assert ev["pagePath"] == "/policies" and ev["controlAnchor"] == "btn-reject"
        assert ev["method"] == "POST"

    def test_G2带路由组(self):
        assert _p(g2=[_g2()])["rows"][0]["evidence"]["group"] == "Policies"
