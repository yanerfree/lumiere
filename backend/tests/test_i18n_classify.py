"""按键路径给文案分类。

**为什么要有它**：从被测系统 locale 导入时我给 2400 多条一律写了
`category="text"` —— 写死的常量等于没分类，页面上一列全是 text，
筛不了，也看不出哪些是断言最常用的错误提示语。

分类是**存下来、可编辑**的（和 module 一样）；分类器只负责导入时给个准确初值。
"""
from __future__ import annotations

import pytest

from app.services.i18n_classify import classify


@pytest.mark.parametrize("key,want", [
    # 控件形态
    ("apps.btn.disable", "button"),
    ("services.detail.btn.confirmDisable", "button"),
    ("services.list.searchPlaceholder", "placeholder"),
    ("apps.bindModal.configTitle", "title"),
    ("subscription.manage.tabProvider", "tab"),
    ("apps.field.appName", "label"),
    # 语义强的两类 —— 断言最常用
    ("common.yaml.validation.nameRequired", "validation"),
    ("services.form.namePattern", "validation"),
    ("services.lifecycle.publishSuccess", "message"),
    ("apps.bindFailed", "message"),
    ("subscription.status.active", "status"),
    # 判不出来落 text，不猜
    ("services.transform.enabled", "text"),
])
def test_按键分类(key, want):
    assert classify(key) == want


def test_校验错误和提示消息必须分开():
    """「必填」「格式不对」是**可预期的输入校验**，而 message 里还混着成功提示 ——
    断言的写法完全不同，混成一类等于没分。"""
    assert classify("services.form.nameRequired") == "validation"
    assert classify("services.form.saveSuccess") == "message"


def test_语义判据优先于控件形态():
    """`confirmDisable` 既像按钮又像确认语；`validation.confirmRequired` 该是校验。
    顺序错了会被 button 抢先。"""
    assert classify("common.validation.confirmRequired") == "validation"
    assert classify("apps.detail.confirmDisable") == "button"


def test_命名空间不参与判断():
    """否则 `services.*` 里的 "service" 之类的词会到处干扰。"""
    assert classify("status.foo.bar") == classify("apps.foo.bar")


def test_空键不炸():
    assert classify("") == "text"
    assert classify(None) == "text"


def test_导入脚本用分类器不写死text():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "scripts/import_i18n_from_sut.py"
           ).read_text(encoding="utf-8")
    assert "category=classify(key)" in src, "又写死 category 了"
    assert 'category="text"' not in src


def test_前端有校验错误和提示消息两个选项():
    """分类存了但页面上选不到、显示不出中文，等于白分。"""
    from pathlib import Path
    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/settings/I18nMessages.jsx").read_text(encoding="utf-8")
    for v, label in (("validation", "校验错误"), ("message", "提示消息"), ("status", "状态值")):
        assert f"value: '{v}'" in jsx, f"分类选项缺 {v}"
        assert label in jsx, f"分类没有中文标签「{label}」"
    assert "CATEGORY_LABEL" in jsx, "列表上还在直接显示英文 value"
