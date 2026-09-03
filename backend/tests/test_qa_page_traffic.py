"""P 边：HAR 按导航时窗归页。**这个文件盯的是「归错了会不会被发现」。**

三个方向的错，代价完全不对称，所以门禁也不对称：

① **归错页**（把落地页的流量记到 `/login` 名下、按"最近那一页"猜一个归属）——
   错的归属在报告上和对的长得**一模一样**，没人查得出来。所以宁可记进
   `edgesUnwindowed`（归不了页）也不猜：那是个看得见的数。
② **少算一条端点**（把真 API 当静态资源扔了）—— 一个真缺口凭空消失，永远不报错。
   所以认不出的一律 `unclear` **照样进边**，不扔。
③ **多算一条**（把 `/assets/index-a1b2.js` 当端点）—— G1 里多一条噪声，看得见。

还有一条跨模块的：P 边的 `source` 必须落在 `qa_coverage_reconcile.EDGE_SOURCES`
里，否则 `edge_ok` 会把它们**全部**拒掉 —— 而 P 账变空在页面上长得像「没缺口」。

Test ID: qa-page-traffic-UT-001
Priority: P0
"""
import pytest

from app.services import qa_page_traffic as t
from app.services.qa_coverage_reconcile import EDGE_SOURCES, edge_ok
from app.services.qa_survey_guard import drop_credentials

T0 = "2026-09-03T10:00:00.000Z"


def _win(path, start, end, **kw):
    w = {"path": path, "startedAt": start, "endedAt": end}
    w.update(kw)
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


# ── 归页 ─────────────────────────────────────────────────────────────────

class Test归页:
    def test_各归各页(self):
        har = _har(_entry("http://h/api/v1/teams", _at(1)),
                   _entry("http://h/api/v1/adapters", _at(11)))
        out = t.bucket_entries(har, [_win("/teams", _at(0), _at(5)),
                                     _win("/adapters", _at(10), _at(15))],
                               role="qa-auditor", closed_at=_at(20))
        assert [(e["pagePath"], e["path"]) for e in out["edges"]] == [
            ("/teams", "/api/v1/teams"), ("/adapters", "/api/v1/adapters")]
        assert out["counters"]["edgesUnwindowed"] == 0

    def test_窗外的不归页只记账(self):
        """**这条是本模块的第一纪律。** 挑一个"最近的"页面塞进去是最容易犯的错：
        那条边会长成「这个页面打了这个端点」，读的人照着查一个不存在的关系。
        """
        har = _har(_entry("http://h/api/v1/early", _at(1)))
        out = t.bucket_entries(har, [_win("/teams", _at(10), _at(15))],
                               closed_at=_at(20))
        assert out["edges"] == []
        assert out["counters"]["edgesUnwindowed"] == 1
        # 记数不够，得留得下形状：光一个数字没人查得出是哪几条
        assert out["samples"]["unwindowed"][0]["path"] == "/api/v1/early"

    def test_一条边同时压在两格上不二选一(self):
        """时窗重叠只可能是记账出错了。二选一就是猜，猜出来的归属看不出错。"""
        har = _har(_entry("http://h/api/v1/x", _at(3)))
        out = t.bucket_entries(har, [_win("/a", _at(0), _at(9)),
                                     _win("/b", _at(2), _at(9))], closed_at=_at(9))
        assert out["edges"] == []
        assert out["counters"]["edgesAmbiguous"] == 1
        assert out["samples"]["ambiguous"][0]["pages"] == ["/a", "/b"]

    def test_读不出时间的时窗不参与也不害人(self):
        har = _har(_entry("http://h/api/v1/x", _at(11)))
        out = t.bucket_entries(har, [_win("/bad", "", ""),
                                     _win("/good", _at(10), _at(15))], closed_at=_at(20))
        assert [e["pagePath"] for e in out["edges"]] == ["/good"]
        assert out["counters"]["windows"] == 1

    def test_没有时窗就明说一条边都归不了(self):
        out = t.bucket_entries(_har(_entry("http://h/api/v1/x", _at(1))), [],
                               role="tester")
        assert out["edges"] == []
        assert any("归不了页" in d for d in out["declarations"])


class Test时窗延长:
    def test_networkidle_之后的轮询算这一页的(self):
        """`goto` + `networkidle` 收工之后浏览器还停在这一页上，那些请求确实是它发的。"""
        har = _har(_entry("http://h/api/v1/poll", _at(7)))
        out = t.bucket_entries(har, [_win("/teams", _at(0), _at(5)),
                                     _win("/adapters", _at(10), _at(15))],
                               closed_at=_at(20))
        assert [(e["pagePath"], e["tail"]) for e in out["edges"]] == [("/teams", True)]
        # 延长是个**判断**，得让人看见它带进来多少
        assert out["counters"]["edgesTail"] == 1

    def test_最后一页延到_context_关闭(self):
        har = _har(_entry("http://h/api/v1/poll", _at(18)))
        out = t.bucket_entries(har, [_win("/teams", _at(0), _at(5))], closed_at=_at(20))
        assert [e["pagePath"] for e in out["edges"]] == ["/teams"]

    def test_没有关闭时刻就不延长(self):
        """`contextClosedAt` 没记上的时候，延长到"无穷远"会把之后所有流量都算给
        最后一页 —— 那正是"归错页"。宁可记不了账。
        """
        har = _har(_entry("http://h/api/v1/poll", _at(18)))
        out = t.bucket_entries(har, [_win("/teams", _at(0), _at(5))])
        assert out["edges"] == [] and out["counters"]["edgesUnwindowed"] == 1

    def test_登录那格不许延长(self):
        """`tail: False` 的用处：提交完浏览器自己跳到落地页，延长会把落地页的
        流量记到 `/login` 名下 —— 报告上看不出这是错的。
        """
        har = _har(_entry("http://h/api/v1/me", _at(7)))
        wins = [_win("/login", _at(0), _at(5), tail=False),
                _win("/teams", _at(10), _at(15))]
        out = t.bucket_entries(har, wins, closed_at=_at(20))
        assert out["edges"] == []
        assert out["counters"]["edgesUnwindowed"] == 1

    def test_宽度为零的那格靠第二遍捞回来(self):
        """`endedAt` 没记上（页面崩在 goto 里）时时窗退化成一个瞬间。
        左闭右开会让它永远命中不了，那一页的请求就凭空消失了。
        """
        har = _har(_entry("http://h/api/v1/x", _at(0)))
        out = t.bucket_entries(_har(*har["log"]["entries"]),
                               [{"path": "/a", "startedAt": _at(0)}])
        assert [e["pagePath"] for e in out["edges"]] == ["/a"]


# ── 哪些 entry 算端点 ────────────────────────────────────────────────────

class Test分类:
    def test_静态资源不算端点(self):
        har = _har(_entry("http://h/assets/index-a1b2.js", _at(1), rtype="script"),
                   _entry("http://h/logo.png", _at(1), rtype="image"),
                   _entry("http://h/api/v1/teams", _at(1)))
        out = t.bucket_entries(har, [_win("/teams", _at(0), _at(5))], closed_at=_at(9))
        assert [e["path"] for e in out["edges"]] == ["/api/v1/teams"]
        assert out["counters"]["assetEntries"] == 2

    def test_认不出的照样进边并标出来(self):
        """**这条是反向锚点。** 把 `unclear` 改成"扔掉"不会有别的测试变红，
        而它扔掉的是"没认出来的真 API" —— 一个真缺口从此永远不出现。
        """
        e = {"startedDateTime": _at(1), "request": {"method": "GET", "url": "http://h/graphql"}}
        out = t.bucket_entries(_har(e), [_win("/teams", _at(0), _at(5))], closed_at=_at(9))
        assert [(x["path"], x["classified"]) for x in out["edges"]] == [("/graphql", "unclear")]
        assert out["counters"]["unclearEntries"] == 1

    def test_没有前缀兜底就要明说(self):
        out = t.bucket_entries(_har(), [_win("/a", _at(0), _at(5))])
        assert any("`_resourceType`" in d for d in out["declarations"])

    def test_路由表前缀能救回认不出的那些(self):
        e = {"startedDateTime": _at(1),
             "request": {"method": "GET", "url": "http://h/api/v1/teams"}}
        out = t.bucket_entries(_har(e), [_win("/t", _at(0), _at(5))], closed_at=_at(9),
                               api_prefixes=("/api", "/api/v1"))
        assert out["edges"][0]["classified"] == "api"
        assert out["declarations"] == []

    def test_扩展名只认结尾(self):
        assert t.classify_entry({"request": {"url": "http://h/api/v1/files.js/meta"}}) == "unclear"
        assert t.classify_entry({"request": {"url": "http://h/x/a.css"}}) == "asset"

    def test_预检不成边但要记数(self):
        """OPTIONS 不是页面"调"的端点，而真正那条请求本来就在同一份 HAR 里 ——
        排掉它一条边都不少。记数是为了让"排掉了多少"可查。
        """
        har = _har(_entry("http://h/api/v1/teams", _at(1), method="OPTIONS", rtype="preflight"))
        out = t.bucket_entries(har, [_win("/t", _at(0), _at(5))], closed_at=_at(9))
        assert out["edges"] == [] and out["counters"]["preflightEntries"] == 1


class Test前缀推导:
    def test_不漏掉非_api_的那几段(self):
        pre = t.api_prefixes_from_routes(
            [{"path": "/api/v1/teams"}, {"path": "/mcp/tools/call"}, {"path": "/healthz"}])
        assert "/api/v1" in pre and "/mcp" in pre and "/healthz" in pre

    def test_拿不到路由表就是空的(self):
        assert t.api_prefixes_from_routes(None) == ()
        assert t.api_prefixes_from_routes([{"path": "not-a-path"}, {}]) == ()


# ── 出处 ─────────────────────────────────────────────────────────────────

class Test出处:
    def test_写请求没响应就是被_L1_拦下的(self):
        """拦截既是闸门也是事实来源：拦下的那一刻，「这个页面会发这个写请求」
        已经是观测到的事实。
        """
        e = _entry("http://h/api/v1/teams", _at(1), method="POST", status=None)
        out = t.bucket_entries(_har(e), [_win("/t", _at(0), _at(5))], closed_at=_at(9))
        assert out["edges"][0]["source"] == "aborted"
        assert out["counters"]["edgesAborted"] == 1

    def test_读请求没响应照样是观测到的(self):
        """请求确实发出去了。「页面会调这个端点」不因为没收到回包而变假。"""
        e = _entry("http://h/api/v1/teams", _at(1), status=None)
        out = t.bucket_entries(_har(e), [_win("/t", _at(0), _at(5))], closed_at=_at(9))
        assert out["edges"][0]["source"] == "observed"

    def test_出处必须是白名单里的那两种(self):
        """**跨模块封样。** `source` 写成别的（`crawl`、`page`、空）都会被
        `edge_ok` 全部拒掉，而 P 账变空在页面上长得像「这个域没缺口」。
        """
        har = _har(_entry("http://h/api/v1/a", _at(1)),
                   _entry("http://h/api/v1/b", _at(1), method="DELETE", status=None))
        out = t.bucket_entries(har, [_win("/t", _at(0), _at(5))], closed_at=_at(9))
        assert len(out["edges"]) == 2
        for e in out["edges"]:
            assert e["source"] in EDGE_SOURCES
            assert edge_ok(e) is True


class Test说不通的就别造边:
    def test_洗过之后_url_不会被截断_这是别人的实现细节(self):
        """**这条是记事实，不是记期望。** `drop_credentials` 对 300 字截尾有一个
        `key == "url"` 的豁免（走 `_clean_url`，只换 query 的值），所以今天
        `request.url` 到不了下面那条截断分支。哪天豁免被拿掉，这条会红在
        「谁改了」而不是红在「P 边突然少了一批」—— 后者查不出来。
        """
        req = {"method": "GET", "url": "http://h/api/v1/" + "a" * 400}
        assert not drop_credentials(req)["url"].endswith("…")

    def test_截断过的路径不造边(self):
        """深度封顶（`depth > 12`）返回的就是这个省略号；上面那条豁免要是没了，
        长 url 也会长成这样。半截路径会变成一个根本不存在的端点
        （多一条假缺口），而真的那条同时消失。
        """
        e = _entry("http://h/api/v1/aaa…", _at(1))
        out = t.bucket_entries(_har(e), [_win("/t", _at(0), _at(5))], closed_at=_at(9))
        assert out["edges"] == [] and out["counters"]["edgesUnusable"] == 1
        assert "截断" in out["samples"]["unusable"][0]["why"]

    def test_截断在_query_里就照样造边(self):
        """query 本来就要剥掉，切在它里面不影响路径。"""
        e = _entry("http://h/api/v1/teams?q=bbb…", _at(1))
        out = t.bucket_entries(_har(e), [_win("/t", _at(0), _at(5))], closed_at=_at(9))
        assert [x["path"] for x in out["edges"]] == ["/api/v1/teams"]

    def test_读不出_startedDateTime_的不造边(self):
        e = _entry("http://h/api/v1/x", "昨天下午")
        out = t.bucket_entries(_har(e), [_win("/t", _at(0), _at(5))], closed_at=_at(9))
        assert out["edges"] == [] and out["counters"]["edgesUnusable"] == 1

    def test_不带时区的按_UTC_读(self):
        """按本机时区猜会把边归到错的页上（看不见）；按 UTC 读最坏是整片落窗外（看得见）。"""
        assert t._ts("2026-09-03T10:00:00").isoformat() == "2026-09-03T10:00:00+00:00"


# ── 洗过凭证之后还得能归页 ───────────────────────────────────────────────

class Test洗过的_HAR:
    def test_drop_credentials_之后归页照样成立(self):
        """`_BODY_KEYS` 里有 `content`/`text`，正文整个没了 —— 归页依赖的
        `startedDateTime` / `_resourceType` / `url` / `response.status`
        必须都还在，否则这条链在真数据上直接断掉，而单测全绿。
        """
        raw = _har({"startedDateTime": _at(1), "_resourceType": "xhr",
                    "request": {"method": "POST", "url": "http://h/api/v1/teams?token=abc",
                                "headers": [{"name": "Authorization", "value": "Bearer x"}],
                                "postData": {"text": '{"name":"x"}'}},
                    "response": {"status": 201, "content": {"text": "{}"}}})
        washed = drop_credentials(raw)
        entry = washed["log"]["entries"][0]
        assert "Bearer x" not in str(washed)             # 凭证是**扔掉**不是脱敏
        assert entry["request"]["method"] == "POST"
        out = t.bucket_entries(washed, [_win("/teams", _at(0), _at(5))], closed_at=_at(9))
        assert [(e["path"], e["status"], e["source"]) for e in out["edges"]] == [
            ("/api/v1/teams", 201, "observed")]


# ── 多角色合并 ───────────────────────────────────────────────────────────

class Test合并:
    def test_角色取并集不是拿主爬那份当底(self):
        """低权角色看得见、只读账号看不见的请求，正是角色维度唯一有价值的信号。"""
        a = t.bucket_entries(_har(_entry("http://h/api/v1/teams", _at(1))),
                             [_win("/t", _at(0), _at(5))], role="qa-auditor",
                             closed_at=_at(9))
        b = t.bucket_entries(_har(_entry("http://h/api/v1/teams", _at(1)),
                                  _entry("http://h/api/v1/secret", _at(1))),
                             [_win("/t", _at(0), _at(5))], role="tester", closed_at=_at(9))
        out = t.merge_edges([a, b])
        rows = {e["path"]: e for e in out["edges"]}
        assert rows["/api/v1/teams"]["roles"] == ["qa-auditor", "tester"]
        assert rows["/api/v1/secret"]["roles"] == ["tester"]
        assert out["counters"]["pageEdges"] == 2

    def test_拦下过就是拦下过(self):
        a = t.bucket_entries(_har(_entry("http://h/api/v1/x", _at(1), method="DELETE",
                                         status=None)),
                             [_win("/t", _at(0), _at(5))], role="r1", closed_at=_at(9))
        b = t.bucket_entries(_har(_entry("http://h/api/v1/x", _at(1), method="DELETE",
                                         status=204)),
                             [_win("/t", _at(0), _at(5))], role="r2", closed_at=_at(9))
        out = t.merge_edges([a, b])
        assert out["edges"][0]["source"] == "aborted"

    def test_账和声明一起并(self):
        a = t.bucket_entries(_har(_entry("http://h/api/v1/x", _at(59))),
                             [_win("/t", _at(0), _at(5))], role="r1", closed_at=_at(9))
        b = t.bucket_entries(_har(), [], role="r2")
        out = t.merge_edges([a, b])
        assert out["counters"]["edgesUnwindowed"] == 1
        assert any("归不了页" in d for d in out["declarations"])

    def test_空输入不炸(self):
        out = t.merge_edges([])
        assert out["edges"] == [] and out["counters"]["pageEdges"] == 0
        assert t.bucket_entries(None, None)["edges"] == []


# ── 模块纪律 ─────────────────────────────────────────────────────────────

class Test模块纪律:
    def test_不许自己判是不是写请求(self):
        """两套「什么算写」的判据一旦对不上，`aborted` 会静静变成 `observed`。"""
        src = open(t.__file__, encoding="utf-8").read()
        assert "from app.services.qa_survey_guard import is_write_request" in src

    def test_纯函数不碰_IO(self):
        src = open(t.__file__, encoding="utf-8").read()
        for bad in ("import httpx", "open(", "async def", "session"):
            assert bad not in src, bad

    @pytest.mark.parametrize("src", ["模型", "推断", "猜"])
    def test_不许出现第三种出处(self, src):
        """`EDGE_SOURCES` 只有三个值，「模型推断」不在此列，以后也不许加。"""
        assert src not in " ".join(EDGE_SOURCES)
