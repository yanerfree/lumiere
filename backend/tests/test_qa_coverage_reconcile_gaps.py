"""S7.4 五类缺口。

这一份要盯的**不是**「算得对不对」，而是三件更难看见的事：

  1. **G1 和 G3 字面上重叠**（都含「页面上有 ∧ 没人测」）。分不开的话同一个端点
     同时出现在两张表里，读的人无从知道该找 QA 补清单还是找脚本作者补断言。
  2. **抽不出来 ≠ 没打过。** 脚本 url 抽取必然不完备（变量套变量、helper 封装）。
     把抽不出来的算成「没打过」，第一版就是一片「你们没兑现」，
     然后 QA 那边**合理地**不再看这份报告 —— 这个模块就死了。
  3. **只剩 G2 = 一个更慢的 route-drift。** QA 自己的 `check-route-drift.sh` 已经在
     比「路由表 vs 基线」了。本模块新增的是页面维度和角色维度；页面维度没跑起来时
     必须**自己说出来**，否则报告看着像"跑过了、缺口不多"。
"""
from app.services.qa_coverage_reconcile import (
    EDGE_SOURCES,
    build_group_index,
    compute_gaps,
    covers,
    edge_ok,
    extract_endpoints,
)

_DOMAINS = {
    "POL": {"name": "策略", "groups": ["Policies"], "groupsRaw": "Policies"},
    "TEM": {"name": "模板", "groups": ["Templates"], "groupsRaw": "Templates"},
    "MCP": {"name": "MCP", "groups": ["MCP-Tools"], "groupsRaw": "MCP-Tools"},
}

_ROUTES = [
    {"group": "Policies", "method": "POST", "path": "/api/policies/{}/reject"},
    {"group": "Policies", "method": "GET", "path": "/api/internal/metrics"},
    {"group": "Templates", "method": "PUT", "path": "/api/templates/{}"},
    {"group": "MCP-Tools", "method": "GET", "path": "/api/mcp/tools"},
]

_PAGE = [
    {"page_path": "/policies", "anchor": "[data-testid=bulk-reject]", "label": "批量驳回",
     "control_type": "button", "state": "enabled",
     "endpoints": [{"source": "observed", "method": "POST", "path": "/api/policies/27/reject"}]},
    {"page_path": "/templates", "anchor": "[data-testid=save]", "label": "保存模板",
     "control_type": "button", "state": "enabled",
     "endpoints": [{"source": "observed", "method": "PUT", "path": "/api/templates/9"}]},
    {"page_path": "/mcp", "anchor": "[data-testid=tools]", "label": "工具列表",
     "control_type": "tab", "state": "enabled",
     "endpoints": [{"source": "observed", "method": "GET", "path": "/api/mcp/tools"}]},
    {"page_path": "/policies", "anchor": "th.name", "label": "按名称排序",
     "control_type": "sorter", "state": "enabled", "endpoints": []},
    {"page_path": "/policies", "anchor": "[data-testid=export]", "label": "导出",
     "control_type": "button", "state": "present", "endpoints": []},
]

_SCRIPTS = [
    {"domain": "TEM", "scenarioId": "TEM-03", "path": "tem/03.sh",
     "text": 'curl -s -X GET "$API/api/templates" | jq .\nrun_helper save_template  # curl'},
    {"domain": "MCP", "scenarioId": "MCP-01", "path": "mcp/01.sh",
     "text": 'curl -s -X GET "$BFF/api/mcp/tools" | jq .'},
]


def _gaps(**kw):
    args = dict(page_items=_PAGE, routes=_ROUTES, scripts=_SCRIPTS,
                index=build_group_index(_DOMAINS), claimed_domains={"TEM", "MCP"})
    args.update(kw)
    return compute_gaps(**args)


class Test五类各命中一条:
    def test_五类各命中一条(self):
        """AC 原文：三方账本造桩 ⇒ 五类各命中一条。

        ⚠ **边界写死**：如果实现出来只剩 G2，那就是一个更慢的 route-drift，
        必须推翻重做。所以这条断言的重点不在数量，在于 **G1/G3/G4/G5 都不为空** ——
        它们全都来自页面维度，是本模块唯一比 `check-route-drift.sh` 多出来的东西。
        """
        g = _gaps()
        assert [x["path"] for x in g["g1"]] == ["/api/policies/{}/reject"]
        assert [x["path"] for x in g["g2"]] == ["/api/internal/metrics"]
        assert [x["path"] for x in g["g3"]] == ["/api/templates/{}"]
        assert [x["label"] for x in g["g4"]] == ["按名称排序"]
        assert [x["label"] for x in g["g5"]] == ["导出"]

    def test_测到了的不许报成缺口(self):
        """降级/缺口清单的**反向锚点**：五类都能报，很容易滑成"什么都报一遍"，
        那这份报告就是噪声。脚本真打过的端点，一类都不许出现。"""
        g = _gaps()
        everywhere = [x.get("path") for k in ("g1", "g2", "g3") for x in g[k]]
        assert "/api/mcp/tools" not in everywhere

    def test_每条都带可grep的锚点(self):
        """「每条自带可 grep 的锚点」—— 报告上给出的字符串要能直接拿去
        在路由表/页面枚举里搜到，否则读的人只能凭域码去猜是哪一个。"""
        g = _gaps()
        assert g["g1"][0]["anchor"] == "POST /api/policies/{}/reject"
        assert g["g2"][0]["anchor"] == "GET /api/internal/metrics"
        assert "/policies" in g["g4"][0]["anchor"] and "th.name" in g["g4"][0]["anchor"]


class TestG1和G3怎么分开:
    def test_清单没认领该域算G1找清单(self):
        """POL 一条场景都没有 ⇒ 是 QA 清单缺场景，不是脚本没兑现。"""
        g = _gaps()
        assert g["g1"][0]["domain"] == "POL"
        assert g["g1"][0]["blame"] == "catalog"

    def test_认领了没兑现算G3找脚本(self):
        """TEM 认领了场景、脚本却没打这个端点 ⇒ 找脚本作者，不是找 QA 补清单。
        两者字面上都是「页面上有 ∧ 没人测」，只有「清单认没认领」能分开。"""
        g = _gaps()
        assert g["g3"][0]["domain"] == "TEM"
        assert g["g3"][0]["blame"] == "script"

    def test_同一个端点不许同时进两类(self):
        """不分开的话它会同时出现在 G1 和 G3 里，读的人不知道该找谁 ——
        两张表都会被当成"另一张表的重复"而略过。"""
        g = _gaps()
        assert {x["path"] for x in g["g1"]} & {x["path"] for x in g["g3"]} == set()

    def test_清单一条都没认领时全是G1(self):
        g = _gaps(claimed_domains=set())
        assert g["g3"] == []
        # `/api/mcp/tools` 脚本真打过 —— 没人认领它也**不是**缺口。
        # 「谁的锅」和「有没有缺口」是两个问题，别让前者把后者带歪。
        assert {x["path"] for x in g["g1"]} == {"/api/policies/{}/reject",
                                                "/api/templates/{}"}

    def test_G3严重度低于G1(self):
        """Epic 写死的：G3 默认严重度低于 G1。第一版把「你们没兑现」排在最上面，
        等于拿一份不完备的抽取去指责别人。"""
        g = _gaps()
        assert g["g1"][0]["severity"] == "high"
        assert g["g3"][0]["severity"] == "low"


class Test抽不出来不等于没打过:
    def test_抽不出来的行要记账(self):
        """`run_helper save_template  # curl` 明显在发请求但 url 拼不出来。
        它必须留在账本里 —— 静默丢掉的话，这条脚本看起来就是"什么都没测"。"""
        g = _gaps()
        assert g["counters"]["endpointsUnextracted"] == 1
        assert g["endpointsUnextracted"][0]["scenarioId"] == "TEM-03"

    def test_G3必须带没抽出来的行数(self):
        """带着它，读的人才知道这条「没兑现」有多可信。
        不带的话第一版喷出一片指责，然后 QA 那边**合理地**不再看这份报告。"""
        assert _gaps()["g3"][0]["endpointsUnextracted"] == 1

    def test_注释掉的调用不算(self):
        hits, misses = extract_endpoints('# curl -s "$API/api/x/y"')
        assert hits == [] and misses == []

    def test_没有调用痕迹的普通行不进账本(self):
        """账本的反向锚点：把读不懂的记下来，很容易滑成"整个文件都读不懂"，
        那个数字一大就没人看了，等于没记。"""
        _, misses = extract_endpoints("echo hello\nset -euo pipefail\nlocal x=1")
        assert misses == []

    def test_同一行多个url都要抽出来(self):
        hits, _ = extract_endpoints('curl "$API/api/a" && curl "$BFF/api/b"')
        assert [h["path"] for h in hits] == ["/api/a", "/api/b"]

    def test_带query和主机名的都归一掉(self):
        """这份保证**不在本模块**，是 `normalize_path` 给的（它连 host 一起剥）。
        本地再 split 一次是死代码，删了 —— 留着会让人以为改 `normalize_path`
        不影响这里。这条测试就是那个依赖的封样。"""
        hits, _ = extract_endpoints('curl "$API/api/a?page=2&size=10"')
        assert hits[0]["path"] == "/api/a"


class Test算不算打过:
    def test_部署前缀差一段照样算打过(self):
        """`$API` 展开成 `http://host` 还是 `http://host/api`，**我们说了不算** ——
        那是别人的脚本和别人的环境。严格相等会让 G3 整个炸开。"""
        assert covers("/policies/{}/reject", "/api/policies/{}/reject")

    def test_差太多就不算(self):
        """放宽到"后缀对上就算"的话，`/a/b` 会跟一堆端点都对上 ——
        于是一批真没测的端点被算成测过了，**缺口凭空消失，而且不会红**。"""
        assert not covers("/a/b", "/x/y/z/a/b")

    def test_太短的锚点不算(self):
        """`/tools` 太弱，会跟 `/api/mcp/tools`、`/api/agent/tools` 都对上。"""
        assert not covers("/tools", "/api/mcp/tools")

    def test_方法不同不算打过(self):
        """脚本 GET 过 `/api/templates`，不代表 PUT `/api/templates/{}` 测过了。"""
        assert [x["path"] for x in _gaps()["g3"]] == ["/api/templates/{}"]

    def test_同一条路径不同方法也不算打过(self):
        """上一条其实是**路径**就对不上，方法比不比都红 —— 它证明不了方法这一关。
        这条把路径钉成完全相同，只剩方法能分开：GET 过一个只读接口，
        不代表那个改数据的 PUT 有人测。"""
        g = _gaps(scripts=[{"domain": "TEM", "scenarioId": "TEM-03",
                            "text": 'curl -s -X GET "$API/api/templates/9"'},
                           _SCRIPTS[1]])
        assert [x["path"] for x in g["g3"]] == ["/api/templates/{}"]

    def test_脚本没写方法时不因方法漏报(self):
        """`-X` 缺省是 GET，但脚本里也可能用 helper 设方法。宁可算成打过
        （少一条 G3，看得见地少了一条噪声），也不要凭方法凭空造一条指责。"""
        g = _gaps(scripts=[{"domain": "TEM", "scenarioId": "TEM-03",
                            "text": 'curl -s "$API/api/templates/9"'}])
        assert "/api/templates/{}" not in [x["path"] for x in g["g3"]]


class Test控件没请求的两种:
    def test_禁用的是G5情报不是缺口(self):
        """`state == "present"` 在页面枚举里的含义就是 **disabled**
        （`qa_page_survey_crawl.py`：`return "present" if raw.get("disabled") else "enabled"`）。
        死按钮/flag 关掉是**情报**，混进缺口里就是在要求别人去测一个点不动的东西。"""
        g = _gaps()
        assert g["g5"][0]["blame"] == "情报"
        assert g["g5"][0]["label"] == "导出"

    def test_可用但没请求的是G4需判断(self):
        """纯前端行为（排序/筛选/弹窗）—— 不是缺口，也不是情报，是**要人看一眼**。"""
        g = _gaps()
        assert g["g4"][0]["blame"] == "需判断"
        assert g["g4"][0]["label"] == "按名称排序"

    def test_两者不许混成一类(self):
        assert {x["label"] for x in _gaps()["g4"]} & {x["label"] for x in _gaps()["g5"]} == set()


class Test归不了属的单独记账:
    def test_归不了属不塞进任何一类(self):
        """域码表里找不到归属的端点：塞进 G1 是误报（可能压根不该这个域管），
        丢掉是漏报（更坏）。**单独记一笔**，让人去补域码表第三列。"""
        g = _gaps(routes=_ROUTES + [{"group": "Unknown-Group", "method": "GET",
                                     "path": "/api/whatever"}])
        allp = [x.get("path") for k in ("g1", "g2", "g3") for x in g[k]]
        assert "/api/whatever" not in allp
        assert g["counters"]["endpointsUnattributed"] == 1

    def test_页面上归不了属的端点也单独记账(self):
        """上一条走的是**路由表**那一侧。页面这一侧要单独钉一次 ——
        G1（最硬的那类缺口）就是从这里出来的：随手安一个域是凭空指责，
        静默丢掉是把最硬的缺口弄丢了。两个都不行。"""
        g = _gaps(page_items=_PAGE + [
            {"page_path": "/x", "anchor": "[data-testid=z]", "label": "别处来的",
             "control_type": "button", "state": "enabled",
             "endpoints": [{"source": "observed", "method": "GET", "path": "/api/whatever"}]}])
        allp = [x.get("path") for k in ("g1", "g3") for x in g[k]]
        assert "/api/whatever" not in allp
        assert g["counters"]["endpointsUnattributed"] == 1

    def test_归属规则没读懂的域数也带出来(self):
        """S7.3 的 `unresolved` 要一路带到这里 —— 那些域在报告上会是「0 缺口」，
        跟真的没缺口长得一模一样。"""
        idx = build_group_index({**_DOMAINS,
                                 "ZZZ": {"name": "?", "groups": [], "groupsRaw": "见另一份文档"}})
        assert _gaps(index=idx)["counters"]["domainsUnresolved"] == 1


class Test两条降级声明:
    def test_没有路由表时G2未验证(self):
        """S7.2 已经把这句话准备好了。这里要保证 G2 **空着**的同时
        声明也在 —— 空的 G2 和「没有 G2 类缺口」长得一模一样。"""
        g = _gaps(route_table_available=False)
        assert g["g2"] == []
        assert g["dimensions"]["g2"] == "notVerified"
        assert any("G2 未验证" in d for d in g["declarations"])

    def test_没有页面枚举就等于route_drift要自己说出来(self):
        """**Epic 的边界条款**：只剩 G2 的话，这份报告做的事跟 QA 自己的
        `check-route-drift.sh` 一模一样，只是更慢。不明说的话它看起来像
        "跑过了、缺口不多"。"""
        g = _gaps(page_items=[], page_survey_available=False)
        assert g["g1"] == [] and g["g3"] == [] and g["g4"] == [] and g["g5"] == []
        assert g["dimensions"]["page"] == "notVerified"
        assert any("route-drift" in d for d in g["declarations"])

    def test_都跑到了就不许有声明(self):
        """降级声明的**反向锚点**：常驻的免责声明等于没有声明。"""
        g = _gaps()
        assert g["declarations"] == []
        assert g["dimensions"] == {"page": "verified", "routeTable": "verified",
                                   "g2": "verified"}


class Test控件到端点那条边从哪来:
    """S8.1 · P 侧整套账都建在这条边上，所以它必须**说得清自己从哪来**。

    这里防的不是恶意，是**图省事**：让模型补一条"这个按钮大概会发这个请求"，
    比让爬虫真点一遍便宜太多了。而那么做是把「猜」从场景层挪到端点层 ——
    还更隐蔽：场景层的猜写在 `catalogGaps` 里，读的人知道那是模型说的；
    端点层的猜混进 `pageEndpoints`，长得跟 HAR 抓来的一模一样。

    ⚠ 造假的方向是**单向**的：多认一条边 ⇒ 缺口消失 ⇒ 报告更好看 ⇒ 没有一条测试会红。
    """

    def _one(self, ep):
        return compute_gaps(
            page_items=[{"page_path": "/policies", "anchor": "[data-testid=r]",
                         "label": "驳回", "control_type": "button",
                         "state": "enabled", "endpoints": [ep]}],
            routes=_ROUTES, scripts=[], index=build_group_index(_DOMAINS),
            claimed_domains={"POL"})

    _EP = {"method": "POST", "path": "/api/policies/27/reject"}

    def test_观测到的算数(self):
        g = self._one({**self._EP, "source": "observed"})
        assert g["counters"]["pageEndpoints"] == 1
        assert g["counters"]["edgesUnsourced"] == 0

    def test_被拦下来的写请求也算数(self):
        """**L1 的拦截既是闸门也是事实来源。**

        拦下来的那一刻，「这个控件会发这个写请求」已经是观测到的事实了 ——
        不认它的话，安全护栏越严、P 侧账本越空，而空账本报出来是"没有缺口"。
        """
        g = self._one({**self._EP, "source": "aborted"})
        assert g["counters"]["pageEndpoints"] == 1

    def test_没写来源的一律不算数(self):
        """⚠ **别默认成 `observed`。**

        默认放行等于这道闸门不存在：以后任何一条新造边的路径，
        只要"忘了"写来源就自动被采信 —— 而这里防的恰恰是忘。
        """
        g = self._one(dict(self._EP))
        assert g["counters"]["pageEndpoints"] == 0
        assert g["counters"]["edgesUnsourced"] == 1

    def test_模型推断不在白名单里(self):
        assert "model" not in EDGE_SOURCES
        assert "inferred" not in EDGE_SOURCES
        assert set(EDGE_SOURCES) == {"observed", "aborted", "static"}
        assert self._one({**self._EP, "source": "model"})["counters"]["pageEndpoints"] == 0

    def test_丢掉的边整条记账不是记个数(self):
        """只有整行还在，读的人才判得出「丢的是哪几条、该去修哪条产出路径」。"""
        g = self._one({**self._EP, "source": "model"})
        assert g["edgesUnsourced"][0]["source"] == "model"
        assert g["edgesUnsourced"][0]["path"] == "/api/policies/{}/reject"
        assert "[data-testid=r]" in g["edgesUnsourced"][0]["anchor"]

    def test_有请求但全没来源的控件不许算成G4(self):
        """**这条是整组里最要紧的一条。**

        G4 的字面意思是「点了没有请求」。把一个"发了请求、只是没一条说得清出处"
        的控件塞进去，报告上它会长成「这按钮点下去什么都没发生」——
        拿一句假话去填一个空位，比空着坏得多。
        """
        g = self._one({**self._EP, "source": "model"})
        assert g["g4"] == []
        assert g["g5"] == []
        assert g["counters"]["edgesUnsourced"] == 1

    def test_静态提取没指纹就不认(self):
        """源码里写着 ≠ 点下去真会发（条件分支、feature flag、死代码）；
        而指纹对不上时连"源码里写着"都不成立 —— 那是另一个版本的源码。

        **fail-closed**：没人传指纹进来 ⇒ 一条都不认。
        「没查」和「查过了、一致」在这儿绝不能是同一个结果。
        """
        assert edge_ok({"source": "static", "buildFingerprint": "abc"}) is False
        assert edge_ok({"source": "static", "buildFingerprint": "abc"},
                       build_fingerprint="def") is False
        assert edge_ok({"source": "static"}, build_fingerprint="abc") is False
        # 两头都没有 ⇒ `None == None` 会真过。**这是最容易漏的一格**：
        # 谁把 `bool(build_fingerprint)` 当冗余删掉，一条"谁也没查过"的边
        # 就成了合法边，而它长得跟指纹对上的那条一模一样。
        assert edge_ok({"source": "static"}) is False
        assert edge_ok({"source": "static", "buildFingerprint": "abc"},
                       build_fingerprint="abc") is True


class Test计数为0也要渲染:
    def test_一个都没有时计数仍然在(self):
        """只在非 0 时出现的计数，跟「没算过」长得一模一样。"""
        g = compute_gaps(page_items=[], routes=[], scripts=[],
                         index=build_group_index({}), claimed_domains=set())
        assert g["counters"] == {"endpointsUnextracted": 0, "endpointsUnattributed": 0,
                                 "domainsUnresolved": 0, "scriptsScanned": 0,
                                 "edgesUnsourced": 0,
                                 "pageEndpoints": 0, "routeEndpoints": 0}
