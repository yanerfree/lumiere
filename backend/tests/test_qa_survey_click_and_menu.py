"""菜单树 + 弹层：让「这个域到底覆盖了多少」这个问题有分母。

上一趟真跑（2026-09-04，UAG 全量）的三个数说明了为什么要有这一层：

- 页面清单只有 QA 仓脚本头里写死的那些 → **详情页一页都没进去过**；
- 1266 个可操作项里 **0 个输入框** → 表单覆盖率的分母是 0，任何数都成立；
- 1266 项里只有 24 个 write → 写操作的表单全在**没被打开的层**里。

三件事的共同点：产出的报告看着完整，缺的部分不在报告上留任何痕迹。
所以这一批的每个新能力都配一个计数，且**0 也要渲染**。

⚠ 这里全是**通用**判据：动词表、ARIA 角色、页面自己的导航链接。
一条针对某个域/某个页面的特判都不许有 —— 换个项目要照样能跑。
"""
import pytest

from app.engine.surveys import qa_page_survey_crawl as c
from app.services.qa_coverage_reconcile import compute_gaps
from app.services.qa_role_visibility import merge_shards
from app.services.qa_survey_guard import click_intent


# ── L2b：点不点 ──────────────────────────────────────────────────────────

class Test点不点这一问:
    def test_退出登录一个都不点(self):
        """点完这一下，后面每一页都是登录页，**而每一格都是绿的**。

        批1 刚修完一次一模一样的假绿（登录没成也照爬），别自己再造一次。
        """
        for label in ("退出登录", "登出", "注销", "Sign out", "Logout"):
            assert click_intent(label, "button") == "never", label

    def test_删除清空一个都不点(self):
        for label in ("删除", "批量删除", "清空日志", "重置密码", "停用服务",
                      "Delete", "Reset"):
            assert click_intent(label, "button") == "never", label

    def test_新建编辑算开层(self):
        for label in ("新建", "新建团队", "编辑", "修改配置", "添加成员",
                      "Create", "Edit"):
            assert click_intent(label, "button") == "opener", label

    def test_查看详情照旧是安全的(self):
        for label in ("查看", "详情", "刷新", "导出"):
            assert click_intent(label, "button") == "safe", label

    def test_禁点压过开层(self):
        """`删除配置` 两个词表都命中。判错的代价不对称，所以禁点先判。"""
        assert click_intent("删除配置", "button") == "never"
        assert click_intent("重置并新建", "button") == "never"

    def test_开关不因为文案像新建就被点(self):
        """角色优先。一个文案叫「新增」的开关，点下去是打开一个开关。

        少了这一条，`_OPENER_WORDS` 会把整档 switch/checkbox 重新放行 ——
        而那正是 `classify_control` 里角色优先本来要挡的东西。
        """
        for role in ("switch", "checkbox", "radio"):
            assert click_intent("新增", role) == "never", role

    def test_认不出来的一律不点(self):
        assert click_intent("···", "button") == "never"
        assert click_intent("", "") == "never"


# ── 路径模板 ────────────────────────────────────────────────────────────

class Test记账用模板导航用真路径:
    def test_id_段换成占位(self):
        assert c.route_template("/teams/9f3a1b2c3d4e5f60/members") == "/teams/:id/members"
        assert c.route_template("/users/42") == "/users/:id"

    def test_普通路径一个字不动(self):
        """清单里那些路径**必须原样** —— 换写法会让整批 item 的 key 变，
        diff 立刻报一片「功能没了」，而一次改版都没发生。
        """
        for p in ("/settings/env", "/qa/reconcile", "/monitoring/mcp-call-logs",
                  "/v2/agents"):
            assert c.route_template(p) == p


# ── 缺口：有反应就不是死按钮 ─────────────────────────────────────────────

def _item(**kw):
    base = {"key": "", "page_path": "/x", "page_title": "", "anchor": "a",
            "anchor_kind": "testid", "label": "", "control_type": "read",
            "state": "enabled", "endpoints": []}
    base.update(kw)
    return base


class TestG4要的是死按钮:
    def test_点开了层就不算没反应(self):
        """点开一个层 = 它**做了事**，只是没发请求（表单是前端渲染的）。

        算成 G4 等于凭空产出一条「这按钮点下去什么都没发生」，
        而 SEC/QA 那边会跑去查一个不存在的东西。查空两次这份清单就没人看了。
        """
        out = compute_gaps(
            page_items=[_item(anchor="new", clicked=True, effect="dialog"),
                        _item(anchor="dead", clicked=True)],
            page_edges=[], routes=[], index={"byDomain": {}, "unresolved": []},
            scripts=[], build_fingerprint="", controls_clicked=2)
        assert [r["anchor"] for r in out["g4"]] == ["/x :: dead"]
        assert out["counters"]["controlsWithEffect"] == 1

    def test_跳走了也算有反应(self):
        out = compute_gaps(
            page_items=[_item(anchor="go", clicked=True, effect="navigate")],
            page_edges=[], routes=[], index={"byDomain": {}, "unresolved": []},
            scripts=[], build_fingerprint="", controls_clicked=1)
        assert out["g4"] == []

    def test_有反应的数一定要渲染(self):
        """G4 从 40 掉到 3，少了这个数就会被读成「缺口变少了」。"""
        out = compute_gaps(page_items=[], page_edges=[], routes=[],
                           index={"byDomain": {}, "unresolved": []}, scripts=[],
                           build_fingerprint="", controls_clicked=0)
        assert out["counters"]["controlsWithEffect"] == 0


# ── 跨分片：点过是关于控件的事实 ─────────────────────────────────────────

class Test跨分片取并集:
    def test_点过和有反应都并上来(self):
        """主爬那份先进来。它的 `clicked=False` 不许盖掉别的角色点出来的证据 ——
        盖掉的表现是「G4 是空的」，和真的没有死按钮长得一样。
        """
        rows = merge_shards([
            {"role": "admin", "items": [{"key": "k1", "clicked": False, "effect": ""}]},
            {"role": "auditor", "items": [{"key": "k1", "clicked": True,
                                           "effect": "dialog"}]},
        ], main_role="admin")
        assert len(rows) == 1
        assert rows[0]["clicked"] is True
        assert rows[0]["effect"] == "dialog"
        assert rows[0]["roles_visible"] == ["admin", "auditor"]

    def test_没人点过就还是没点过(self):
        rows = merge_shards([
            {"role": "admin", "items": [{"key": "k1", "clicked": False, "effect": ""}]},
            {"role": "auditor", "items": [{"key": "k1", "clicked": False, "effect": ""}]},
        ], main_role="admin")
        assert rows[0]["clicked"] is False


# ── 枚举：层内的东西要认得出是层内的 ─────────────────────────────────────

class Test层内枚举:
    def test_同名控件不会串到页面上(self):
        """页面上有个「名称」列头，层里也有个「名称」输入框。

        key 不分层的话两者撞成一个 —— 而 `(survey_id, key)` 撞库是故意留的
        探测器（锚点推断塌掉时会响），在这里制造假撞会把它废掉。
        """
        page = c.collect_items("/x", "T", [{"label": "名称", "role": "button",
                                            "id": "n"}], {})
        inner = c.collect_items("/x", "T", [{"label": "名称", "role": "button",
                                             "id": "n"}], {}, scope="[新建]")
        assert page[0]["key"] != inner[0]["key"]
        assert "新建" in inner[0]["page_title"]

    def test_锚点不带层名(self):
        """锚点是**页面上怎么找到它**，跟从哪个按钮点进来的无关。

        掺进去的话，S6.5 那条「选择器登记表能不能对上」的回路会全部落空。
        """
        inner = c.collect_items("/x", "T", [{"label": "名称", "role": "button",
                                             "id": "n"}], {}, scope="[新建]")
        assert inner[0]["anchor"] == "n"

    def test_每一行都带点过没有(self):
        """缺这个键，run 级 `controlsClicked > 0` 会让 1200 多个没碰过的控件
        **一起**变成假的 G4。
        """
        rows = c.collect_items("/x", "T", [{"label": "查看", "role": "button",
                                            "id": "v"}], {})
        assert rows[0]["clicked"] is False
        assert rows[0]["effect"] == ""

    def test_枚举脚本收得下一个范围(self):
        assert "rootSel" in c._COLLECT_JS
        assert "root.querySelectorAll" in c._COLLECT_JS


# ── 菜单树 ──────────────────────────────────────────────────────────────

class _MenuPage:
    """会说话的假页面：菜单里报几条路径，某些页上有几个开层按钮。"""

    def __init__(self, menu=None, items_by_path=None, dialog_items=None):
        self.menu = menu or []
        self.items_by_path = items_by_path or {}
        self.dialog_items = dialog_items or []
        self.visited = []
        self.clicked = []
        self._cur = ""
        self._dialog_open = False
        self.keyboard = self

    async def goto(self, url, timeout=None):
        self._cur = url
        self._dialog_open = False
        self.visited.append(url)

    async def wait_for_load_state(self, *a, **k):
        return None

    async def wait_for_selector(self, selector, state=None, timeout=None):
        if selector == c.DIALOG_SEL:
            want_visible = state != "hidden"
            if want_visible != self._dialog_open:
                raise TimeoutError("no dialog")
        return None

    async def evaluate(self, js, arg=None):
        if js is c._MENU_JS:
            return {"paths": list(self.menu), "expanded": 0}
        if arg == c.DIALOG_SEL:
            return list(self.dialog_items)
        for path, items in self.items_by_path.items():
            if self._cur.endswith(path.lstrip("/")):
                return list(items)
        return []

    async def title(self):
        return "T"

    async def fill(self, *a, **k):
        return None

    async def click(self, *a, **k):
        return None

    async def press(self, *a, **k):                 # keyboard.press
        self._dialog_open = False

    def locator(self, sel):
        page = self

        class _L:
            first = None

            async def click(self, timeout=None):
                page.clicked.append(sel)
                page._dialog_open = True

        loc = _L()
        loc.first = loc
        return loc

    @property
    def url(self):
        return self._cur


class _Ctx:
    def __init__(self, page):
        self._page = page
        self.closed = False

    async def route(self, *a, **k):
        return None

    async def new_page(self):
        return self._page

    async def close(self):
        self.closed = True


class _Browser:
    def __init__(self, page):
        self._ctx = _Ctx(page)

    async def new_context(self, **k):
        return self._ctx


@pytest.fixture
def _creds(monkeypatch):
    monkeypatch.setenv("QA_AUDITOR_USERNAME", "u")
    monkeypatch.setenv("QA_AUDITOR_PASSWORD", "p")


class Test菜单里发现的页也要爬:
    @pytest.mark.asyncio
    async def test_菜单发现的页会被接到队尾(self, tmp_path, _creds):
        """清单只写了列表页，详情页从来没人进去过 —— 让页面自己说它还能去哪。"""
        page = _MenuPage(menu=["/teams", "/teams/abc123def4567890/members"])
        ledger = {}
        await c.crawl_role(_Browser(page), "http://h", "qa-auditor",
                           ["/teams"], ledger, tmp_path)
        assert ledger["menuDiscovered"] == ["/teams/:id/members"]
        assert ledger["pagesVisited"] == 2

    @pytest.mark.asyncio
    async def test_详情页按模板记账(self, tmp_path, _creds):
        """拿具体 id 记账的话，下一趟同一个页面会整批报成「新增」+「功能没了」。"""
        page = _MenuPage(menu=["/teams/abc123def4567890"],
                         items_by_path={"abc123def4567890": [
                             {"label": "查看", "role": "button", "id": "v"}]})
        ledger = {}
        rows = await c.crawl_role(_Browser(page), "http://h", "qa-auditor",
                                  ["/teams"], ledger, tmp_path)
        assert [r["page_path"] for r in rows] == ["/teams/:id"]
        # 导航用的还是**真**路径，不然打开的是一个不存在的页面
        assert any("abc123def4567890" in u for u in page.visited)

    @pytest.mark.asyncio
    async def test_清单里的页一页都不许少(self, tmp_path, _creds):
        page = _MenuPage(menu=[f"/x{i}" for i in range(60)])
        ledger = {}
        await c.crawl_role(_Browser(page), "http://h", "qa-auditor",
                           ["/a", "/b"], ledger, tmp_path)
        assert ledger["pagesProbed"]["qa-auditor"][:2] == ["/a", "/b"]
        assert ledger["pagesVisited"] == 2 + c.MENU_EXTRA_MAX_PAGES

    @pytest.mark.asyncio
    async def test_预算用完要记账(self, tmp_path, _creds):
        """「这个域只有这些页」和「还有 30 页没去看」不许长得一样。"""
        page = _MenuPage(menu=[f"/x{i}" for i in range(60)])
        ledger = {}
        await c.crawl_role(_Browser(page), "http://h", "qa-auditor",
                           ["/a"], ledger, tmp_path)
        assert ledger["menuExtraCapped"] > 0

    @pytest.mark.asyncio
    async def test_同一页不会被爬两遍(self, tmp_path, _creds):
        page = _MenuPage(menu=["/a", "/a", "/teams/1", "/teams/2"])
        ledger = {}
        await c.crawl_role(_Browser(page), "http://h", "qa-auditor",
                           ["/a"], ledger, tmp_path)
        assert ledger["pagesVisited"] == 2      # /a + /teams/:id
        assert ledger["menuDiscovered"] == ["/teams/:id"]


class Test点开层才看得见表单:
    @pytest.mark.asyncio
    async def test_层里的控件进账本(self, tmp_path, _creds):
        page = _MenuPage(
            items_by_path={"/teams": [{"label": "新建团队", "role": "button",
                                       "id": "new"}]},
            dialog_items=[{"label": "团队名称", "role": "input", "id": "nm",
                           "isField": True, "required": True}])
        ledger = {}
        rows = await c.crawl_role(_Browser(page), "http://h", "qa-auditor",
                                  ["/teams"], ledger, tmp_path)
        labels = {r["label"]: r for r in rows}
        assert "团队名称" in labels
        assert labels["团队名称"]["control_type"] == "field"
        assert ledger["dialogsOpened"] == 1
        assert ledger["controlsClicked"] == 1
        assert ledger["fieldsSeen"] == 1
        # 开层的那个按钮自己也要被标上「点过、有反应」
        assert labels["新建团队"]["clicked"] is True
        assert labels["新建团队"]["effect"] == "dialog"

    @pytest.mark.asyncio
    async def test_删除按钮一次都不点(self, tmp_path, _creds):
        page = _MenuPage(items_by_path={"/teams": [
            {"label": "删除", "role": "button", "id": "del"},
            {"label": "退出登录", "role": "button", "id": "out"}]})
        ledger = {}
        await c.crawl_role(_Browser(page), "http://h", "qa-auditor",
                           ["/teams"], ledger, tmp_path)
        assert page.clicked == []
        # `controlsClicked` 由 `run_survey` 初始化成 0（0 也要渲染）；
        # 单跑一个角色时它还没被建出来，这里问的是"有没有点过"。
        assert ledger.get("controlsClicked", 0) == 0

    @pytest.mark.asyncio
    async def test_一页最多点几个(self, tmp_path, _creds):
        page = _MenuPage(items_by_path={"/teams": [
            {"label": f"新建{i}", "role": "button", "id": f"n{i}"} for i in range(9)]})
        ledger = {}
        await c.crawl_role(_Browser(page), "http://h", "qa-auditor",
                           ["/teams"], ledger, tmp_path)
        assert ledger["controlsClicked"] == c.DIALOG_PROBE_PER_PAGE
