"""UI 文案的换语种能力：t() 运行时 + 文案纪律 + 硬编码软警告。

方案不是新定的 —— docs/cc-platform-loop-spec.md §2.9 早就写清楚了：
「数据不许写死已有硬拦截，文案没管；文案该从 i18n 词典 t() 取，平台按 LOCALE 注入译文」，
并且 §2.11 明确「不进第一刀，先把回推链跑顺」。现在回推链跑顺了（21 条用例全维度齐全），
补上这一刀。

做的顺序有讲究：**先导译文再接 t()**。反过来的话，t() 上线当天所有词都查不到、
全部退回中文，看不出到底生效没有 —— 词典里 33 条的 translations 一直是空的。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from app.engine.pw_conftest import write_playwright_conftest
from app.mcp.tools.sync import _scan_ui_script


def _sandbox(env, table):
    """env 可以是语种串（老写法）或环境变量字典。"""
    d = tempfile.mkdtemp()
    ev = env if isinstance(env, dict) else {"PLAYWRIGHT_LOCALE": env}
    write_playwright_conftest(d, ev, None, table)
    sys.path.insert(0, d)
    for m in list(sys.modules):
        if m == "tea_i18n":
            del sys.modules[m]
    import tea_i18n
    return tea_i18n


def test_英文环境取译文():
    t = _sandbox("en-US", {"更多": {"en-US": "More"}}).t
    assert t("更多") == "More"


def test_中文环境原样返回():
    """中文是原文，不用查表 —— 也别因为词典没收录就变成别的。"""
    t = _sandbox("zh-CN", {"更多": {"en-US": "More"}}).t
    assert t("更多") == "更多"


def test_查不到就原样返回不许抛():
    """**词典一定是不全的。** 让脚本因为缺一条词而挂掉，比不做这个功能还糟。"""
    t = _sandbox("en-US", {}).t
    assert t("某个没收录的词") == "某个没收录的词"


def test_没传词典也能跑():
    t = _sandbox("en-US", None).t
    assert t("更多") == "更多"


# 片段必须带 def test_ —— 另有一条硬拦截要求脚本里有 pytest 认得的测试函数，
# 不带的话会先被那条拦下，测不到文案这条。
_SNIP = "def test_x(page):\n    "


def test_写死中文给软警告不硬拦():
    e, w = _scan_ui_script(_SNIP + 'page.get_by_role("button", name="更多").click()', "python")
    assert not e, "硬拦了 —— 词典总有不全的时候，会把人卡死"
    assert any("硬编码中文" in x for x in w), w


def test_用了t就不警告():
    e, w = _scan_ui_script(
        'from tea_i18n import t\n' + _SNIP + 'page.get_by_role("button", name=t("更多")).click()',
        "python")
    assert not any("硬编码中文" in x for x in w), w


def test_testid定位不警告():
    e, w = _scan_ui_script(_SNIP + 'page.get_by_test_id("sync-status-bar").click()', "python")
    assert not any("硬编码中文" in x for x in w), w


def test_注释里的中文不算():
    """只扫定位/断言里的文案。脚本头部的中文说明是好事，警告它会让人把注释删了。"""
    e, w = _scan_ui_script('# 打开服务管理页面\n' + _SNIP + 'page.goto(BASE_URL)', "python")
    assert not any("硬编码中文" in x for x in w), w


def test_语种开关是短名字两个值():
    """`PLAYWRIGHT_LOCALE=en-US` 要求人知道①它是 Playwright 概念②得写 BCP-47 全码。
    写错一个字就静默退回中文，而"没生效"和"译文没导"长得一模一样。
    `TEST_LANGUAGE=zh|en` 写错的空间小得多。"""
    t_en = _sandbox({"TEST_LANGUAGE": "en"}, {"更多": {"en-US": "More"}}).t
    assert t_en("更多") == "More"
    t_up = _sandbox({"TEST_LANGUAGE": "EN"}, {"更多": {"en-US": "More"}}).t
    assert t_up("更多") == "More", "大小写不该影响"


def test_不配语种就是中文():
    """绝大多数时候跑的是中文，默认值该是最常用的那个。"""
    mod = _sandbox({}, {"更多": {"en-US": "More"}})
    assert mod.LOCALE == "zh-CN" and mod.t("更多") == "更多"


def test_值写错了退回中文不报错():
    assert _sandbox({"TEST_LANGUAGE": "xx"}, {"更多": {"en-US": "More"}}).t("更多") == "更多"


def test_只给en也能匹配到en_US():
    """词典键是 en-US，人可能只给 en —— 按语言前缀兜一层。"""
    assert _sandbox({"PLAYWRIGHT_LOCALE": "en"}, {"更多": {"en-US": "More"}}).t("更多") == "More"


def test_规范里写了文案纪律():
    from app.mcp.tools.sync import _SPEC_UI_SCRIPT
    for k in ("data-testid", "tea_i18n", "TEST_LANGUAGE", "原样返回中文"):
        assert k in _SPEC_UI_SCRIPT, f"规范缺「{k}」—— CC 只照规范写"


def test_导入脚本按key路径配对():
    """两个 locale 文件的键序不保证一致。按顺序配会把「保存」映射成「Cancel」，
    而且错得悄无声息。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from import_i18n_from_sut import _flatten
    assert _flatten({"a": {"b": "保存"}, "c": "取消"}) == {"a.b": "保存", "c": "取消"}


# ── 接口断言的文案占位 ──────────────────────────────────────────

def test_接口断言按语种换文案():
    """**文案不是 UI 专属的。** 接口错误提示语跟着 Accept-Language 变，
    断言里写死中文，跑英文环境照样全红。
    UI 那边是脚本里 import t()，接口场景是平台执行的 JSON、没有脚本可以 import，
    所以给一个 ${T:} 占位走 ${} 这条既有的解析路。"""
    from app.services.api_test_runner import _resolve_variables as r
    env = {"TEST_LANGUAGE": "en",
           "__I18N__": {"服务名已存在": {"en-US": "Service name already exists"}}}
    assert r("${T:服务名已存在}", env) == "Service name already exists"


def test_接口断言中文环境原样():
    from app.services.api_test_runner import _resolve_variables as r
    assert r("${T:服务名已存在}", {"TEST_LANGUAGE": "zh", "__I18N__": {}}) == "服务名已存在"


def test_接口断言查不到原样返回():
    from app.services.api_test_runner import _resolve_variables as r
    assert r("${T:没收录这句}", {"TEST_LANGUAGE": "en", "__I18N__": {}}) == "没收录这句"


def test_文案占位和普通变量能混用():
    """译文里也可能带 ${var}（「服务 ${name} 已存在」），所以文案要先解、再过变量。"""
    from app.services.api_test_runner import _resolve_variables as r
    env = {"TEST_LANGUAGE": "en", "svc": "payment",
           "__I18N__": {"服务已存在": {"en-US": "service ${svc} exists"}}}
    assert r("${T:服务已存在}", env) == "service payment exists"


def test_接口规范里写了文案占位():
    from app.mcp.tools.sync import _SPEC_API_SCENARIO
    for k in ("${T:", "TEST_LANGUAGE", "原样返回中文"):
        assert k in _SPEC_API_SCENARIO, f"接口规范缺「{k}」—— CC 只照规范写"


# ── 键必须是语言中立的 ──────────────────────────────────────────

def test_同一个键切语种取不同值():
    """**这是这套设计的核心。** 第一版拿中文当键（`${T:服务名已存在}`）是错的：
    中文既是键又是值，不对称 —— 而且中文文案一改（「服务名已存在」→「服务名称已存在」），
    键就失效、静默退回原文，红都不红。
    """
    from app.services.api_test_runner import _resolve_variables as r
    D = {"services.form.nameRequired": {"zh-CN": "服务名称(name)必填",
                                        "en-US": "Service name (name) is required"}}
    ref = "${T:services.form.nameRequired}"
    assert r(ref, {"TEST_LANGUAGE": "zh", "__I18N__": D}) == "服务名称(name)必填"
    assert r(ref, {"TEST_LANGUAGE": "en", "__I18N__": D}) == "Service name (name) is required"


def test_UI侧同一个键也切两种():
    mod_zh = _sandbox({"TEST_LANGUAGE": "zh"},
                      {"common.create": {"zh-CN": "创建", "en-US": "Create"}})
    mod_en = _sandbox({"TEST_LANGUAGE": "en"},
                      {"common.create": {"zh-CN": "创建", "en-US": "Create"}})
    assert mod_zh.t("common.create") == "创建"
    assert mod_en.t("common.create") == "Create"


def test_采集器不再往词典里插中文当键():
    """它以前拿中文原文当键插进来，而 translations 是空的 —— t() 查不到译文就返回键
    （正好是中文），**和没这条一模一样**，凭空多了一套不一致的键约定。
    现在它只报告：脚本里的硬编码中文该换成哪个键、哪些在词典里找不到。"""
    import inspect

    from app.services import i18n_harvest_service as h
    src = inspect.getsource(h.harvest_project)
    assert "session.add" not in src, "又开始往词典里写了"
    for k in ("mapped", "unmapped"):
        assert f'"{k}"' in src, f"报告里缺 {k}"


def test_导入脚本用key路径当键():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import importlib

    import import_i18n_from_sut as imp
    importlib.reload(imp)
    src = Path(imp.__file__).read_text(encoding="utf-8")
    assert 'key_text=key' in src, "又拿中文当键了"
    assert '"zh-CN": zh_text' in src, "没把中文也存成值 —— 那样切回中文就取不到"
