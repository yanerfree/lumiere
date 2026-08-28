"""页面枚举爬虫：入仓的那份脚本本身要过的门禁，以及编排层的降级纪律。

这个文件盯的不是"爬得全不全"，是**爬错了会不会被发现**：

① 脚本里写死地址/凭据 —— MCP 回推那条通道有门禁，仓内文件没有，所以在这里补上
   （`assert_no_hardcoded_endpoint_or_secret` 直接对源码文件跑）。写死一个地址的
   后果不是"换环境挂了"，是**打到了不该打的那台机器上**，而且脚本照跑不误。
② 任务函数没登记进 arq 白名单 —— enqueue 之后既不跑也不报错，页面上是「一直在跑」。
③ 一页打不开就整趟红 / 或者反过来照样算 done —— 后者更毒：没爬到的页面会在对账
   那边被报成「这个功能没了」。缺的信号一律算「没验证」。

Test ID: qa-page-survey-crawl-UT-001
Priority: P0
"""
import ast
import json
import pathlib

import pytest

from app.engine.surveys import qa_page_survey_crawl as c
from app.services.ui_script_guard import (
    assert_no_hardcoded_endpoint_or_secret,
    scan_hardcoded_endpoint_or_secret,
)

SRC = pathlib.Path(c.__file__)


# ── 门禁：仓内脚本也要过 ──────────────────────────────────────────────────

class Test脚本门禁:
    def test_爬虫脚本里不许写死地址和凭据(self):
        """S6.3 的验收点：对**源码文件**跑那个校验函数，不是对某段拼出来的字符串。"""
        assert_no_hardcoded_endpoint_or_secret(
            SRC.read_text(encoding="utf-8"), "python", where=str(SRC))

    def test_门禁真的会拦下写死的地址(self):
        """反向锚点：上面那条全绿，也可能是校验函数本身瞎了。

        没有这一条，把 `assert_no_hardcoded_endpoint_or_secret` 改成 `pass`
        不会有任何东西变红。
        """
        bad = 'BASE = "http://uag-138:3000"\n'
        assert scan_hardcoded_endpoint_or_secret(bad, "python")
        with pytest.raises(ValueError):
            assert_no_hardcoded_endpoint_or_secret(bad, "python")

    def test_地址只能从变量取(self):
        """没配 BASE_URL 就**不开爬**，不猜、不用默认值。"""
        import os
        old = os.environ.pop("BASE_URL", None)
        try:
            with pytest.raises(ValueError):
                c._base_url()
        finally:
            if old is not None:
                os.environ["BASE_URL"] = old


# ── AD-4：登记进 arq 白名单 ───────────────────────────────────────────────

class Test任务登记:
    def test_任务函数在_worker_白名单里(self):
        """没登记 = enqueue 之后躺在 redis 里，不执行也不报错。"""
        from app.engine.worker import WorkerSettings
        from app.engine.tasks.page_survey import run_page_survey
        assert run_page_survey in WorkerSettings.functions

    def test_没把别的任务挤掉(self):
        from app.engine.worker import WorkerSettings
        names = {f.__name__ for f in WorkerSettings.functions}
        assert {"run_git_sync", "run_automated_execution"} <= names


# ── L1 接到 Playwright 上那一段 ──────────────────────────────────────────

class _FakeRoute:
    def __init__(self):
        self.aborted = False
        self.continued = False

    async def abort(self):
        self.aborted = True

    async def continue_(self):
        self.continued = True


class _FakeRequest:
    def __init__(self, method, url):
        self.method = method
        self.url = url


class TestL1接线:
    @pytest.mark.asyncio
    async def test_写请求被_abort_并记账(self):
        ledger = {}
        guard = c.make_readonly_guard(ledger)
        route = _FakeRoute()
        await guard(route, _FakeRequest("POST", "https://x/api/services"))
        assert route.aborted is True and route.continued is False
        assert ledger["writesBlocked"] == 1
        # 拦到东西不是"没事发生"，要能在页面上看见拦了什么
        assert ledger["writesBlockedSample"] == ["POST /api/services"]

    @pytest.mark.asyncio
    async def test_读请求放行(self):
        ledger = {}
        guard = c.make_readonly_guard(ledger)
        route = _FakeRoute()
        await guard(route, _FakeRequest("GET", "https://x/api/services?page=2"))
        assert route.continued is True and route.aborted is False
        assert ledger.get("writesBlocked", 0) == 0

    @pytest.mark.asyncio
    async def test_没见过的方法当写处理(self):
        """判定本体在 guard 模块，这里确认**接线没把它绕过去**。"""
        ledger = {}
        route = _FakeRoute()
        await c.make_readonly_guard(ledger)(route, _FakeRequest("PROPFIND", "https://x/a"))
        assert route.aborted is True

    @pytest.mark.asyncio
    async def test_登录放行(self):
        """登录是唯一默认放行的写请求。

        它要是也被 abort，整趟会**安静地**爬回零条 —— 没有报错，只有一份空账本，
        而空账本在对账那边长得像「这些功能全没了」。
        """
        ledger = {}
        route = _FakeRoute()
        await c.make_readonly_guard(ledger)(
            route, _FakeRequest("POST", "https://x/api/auth/login"))
        assert route.continued is True and route.aborted is False

    @pytest.mark.asyncio
    async def test_样本有上限不会把账本撑爆(self):
        ledger = {}
        guard = c.make_readonly_guard(ledger)
        for i in range(50):
            await guard(_FakeRoute(), _FakeRequest("POST", f"https://x/api/s{i}"))
        assert ledger["writesBlocked"] == 50           # 计数不封顶
        assert len(ledger["writesBlockedSample"]) == 20  # 样本封顶


# ── 账本行 ───────────────────────────────────────────────────────────────

class Test控件账本:
    def test_anchor_优先_testid(self):
        rows = c.collect_items("/svc", "服务", [
            {"label": "新建", "role": "button", "testid": "svc-create", "id": "x"},
        ], {})
        assert rows[0]["anchor"] == "svc-create"
        assert rows[0]["anchor_kind"] == "testid"

    def test_没有_testid_退到_id_再退到文案(self):
        rows = c.collect_items("/svc", "服务", [
            {"label": "新建", "role": "button", "testid": "", "id": "btn1"},
            {"label": "详情", "role": "link", "testid": "", "id": ""},
        ], {})
        assert (rows[0]["anchor"], rows[0]["anchor_kind"]) == ("btn1", "id")
        assert (rows[1]["anchor"], rows[1]["anchor_kind"]) == ("详情", "text")

    def test_认不出的控件照记不漏只是不点(self):
        """`unknown` 要进账本、要计数 —— 它是**待人看的缺口**，不是"当它不存在"。"""
        ledger = {}
        rows = c.collect_items("/svc", "服务", [
            {"label": "赫赫", "role": "button", "testid": "", "id": ""},
        ], ledger)
        assert len(rows) == 1
        assert rows[0]["control_type"] == "unknown"
        assert ledger["controlsUnknown"] == 1
        assert "unknown" not in c.SAFE_TO_CLICK    # 认不出的绝不点

    def test_禁用的记_present_可用的记_enabled(self):
        rows = c.collect_items("/svc", "服务", [
            {"label": "新建", "role": "button", "disabled": True},
            {"label": "新建", "role": "button", "disabled": False},
        ], {})
        assert rows[0]["state"] == "present"
        assert rows[1]["state"] == "enabled"

    def test_不许出现_reachable(self):
        """`reachable` 要真点进去才知道，这一版不做。

        写成 `reachable` 就是把没验证过的事记成验证过了。
        """
        rows = c.collect_items("/svc", "服务",
                               [{"label": "详情", "role": "link"}], {})
        assert rows[0]["state"] in ("present", "enabled")

    def test_key_带页面路径(self):
        """同一个「新建」在两个页面上是两条，不是一条。"""
        a = c.collect_items("/svc", "", [{"label": "新建", "role": "button"}], {})
        b = c.collect_items("/env", "", [{"label": "新建", "role": "button"}], {})
        assert a[0]["key"] != b[0]["key"]

    def test_空页面不炸(self):
        assert c.collect_items("/svc", "", None, {}) == []
        assert c.collect_items("/svc", "", [], {}) == []


# ── HAR 落库前 ───────────────────────────────────────────────────────────

def _har_file(tmp_path: pathlib.Path) -> pathlib.Path:
    har = {"log": {"entries": [{
        "request": {
            "method": "GET",
            "url": "https://x/api/services?access_token=zzz&page=2",
            "headers": [
                {"name": "Authorization", "value": "Bearer eyJreal.token.value"},
                {"name": "Cookie", "value": "session=abc123"},
                {"name": "Accept", "value": "application/json"},
            ],
        },
        "response": {
            "status": 200,
            "headers": [{"name": "Set-Cookie", "value": "session=def456"}],
        },
    }]}}
    p = tmp_path / "qa-auditor.har"
    p.write_text(json.dumps(har), encoding="utf-8")
    return p


class TestHAR落库前:
    def test_凭证被扔掉(self, tmp_path):
        out = c.sanitize_har(_har_file(tmp_path))
        blob = json.dumps(out, ensure_ascii=False)
        for secret in ("eyJreal.token.value", "abc123", "def456", "zzz"):
            assert secret not in blob, f"{secret} 漏出来了"

    def test_证据还在(self, tmp_path):
        """扔凭证不能把证据一起扔了 —— HAR 是失败定位时唯一的网络证据。"""
        out = c.sanitize_har(_har_file(tmp_path))
        req = out["log"]["entries"][0]["request"]
        assert req["method"] == "GET"
        assert "/api/services" in req["url"]
        assert [h["name"] for h in req["headers"]] == ["Accept"]
        assert out["log"]["entries"][0]["response"]["status"] == 200

    def test_没有文件返回_none_不抛(self, tmp_path):
        assert c.sanitize_har(tmp_path / "nope.har") is None

    def test_坏文件返回_none_不抛(self, tmp_path):
        """HAR 没写全（爬到一半被杀）不该让整趟结果丢掉。"""
        p = tmp_path / "broken.har"
        p.write_text("{not json", encoding="utf-8")
        assert c.sanitize_har(p) is None


# ── 终态降级 ─────────────────────────────────────────────────────────────

class Test终态降级:
    def test_有页面没打开就不许叫_done(self):
        assert c.degrade_for_gaps("done", {"pagesFailed": ["/svc: TimeoutError"]}) == "partial"

    def test_有角色被跳过就不许叫_done(self):
        assert c.degrade_for_gaps("done", {"rolesSkipped": ["tester"]}) == "partial"

    def test_没缺口才是_done(self):
        assert c.degrade_for_gaps("done", {"pagesVisited": 10}) == "done"

    @pytest.mark.parametrize("s", ["dirty", "failed", "partial"])
    def test_只降不升(self, s):
        """已经是 dirty/failed 的，不会因为"没缺口"被抬成 done。"""
        assert c.degrade_for_gaps(s, {}) == s

    def test_dirty_压过缺口(self):
        """环境被动过 > 这趟没看全 —— 先看我们动了什么。"""
        assert c.degrade_for_gaps("dirty", {"pagesFailed": ["/a"]}) == "dirty"


# ── 一页失败不拖垮整趟 ───────────────────────────────────────────────────

class _FakePage:
    def __init__(self, fail_paths=(), items_by_path=None):
        self.fail_paths = set(fail_paths)
        self.items_by_path = items_by_path or {}
        self.visited = []
        self._cur = ""

    async def goto(self, url, timeout=None):
        self._cur = url
        for bad in self.fail_paths:
            if url.endswith(bad.lstrip("/")):
                raise TimeoutError("page did not load")
        self.visited.append(url)

    async def wait_for_load_state(self, *a, **k):
        return None

    async def evaluate(self, js):
        for path, items in self.items_by_path.items():
            if self._cur.endswith(path.lstrip("/")):
                return items
        return []

    async def title(self):
        return "T"

    async def fill(self, *a, **k):
        return None

    async def click(self, *a, **k):
        return None


class _FakeContext:
    def __init__(self, page):
        self._page = page
        self.closed = False
        self.routed = False

    async def route(self, pattern, handler):
        self.routed = True

    async def new_page(self):
        return self._page

    async def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self, page):
        self._ctx = _FakeContext(page)

    async def new_context(self, **kw):
        return self._ctx


@pytest.fixture
def _creds(monkeypatch):
    monkeypatch.setenv("QA_AUDITOR_USERNAME", "u")
    monkeypatch.setenv("QA_AUDITOR_PASSWORD", "p")


class Test一页失败不拖垮整趟:
    @pytest.mark.asyncio
    async def test_坏页记账其余照爬(self, tmp_path, _creds):
        page = _FakePage(fail_paths=["/bad"],
                         items_by_path={"/ok2": [{"label": "新建", "role": "button"}]})
        browser = _FakeBrowser(page)
        ledger = {}
        rows = await c.crawl_role(browser, "http://h", "qa-auditor",
                                  ["/ok1", "/bad", "/ok2"], ledger, tmp_path)
        assert ledger["pagesFailed"] == ["/bad: TimeoutError"]
        assert ledger["pagesVisited"] == 2          # 坏页之后没停
        assert [r["label"] for r in rows] == ["新建"]

    @pytest.mark.asyncio
    async def test_空页面记账但不算失败(self, tmp_path, _creds):
        """空状态和"没打开"是两回事：前者是事实，后者是缺口。"""
        page = _FakePage()
        ledger = {}
        await c.crawl_role(_FakeBrowser(page), "http://h", "qa-auditor",
                           ["/empty"], ledger, tmp_path)
        assert ledger["pagesEmptyState"] == ["/empty"]
        assert "pagesFailed" not in ledger

    @pytest.mark.asyncio
    async def test_没账号的角色跳过并记账(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TESTER_USERNAME", raising=False)
        monkeypatch.delenv("TESTER_PASSWORD", raising=False)
        ledger = {}
        rows = await c.crawl_role(_FakeBrowser(_FakePage()), "http://h", "tester",
                                  ["/a"], ledger, tmp_path)
        assert rows == []
        assert ledger["rolesSkipped"] == ["tester"]
        # 跳过必须让整趟落 partial，不能悄悄算 done
        assert c.degrade_for_gaps("done", ledger) == "partial"

    @pytest.mark.asyncio
    async def test_context_一定会_close(self, tmp_path, _creds):
        """HAR 只在 close 时落盘 —— 不 close 就是一个空文件，凭证也就没经过清洗。"""
        page = _FakePage(fail_paths=["/a"])
        browser = _FakeBrowser(page)
        await c.crawl_role(browser, "http://h", "qa-auditor", ["/a"], {}, tmp_path)
        assert browser._ctx.closed is True

    @pytest.mark.asyncio
    async def test_route_一定挂上了(self, tmp_path, _creds):
        """漏挂这一行 = 整趟没有只读保护，而且什么都不会报错。"""
        browser = _FakeBrowser(_FakePage())
        await c.crawl_role(browser, "http://h", "qa-auditor", ["/a"], {}, tmp_path)
        assert browser._ctx.routed is True


# ── 模块自身的约束 ───────────────────────────────────────────────────────

class Test模块纪律:
    def test_判定不许在这个模块里重写一份(self):
        """五层判定必须来自 `qa_survey_guard`。

        在 Playwright 回调里就地判一遍，逻辑要起浏览器才测得到 —— 实际上就是不会
        被测（架构 AD-7）。这里按名字钉死：这些名字必须是**导入进来**的。
        """
        tree = ast.parse(SRC.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and \
                    "qa_survey_guard" in node.module:
                imported |= {a.asname or a.name for a in node.names}
        assert {"is_write_request", "classify_control", "pick_main_crawl_role",
                "shallow_scan_roles", "drop_credentials",
                "resolve_terminal_status"} <= imported

    def test_没配自检就得说没做(self):
        """没有 totals_probe 时 `dirty` 永远不会触发。

        那时一趟 `done` 的意思会从「确认没动过环境」滑成「根本没查过」——
        这正是本模块要抓的那类错，不能自己犯。
        """
        assert c.self_check_label(None) == "notConfigured"
        assert c.self_check_label(lambda: None) == "done"

    def test_自检结论真的写进了账本(self):
        """上一条只证明函数是对的，不证明 `run_survey` 调了它。"""
        assert 'ledger["selfCheck"] = self_check_label(totals_probe)' in \
            SRC.read_text(encoding="utf-8")

    def test_并发对测试环境是克制的(self):
        """对方是测试环境不是压测靶子。"""
        assert 1 <= c.MAX_PARALLEL_SHARDS <= 3
