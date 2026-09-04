"""§14.2「业务不只是增删改查」+ §13.2「链路骨架 = 脚本里的调用顺序」的封样。

这份测试盯着的是**判据本身**，不是某个域的具体动作名 —— 一旦有人为了让某个
产品好看而往里塞一张动词表，`test_不认识任何产品名词` 会红。
"""
from app.services.qa_business_actions import (
    action_verb,
    chain_of,
    readable_paths_of,
    verb_inventory,
)
from app.services.qa_coverage_reconcile import (
    build_group_index,
    compute_gaps,
    extract_endpoints,
)
from app.services.qa_script_endpoints import extract_helper_calls, parse_helper_lib

_R = {"/api/v1/services", "/api/v1/services/{}", "/api/v1/services/{}/tools",
      "/api/v1/services/{}/logs"}


class Test动作还是增删改查:
    def test_末段是id一律按方法算(self):
        for m, verb in (("GET", "read"), ("PUT", "update"),
                        ("PATCH", "update"), ("DELETE", "delete")):
            got = action_verb(m, "/api/v1/services/$SVC_ID", _R)
            assert (got["verb"], got["kind"]) == (verb, "crud"), m

    def test_同路径有GET的是资源不是动作(self):
        """`POST /x/{}/tools` 和 `POST /x/{}/submit` 长得一模一样。

        **唯一分得开的判据是"这条路径能不能 GET"** —— 能列出来的是子集合，
        往里 POST 是"加一个"；列不出来的才是业务动作。
        """
        got = action_verb("POST", "/api/v1/services/$ID/tools", _R)
        assert (got["verb"], got["kind"]) == ("create", "crud")

    def test_同路径没有GET的是业务动作(self):
        got = action_verb("POST", "/api/v1/services/$ID/submit", _R)
        assert (got["verb"], got["kind"]) == ("submit", "action")

    def test_不带id的动作也认得出来(self):
        """`POST /x/import` 不跟在 id 后面，但它同样不是增删改查。"""
        got = action_verb("POST", "/api/v1/services/import", _R)
        assert (got["verb"], got["kind"]) == ("import", "action")

    def test_集合根的POST是建不是动作(self):
        got = action_verb("POST", "/api/v1/services", _R)
        assert (got["verb"], got["kind"]) == ("create", "crud")

    def test_GET深路径单独一档不许丢(self):
        """导出/下载/统计/下钻都长这样。丢了等于「这个域没有导出功能」。"""
        got = action_verb("GET", "/api/v1/services/$ID/logs", _R)
        assert (got["verb"], got["kind"]) == ("logs", "subread")

    def test_方法读不出来的不许当成动作(self):
        """一条读不懂的行不许给这个域凭空加一个业务动作。"""
        got = action_verb("", "/api/v1/services/$ID/submit", _R)
        assert (got["verb"], got["kind"]) == ("", "")

    def test_不认识任何产品名词(self):
        """换个产品、换套命名，判据照样成立。

        `ratify` / `activate` 这类词**不在任何清单里**，但它们同样被判成动作 ——
        靠的是「没有对应的 GET」，不是认识这个词。
        """
        for word in ("ratify", "activate", "zzz-whatever", "manual-sign"):
            got = action_verb("POST", "/gw/tenants/$T/" + word, {"/gw/tenants/{}"})
            assert (got["verb"], got["kind"]) == (word, "action"), word


class Test没有GET集合时要保守:
    """空集合读作「查过了，都不能 GET」——那会给每个域造出一堆假动作。"""

    def test_普通的建不许被算成动作(self):
        got = action_verb("POST", "/api/v1/services", None)
        assert got["kind"] == "crud"
        assert got["verb"] == "create"

    def test_跟在id后面的才算动作而且写明依据弱(self):
        got = action_verb("POST", "/api/v1/services/$ID/submit", None)
        assert (got["verb"], got["kind"]) == ("submit", "action")
        assert "依据弱" in got["why"]

    def test_宁可少报一个也不多报一个(self):
        """`/x/import` 这时候会被少报成 create。

        **方向是故意的**：少报一个动作是缺一格，多报一个是造一格假的 ——
        后者会让「页面上没这个按钮」变成一条查不出来的假缺口。
        """
        assert action_verb("POST", "/api/v1/services/import", None)["kind"] == "crud"

    def test_空集合和没给不是一回事(self):
        """传 `set()` 就是明说「都不能 GET」，那时该按主判据判。"""
        assert action_verb("POST", "/api/v1/services", set())["kind"] == "action"
        assert action_verb("POST", "/api/v1/services", None)["kind"] == "crud"


class Test链路骨架:
    _HITS = [
        {"method": "POST", "path": "/api/v1/svc", "lineNo": 10},
        {"method": "GET", "path": "/api/v1/svc/$ID", "lineNo": 12},
        {"method": "POST", "path": "/api/v1/svc/$ID/submit", "lineNo": 20},
        {"method": "POST", "path": "/api/v1/svc/$ID/approve", "lineNo": 30},
        {"method": "DELETE", "path": "/api/v1/svc/$ID", "lineNo": 40},
    ]

    def test_按文件顺序不按字母顺序(self):
        steps = chain_of(self._HITS)
        assert [x["verb"] for x in steps] == ["create", "submit", "approve", "delete"]
        assert [x["seq"] for x in steps] == [1, 2, 3, 4]

    def test_行号乱序传进来照样排得对(self):
        steps = chain_of(list(reversed(self._HITS)))
        assert [x["verb"] for x in steps] == ["create", "submit", "approve", "delete"]

    def test_读操作默认不进链路(self):
        assert all(x["method"] != "GET" for x in chain_of(self._HITS))
        assert any(x["method"] == "GET"
                   for x in chain_of(self._HITS, keep_reads=True))

    def test_连续重复压成一步(self):
        """轮询/重试在文件里是几十行，在业务上是一步。"""
        hits = [{"method": "POST", "path": "/a/b/$I/submit", "lineNo": i}
                for i in range(1, 8)]
        assert len(chain_of(hits)) == 1

    def test_只压连续的不压隔开的(self):
        """`建→提交→建→提交` 是真的两轮，不许压成一轮。"""
        hits = [{"method": "POST", "path": "/a/b", "lineNo": 1},
                {"method": "POST", "path": "/a/b/$I/submit", "lineNo": 2},
                {"method": "POST", "path": "/a/b", "lineNo": 3},
                {"method": "POST", "path": "/a/b/$I/submit", "lineNo": 4}]
        assert len(chain_of(hits)) == 4

    def test_没有行号时按传入顺序(self):
        hits = [{"method": "POST", "path": "/a/b"},
                {"method": "POST", "path": "/a/b/$I/submit"}]
        assert [x["seq"] for x in chain_of(hits)] == [1, 2]


class Test动作面清单:
    def test_三档分开数(self):
        inv = verb_inventory(Test链路骨架._HITS, readable_paths={"/api/v1/svc"})
        assert sorted(inv["actions"]) == ["approve", "submit"]
        assert sorted(inv["crud"]) == ["create", "delete", "read"]

    def test_动作和增删改查不许混成一档(self):
        """混了之后「这个域有几个非增删改查的动作」就永远答不出来了。"""
        inv = verb_inventory(Test链路骨架._HITS, readable_paths={"/api/v1/svc"})
        assert "create" not in inv["actions"]
        assert "submit" not in inv["crud"]

    def test_可读路径能从命中里兜底抽出来(self):
        assert readable_paths_of(Test链路骨架._HITS) == {"/api/v1/svc/{}"}


class Test跟对账接上:
    _LIB = {"lib/api.sh": 'api_post() { curl -X POST "${API}$1" ; }\n'
                          'api_get() { curl "${API}$1" ; }\n'}

    def _script(self, body):
        return [{"domain": "SVC", "scenarioId": "S-1", "path": "svc/x.sh", "text": body}]

    def _gaps(self, body, routes=None):
        return compute_gaps(page_items=[], routes=routes or [],
                            scripts=self._script(body),
                            index=build_group_index({"SVC": {"name": "服务"}}),
                            claimed_domains={"SVC"}, helper_lib=self._LIB)

    def test_行号一路带到链路上(self):
        g = self._gaps('api_get "/services"\n'
                       'api_post "/services"\n'
                       'api_post "/services/$ID/submit"\n'
                       'api_post "/services/$ID/approve"\n')
        assert g["counters"]["chains"] == 1
        assert [s["verb"] for s in g["chains"][0]["steps"]] == \
            ["create", "submit", "approve"]

    def test_动作面按域分(self):
        g = self._gaps('api_post "/services/$ID/submit"\napi_get "/services"\n')
        assert list(g["businessActions"]["SVC"]["actions"]) == ["submit"]
        assert g["counters"]["actionVerbs"] == 1

    def test_一步不叫链路(self):
        g = self._gaps('api_post "/services"\n')
        assert g["chains"] == []
        # 但它的动作还是数到了 —— 链路数和动作数是两回事。
        assert g["counters"]["chainSteps"] == 1

    def test_两个抽取器的命中按行号合并(self):
        """写在行里的 url 和 helper 封装的调用，在同一个文件里要能排到一起。"""
        g = self._gaps('api_post "/services"\n'
                       'curl -X POST "${API}/services/$ID/submit"\n'
                       'api_post "/services/$ID/approve"\n')
        assert [s["verb"] for s in g["chains"][0]["steps"]] == \
            ["create", "submit", "approve"]

    def test_两个抽取器都带行号(self):
        hits, _ = extract_endpoints('x\ncurl "${API}/a/b"\n')
        assert hits[0]["lineNo"] == 2
        out = extract_helper_calls('x\napi_post "/a/b"\n', parse_helper_lib(self._LIB))
        assert out["hits"][0]["lineNo"] == 2

    def test_一条GET都没有时要声明依据弱(self):
        g = self._gaps('api_post "/services/$ID/submit"\n')
        assert any("分不开" in d for d in g["declarations"])

    def test_有路由表时用它当可读集合(self):
        """R 边比 Q 边自己的 GET 准 —— 有它就不该出那条「依据弱」声明。"""
        g = self._gaps('api_post "/services"\n'
                       'api_post "/services/$ID/submit"\n',
                       routes=[{"method": "GET", "path": "/api/v1/services"}])
        assert not any("分不开" in d for d in g["declarations"])
        assert [s["verb"] for s in g["chains"][0]["steps"]] == ["create", "submit"]


class Test两本账的前缀对不上:
    """R 边带 `/api/v1`，Q 边不带 —— 按字面比一条都对不上。

    这不是小瑕疵：对不上的时候每个普通的 `POST /services`（建一条）
    都会被判成一个叫 `services` 的业务动作，于是每个域凭空多出一堆假动作。
    """

    def test_差几段部署前缀也算同一条路径(self):
        got = action_verb("POST", "/services", {"/api/v1/services"})
        assert (got["verb"], got["kind"]) == ("create", "crud")

    def test_反过来也认(self):
        """谁带前缀我们事先不知道 —— 两个方向都要试。"""
        got = action_verb("POST", "/api/v1/services", {"/services"})
        assert (got["verb"], got["kind"]) == ("create", "crud")

    def test_差太多段就不是同一条了(self):
        """容忍是给部署前缀留的，不是给「末段一样就算」留的。"""
        got = action_verb("POST", "/a/b/c/services", {"/services"})
        assert got["kind"] == "action"

    def test_id段只跟id段相等(self):
        """`GET /x/{}` 不许把 `POST /x/import` 兜成「往子集合里加一个」——
        兜住了 `import` 这个业务动作就凭空消失，而且不报错。"""
        got = action_verb("POST", "/api/v1/services/import", {"/api/v1/services/{}"})
        assert (got["verb"], got["kind"]) == ("import", "action")
