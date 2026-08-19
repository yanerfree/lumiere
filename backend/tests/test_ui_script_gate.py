"""UI 脚本回推门禁的封样。

新增的一条是"别自己 sync_playwright()"：实测走完整条 CC 链路时，
用 `with sync_playwright() as p:` 写的脚本**入库顺利通过**，直到执行才抛

    playwright._impl._errors.Error: It looks like you are using
    Playwright Sync API inside the asyncio loop. Please use the Async API instead.

平台用 pytest 跑，而仓库的 pytest 配置是 `asyncio_mode=auto`，每个用例都被包进
事件循环，此时调 sync API 必挂。那句报错完全看不出该怎么改，而且要等到执行那一步
才出现 —— 中间隔着排队和几十秒。所以挪到入库时挡住，并直接给出该怎么写。
"""
from app.mcp.tools.sync import _scan_ui_script

GOOD = '''import os
from playwright.sync_api import Page, expect

BASE_URL = os.getenv("BASE_URL", "")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")


def test_登录后能看到项目列表(page: Page):
    page.goto(f"{BASE_URL}/login")
    page.get_by_placeholder("用户名").fill(ADMIN_USERNAME)
    expect(page.get_by_text("项目")).to_be_visible()
'''

BAD_SYNC = '''import os
from playwright.sync_api import sync_playwright


def test_probe():
    base = os.getenv("BASE_URL", "")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(base)
        b.close()
'''


def test_照规范写的脚本能过():
    errors, _ = _scan_ui_script(GOOD, "python")
    assert errors == []


def test_自己起sync_playwright被拦住():
    errors, _ = _scan_ui_script(BAD_SYNC, "python")
    assert errors, "这种写法在平台上必挂，不该放它入库"


def test_拦住时得说清怎么改():
    """只说"不行"没用 —— 得给出能直接照抄的写法。"""
    msg = "\n".join(_scan_ui_script(BAD_SYNC, "python")[0])
    assert "page fixture" in msg or "page: Page" in msg
    assert "事件循环" in msg


def test_写死服务地址仍然被拦():
    bad = GOOD.replace('os.getenv("BASE_URL", "")', '"http://192.168.51.108:5173"')
    assert _scan_ui_script(bad, "python")[0]


def test_没有测试函数仍然被拦():
    assert _scan_ui_script("import os\nprint('hi')\n", "python")[0]


def test_typescript不受这条影响():
    """sync_playwright 是 python 侧的问题，别误伤 TS 脚本。"""
    ts = '''import { test, expect } from "@playwright/test";
test("login", async ({ page }) => {
  await page.goto(`${process.env.BASE_URL}/login`);
});
'''
    assert _scan_ui_script(ts, "typescript")[0] == []


# ── 规范里必须写清的两件事（写不清，CC 就会撞同一堵墙）────────────────

def test_规范里写了多角色怎么写():
    """审批类功能天然多角色（申请人/审批人/二级审批人），而规范只讲了变量、形状、
    文案、流程，唯独没讲多角色。外部 CC 按「一个 page 反复清 storage 换人」写，
    规范既没拦也没提示，直到**渲染进程挂死**（连 Page.screenshot 都 30s 超时）才暴露。
    清 storage 擦不掉内存里的 store 和查询缓存，而且"一个会话扮多个人"本就不是真实场景。
    """
    from app.mcp.tools.sync import _SPEC_UI_SCRIPT as spec
    assert "new_context" in spec, "没给出独立 context 的写法"
    assert "清 storage" in spec, "没点名「清 storage 换人」这个具体错法"


def test_规范里写了SPA怎么等():
    """timing 那节只讲接口侧的 retry_timeout_ms，UI 侧只字未提。
    默认 goto 等 load，在 SPA + 有轮询的页面上直接卡满 30s。
    """
    from app.mcp.tools.sync import _SPEC_UI_SCRIPT as spec
    assert "domcontentloaded" in spec
    assert "networkidle" in spec, "没说清 networkidle 同样不能用（轮询永远不 idle）"
