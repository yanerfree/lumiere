"""页面枚举爬取的只读五层：判定逻辑本体。

爬的是**别人的测试环境**，所以这五层里每一层判错的代价都不对称：
漏爬一个按钮只是账本少一条，误判一次"这是读操作"就是动了别人的数据。
测试按这个不对称来写 —— 每层都盯**判宽了**的那一侧，不盯判严了的那一侧。

真正值得钉死的四件事：
① 没见过的方法/认不出的控件必须落在"会写"那边（默认放行 = 静默出事）；
② 白名单按**路径**匹配（`in url` 那种写法会被查询串骗过去）；
③ HAR 的凭证是**扔掉**不是脱敏，而且要够得着 HAR 那个深度
   （按键名脱敏的通用做法对 `{"name": "Authorization"}` 这个形状结构性失明）；
④ `dirty` 压过 `failed`（"我们可能动了数据"不能被"这趟没跑成"盖掉）。

Test ID: qa-survey-guard-UT-001
Priority: P0
"""
import ast
import copy
import json
import pathlib

import pytest

from app.services import qa_survey_guard as g


# ── L1 网络 ──────────────────────────────────────────────────────────────

class TestL1写请求判定:
    @pytest.mark.parametrize("m", ["GET", "get", " head ", "OPTIONS"])
    def test_读方法放行(self, m):
        assert g.is_write_request(m, "https://x/api/services") is False

    @pytest.mark.parametrize("m", ["POST", "PUT", "PATCH", "DELETE"])
    def test_写方法拦住(self, m):
        assert g.is_write_request(m, "https://x/api/services") is True

    def test_没见过的方法算写(self):
        """自定义动词、PROPFIND、拼错的方法 —— 一律当写。

        反过来写（"这几个是写、其余放行"）在遇到新动词时会**静默放行**，
        而这一层出事是没有第二次机会的。
        """
        for m in ["PROPFIND", "PURGE", "POSTT", "TRACE"]:
            assert g.is_write_request(m, "https://x/api/x") is True

    def test_方法拿不到也算写(self):
        assert g.is_write_request("", "https://x/api/x") is True
        assert g.is_write_request(None, "https://x/api/x") is True

    def test_登录放行(self):
        assert g.is_write_request("POST", "https://x/api/auth/login") is False

    def test_白名单只按路径匹配(self):
        """`/api/auth/login` 出现在**查询串**里不算命中。

        用 `entry in url` 是最顺手的写法，也正好放行了
        `DELETE /api/services/x?next=/api/auth/login` —— 白名单被降级成
        「URL 里任何位置出现这串字符」，而那串字符是别人可控的。
        """
        assert g.is_write_request(
            "DELETE", "https://x/api/services/abc?next=/api/auth/login") is True

    def test_前缀不能蹭进来(self):
        """`/api/auth/login-as-anyone` 不是 `/api/auth/login`。"""
        assert g.is_write_request("POST", "https://x/api/auth/login-as-anyone") is True

    def test_子路径算命中(self):
        assert g.is_write_request("POST", "https://x/api/auth/login/refresh") is False


# ── L2 控件 ──────────────────────────────────────────────────────────────

class TestL2控件分档:
    def test_认不出来的不点(self):
        """`unknown` 必须**不在**可点档里。

        这条测的不是 `classify_control` 而是 `SAFE_TO_CLICK` 那个常量：
        判定再准，只要有人往这个元组里加一个 `"unknown"`，五层就塌了一层，
        而那一行改动在 diff 里看着人畜无害。
        """
        assert "unknown" not in g.SAFE_TO_CLICK
        assert "write" not in g.SAFE_TO_CLICK
        assert g.classify_control("Ω") == "unknown"

    @pytest.mark.parametrize("label", ["删除", "批量删除", "确认提交", "Save", "重启服务", "禁用"])
    def test_写词命中(self, label):
        assert g.classify_control(label) == "write"

    @pytest.mark.parametrize("label", ["查看详情", "搜索", "导出", "下一页", "Refresh"])
    def test_读词命中(self, label):
        assert g.classify_control(label) == "read"

    def test_角色先于文案(self):
        """开关的文案常常是「已启用」—— 按文案判会去点它，而那一下就是停一个服务。"""
        assert g.classify_control("已启用", role="switch") == "write"
        assert g.classify_control("查看", role="checkbox") == "write"

    def test_没有文案时靠角色兜底(self):
        assert g.classify_control("", role="link") == "read"
        assert g.classify_control("", role="button") == "unknown"


# ── L3 账号 ──────────────────────────────────────────────────────────────

class TestL3主爬账号:
    def test_没有只读账号就不许开爬(self):
        """不许"先用 admin 顶一下"。

        顶一下的后果不是「风险高一点」：L1 的白名单和 L2 的词典都是**我们自己**
        判的，判错就没有第二道网。只读账号是唯一由**对方系统**兜底的一层。
        """
        with pytest.raises(ValueError) as e:
            g.pick_main_crawl_role(["admin", "tester"])
        assert "auditor" in str(e.value)
        with pytest.raises(ValueError):
            g.pick_main_crawl_role([])

    def test_有只读账号就用它(self):
        assert g.pick_main_crawl_role(["admin", "auditor"]) == "auditor"

    def test_浅扫排掉主爬那个且不重复(self):
        assert g.shallow_scan_roles(
            ["auditor", "admin", "tester", "admin", "", None]) == ["admin", "tester"]


# ── L4 凭证 ──────────────────────────────────────────────────────────────

def _har(token="Bearer eyJreal.token.value", cookie="session=abc123"):
    """一份形状真实的 HAR：凭证躺在 `log.entries[i].request.headers[j].value`。"""
    return {"log": {"version": "1.2", "entries": [{
        "startedDateTime": "2026-08-29T10:00:00Z",
        "request": {
            "method": "GET",
            "url": "https://uag/api/services?access_token=zzz&page=2",
            "headers": [
                {"name": "Authorization", "value": token},
                {"name": "Cookie", "value": cookie},
                {"name": "Accept", "value": "application/json"},
            ],
            "postData": {"mimeType": "application/json",
                         "text": '{"password": "hunter2"}'},
        },
        "response": {
            "status": 200,
            "headers": [{"name": "Set-Cookie", "value": "session=def456; HttpOnly"},
                        {"name": "Content-Type", "value": "application/json"}],
            "content": {"text": '{"secret": "leaked"}'},
        },
    }]}}


class TestL4凭证落库前扔掉:
    def test_三个凭证头连键带值都不见了(self):
        s = json.dumps(g.drop_credentials(_har()), ensure_ascii=False)
        for gone in ["eyJreal.token.value", "abc123", "def456",
                     "Authorization", "Cookie", "Set-Cookie"]:
            assert gone not in s, f"{gone} 还在落库的产物里"

    def test_是扔掉不是脱敏(self):
        """`"Authorization": "***"` 不算数。

        留个键在那儿，等于留下一条"凭证真的流经这里"的路径，
        外加一个"我们存了、但很安全"的印象 —— 下一个人加一行调试日志就又出去了。
        """
        req = g.drop_credentials(_har())["log"]["entries"][0]["request"]
        assert [h["name"] for h in req["headers"]] == ["Accept"]

    def test_深度够得着_har_里的证据(self):
        """HAR 的头躺在第 8 层，深度封顶太浅**不会漏凭证，会静默吃掉证据**。

        这条钉的是一个很有诱惑力的重构：复用现成那个按键名脱敏的 `_mask_deep`
        （6 层封顶）。变异实测过：真换成 6，凭证**照样不漏** —— 因为到底了返回的是
        `"…"` 不是原对象。漏的是别的：整个 `request` 塌成一个省略号，
        url、方法、`Accept` 全没了，而这份 HAR 是失败分类唯一的网络证据来源。
        它不会报错，只会让后面的人看到一份"什么都没抓到"的证据包。

        （`_mask_deep` 另一半不合用的理由在被测函数的注释里：HAR 把头名放在
        `name` 键的**值**里，按键名扫的做法对这个形状结构性失明 —— 实测
        `Bearer …` / `session=…` / `Set-Cookie` 三个原样全在。那一半由
        `test_三个凭证头连键带值都不见了` 钉。）
        """
        out = g.drop_credentials({"a": {"b": {"c": {"d": _har()}}}})   # 再往下压 4 层
        req = out["a"]["b"]["c"]["d"]["log"]["entries"][0]["request"]
        assert req["method"] == "GET"
        assert "/api/services" in req["url"]
        assert [h["name"] for h in req["headers"]] == ["Accept"]
        assert "eyJreal.token.value" not in json.dumps(out)

    def test_到底了截断而不是原样返回(self):
        obj = cur = {}
        for _ in range(30):
            cur["n"] = {}
            cur = cur["n"]
        cur["Authorization"] = "Bearer deep.token"
        assert "deep.token" not in json.dumps(g.drop_credentials(obj))

    def test_正文一概不落库(self):
        s = json.dumps(g.drop_credentials(_har()), ensure_ascii=False)
        assert "hunter2" not in s and "leaked" not in s

    def test_url_上的_token_值没了键还在(self):
        """键留着是故意的：「这个接口在 URL 上收 token」本身是要能看见的事实。"""
        url = g.drop_credentials(_har())["log"]["entries"][0]["request"]["url"]
        assert "zzz" not in url
        assert "access_token" in url and "page=2" in url

    def test_不改原来那份(self):
        src = _har()
        before = copy.deepcopy(src)
        g.drop_credentials(src)
        assert src == before


# ── L5 自检 ──────────────────────────────────────────────────────────────

class TestL5爬前爬后自检:
    def test_只出现在一边的计数也算变了(self):
        """爬完多出来一类对象，在 `before` 里根本没有这个键。

        只比交集（`for k in before if before[k] != after.get(k)`）是最顺手的写法，
        也正好漏掉最该抓的那种：**我们建出来的东西**。
        """
        assert g.totals_changed({"services": 3}, {"services": 3, "keys": 1}) == ["keys"]
        assert g.totals_changed({"a": 1}, {"a": 1}) == []
        assert g.totals_changed(None, None) == []

    def test_数变了就是_dirty(self):
        assert g.resolve_terminal_status(
            shards_total=6, shards_ok=6,
            totals_before={"services": 3}, totals_after={"services": 4}) == "dirty"

    def test_dirty_压过_failed(self):
        """一趟全片失败、但环境里的数变了 —— 要看的是"我们动了什么"。

        把 `failed` 排在前面，这条信息就被一句"这趟失败了"盖过去了，
        而那恰恰是最需要人来看的一趟。
        """
        assert g.resolve_terminal_status(
            shards_total=6, shards_ok=0,
            totals_before={"services": 3}, totals_after={"services": 4}) == "dirty"

    def test_分片全成才是_done(self):
        assert g.resolve_terminal_status(shards_total=6, shards_ok=6) == "done"
        assert g.resolve_terminal_status(shards_total=6, shards_ok=4) == "partial"
        assert g.resolve_terminal_status(shards_total=6, shards_ok=0) == "failed"

    def test_终态都在模型那张表的白名单里(self):
        from app.models.qa_page_survey import TERMINAL_STATUSES
        for s in ["done", "partial", "failed", "dirty"]:
            assert s in TERMINAL_STATUSES


# ── 封样 ────────────────────────────────────────────────────────────────

class TestPure:
    def test_这个模块不做_io_也不碰模型(self):
        """五层判定必须留在纯函数里（架构 AD-7）。

        一旦这里 import 了数据库/HTTP/模型，判定就只能在跑起整条链路时才测得到，
        于是它就会跟 fixture 里那份一样 —— **实际上不会被测**。
        """
        src = pathlib.Path(g.__file__).read_text(encoding="utf-8")
        mods = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        bad = mods & {"sqlalchemy", "httpx", "requests", "aiohttp", "app",
                      "anthropic", "openai", "playwright", "redis", "arq"}
        assert not bad, f"判定模块里出现了 IO/模型依赖：{sorted(bad)}"


class TestL2词表按词边界匹配:
    """ASCII 词按词边界匹配，中文按子串。

    2026-09-04 实测那一趟量出来的账：`dialogsOpened` **恒为 0**，而
    `controlsClicked` 是 255 —— 因为表头 `Created At` 里的 `create` 被子串
    命中判成了「开层按钮」，一页三个名额全被表头和导航占掉。
    子串匹配同时还把 `Address`（含 `add`）判成写、`Preset`（含 `reset`）判成禁点。
    """

    def test_created_at_不是新建(self):
        assert g.classify_control("Created At", "columnheader") == "unknown"
        assert g.click_intent("Created At", "columnheader") == "never"
        assert g.click_intent("Updated", "button") == "never"

    def test_address_不是添加(self):
        assert g.classify_control("Address", "textbox") == "unknown"

    def test_preset_不是重置(self):
        # 判成 `unknown` 就够了 —— `unknown` 本来就不点。要紧的是它不再被
        # 当成**禁点词命中**：那会让"认不出来所以不点"和"认出来是删除所以不点"
        # 混成同一格，而这两件事的下一步完全不同（一个该补词表，一个不该动）。
        assert g.classify_control("Preset", "button") == "unknown"
        assert not any(g._word_hit("preset", w) for w in g._NEVER_CLICK_WORDS)

    def test_真的新建照旧认得出(self):
        assert g.classify_control("Create Team", "button") == "write"
        assert g.click_intent("Create Team", "button") == "opener"
        assert g.click_intent("New Adapter", "button") == "opener"
        assert g.click_intent("Add", "button") == "opener"

    def test_中文照旧走子串(self):
        # 汉字在 Python 的 `\w` 里也是词字符，`\b新建\b` 在「新建团队」里
        # 两边都不是边界 —— 中文必须留子串，不然整张中文词表一条都不命中。
        assert g.classify_control("新建团队", "button") == "write"
        assert g.click_intent("新建团队", "button") == "opener"
        assert g.click_intent("删除成员", "button") == "never"

    def test_复数和ing照旧命中(self):
        assert g.classify_control("Details", "button") == "read"
        assert g.classify_control("Filters", "button") == "read"
        assert g.click_intent("Previous", "button") == "safe"
