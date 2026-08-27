"""选择器登记表（`${SEL:键}`）的封样。

起因是实测：本库 18 个 UI 脚本、125 处 `page.locator(...)`，只有 4 处用 testid，
其余大量是 `.card.card-pad` / `button.btn.sm.primary` / `.ant-modal` 这种样式类 ——
前端改一次样式要逐条改脚本，改漏的那几条等下次回归红了才知道。

这里封的是三件容易被悄悄拆掉的事：
  ① `${SEL:}` 不能被当成文案键（两套占位共用一个正则，撞了会报一个人根本
     找不到该去哪登记的错）
  ② **每条执行路径都要渲染选择器**，且顺序是先选择器再文案
  ③ `status='gap'` 那一档不许存 selector，也不许在入库时被放行 ——
     没有它，「被测前端没抓手」就是一个免费且无痕的借口
"""
import inspect

import pytest


# ── ① 两套占位不许打架 ────────────────────────────────────────────────
def test_SEL占位不会被当成文案键():
    """`${SEL:用例列表.新建按钮}` 带冒号带点号，长得就像个带命名空间的文案键。
    不排掉的话文案门禁会硬拦成"这个键词典里没有"——而人根本找不到该去哪登记它。"""
    from app.services.ui_text_render import text_key, unresolved

    assert text_key("SEL:用例列表.新建按钮") is None
    assert text_key("services.list.q") == "services.list.q"
    assert unresolved("${SEL:a.b} ${services.list.q}") == ["services.list.q"]


def test_文案渲染不碰SEL占位():
    from app.services.ui_text_render import render

    out, stat = render('page.locator("${SEL:列表.按钮}")', {}, "zh-CN")
    assert "${SEL:列表.按钮}" in out
    assert stat["missing"] == []


# ── ② 渲染与转义 ──────────────────────────────────────────────────────
def test_渲染分三桶且转义引号():
    from app.services.ui_selector_render import render

    tbl = {
        "列表.按钮": {"selector": '[data-testid="x"]', "status": "active"},
        "面板.申请": {"selector": None, "status": "gap"},
    }
    src = ('a = "${SEL:列表.按钮}"\n'
           'b = "${SEL:面板.申请}"\n'
           'c = "${SEL:没登记的}"\n')
    out, stat = render(src, tbl)
    assert stat["resolved"] == ["列表.按钮"]
    assert stat["gap"] == ["面板.申请"]
    assert stat["missing"] == ["没登记的"]
    # 引号必须转义，否则替进去就把字符串字面量截断了（语法错，且报错点在别处）
    assert r'\"x\"' in out
    # 换不掉的原样留着 —— 交给执行前那道硬拦截，别静默吞掉
    assert "${SEL:面板.申请}" in out and "${SEL:没登记的}" in out


def test_未解析的提示区分没登记和缺抓手():
    """两句话指向完全不同的下一步：一个是"去登记"，一个是"去被测前端提 MR"。
    合成一句的话，人会照着前一句给 gap 行硬塞一个脆选择器 —— 正是要治的东西。"""
    from app.services.ui_selector_render import unresolved_hint

    tbl = {"面板.申请": {"selector": None, "status": "gap", "gap_note": "缺 testid"}}
    h1 = unresolved_hint('"${SEL:没登记的}"', tbl)
    h2 = unresolved_hint('"${SEL:面板.申请}"', tbl)
    assert "lum_upsert_selectors" in h1
    assert "testid" in h2 and "MR" in h2
    assert h1 != h2


# ── ③ 稳定性档位 ─────────────────────────────────────────────────────
@pytest.mark.parametrize("sel,kind", [
    ('[data-testid="case-create"]', "testid"),
    ("#app-root", "id"),
    ('[aria-label="关闭"]', "semantic"),
    (".card.card-pad", "style"),
    (".ant-modal .btn", "style"),
    ("text=新建", "text"),
])
def test_kind按稳定性判(sel, kind):
    from app.services.ui_selector_render import infer_kind

    assert infer_kind(sel) == kind


def test_扫得出脆选择器也扫得出写死的testid():
    from app.services.ui_selector_render import fragile_literals, testid_literals

    src = ('page.locator(".card.card-pad").click()\n'
           'page.locator("[data-testid=case-create]").click()\n')
    assert fragile_literals(src) == [".card.card-pad"]
    assert testid_literals(src) == ["[data-testid=case-create]"]


# ── ④ 入库门禁 ───────────────────────────────────────────────────────
_SCRIPT = '''import os
from playwright.sync_api import Page, expect

BASE_URL = os.getenv("BASE_URL", "")


def test_x(page: Page):
    page.goto(f"{BASE_URL}/cases")
    page.locator("${SEL:用例列表.新建按钮}").click()
    expect(page.locator("${SEL:用例列表.新建按钮}")).to_be_visible()
'''


def test_引用没登记的键硬拦():
    from app.mcp.tools.sync import _scan_ui_script

    errors, _ = _scan_ui_script(_SCRIPT, "python", set(), {})
    assert any("登记表里没有" in e for e in errors)


def test_引用还挂着gap的键硬拦且指向提MR():
    """MR 还没合就把脚本传上来，等于把"等前端补 testid"这件事丢了。"""
    from app.mcp.tools.sync import _scan_ui_script

    tbl = {"用例列表.新建按钮": {"selector": None, "status": "gap"}}
    errors, _ = _scan_ui_script(_SCRIPT, "python", set(), tbl)
    assert any("gap" in e and "MR" in e for e in errors)


def test_登记齐了就放行():
    from app.mcp.tools.sync import _scan_ui_script

    tbl = {"用例列表.新建按钮": {"selector": "[data-testid=x]", "status": "active"}}
    errors, _ = _scan_ui_script(_SCRIPT, "python", set(), tbl)
    assert errors == []


def test_写死样式类只软警告不硬拦():
    """脆不等于错：它会红在明面上（不像假绿那样骗人），硬拦会把
    「先跑通再加固」这条正常路径堵死。"""
    from app.mcp.tools.sync import _scan_ui_script

    src = _SCRIPT.replace('"${SEL:用例列表.新建按钮}"', '".card.card-pad"')
    errors, warns = _scan_ui_script(src, "python", set(), {})
    assert errors == []
    assert any("样式类" in w for w in warns)


# ── ⑤ 每条执行路径都要渲染 + 拦截 ──────────────────────────────────────
def test_四条执行路径都渲染了选择器():
    """这库栽过一次同型的坑：文案词典只在一条路注入，另一条静默跑字面量 ——
    而"没渲染"和"登记表没这条"在结果上长得一模一样（占位都原样留着）。"""
    from app.api import scripts as api_scripts
    from app.mcp.tools import ui_scripts

    for mod in (api_scripts, ui_scripts):
        src = inspect.getsource(mod)
        assert "ui_selector_render" in src, mod.__name__

    from app.engine import executor
    exe = inspect.getsource(executor.execute_single_case)
    assert "ui_selector_render" in exe, "兜底拦截在 executor —— 四条路都过它"


def test_未解析的选择器一律拦在执行前():
    """正例红在「找不到元素」上看得见，**负例假绿**：占位匹配不到任何元素，
    「不应出现」当然成立。恒真断言不会自己喊疼，只能拦在执行前。"""
    from app.engine import executor

    src = inspect.getsource(executor.execute_single_case)
    assert "_unresolved_sel" in src
    assert "拒绝执行" in src


# ── ⑥ 盯住机制：两个队列必须都在 ────────────────────────────────────────
def test_next_duty有待补testid和回来写UI两个队列():
    """配套的一对，缺一个就断链。「回来写 UI」是最容易烂尾的地方 ——
    前端 MR 一合，"缺 testid"这个借口就消失了，而没人会主动想起还有用例欠着。"""
    from app.mcp.tools import duty

    src = inspect.getsource(duty.next_duty)
    assert "待补 testid" in src and "回来写 UI" in src
    assert "selector_gaps_for_branch" in src


def test_回来写UI靠有没有脚本自消不靠人关单():
    from app.mcp.tools import selectors

    src = inspect.getsource(selectors.selector_gaps_for_branch)
    assert "script_type" in src and "ui" in src


def test_gap行不许存selector():
    """留着它，下一个人会直接拿去用，于是"等前端补 testid"永远不会发生。"""
    from app.mcp.tools import selectors

    src = inspect.getsource(selectors.upsert_selectors)
    assert "gap" in src and "selector" in src


def test_两个工具都注册了也进了uiscript档位():
    from app.mcp import TOOL_CATALOG
    from app.mcp.profiles import PROFILES

    names = {t["name"] for t in TOOL_CATALOG}
    assert {"lum_upsert_selectors", "lum_list_selectors"} <= names
    ui = next(p for p in PROFILES if p["key"] == "uiscript")
    assert {"lum_upsert_selectors", "lum_list_selectors"} <= set(ui["tools"])


def test_spec里写了选择器纪律():
    from app.mcp.tools.sync import _SPEC_UI_SCRIPT as spec

    assert "${SEL:" in spec
    assert "lum_upsert_selectors" in spec
    assert "gap" in spec and "MR" in spec


def test_队列数字不许被limit截小():
    """11 个缺口显示成「待补 testid: 3」，看着像快做完了 ——
    展示层的 [:limit] 只该管列多少条，不该管"到底欠多少"。"""
    import inspect

    from app.mcp.tools import selectors

    sig = inspect.signature(selectors.selector_gaps_for_branch)
    assert "limit" not in sig.parameters, "它不该收 limit —— 收了迟早有人拿它截 count"
