# -*- coding: utf-8 -*-
"""Q 边 helper 解析器的封样。

这个模块的每一条错都是**单向**的：多认一条 helper 调用 ⇒ Q 边多一条"测过了"
⇒ 一个真缺口凭空消失 ⇒ 报告更好看 ⇒ **没有任何一条测试会红**。
所以下面的用例几乎全是在钉「什么时候必须不认」，不是在钉"能认出多少"。

下面的 helper 形状都照抄 UAG QA 仓 `lib/*.sh` 的真实写法（只删无关的行），
不是编的 —— 编出来的形状会把解析器钉在一个对方根本不用的语法上。
"""
import pytest

from app.services.qa_script_endpoints import (
    extract_helper_calls,
    normalize_script_path,
    parse_helper_lib,
)

# ── 照抄对方 lib/common.sh 的几种真实形状 ────────────────────────────
LIB = {"lib/common.sh": (
    # ① 方法是 curl 默认（GET），路径在 $1
    'api_get() {\n'
    '  local path="$1"\n'
    '  curl -s "${API}${path}" -H "Authorization: Bearer $TOKEN"\n'
    '}\n'
    # ② 带鉴权：url 前面那个词是 `$tok`，而 tok 也是位置参数 —— 见下面那条测试
    'api_get_as() {\n'
    '  local tok="$1" path="$2"\n'
    '  curl -s -H "Authorization: Bearer $tok" "${API}${path}"\n'
    '}\n'
    # ③ 方法在位置参数里，用 `-X "$m"` 传给 curl
    'api_json_code() {\n'
    '  local m="$1" path="$2" body="$3"\n'
    '  curl -s -o /dev/null -w "%{http_code}" -X "$m" "${API}${path}" -d "$body"\n'
    '}\n'
    # ④ 方法在位置参数里，但**不用 -X**：直接把 url 拼好交给下层 helper
    'api_json_once() {\n'
    '  local m="$1" path="$2" body="$3"\n'
    '  http_once "$m" "${API}${path}" -H "$CT" -d "$body"\n'
    '}\n'
    # ⑤ 路径写死在 helper 里：探活/登录/自检那一类
    'require_login() {\n'
    '  curl -s "${API}/admin-users?page=1&page_size=1" -H "Authorization: Bearer $TOKEN"\n'
    '}\n'
    # ⑥ 打的不是 BFF（Kong）
    'gw_call() {\n'
    '  local p="$1"\n'
    '  curl -s "${GW}${p}"\n'
    '}\n'
    # ⑦ 自己不拼 url，转手调 ①，路径原样传下去
    'list_of() {\n'
    '  api_get "$1"\n'
    '}\n'
    # ⑧ 自己不拼 url，转手调 ①，但路径是自己拼的
    'tools_of() {\n'
    '  api_get "/mcp/$1/tools"\n'
    '}\n'
)}


@pytest.fixture(scope="module")
def P():
    return parse_helper_lib(LIB)


class Test参数位置从对方源码解析:
    """写死一张 `api_json_code = (方法在$1, 路径在$2)` 的表能跑，但它会**漂移**：
    对方改一次签名，我们照旧按老位置取参 —— 取到的是 token 或 body，
    而 `normalize_path` 照样能把它变成一条像路径的东西，然后拿去 `covers()`
    碰运气。抽错路径不报错，只让一个真缺口消失。"""

    def test_路径和方法的位置都从正文解出来(self, P):
        assert P["helpers"]["api_json_code"]["pathPos"] == 2
        assert P["helpers"]["api_json_code"]["methodPos"] == 1

    def test_curl没写方法就是GET(self, P):
        """这是 **curl 的语义**，不是我们的猜测。"""
        assert P["helpers"]["api_get"]["method"] == "GET"
        assert P["helpers"]["api_get"]["pathPos"] == 1

    def test_鉴权token绝不能被当成方法(self, P):
        """⚠ 回归封样。`api_get_as` 的 url 前面那个词是
        `-H "Authorization: Bearer $tok"` 里的 `$tok`，而 `tok` 恰好也是位置参数
        `$1` —— 「紧挨 url 前面那个词就是方法」这条规则会把**鉴权 token 当成方法**。

        后果不是少认一个 helper：它变成"方法在位置 1"，于是每个调用点传的 token
        都不是字面方法 ⇒ 一律记漏读 ⇒ `api_get_as` 那一整批 GET 覆盖凭空消失。
        判据得是结构性的：切到本句后必须正好是 `命令 方法` 两个词。"""
        spec = P["helpers"]["api_get_as"]
        assert (spec["method"], spec["methodPos"]) == ("GET", 0)
        assert spec["pathPos"] == 2

    def test_方法当位置参数传给下层helper也要认(self, P):
        """他们大量用 `http_once "$m" "${API}${path}"` —— 方法是位置参数，
        不是 `-X`。只认 `-X` 的话 `mcpb_post_as`（名字里明写着 post）会被判成
        GET：它的调用点全变成假的 GET 覆盖，而它真正打的 POST 端点显示没人测。"""
        assert P["helpers"]["api_json_once"]["methodPos"] == 1

    def test_读不出参数位置的不猜(self, P):
        """推不出来就进 `unparsed`，调用点一律记漏读 —— 漏报可见，误报不可见。"""
        lib = {"lib/x.sh": 'weird() {\n  local ep="$OTHER"\n'
                           '  curl -s -X GET "${API}${ep}"\n}\n'}
        p = parse_helper_lib(lib)
        assert p["helpers"] == {}
        assert [u["helper"] for u in p["unparsed"]] == ["weird"]


class Test方法只从url所在那一句里取:
    def test_同句里的X才算这个url的方法(self):
        """`cleanup() { curl "${API}${path}"; curl -X DELETE "${API}/other/$id"; }`
        —— 整个函数体里搜 `-X` 会把第二条的 DELETE 记到第一条那个**调用方路径**上：
        一条只被 GET 打过的路径被记成"DELETE 测过了"，**误报**那一侧。

        （实测对方 lib 里"多处拼 url"的 helper 只有 `login` 一个，且它同句就带
        `-X`，所以这条收紧在真实数据上零代价 —— 命中/漏读/去重端点三个数字一个没动。）"""
        lib = {"lib/x.sh": 'two() {\n  local path="$1"\n'
                           '  curl -s "${API}${path}"\n'
                           '  curl -s -X DELETE "${API}/other/$id"\n}\n'}
        assert parse_helper_lib(lib)["helpers"]["two"]["method"] == "GET"

    def test_只拼一处url时允许跨行找X(self):
        """`local url="${API}${path}"` 换行再 `curl -X "$m" "$url"` 是常见写法，
        这时候跨句找 `-X` 不会串台（只有一个 url，没有"归给谁"的问题）。
        一刀切锁本句会把这类 helper 整批判成读不懂。"""
        lib = {"lib/x.sh": 'one() {\n  local m="$1" path="$2"\n'
                           '  local url="${API}${path}"\n'
                           '  curl -s -X "$m" "$url"\n}\n'}
        assert parse_helper_lib(lib)["helpers"]["one"]["methodPos"] == 1


class Test口径外和基础设施两条闸门:
    def test_网关helper不进命中(self, P):
        """⚠ `covers()` 容忍 2 段部署前缀，所以 `${GW}/v1/chat/completions` 会
        **盖住** BFF 的 `/api/v1/chat/completions` —— 一个 Kong 调用把一个 BFF
        端点标成"测过了"。所以它单独记账，绝不进 hits。"""
        assert P["otherBase"]["gw_call"] == "GW"
        r = extract_helper_calls('gw_call "/v1/chat/completions"', P)
        assert r["hits"] == [] and r["misses"] == []
        assert [x["helper"] for x in r["otherBase"]] == ["gw_call"]

    def test_路径写死在helper里的记账不计覆盖(self, P):
        """`require_login` 在 369 个脚本里被调 359 次，内部固定探
        `/admin-users?page=1&page_size=1` 看 token 活没活。按"它确实发了这个请求"
        算的话 `/admin-users` 就成了「359 个脚本都在测」——**最典型的误报**：
        那个端点真挂了，359 条里可能一条都不会红（探活只看 token，不看它的业务语义）。"""
        assert P["infra"]["require_login"]["pathLiteral"].startswith("/admin-users")
        r = extract_helper_calls("require_login\n", P)
        assert r["hits"] == []
        assert [(x["method"], x["path"]) for x in r["infra"]] == [("GET", "/admin-users")]


class Test转手一跳:
    def test_路径原样传下去的算覆盖(self, P):
        assert P["helpers"]["list_of"]["pathPos"] == 1
        r = extract_helper_calls('list_of "/policies"', P)
        assert [(h["method"], h["path"]) for h in r["hits"]] == [("GET", "/policies")]

    def test_转手但路径自己拼的算基础设施(self, P):
        """**这是一条明知会漏报的取舍。** `tools_of "$ID"` 打的确实是
        `/mcp/{}/tools`，但"它是在测这个端点，还是只是路过（造数/探活）"没有
        机械判据 —— 而认成覆盖是误报那一侧。所以跟 `require_login` 同档：
        记账、不计覆盖。"""
        assert "tools_of" not in P["helpers"]
        assert P["infra"]["tools_of"]["pathLiteral"] == "/mcp/$1/tools"


class Test方法读不出来不留空方法:
    def test_方法是变量的调用点记漏读(self, P):
        """⚠ `compute_gaps._covered()` 里**空方法匹配任何方法**，所以一条
        `("", "/teams")` 会把这个路径上的 DELETE 也算成测过了。
        「不知道打的什么方法」和「什么方法都打过」差着一个真缺口。"""
        r = extract_helper_calls('api_json_code "$M" "/teams"', P)
        assert r["hits"] == []
        assert [m["why"] for m in r["misses"]] == ["方法不是字面量"]

    def test_调用点自己带X的优先(self, P):
        """`bff_code "/x" -X POST` 这种把额外参数透传给 curl 的写法，
        方法是调用方给的，不在 helper 签名里。"""
        r = extract_helper_calls('api_get "/teams" -X POST', P)
        assert [(h["method"], h["path"]) for h in r["hits"]] == [("POST", "/teams")]

    def test_路径不是字面量的记漏读(self, P):
        r = extract_helper_calls('api_get "$EP"', P)
        assert r["hits"] == []
        assert [m["why"] for m in r["misses"]] == ["路径不是字面量"]


class Test路径归一化:
    def test_位置参数也要压成通配(self):
        """⚠ 回归封样。共享的 `normalize_path` 变量正则是 `\\$[A-Za-z_]…`，
        开头是数字**不匹配** —— 于是 `/admin-users/$1` 原样留着，跟 R 边的
        `/admin-users/{}` 永远对不上，表现是这个端点"没人测"。实测 20 条命中踩它。
        在 Q 边补一手，不去改那个函数（它同时给分支对账的 R/P 两边用）。"""
        assert normalize_script_path("/admin-users/$1") == "/admin-users/{}"
        assert normalize_script_path("/agents/${ID}/tools") == "/agents/{}/tools"

    def test_json_body不算路径(self):
        """`normalize_path('{"name":"x"}')` 会把那段 body 当成 `{id}` 形状的段
        压成 `/{}` —— 一个 JSON body 就这么变成一条"端点"。"""
        assert normalize_script_path('{"name":"x"}') == ""

    def test_全通配不算路径(self):
        """`/$1` 归一成 `/{}`，它跟**任何**一段路径都能对上。"""
        assert normalize_script_path("/$1") == ""


class Test分词:
    def test_引号里的管道不算分词失败(self):
        """先用正则在 `|`/`;`/`)` 上切一刀再分词的话，`jq '.data[] | select(.id)'`
        会被切断、`shlex` 抛错 —— 实测那么写有 1744 行"分词失败"，而它们绝大多数
        是好行。**一个解析器的账本上 1700 条噪声等于没有账本。**"""
        P = parse_helper_lib(LIB)
        r = extract_helper_calls("api_get \"/teams\" | jq '.data[] | select(.id)'", P)
        assert [(h["method"], h["path"]) for h in r["hits"]] == [("GET", "/teams")]
        assert r["misses"] == []

    def test_注释掉的调用不算(self):
        P = parse_helper_lib(LIB)
        r = extract_helper_calls('# api_get "/teams"', P)
        assert r["hits"] == [] and r["misses"] == []

    def test_长名字的helper先匹配(self):
        """`api_get_as` 里含 `api_get`，短的先命中就会按 `api_get` 的位置去 `$1`
        取参 —— 而 `$1` 是 token，于是抽出一条**以 token 为路径**的假端点。"""
        P = parse_helper_lib(LIB)
        r = extract_helper_calls('api_get_as "$TOKEN" "/teams"', P)
        assert [(h["helper"], h["path"]) for h in r["hits"]] == [("api_get_as", "/teams")]
