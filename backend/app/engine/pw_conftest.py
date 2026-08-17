"""Playwright 沙箱准备 — 生成 conftest.py 等运行环境文件"""
from __future__ import annotations
from pathlib import Path


def write_playwright_conftest(
    sandbox_dir: str,
    env_vars: dict[str, str] | None = None,
    har_path: str | None = None,
    i18n: dict[str, dict] | None = None,
):
    """在沙箱目录写入 Playwright conftest.py — 包含登录 fixture 和浏览器配置。

    har_path：录制执行期的浏览器网络流量。**这是失败分类唯一的网络证据来源**——
    tea_capture 插件 patch 的是 httpx，浏览器流量根本不经过它。
    HAR 只在 context.close() 时 flush，所以进程超时被 kill 时拿不到，属正常。
    """
    ev = env_vars or {}
    pw_locale = _resolve_locale(ev)
    _write_i18n_module(sandbox_dir, pw_locale, i18n)
    admin_user = ev.get("ADMIN_USERNAME", "")
    admin_pass = ev.get("ADMIN_PASSWORD", "")
    tenant_user = ev.get("TENANT_USERNAME", "")
    tenant_pass = ev.get("TENANT_PASSWORD", "")
    base_url = ev.get("BASE_URL", "")
    har_literal = repr(har_path) if har_path else "None"

    Path(sandbox_dir, "conftest.py").write_text(f'''import pytest
from playwright.sync_api import Page
from tea_step import tea_step

# 自动埋点：普通 Playwright 脚本（没用 tea_step 的）也能出步骤和验证结果。
# 不装的话执行历史里只有 pytest 那一行 "1 passed"，脚本里十几个 expect() 一个不落。
# 包不上就静默退回原样 —— 埋点绝不能拖垮执行本身。
try:
    import tea_autolog
    tea_autolog.install()
except Exception:
    pass

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

def pytest_runtest_teardown(item, nextitem):
    """进入收尾阶段就打个标记 —— 平台据此告诉用户"在关浏览器、存流量"。

    为什么要**确定的信号**而不是靠"沉默超过 N 秒"猜：实测收尾是 2.2 秒，
    而中途的 wait_for_url / expect 重试也能停 1.2 秒以上 —— 用沉默判会在跑到
    第 20 步时弹出「正在收尾」，那是句假话。这个 hook 在 teardown 一开始就调，
    位置准确。
    """
    print("##TEARDOWN##", flush=True)


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


# 语种开关：环境变量里配 TEST_LANGUAGE=zh / en 就行。
# 不配就是中文 —— 绝大多数时候跑的是中文，默认值该是最常用的那个。
#
# 为什么不直接让人写 PLAYWRIGHT_LOCALE=en-US：那个名字要求人知道
# ① 它是 Playwright 的概念 ② 得写成 BCP-47 全码。写错一个字（en / EN-us）
# 就静默退回中文，而"没生效"和"译文没导"长得一模一样。
# 短名字 + 两个值，写错的空间小得多。
_LANG_TO_LOCALE = {"zh": "zh-CN", "en": "en-US"}


def _resolve_locale(ev: dict) -> str:
    """算出这次跑用哪个语种。TEST_LANGUAGE 是给人用的，PLAYWRIGHT_LOCALE 更具体所以优先。"""
    explicit = (ev.get("PLAYWRIGHT_LOCALE") or "").strip()
    if explicit:
        return explicit
    lang = (ev.get("TEST_LANGUAGE") or "").strip().lower()
    return _LANG_TO_LOCALE.get(lang, "zh-CN")


def _write_i18n_module(sandbox_dir: str, locale: str, mapping: dict[str, dict] | None) -> None:
    """在沙箱里写 tea_i18n.py —— 脚本用 `t("更多")` 取当前语种的文案。

    **文案和数据要对称**（见 docs/cc-platform-loop-spec.md §2.9）：数据不许写死
    已经有硬拦截，文案却一直是硬编码中文（`get_by_role("button", name="更多")`），
    而 conftest 又把浏览器 locale 锁死成 zh-CN —— 于是"能不能测英文"这件事
    根本没法回答，实测 9 个脚本 57 处写死中文。

    现在语种由环境变量 PLAYWRIGHT_LOCALE 决定（它本来就在，只是以前只切浏览器
    语言、不翻脚本文案）。词典由平台按项目注入。

    **查不到就原样返回中文，绝不抛异常** —— 词典一定是不全的，
    让脚本因为缺一条词而挂掉，比不做这个功能还糟。
    """
    import json as _json
    from pathlib import Path as _Path

    table = {k: v for k, v in (mapping or {}).items()}
    _Path(sandbox_dir, "tea_i18n.py").write_text(
        "# 平台注入：按 PLAYWRIGHT_LOCALE 把中文 UI 文案换成当前语种。\n"
        "# 查不到就原样返回 —— 词典不全是常态，不能因此让脚本挂掉。\n"
        "import os\n\n"
        f"LOCALE = os.getenv('PLAYWRIGHT_LOCALE', {locale!r})\n"
        f"_TABLE = {_json.dumps(table, ensure_ascii=False)}\n\n"
        "def t(text: str) -> str:\n"
        "    if LOCALE.startswith('zh'):\n"
        "        return text\n"
        "    row = _TABLE.get(text) or {}\n"
        "    # 精确匹配优先，再按语言前缀兜（词典键是 en-US，人可能只给 en）\n"
        "    if LOCALE in row:\n"
        "        return row[LOCALE]\n"
        "    pre = LOCALE.split('-')[0]\n"
        "    for k, v in row.items():\n"
        "        if k.split('-')[0] == pre and v:\n"
        "            return v\n"
        "    return text\n",
        encoding="utf-8")
