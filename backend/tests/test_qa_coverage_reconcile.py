"""S7.3 对账侧的名字对齐。

这一份的哨兵盯着**两个相反方向**的错：
  · **归一不够** → 同一个组因写法不同没对上 → 凭空多出一条「这组没人测过」
  · **归一过头** → 两个真不同的组撞成一个 → 其中一个组的缺口凭空消失
后者更毒（少算的缺口不会红），但前者更常见（报告喷一堆假缺口，第二次就没人看了）。
"""
from app.services.qa_catalog import parse_catalog
from app.services.qa_coverage_reconcile import (
    build_group_index,
    domains_for,
    norm_group,
)

_CATALOG = """\
## 2. 域码表

| 域码 | 名称 | 覆盖的 API 组 |
|---|---|---|
| `SMK` | 冒烟 | Health, Docs, Root |
| `MCP` | MCP 能力 | MCP-Tools, Root |
| `SEC` | 安全 | Root, Internal |
| `TEM` | 模板 | Templates |
| `PUB` | 对外公共 API | **按路径前缀 `/api/public/v1/*` 划定（18 条）** |
| `ZZZ` | 规则没写清 | 见另一份文档 |

## 3. 场景清单

| ID | 场景 | P | R | 层 | 状 |
|---|---|---|---|---|---|
| SMK-01 | `GET /healthz` | P0 | 6 | smoke | ✅ |
"""


def _index():
    return build_group_index(parse_catalog(_CATALOG)[1])


class Test组名归一:
    def test_大小写和分隔符不影响(self):
        assert norm_group("MCP-Tools") == norm_group("mcp tools") == norm_group("MCPTools")

    def test_单复数归到一起(self):
        """域码表自己警告过 2.1.1→2.2.0 改过写法。按字面比对会**凭空多出 7 个新组**，
        报告上就是 7 条「这些组一条用例都没有」—— 全是假的。"""
        assert norm_group("Tags") == norm_group("Tag")
        assert norm_group("Policies") == norm_group("Policy")
        assert norm_group("Templates") == norm_group("Template")

    def test_不许过度归一(self):
        """`Status` 剥成 `statu` 不只是难看 —— 它会跟别的词撞在一起，
        把两个真不同的组合并成一个，于是其中一个组的缺口**凭空消失**。
        归一不够和归一过头，坏的方向正好相反，两个都要防。"""
        assert norm_group("Status") == "status"
        assert norm_group("Access") == "access"
        assert norm_group("Analysis") == "analysis"

    def test_短名不剥(self):
        """`Ops` 剥成 `op` 就开始跟别的东西撞了。宁可少归一一个短名
        （代价是一条假缺口，看得见），也不要撞掉一个真组（代价看不见）。"""
        assert norm_group("Ops") == "ops"

    def test_空的就是空的不猜(self):
        assert norm_group(None) == ""
        assert norm_group("  ") == ""


class Test一对多归属:
    def test_一个组同属多个域一个都不许丢(self):
        """AC 原文：一对多映射，集合类型。`Root` 同属 SMK/MCP/SEC。"""
        idx = _index()
        assert idx["byGroup"]["root"] == {"SMK", "MCP", "SEC"}
        assert isinstance(idx["byGroup"]["root"], set)

    def test_按组归属(self):
        assert domains_for("/api/mcp/tools", "MCP-Tools", _index()) == {"MCP"}

    def test_写法变了照样对得上(self):
        """清单写 `MCP-Tools`，路由表写 `MCP Tools` —— 按字面比对就是两个组。"""
        assert domains_for("/api/mcp/tools", "MCP Tools", _index()) == {"MCP"}

    def test_前缀域和组域故意重叠两个都要算(self):
        """`/api/public/v1/templates` 既在 `PUB` 的路径前缀底下、又属 `Templates` 组。
        清单是**故意**这么设计的。只走组、或者只走前缀，都会漏掉一个域，
        然后那个域的缺口凭空消失。"""
        got = domains_for("/api/public/v1/templates", "Templates", _index())
        assert got == {"PUB", "TEM"}

    def test_前缀不许沾边就算(self):
        """`/api/public/v10` 不在 `/api/public/v1` 底下。裸 `startswith` 会把它算进去，
        于是一个不属于 PUB 的端点被算成"已归属"，它真正的缺口就没人报了。"""
        assert domains_for("/api/public/v10/x", None, _index()) == set()

    def test_带主机名和query的也能归属(self):
        """路径来自 HAR，长的是 `http://host:3000/api/...?x=1` 那个样子。"""
        got = domains_for("http://192.168.51.138:3000/api/public/v1/a?x=1", None, _index())
        assert got == {"PUB"}

    def test_查一次不许把索引改了(self):
        """并集写成"拿索引里那个集合接着往上加"的话，第一次查
        `/api/public/v1/templates` 就把 `PUB` **永久写进了 `Templates` 组**，
        之后所有 Templates 端点都被算成已归属 PUB —— PUB 的真缺口从此消失。
        单次调用的返回值是对的，所以这个 bug 在任何一条"查得对不对"的测试里都不红。"""
        idx = _index()
        before = set(idx["byGroup"]["template"])
        domains_for("/api/public/v1/templates", "Templates", idx)
        assert idx["byGroup"]["template"] == before
        assert domains_for("/api/other", "Templates", idx) == {"TEM"}

    def test_归不了属返回空集合而不是瞎猜(self):
        """空集合 = 「清单里找不到这个端点的归属」，**不是**「它没有缺口」。
        S7.4 必须把它单独记账。"""
        assert domains_for("/api/whatever", "Unknown-Group", _index()) == set()


class Test归一化本身要留痕:
    def test_合并了哪些原名要记下来(self):
        """归一化会把「清单把 Tags 改成了 Tag」这个信号吃掉 ——
        除非把合并前的原名留在 `aliases` 里。这跟 `unparsedRows` 是同一条纪律：
        **这一趟对齐时动了什么手脚，页面上要看得见。**"""
        idx = build_group_index({
            "A": {"name": "甲", "groups": ["Tags"], "groupsRaw": "Tags"},
            "B": {"name": "乙", "groups": ["Tag"], "groupsRaw": "Tag"},
        })
        assert idx["byGroup"]["tag"] == {"A", "B"}
        assert idx["aliases"]["tag"] == ["Tags", "Tag"]

    def test_归属规则没读懂的域要单独列出来(self):
        """`ZZZ` 第三列写的是散文，既没组名也没前缀 —— 它的端点归属**算不出来**。
        不列出来的话它在报告上是「0 缺口」，跟真的没缺口长得一模一样。"""
        assert _index()["unresolved"] == ["ZZZ"]

    def test_能读懂的域不许混进unresolved(self):
        """降级清单的**反向锚点**：把没读懂的列出来，很容易滑成"全都列出来"，
        那这一列就成了噪声，跟不列是同一个结果。"""
        assert "PUB" not in _index()["unresolved"]
        assert "SMK" not in _index()["unresolved"]

    def test_没有域码表时索引是空的不是猜的(self):
        idx = build_group_index({})
        assert idx["byGroup"] == {} and idx["byPrefix"] == [] and idx["unresolved"] == []
