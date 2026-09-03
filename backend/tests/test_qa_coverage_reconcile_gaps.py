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
from app.services.qa_coverage_reconcile import (  # noqa: E402  私有的两个：join 的判据本身要能单独封样
    _lookup,
    _same_endpoint,
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


# 一份**最小**的 helper 库：只为让「都跑到了」这个状态可造出来。
# 故意不定义 `_SCRIPTS` 里出现的那个 `run_helper` —— 桩里那行是"漏读"的样本，
# 让它突然变成命中会把 G1/G3 的桩一起改掉，那就不是在测降级声明了。
_HELPER_LIB = {"lib/common.sh": 'api_get() {\n  local path="$1"\n'
                               '  curl -s "${API}${path}"\n}\n'}


def _gaps(**kw):
    # `controls_clicked` 显式给一个正数：G4（"点了没有请求"）的硬前提是**点过**，
    # 缺省 `None` 时它一条都不产出。这份桩要造出"五类各一条"，所以这一趟得是
    # "点过的"那种。今天的爬取一个控件都不点 —— 那条口径在 `TestG4要点过才算`。
    args = dict(page_items=_PAGE, routes=_ROUTES, scripts=_SCRIPTS,
                index=build_group_index(_DOMAINS), claimed_domains={"TEM", "MCP"},
                helper_lib=_HELPER_LIB, controls_clicked=len(_PAGE))
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

    def test_网关的url绝不能算成BFF的(self):
        """⚠ **这条是单向致命的那种。** `covers()` 容忍 2 段部署前缀，
        所以 `${GW}/v1/chat/completions`（Kong）会跟 BFF 的
        `/api/v1/chat/completions` 对上 —— 一个网关调用抹掉一个 BFF 缺口，
        缺口凭空消失，没有任何测试会红。

        所以 `GW` **不在** `_URL_TOKEN` 里；它走口径外那条路：不进命中、
        也不算"读不懂"（读懂了，只是打的不是 BFF）。"""
        hits, misses = extract_endpoints('curl -s "${GW}/v1/chat/completions"')
        assert hits == [] and misses == []

    def test_AUTH前缀要认(self):
        """实读对方 `env.sh`：`AUTH=${BFF}/api/auth` —— 登录/刷新/登出那一批
        全走它。漏掉这个前缀，那批端点会整批变成"没人测"。"""
        hits, _ = extract_endpoints('curl -s -X POST "${AUTH}/login"')
        assert [(h["method"], h["path"]) for h in hits] == [("POST", "/login")]

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


class Test三条降级声明:
    def test_没读到helper库要说出来(self):
        """Q 边**大头在 helper 里**：实测同一个仓库（`refs/remotes/origin/main`，
        369 个脚本），只认写在行里的 url 是 136 条命中，把 `lib/*.sh` 的 helper
        签名解出来是 2943 条 —— 差 20 倍。

        所以"没读到 helper 库"不是个附注，是**这份报告的结论全反了**：
        Q 边空掉 ⇒ G1/G3 一片假缺口 ⇒ 看起来像"他们真的少测了很多"。
        这条声明跟另外两条同等，不许降级成注释或日志。"""
        g = _gaps(helper_lib=None)
        assert any("helper" in d for d in g["declarations"])
        assert g["counters"]["helpersParsed"] == 0

    def test_读不出参数位置的helper要点名(self):
        """读失败的 helper 一律让它的调用点**记漏读**（宁可漏报不可误报），
        但必须**点名**是哪几个 —— 不点名的话，"这个端点没人测"和
        "这个 helper 我没读懂"在报告上长得一模一样。"""
        # 路径来自另一个变量、不是位置参数 ⇒ 参数位置读不出来
        lib = {"lib/x.sh": 'weird_get() {\n  local ep="$OTHER"\n'
                           '  curl -s -X GET "${API}${ep}"\n}\n'}
        g = _gaps(helper_lib=lib)
        assert g["counters"]["helpersUnparsed"] == 1
        assert any("weird_get" in d for d in g["declarations"])

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
        """降级声明的**反向锚点**：常驻的免责声明等于没有声明。

        这里要显式喂一条页面级 P 边 —— `page_edges` 缺省是 `None`（"这趟没算过"），
        而那也是一条声明。桩里挑的是脚本已经打过的那个端点，免得顺手多造一条缺口。
        """
        g = _gaps(page_edges=[{"source": "observed", "pagePath": "/mcp",
                               "method": "GET", "path": "/api/mcp/tools"}])
        assert g["declarations"] == []
        assert g["dimensions"] == {"page": "verified", "routeTable": "verified",
                                   "g2": "verified", "g4": "verified"}


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
                                 "pageEndpoints": 0, "routeEndpoints": 0,
                                 # Q 边分四本账 + 三个解析计数：`qHelperHits` 掉回 0
                                 # 是"helper 库没读到／对方改了签名"的唯一信号，
                                 # 而那时候 G1/G3 会暴涨、且看起来完全正常。
                                 "qInlineHits": 0, "qHelperHits": 0,
                                 "qOutOfScope": 0, "qInfraCalls": 0,
                                 "helpersParsed": 0, "helpersInfra": 0,
                                 "helpersUnparsed": 0,
                                 # 页面级 P 边的条数。0 和"这趟没算"要能分开，
                                 # 后者看 declarations，不是看这里少一个键。
                                 "pageLoadEdges": 0,
                                 # 点过几个控件 / 本来会落进 G4 的有几个。
                                 # 两个都得在：G4 那张表空着有两种完全不同的
                                 # 原因（没点过 vs 点了都有请求），只有这两个数
                                 # 能分开。
                                 "controlsClicked": 0, "controlsUnclicked": 0}


class Test页面级的边:
    """S8.2 · 无向枚举**一个控件都不点**，所以控件级的边天生接近空。

    页面加载时的流量是这一维唯一的真实来源。它和控件级的边合进同一本 P 账，
    但绝不能长成同一种东西 —— 「有人点了这个按钮」和「打开这一页浏览器自己
    发的」是两个事实，混起来读的人会去页面上找一个不存在的控件。
    """

    _EDGE = {"source": "observed", "pagePath": "/policies",
             "method": "POST", "path": "/api/policies/27/reject"}

    def test_页面级的边照样进P账(self):
        g = _gaps(page_items=[], page_edges=[dict(self._EDGE)])
        assert [x["path"] for x in g["g1"]] == ["/api/policies/{}/reject"]
        assert g["counters"]["pageEndpoints"] == 1
        assert g["counters"]["pageLoadEdges"] == 1

    def test_页面级的边要标出自己是页面级的(self):
        """报告上要能一眼分开。`origin` 缺省是 `control` —— 没有这个字段的话
        「打开页面就打了这个端点」会被读成「有人点了什么」，然后照着锚点
        去页面上找那个控件，找不到。
        """
        g = _gaps(page_items=[], page_edges=[dict(self._EDGE)])
        assert [x["origin"] for x in g["g1"]] == ["page"]
        assert g["g1"][0]["controlAnchor"] == "/policies :: (页面加载)"
        assert g["g1"][0]["label"] == "(页面加载)"

    def test_控件级的边压过页面级的(self):
        """同一个端点两边都有时，报告该指那个控件 —— 那是更具体的事实。"""
        g = _gaps(page_edges=[dict(self._EDGE)])
        assert [x["origin"] for x in g["g1"]] == ["control"]
        assert "bulk-reject" in g["g1"][0]["controlAnchor"]

    def test_说不清出处的页面级边也不采信但要记数(self):
        """和控件边同一个理由：`edge_ok` 拒掉的边一条都不许进账，
        但**扔掉多少条**必须看得见 —— 否则 P 账变空长得像「没缺口」。
        """
        g = _gaps(page_items=[],
                  page_edges=[{"source": "guessed", "pagePath": "/policies",
                               "method": "GET", "path": "/api/x"}])
        assert g["counters"]["pageEndpoints"] == 0
        assert g["counters"]["pageLoadEdges"] == 0
        assert g["counters"]["edgesUnsourced"] == 1
        assert g["edgesUnsourced"][0]["source"] == "guessed"
        assert g["edgesUnsourced"][0]["anchor"] == "/policies :: (页面加载)"

    def test_没算过页面级边要明说(self):
        """`None` = 老 survey / 这趟没算。不说的话 G1/G3 双双接近 0，
        在页面上长得像「这个域没缺口」。
        """
        g = _gaps(page_edges=None)
        assert any("这一维没验" in d for d in g["declarations"])

    def test_算出来是0条也要明说(self):
        """`[]` ≠ `None`：算过了、确实一条都没有。页面加载不打任何接口不正常，
        真相通常在账本的 `edgesUnwindowed` / `edgesUnusable` 里。
        """
        g = _gaps(page_edges=[])
        assert any("edgesUnwindowed" in d for d in g["declarations"])
        assert not any("这一维没验" in d for d in g["declarations"])

    def test_没跑页面枚举时不许拿页面级边硬撑(self):
        """`page_survey_available=False` 那条声明不能被这条盖掉 ——
        「这一维压根没跑」比「跑了但一条边都没算出来」严重得多。
        """
        g = _gaps(page_items=[], page_survey_available=False, page_edges=[])
        assert any("route-drift" in d for d in g["declarations"])
        assert not any("edgesUnwindowed" in d for d in g["declarations"])


class Test模板对真id:
    """S8.2 · P 侧是浏览器真发的路径，R 侧是路由模板。**这个 join 错了两边都假。**

    `normalize_path` 只压得动 uuid 和纯数字，slug 型 id（`kong-prod`）压不动。
    """

    _R = [{"group": "Policies", "method": "GET", "path": "/api/adapters/{}"},
          {"group": "Policies", "method": "GET", "path": "/api/adapters/health"}]
    _P = [{"page_path": "/adapters", "anchor": "tr.row", "label": "适配器",
           "control_type": "row", "state": "enabled",
           "endpoints": [{"source": "observed", "method": "GET",
                          "path": "/api/adapters/kong-prod"}]}]

    def test_slug型id不许报成页面上没有(self):
        """**反向锚点**：换回 `k in p_eps` 那种字面量比较，这条立刻红。
        它当时的表现是「多一条 G2」—— 报告说 R 有这个路由而页面上没有，
        而页面上明明刚打过。
        """
        g = _gaps(routes=self._R[:1], scripts=[], claimed_domains=set(),
                  page_items=self._P)
        assert g["g2"] == []

    def test_精确的不许被通配的抢走(self):
        """`/api/adapters/health` 真存在，页面上没打过就该报 G2。通配兜底排在
        精确命中后面才成立 —— 反过来它会被 `/api/adapters/{}` 吃掉，
        而**少一条缺口是看不见的那一侧**。
        """
        g = _gaps(routes=self._R, scripts=[], claimed_domains=set(),
                  page_items=self._P)
        assert [x["path"] for x in g["g2"]] == ["/api/adapters/health"]

    def test_域也要能查到(self):
        """join 断了的另一面：P 那条查不到 `group`，域就归错 —— 要么挂进
        「归属规则没读懂」，要么归到别的域名下。
        """
        g = _gaps(routes=self._R[:1], scripts=[], claimed_domains=set(),
                  page_items=self._P)
        assert [x["domain"] for x in g["g1"]] == ["POL"]
        assert g["counters"]["endpointsUnattributed"] == 0

    def test_段数不等不算同一个端点(self):
        """`covers()` 那两段部署前缀容忍是给 Q 侧（别人仓库里的路径）用的。
        P 和 R 都是同一个 BFF 自报的，多一段就是另一个端点。
        """
        assert not _same_endpoint("GET", "/api/adapters", "GET", "/api/adapters/{}")
        assert _same_endpoint("GET", "/api/adapters/x", "GET", "/api/adapters/{}")
        assert not _same_endpoint("GET", "/api/adapters/x", "POST", "/api/adapters/{}")
        assert not _same_endpoint("GET", "", "GET", "")

    def test_方法空着算通配(self):
        """R 偶尔不报 method。空着当"对不上"会让那条路由永远报 G2。"""
        assert _same_endpoint("", "/api/adapters/x", "GET", "/api/adapters/{}")
        assert _same_endpoint("GET", "/api/adapters/x", "", "/api/adapters/{}")

    def test_查表精确优先通配兜底(self):
        """`_lookup` 的次序封样。`{}` 容忍是有代价的：`/adapters/health` 对
        `/adapters/{}` 也成立。次序反过来的话字面量路由被通配路由抢走，
        G2 少一条 —— **少一条缺口是看不见的那一侧**。
        """
        table = {"GET /api/adapters/{}": {"method": "GET", "path": "/api/adapters/{}"},
                 "GET /api/adapters/health": {"method": "GET",
                                              "path": "/api/adapters/health"}}
        exact = _lookup("GET /api/adapters/health", "GET", "/api/adapters/health", table)
        assert exact["path"] == "/api/adapters/health"
        slug = _lookup("GET /api/adapters/kong-prod", "GET", "/api/adapters/kong-prod",
                       table)
        assert slug["path"] == "/api/adapters/{}"
        assert _lookup("GET /api/teams", "GET", "/api/teams", table) is None


class TestG4要点过才算:
    """S8.2 · **G4 的字面意思是「点下去，什么请求都没发」。**

    今天的页面枚举一个控件都不点（无向枚举：它不知道自己会造出什么，也清理不掉），
    于是每个 enabled 控件的 `endpoints` 都是空的。照"空就是 G4"写，报告上会出现
    一整页「这些按钮点下去什么都不发生」—— 一句假话乘以控件数，而且**读起来完全
    像真的**：它有锚点、有页面、有控件类型，只是那件被断言的事从没发生过。

    所以这里的方向是**宁可这一维空着**：没有点击证据就不产出，但要记数 + 声明。
    反过来的错（G4 空着、还不说为什么）在下面也封了 —— 空表加沉默等于"没缺口"。
    """

    def test_没点过就一条G4都不许有(self):
        g = _gaps(controls_clicked=0)
        assert g["g4"] == []
        # 但 G5 照旧：disabled 控件"没有请求"是看得见的事实，不需要点。
        assert [x["label"] for x in g["g5"]] == ["导出"]

    def test_没点过要记数并且说出来(self):
        """空的 G4 有两种完全不同的原因，只有这两个数能分开。"""
        g = _gaps(controls_clicked=0)
        assert g["counters"]["controlsClicked"] == 0
        assert g["counters"]["controlsUnclicked"] == 1      # "按名称排序"那个
        assert g["dimensions"]["g4"] == "notVerified"
        assert any("一个控件都没点" in d for d in g["declarations"])

    def test_连点没点都没报比明说没点更坏(self):
        """`None` = 这一趟连"点过几个"这件事都没报（老 survey / 调用方漏传）。
        **fail-closed**：同样不产出 G4，但声明要说的是另一件事 —— 缺的是账本身。"""
        g = _gaps(controls_clicked=None)
        assert g["g4"] == []
        assert g["dimensions"]["g4"] == "notVerified"
        assert any("没报" in d for d in g["declarations"])
        assert not any("一个控件都没点" in d for d in g["declarations"])

    def test_点过了就照常报(self):
        """反向锚点：这道闸门很容易滑成"G4 永远不产出"，那就等于把一类缺口删了。"""
        g = _gaps(controls_clicked=3)
        assert [x["label"] for x in g["g4"]] == ["按名称排序"]
        assert g["dimensions"]["g4"] == "verified"
        assert g["counters"]["controlsUnclicked"] == 0

    def test_控件自己说没点过就压过run级的数(self):
        """将来只点一部分控件的那一趟：没点的那些不能跟着 run 级的"点过"
        一起被记成 G4。item 上的 `clicked` 更具体，优先它。"""
        page = [dict(x) for x in _PAGE]
        page[3]["clicked"] = False                          # "按名称排序"
        g = _gaps(page_items=page, controls_clicked=3)
        assert g["g4"] == []
        assert g["counters"]["controlsUnclicked"] == 1

    def test_控件自己说点过了就算(self):
        page = [dict(x) for x in _PAGE]
        page[3]["clicked"] = True
        g = _gaps(page_items=page, controls_clicked=0)
        assert [x["label"] for x in g["g4"]] == ["按名称排序"]

    def test_没跑页面枚举时不许再多一条G4声明(self):
        """没跑页面枚举的那一趟已经有一条"等同 route-drift"的总声明了，
        再叠一条"没点过控件"是噪声 —— 而声明一多，读的人就不读了。"""
        g = _gaps(page_items=[], page_survey_available=False, controls_clicked=None)
        assert not any("控件" in d for d in g["declarations"])
        assert g["dimensions"]["g4"] == "notVerified"


class Test爬取那边报的数和这边收的参数是一对:
    def test_爬取把controlsClicked明写成0(self):
        """`compute_gaps(controls_clicked=...)` 的**唯一**上游事实。

        缺这个键，下游只能拿 `None` 兜底 —— 结论一样（G4 不产出），但声明会变成
        "连点没点都没报"，读的人会去查爬取是不是坏了。所以键名和参数是一对，
        别单改一边。这里连名字一起封。
        """
        import inspect

        from app.engine.surveys import qa_page_survey_crawl as crawl
        src = inspect.getsource(crawl.run_survey)
        assert '"controlsClicked": 0' in src
        assert "controls_clicked" in inspect.signature(compute_gaps).parameters
