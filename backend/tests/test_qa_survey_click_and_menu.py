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

    def __init__(self, menu=None, items_by_path=None, dialog_items=None,
                 jumps=None, layer_how="role"):
        # 这一层是靠标准属性认出来的（`role`），还是靠几何兜底（`geometry`）。
        self.layer_how = layer_how
        self.menu = menu or []
        self.items_by_path = items_by_path or {}
        self.dialog_items = dialog_items or []
        # `{选择器: 点了会跳到哪}` —— 有的产品「新建」不是弹层，是跳一页。
        self.jumps = jumps or {}
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

    async def wait_for_timeout(self, ms):
        return None

    async def wait_for_selector(self, selector, state=None, timeout=None):
        # 层在不在，认的是**点开之后盖的那个章**（`LAYER_SEL`），
        # 不是"页面上还有没有长得像层的东西"。
        if selector in (c.DIALOG_SEL, c.LAYER_SEL):
            want_visible = state != "hidden"
            if want_visible != self._dialog_open:
                raise TimeoutError("no dialog")
        return None

    async def evaluate(self, js, arg=None):
        if js is c._MENU_JS:
            return {"paths": list(self.menu), "expanded": 0}
        if js is c._MARK_PRE_JS:
            # 点之前盖章。这个假页面上「点之前」没有层状物。
            return 0
        if js is c._FIND_LAYER_JS:
            # 真页面上判的是「新冒出来的、fixed 且够大」；这里直接用假页面
            # 自己的开关代表那个结论 —— 判据本身另有封样盯着（见下面那一档）。
            if not self._dialog_open:
                return None
            return {"how": self.layer_how, "tag": "div", "role": "dialog",
                    "fields": 3, "w": 480, "h": 320}
        if arg in (c.DIALOG_SEL, c.LAYER_SEL):
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
                if sel in page.jumps:
                    page._cur = page.jumps[sel]
                    page._dialog_open = False
                else:
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

    @pytest.mark.asyncio
    async def test_导航链接不占开层预算(self, tmp_path, _creds):
        """左侧导航（`Settings` / `Config` 这类 `<a href>`）点了只会跳走。

        2026-09-04 实测那一趟：255 次点击里 234 次是跳转、**开层 0 次** ——
        预算被导航吃光，真正的「新建」一个都没轮到，而账本上「点了 255 下」
        看着非常健康。**这条断言盯的就是那件事**：同一页上导航和新建都在，
        点开的必须是新建。
        """
        page = _MenuPage(
            items_by_path={"/teams": [
                {"label": "Settings", "role": "a", "href": "/settings", "id": "s1"},
                {"label": "Config", "role": "link", "href": "/config", "id": "s2"},
                {"label": "新建团队", "role": "button", "id": "new"}]},
            dialog_items=[{"label": "团队名称", "role": "input", "id": "nm",
                           "isField": True}])
        ledger = {}
        await c.crawl_role(_Browser(page), "http://h", "qa-auditor",
                           ["/teams"], ledger, tmp_path)
        assert ledger["controlsClicked"] == 1
        assert ledger["dialogsOpened"] == 1

    @pytest.mark.asyncio
    async def test_表头不当成开层按钮(self, tmp_path, _creds):
        """`Created At` 里有 `create`，但它是表头，不是「新建」。

        子串匹配下这一条会被当开层按钮点上去（点不着还记一笔
        `dialogClickFailed`），词边界之后它连候选都不是。
        """
        page = _MenuPage(items_by_path={"/teams": [
            {"label": "Created At", "role": "columnheader", "id": "h1"},
            {"label": "Updated", "role": "button", "id": "h2"}]})
        ledger = {}
        await c.crawl_role(_Browser(page), "http://h", "qa-auditor",
                           ["/teams"], ledger, tmp_path)
        assert page.clicked == []
        assert ledger.get("controlsClicked", 0) == 0

    @pytest.mark.asyncio
    async def test_同一个testid跨页只探一次(self, tmp_path, _creds):
        """顶栏那个每页都在的按钮，探一次就够 —— 否则预算全花在它身上。

        反过来**只认文案的按钮按页各探一次**：`新建` 在两个模块背后是两张
        不同的表单，按文案去重会把第二张整个丢掉。
        """
        page = _MenuPage(items_by_path={
            "/teams": [{"label": "新建", "role": "button", "testid": "gk"},
                       {"label": "新增", "role": "button"}],
            "/agents": [{"label": "新建", "role": "button", "testid": "gk"},
                        {"label": "新增", "role": "button"}]})
        ledger = {}
        await c.crawl_role(_Browser(page), "http://h", "qa-auditor",
                           ["/teams", "/agents"], ledger, tmp_path)
        # testid 那个只点了 1 次，纯文案那个两页各点 1 次
        assert ledger["controlsClicked"] == 3


class Test点新建跳走的那一页也要爬:
    """「新建」不弹层、跳一页 —— 那一页就是表单所在的地方。

    2026-09-04 实测（UAG 全量第二趟）：22 次开层点击里 **11 次是跳转、
    弹层 0 次**。跳走了 `goto` 回来、把地址丢掉的话，表单字段一个也枚举不到,
    而账本上「点了 22 下」看着一切正常 —— 又是一个**缺的部分不留痕迹**的洞。
    跳出来的页跟菜单发现来的**同一个待遇**：同一条队列、同一份预算、同一套去重。
    """

    @pytest.mark.asyncio
    async def test_跳出来的页进队列并被枚举(self, tmp_path, _creds):
        page = _MenuPage(
            items_by_path={
                "/adapters": [{"label": "New Adapter", "role": "button",
                               "testid": "add-adapter"}],
                "/adapters/new": [{"label": "名称", "role": "input",
                                   "id": "nm", "isField": True}]},
            jumps={'[data-testid="add-adapter"]': "/adapters/new"})
        ledger = {}
        items = await c.crawl_role(_Browser(page), "http://h", "qa-auditor",
                                   ["/adapters"], ledger, tmp_path)
        assert ledger["dialogsNavigated"] == 1
        assert "/adapters/new" in ledger["menuDiscovered"]
        assert ledger["pagesVisited"] == 2
        # 表单字段真的被枚举到了 —— 这才是这条链路的目的
        assert ledger["fieldsSeen"] == 1
        assert any(i["page_path"] == "/adapters/new" for i in items)

    @pytest.mark.asyncio
    async def test_跳回已经爬过的页不重复排队(self, tmp_path, _creds):
        """点「新建」跳到的要是本来就在清单里的页，别再排一次。"""
        page = _MenuPage(
            items_by_path={"/adapters": [{"label": "New Adapter",
                                          "role": "button", "testid": "a1"}],
                           "/agents": []},
            jumps={'[data-testid="a1"]': "/agents"})
        ledger = {}
        await c.crawl_role(_Browser(page), "http://h", "qa-auditor",
                           ["/adapters", "/agents"], ledger, tmp_path)
        assert ledger["pagesVisited"] == 2
        assert ledger.get("menuDiscovered", []) == []


class Test同页撞锚点合成一行:
    """表格每一行同一个 `data-testid` —— 一页因此冒出 6 个一模一样的 key。

    2026-09-04 实测：这一条把整趟 214 页、7 个角色的产物全废了
    （落库撞唯一约束 → `status=failed`），而页面上和"这一趟没跑"长得一样。
    现在合成一行 + 记 `anchorCollisions`：**探测器还在，只是不再打死病人。**
    """

    def test_撞了的合成一行并记数(self):
        led = {}
        rows = c.collect_items("/logs", "T", [
            {"label": "", "role": "button", "testid": "expand-row"},
            {"label": "", "role": "button", "testid": "expand-row"},
            {"label": "", "role": "button", "testid": "expand-row"},
            {"label": "导出", "role": "button", "testid": "export"}], led)
        out = c.dedupe_items(rows, led)
        assert len(out) == 2
        assert led["anchorCollisions"] == 2
        assert led["anchorCollisionKeys"] == ["/logs::expand-row"]

    def test_点过的证据不许被合掉(self):
        """先来那份没点过、后来那份点开了层 —— 合完必须留证据。

        丢了的话 G4 会凭空多一条「点了什么都没发生」，而那是**假缺口**。
        """
        led = {}
        a = {"key": "k", "anchor": "x", "label": "", "clicked": False, "effect": ""}
        b = {"key": "k", "anchor": "x", "label": "新建", "clicked": True,
             "effect": "dialog"}
        out = c.dedupe_items([a, b], led)
        assert len(out) == 1
        assert out[0]["clicked"] is True
        assert out[0]["effect"] == "dialog"
        assert out[0]["label"] == "新建"

    def test_没撞也要落一个0(self):
        """0 要渲染。缺键在页面上长成"没算过" —— 而这个数是锚点塌没塌的
        **唯一出口**（此前那个出口是撞库把整趟炸掉）。渲染成"没算过"
        等于把探测器拆了，还没人知道拆过。
        """
        led = {}
        out = c.dedupe_items([{"key": "a"}, {"key": "b"}], led)
        assert len(out) == 2
        assert led["anchorCollisions"] == 0


class Test层怎么认出来的:
    """判据从「它叫什么名字」换成「它表现得像不像一个层」。

    起因是 2026-09-04 那趟真跑：`dialogsOpened` **恒为 0**，而层其实开了 ——
    那个产品的层是 `div.fixed.inset-0.z-50` 加一块 `sm:w-[480px]` 的面板，
    既没有 `role="dialog"`，也不是 `.ant-modal-content`。旧判据是照 antd 写的。

    **归零在报告上和「这个产品没有弹窗」长得一模一样**，所以这一档盯两件事：
    判据里不许再出现某个 UI 库的名字，以及"一个都没开出来"必须自己留痕。
    """

    def test_判据里不许出现某个UI库的名字(self):
        """写死 `.ant-modal-content` 就等于宣布「只有 antd 的产品能测」。

        这套东西要能换项目跑。类名是实现细节，换个库就整维静默归零。
        """
        judged = c.DIALOG_SEL + c._LAYER_CAND_JS + c._FIND_LAYER_JS + c._MARK_PRE_JS
        for name in ("ant-", "MuiDialog", "chakra", "el-dialog", "v-modal"):
            assert name not in judged, name

    def test_点之前就在的层状物不算新开的(self):
        """页面常驻的吸顶栏、侧边抽屉也是 fixed + 高 z-index + 够大。

        不排掉的话，随便点一下都会「开出一个层」，整页控件挂到那个按钮名下 ——
        不是少一条账，是**一条错的账**，而且账面上比真相好看。
        """
        assert "data-qa-pre" in c._MARK_PRE_JS
        assert "hasAttribute('data-qa-pre')" in c._FIND_LAYER_JS

    def test_枚举和关闭认的是同一个章(self):
        """认出来就地盖 `data-qa-layer`，之后枚举/关闭都认它。

        两处各判一次的话，枚举时按 A 算、关闭时按 B 算 —— 关不掉却以为关掉了，
        后面几次探测全在同一个层上点。
        """
        src = c.__loader__.get_source(c.__name__)
        assert "_COLLECT_JS, LAYER_SEL" in src
        assert "wait_for_selector(DIALOG_SEL" not in src

    @pytest.mark.asyncio
    async def test_几何兜底认出来的也照样进账(self, tmp_path, _creds):
        """没有 `role="dialog"` 的层不许被当成"没开层"。"""
        page = _MenuPage(
            items_by_path={"/teams": [{"label": "新建团队", "role": "button",
                                       "id": "new"}]},
            dialog_items=[{"label": "团队名称", "role": "input", "id": "nm",
                           "isField": True}],
            layer_how="geometry")
        ledger = {}
        rows = await c.crawl_role(_Browser(page), "http://h", "qa-auditor",
                                  ["/teams"], ledger, tmp_path)
        assert ledger["dialogsOpened"] == 1
        assert ledger["layersBy"] == {"role": 0, "geometry": 1}
        assert "团队名称" in {r["label"] for r in rows}

    @pytest.mark.asyncio
    async def test_两条路分开记(self, tmp_path, _creds):
        """兜底那一格一旦变成大头，说明前端的层上没有 `role="dialog"` ——

        那是要去问前端的事（脚本定位也会跟着难写），不是我们这边的噪声。
        混成一个数就问不出来了。
        """
        page = _MenuPage(
            items_by_path={"/teams": [{"label": "新建团队", "role": "button",
                                       "id": "new"}]},
            dialog_items=[])
        ledger = {}
        await c.crawl_role(_Browser(page), "http://h", "qa-auditor",
                           ["/teams"], ledger, tmp_path)
        assert ledger["layersBy"] == {"role": 1, "geometry": 0}
        # 认出来的层长什么样要留几条样本，否则"判错了"和"真是那样"没法回看
        assert ledger["layerShapes"][0]["label"] == "新建团队"
        assert ledger["layerShapes"][0]["how"] == "role"

    def test_一个层都没开也要留下两个零(self):
        """`layersBy` 必须在账本初始化时就摆好，不能等认出层才 `setdefault`。

        等到那时候，"这一趟一个层都没开"连格子都不存在 —— 页面上什么都不显示，
        于是「产品没有弹层」和「我们认不出它的弹层」又长回一模一样。
        """
        src = c.__loader__.get_source(c.__name__)
        assert '"layersBy": {"role": 0, "geometry": 0},' in src

    def test_页面上那几格都得先摆一个零(self):
        """页面渲染哪几格，`run_survey` 就得先把哪几格摆成 0。

        只在发生时 `+1` 的计数，一次都没发生的那一趟**根本没有这个键** ——
        前端 `<Num n={undefined}>` 渲染成「没记过（不是 0）」，
        于是「点了一下都没跳走」和「这一路压根没跑」长得一模一样。
        这是这一批反复踩的同一个坑，单独封一条。
        """
        src = c.__loader__.get_source(c.__name__)
        for key in ("controlsClicked", "dialogsOpened", "dialogsNoEffect",
                    "fieldsSeen", "dialogsNavigated", "controlsAnchorless"):
            assert f'"{key}": 0' in src, key
