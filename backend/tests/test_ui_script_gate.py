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
