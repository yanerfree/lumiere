"""语种要两边都发生：**断言按语种取译文，请求也得告诉被测系统要哪个语种。**

原来只有前一半 —— `${T:}` 会把期望值换成英文，请求却不带 Accept-Language，
被测系统照旧回中文，于是切到 en 全红。而这是**假红**：排查的人先去查产品，
查半天才发现是测试自己没把语种发过去（CC 反馈第五条点名的正是这种误判）。
"""
from __future__ import annotations

from app.services.api_test_runner import _inject_accept_language as inject


def test_按TEST_LANGUAGE带上语种():
    h = {}
    inject(h, {"TEST_LANGUAGE": "en"})
    assert h["Accept-Language"] == "en-US"
    h = {}
    inject(h, {"TEST_LANGUAGE": "zh"})
    assert h["Accept-Language"] == "zh-CN"


def test_没配就不加():
    """不配语种是最常见的情况，别凭空往请求里塞头。"""
    h = {}
    inject(h, {})
    assert h == {}


def test_步骤自己写了就不覆盖():
    """写了就是有意为之（比如专门测多语种回退），大小写都要认出来。"""
    h = {"accept-language": "ja-JP"}
    inject(h, {"TEST_LANGUAGE": "en"})
    assert h == {"accept-language": "ja-JP"}


def test_不认识的值原样带过去():
    """写 fr / de 也该发出去，别悄悄吞掉 —— 吞了就又是"设了没生效"。"""
    h = {}
    inject(h, {"TEST_LANGUAGE": "fr"})
    assert h["Accept-Language"] == "fr"


def test_真的接在发请求的路径上():
    import inspect

    from app.services import api_test_runner
    src = inspect.getsource(api_test_runner.run_single_step)
    assert "_inject_accept_language(headers, env)" in src
