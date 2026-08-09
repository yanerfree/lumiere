"""Playwright 沙箱准备 — 生成 conftest.py 等运行环境文件"""
from __future__ import annotations
from pathlib import Path


def write_playwright_conftest(
    sandbox_dir: str,
    env_vars: dict[str, str] | None = None,
    har_path: str | None = None,
):
    """在沙箱目录写入 Playwright conftest.py — 包含登录 fixture 和浏览器配置。

    har_path：录制执行期的浏览器网络流量。**这是失败分类唯一的网络证据来源**——
    tea_capture 插件 patch 的是 httpx，浏览器流量根本不经过它。
    HAR 只在 context.close() 时 flush，所以进程超时被 kill 时拿不到，属正常。
    """
    ev = env_vars or {}
    pw_locale = ev.get("PLAYWRIGHT_LOCALE", "zh-CN")
    admin_user = ev.get("ADMIN_USERNAME", "")
    admin_pass = ev.get("ADMIN_PASSWORD", "")
    tenant_user = ev.get("TENANT_USERNAME", "")
    tenant_pass = ev.get("TENANT_PASSWORD", "")
    base_url = ev.get("BASE_URL", "")
    har_literal = repr(har_path) if har_path else "None"

    Path(sandbox_dir, "conftest.py").write_text(f'''import pytest
from playwright.sync_api import Page
from tea_step import tea_step

ADMIN_USERNAME = "{admin_user}"
ADMIN_PASSWORD = "{admin_pass}"
TENANT_USERNAME = "{tenant_user}"
TENANT_PASSWORD = "{tenant_pass}"
BASE_URL = "{base_url}"

@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    args = {{**browser_context_args, "locale": "{pw_locale}", "viewport": {{"width": 1280, "height": 720}}}}
    if {har_literal}:
        args["record_har_path"] = {har_literal}
        args["record_har_content"] = "embed"
    return args

@pytest.fixture(autouse=True)
def set_timeout(page: Page):
    page.set_default_timeout(10000)
    yield

@pytest.fixture
def logged_in_page(page: Page):
    """管理员已登录的 page"""
    with tea_step("打开系统首页", phase="setup"):
        page.goto(BASE_URL)
        page.wait_for_load_state("networkidle")
    _do_login(page, ADMIN_USERNAME, ADMIN_PASSWORD, "管理员")
    return page

@pytest.fixture
def tenant_page(page: Page):
    """租户账号登录的 page"""
    with tea_step("打开系统首页", phase="setup"):
        page.goto(BASE_URL)
        page.wait_for_load_state("networkidle")
    _do_login(page, TENANT_USERNAME, TENANT_PASSWORD, "租户")
    return page

def _do_login(page: Page, username: str, password: str, role: str = ""):
    """通用登录 — 每个操作都记录 tea_step"""
    if "/login" not in page.url:
        with tea_step("已登录，跳过登录步骤", phase="setup"):
            pass
        return
    with tea_step(f"输入账号 {{username}}", phase="setup"):
        inputs = page.locator("input:not([type=hidden])").all()
        for inp in inputs:
            inp_type = inp.get_attribute("type") or "text"
            if inp_type in ("text", "email", ""):
                inp.fill(username)
                break
    with tea_step("输入密码", phase="setup"):
        pwd = page.locator("input[type=password]")
        if pwd.count() > 0:
            pwd.first.fill(password)
    with tea_step("点击登录按钮", phase="setup"):
        submit = page.locator("button[type=submit]")
        if submit.count() == 0:
            submit = page.get_by_role("button", name="登录", exact=True).or_(
                page.get_by_role("button", name="Login", exact=True))
        submit.first.click()
    with tea_step(f"等待登录完成（{{role}}）", phase="setup"):
        page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
        page.wait_for_load_state("networkidle")
''', encoding="utf-8")
