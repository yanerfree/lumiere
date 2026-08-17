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


def _sandbox(locale, table):
    d = tempfile.mkdtemp()
    write_playwright_conftest(d, {"PLAYWRIGHT_LOCALE": locale}, None, table)
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


def test_规范里写了文案纪律():
    from app.mcp.tools.sync import _SPEC_UI_SCRIPT
    for k in ("data-testid", "tea_i18n", "PLAYWRIGHT_LOCALE", "原样返回中文"):
        assert k in _SPEC_UI_SCRIPT, f"规范缺「{k}」—— CC 只照规范写"


def test_导入脚本按key路径配对():
    """两个 locale 文件的键序不保证一致。按顺序配会把「保存」映射成「Cancel」，
    而且错得悄无声息。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from import_i18n_from_sut import _flatten
    assert _flatten({"a": {"b": "保存"}, "c": "取消"}) == {"a.b": "保存", "c": "取消"}
