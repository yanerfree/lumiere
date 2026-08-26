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
    login_url = ev.get("LOGIN_URL", "") or "/api/auth/login"
    # 多角色造数：环境里凡是成对的 `<X>_USERNAME` + `<X>_PASSWORD` 都算一个角色。
    # 网关那种「平台管理员建服务 → 租户管理员申请订阅 → 提供方审批」的数据，
    # 单靠 admin 一个 token 根本造不出来。角色名 = 前缀小写（admin / tenant / app / provider…）
    roles = {}
    for k, v in (ev or {}).items():
        if k.endswith("_USERNAME") and str(v or "").strip():
            pwd = ev.get(k[: -len("_USERNAME")] + "_PASSWORD")
            if str(pwd or "").strip():
                roles[k[: -len("_USERNAME")].lower()] = (str(v), str(pwd))
    roles_note = "、".join(sorted(roles)) or "（一个都没有 —— 环境里没配账号）"
    roles_literal = repr(roles)
    admin_user = ev.get("ADMIN_USERNAME", "")
    admin_pass = ev.get("ADMIN_PASSWORD", "")
    tenant_user = ev.get("TENANT_USERNAME", "")
    tenant_pass = ev.get("TENANT_PASSWORD", "")
    base_url = ev.get("BASE_URL", "")
    lang_key = ev.get("UI_LANG_STORAGE_KEY", "")
    # 存进去的**值**也各家不同：stoa 要 BCP-47（zh-CN/en-US），Lumiere 自己要短码（zh/en）。
    # 默认 BCP-47；要短码就在环境里配 UI_LANG_STORAGE_VALUE={lang}。
    # 猜错的代价还是"设了没生效"：键对了值不认，页面照旧原语种，断言全红。
    lang_value = (ev.get("UI_LANG_STORAGE_VALUE") or "{locale}") \
        .replace("{locale}", pw_locale).replace("{lang}", pw_locale.split("-")[0])
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
# api fixture 造数要用它登录。环境里配了就用配的，没配退回常见默认值
LOGIN_URL = "{login_url}" or "/api/auth/login"
# 环境里发现的所有角色（成对的 <X>_USERNAME/<X>_PASSWORD）
ROLES = {roles_literal}

@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    args = {{**browser_context_args, "locale": "{pw_locale}", "viewport": {{"width": 1280, "height": 720}}}}
    if {har_literal}:
        args["record_har_path"] = {har_literal}
        args["record_har_content"] = "embed"
    return args


# ── 每个 context 一个 HAR 文件 ────────────────────────────────────
#
# **为什么必须这么做**：多角色脚本会自己开 context
# （`browser.new_context(**browser_context_args)`），而 args 里带着同一个
# `record_har_path`。Playwright 在每个 context close 时把 HAR 整个写一遍 ——
# 后关的那个直接**覆盖**先关的。实测：测试内打印 HAR 是 28MB，
# pytest 退出后只剩 324 字节、0 条 entry。
#
# 后果不是"少了点流量"，是**执行式审核最值钱的那条判据从来没生效过**：
# 「接口场景里的端点，页面到底调不调」靠的就是这份流量，而它恒为空时
# 审核只会说"没发现端点问题"—— 跟"端点都对"在报告上长得一模一样。
#
# 修法是给每个 context 分一个文件名，读的时候合并（见 har.parse_har_dir）。
# 脚本自己显式传了别的 record_har_path 就不动它。
_TEA_HAR = {har_literal}
if _TEA_HAR:
    import itertools as _it
    import pathlib as _pl
    _tea_har_seq = _it.count(1)

    @pytest.fixture(scope="session", autouse=True)
    def _tea_split_har(browser):
        _orig = browser.new_context

        def _patched(**kw):
            if kw.get("record_har_path") == _TEA_HAR:
                kw["record_har_path"] = str(
                    _pl.Path(_TEA_HAR).with_name(f"network-{{next(_tea_har_seq)}}.har"))
            return _orig(**kw)

        browser.new_context = _patched
        try:
            yield
        finally:
            browser.new_context = _orig

def pytest_runtest_teardown(item, nextitem):
    """进入收尾阶段就打个标记 —— 平台据此告诉用户"在关浏览器、存流量"。

    为什么要**确定的信号**而不是靠"沉默超过 N 秒"猜：实测收尾是 2.2 秒，
    而中途的 wait_for_url / expect 重试也能停 1.2 秒以上 —— 用沉默判会在跑到
    第 20 步时弹出「正在收尾」，那是句假话。这个 hook 在 teardown 一开始就调，
    位置准确。
    """
    print("##TEARDOWN##", flush=True)


# 被测系统自己的语言开关。**浏览器 locale 换不动它** —— 实测 stoa：locale 设成 en-US
# 页面照旧全中文，它读的是 localStorage 的 `stoa-lang`。这就是"语种设了没生效"的根子：
# 平台把期望值换成了英文，被测系统却还在说中文，断言全红，人先去查产品。
# 环境里配一行 UI_LANG_STORAGE_KEY=<键名>，这里在页面脚本跑之前把它种下去。
LANG_STORAGE_KEY = "{lang_key}"
LANG_STORAGE_VALUE = "{lang_value}"

@pytest.fixture
def context(context):
    if LANG_STORAGE_KEY:
        context.add_init_script(
            "try{{localStorage.setItem(%r, %r)}}catch(e){{}}" % (LANG_STORAGE_KEY, LANG_STORAGE_VALUE)
        )
    return context

@pytest.fixture(autouse=True)
def set_timeout(page: Page):
    page.set_default_timeout(10000)
    yield

class _Api:
    """给 UI 脚本做**前置和清理**用的接口客户端。**多角色**。

    为什么要有它：UI 脚本里"登录→建服务→发布→等推送"这一串纯造数用页面点，
    慢、脆、而且跟被测点无关 —— 一个 20 步的脚本里往往 15 步是造数。
    造数走接口之后，UI 脚本只剩「做被测那一个动作 + 验页面看得见的结果」。

    **多角色怎么用**（网关那种数据靠一个 admin 造不出来）：
        api.post("/api/v1/services", json=...)          # 默认角色（admin）
        api.role("tenant").post("/api/v1/subscriptions", json=...)   # 换租户身份
        api.login("u", "p").post(...)                   # 环境里没登记的账号

    token 过期会**自动重登一次再重试**（网关的 token 15 分钟就失效，
    造数中途过期的话报出来是一句 401，看不出是过期 —— 那种错最难查）。

    **不要用它验断言**：这是造数工具。断言该看页面的就看页面，
    该看接口的写在接口场景里 —— 拿它在 UI 脚本里断接口，两边职责就糊了。
    """

    def __init__(self, base_url: str, roles: dict, role: str = "admin", shared=None):
        import httpx
        self._base = base_url.rstrip("/")
        self._roles = roles or {{}}
        self._role = role
        self._shared = shared if shared is not None else {{"client": httpx.Client(
            timeout=30, follow_redirects=True), "tokens": {{}}}}

    # ── 身份 ──
    def role(self, name: str):
        """换个角色。角色名来自环境变量前缀：admin / tenant / app / provider …"""
        if name not in self._roles:
            raise AssertionError(
                f"环境里没有角色 {{name!r}} —— 已登记的是 {{sorted(self._roles)}}。"
                f"在环境变量里加 {{name.upper()}}_USERNAME / {{name.upper()}}_PASSWORD，"
                f"或者用 api.login(用户名, 密码)")
        return _Api(self._base, self._roles, name, self._shared)

    def login(self, username: str, password: str):
        """用任意账号（环境里没登记的）。"""
        key = f"__adhoc__{{username}}"
        self._roles = {{**self._roles, key: (username, password)}}
        return _Api(self._base, self._roles, key, self._shared)

    def _token(self, force: bool = False) -> str | None:
        import httpx
        cache = self._shared["tokens"]
        if not force and self._role in cache:
            return cache[self._role]
        cred = self._roles.get(self._role)
        if not cred:
            return None
        try:
            r = httpx.post(f"{{self._base}}{{LOGIN_URL}}",
                           json={{"username": cred[0], "password": cred[1]}}, timeout=30)
            if r.status_code >= 300:
                raise AssertionError(f"角色 {{self._role}} 登录失败 → {{r.status_code}} {{r.text[:200]}}")
            d = r.json().get("data") or {{}}
            tok = d.get("token") or d.get("access_token")
            cache[self._role] = tok
            return tok
        except AssertionError:
            raise
        except Exception as e:      # noqa: BLE001
            raise AssertionError(f"角色 {{self._role}} 登录异常：{{e}}") from e

    # ── 请求 ──
    def _headers(self, extra=None, force_login=False):
        h = {{"Content-Type": "application/json"}}
        tok = self._token(force=force_login)
        if tok:
            h["Authorization"] = f"Bearer {{tok}}"
        return {{**h, **(extra or {{}})}}

    def request(self, method, path, **kw):
        url = path if path.startswith("http") else f"{{self._base}}{{path}}"
        extra = kw.pop("headers", None)
        kw["headers"] = self._headers(extra)
        r = self._shared["client"].request(method.upper(), url, **kw)
        if r.status_code == 401:
            # token 过期就重登一次再来 —— 不重试的话造数会以一句 401 结束，
            # 而"过期"和"没权限"在报错里长得一模一样
            kw["headers"] = self._headers(extra, force_login=True)
            r = self._shared["client"].request(method.upper(), url, **kw)
        return r

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)

    def put(self, path, **kw):
        return self.request("PUT", path, **kw)

    def patch(self, path, **kw):
        return self.request("PATCH", path, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)

    def json(self, method, path, **kw):
        """要 body 的时候用它：直接回解析好的 dict，非 2xx 立刻抛 ——
        造数失败要炸在造数那一步，别让它以"页面上找不到元素"的形式在后面爆。"""
        r = self.request(method, path, **kw)
        if r.status_code >= 300:
            raise AssertionError(
                f"造数失败[{{self._role}}] {{method}} {{path}} → {{r.status_code}} {{r.text[:200]}}")
        return r.json() if r.content else {{}}

    def close(self):
        try:
            self._shared["client"].close()
        except Exception:      # noqa: BLE001
            pass


@pytest.fixture(scope="session")
def api():
    """多角色接口客户端，专门用来**造前置数据和清理**。

        svc = api.json("POST", "/api/v1/services", json={{"name": name}})["data"]
        api.role("tenant").json("POST", "/api/v1/subscriptions", json={{...}})
        ...页面上只做被测那一个动作...
        api.delete(f"/api/v1/services/{{svc['id']}}")

    环境里成对的 `<X>_USERNAME`/`<X>_PASSWORD` 自动成为一个角色（前缀小写）。
    这个环境登记了：{roles_note}
    """
    c = _Api(BASE_URL, ROLES, "admin" if "admin" in ROLES else (
        sorted(ROLES)[0] if ROLES else "admin"))
    yield c
    c.close()


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
    # **别用 page.url 判「已登录」**：SPA 是先把壳渲染出来、再由路由守卫跳 /login，
    # 而 networkidle 常常在跳转之前就满足了。那一瞬间 url 还是 BASE_URL，
    # 于是这里判成"已登录"直接跳过 —— 脚本在未登录状态下往下跑，
    # 挂在十几步之后的某个元素上，报出来是 element_not_found，看不出是没登录。
    # 实测踩到：logged_in_page 打印「已登录，跳过登录步骤」，失败截图却是登录页。
    # 用「页面上还有没有密码框」判，它跟渲染同步，不跟 URL 赛跑。
    try:
        page.wait_for_selector("input[type=password]", timeout=3000)
    except Exception:
        pass
    if page.locator("input[type=password]").count() == 0:
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

    # **按当前语种先解析平**：沙箱里拿到的是 {键: 这次该用的那句话}，一个扁平字典。
    # 以前注入的是 {键: {语种: 文案}}，取值得在运行时挑语种 —— 那逼着脚本写成函数调用
    # `t("键")`，而脚本里别的取值全是 os.getenv(...)：同一份脚本里两套取法，
    # 读的人看不出 t() 也是"平台注入的数据"。摊平之后就能写成变量表下标。
    flat: dict[str, str] = {}
    for ref, row in (mapping or {}).items():
        if not isinstance(row, dict):
            continue
        val = row.get(locale)
        if not val:                                  # en 找 en-US，zh 找 zh-CN
            pre = locale.split("-")[0]
            val = next((v for k, v in row.items() if k.split("-")[0] == pre and v), None)
        if val:
            flat[ref] = val

    _Path(sandbox_dir, "tea_i18n.py").write_text(
        "# 平台注入：这次执行该用的 UI 文案表（已按语种解析好）。\n"
        "# 语种由环境变量 TEST_LANGUAGE=zh|en 决定，脚本不用管。\n"
        "import os\n\n"
        f"LOCALE = os.getenv('PLAYWRIGHT_LOCALE', {locale!r})\n\n"
        "class _Text(dict):\n"
        "    \"\"\"平台注入的文案表 —— 当变量表用，别当函数用。\n\n"
        "        TEXT[\"services.list.searchPlaceholder\"]                      # 取当前语种那句话\n"
        "        TEXT.get(\"services.list.searchPlaceholder\", \"搜索服务名 / 路由…\")  # 推荐\n\n"
        "    推荐带上中文原文（就是 dict.get 的默认值）：读脚本的人一眼知道在验什么，\n"
        "    词典里没收录时也退回中文，而不是把键名当文案去匹配（那必然找不到元素）。\n"
        "    \"\"\"\n"
        # 裸下标查不到就**抛**，不返回键名。返回键名的下场：正例红在「找不到元素」上
        # （看得见），而「不应出现」那类负例**假绿** —— 键名当文案去匹配，匹配不到任何
        # 元素，"不该存在"当然成立。恒真断言不喊疼，所以宁可当场炸。
        # 词典不全是常态 → 那就用带默认值的 TEXT.get(键, "中文原文")，它照旧不抛。
        "    def __missing__(self, ref):\n"
        "        raise KeyError(\n"
        "            f'文案键 {ref!r} 不在平台注入的词典里。裸下标 TEXT[键] 查不到会直接抛 ——\\n'\n"
        "            f'返回键名的话，「不应出现」那类断言会假绿（键名匹配不到任何元素）。\\n'\n"
        "            f'两条任选：把这个键登记进项目词典（lum_upsert_i18n_terms），\\n'\n"
        "            f'或改用 TEXT.get({ref!r}, \"中文原文\")（查不到退回中文，不抛）。')\n\n"
        f"TEXT = _Text({_json.dumps(flat, ensure_ascii=False)})\n\n"
        "def t(ref: str, zh: str = None) -> str:\n"
        "    \"\"\"i18next 那套写法的别名，老脚本还在用；新脚本直接用 TEXT。\"\"\"\n"
        "    return TEXT.get(ref, zh if zh is not None else ref)\n",
        encoding="utf-8")
