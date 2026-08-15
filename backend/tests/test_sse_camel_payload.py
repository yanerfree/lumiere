"""SSE 的 done 事件必须自己驼峰化 —— 中间件管不到流式响应。

实测表现：UI 脚本跑完，面板上写「验证通过 **耗时未记录**」，而 script_runs 里
duration_ms = 13403 明明存着。抽屉里那栏也是「耗时 -」。

根因：驼峰中间件只重写 `JSONResponse.render()`，`StreamingResponse` 的每个 chunk
是手写 `json.dumps` 直接拼进流里的，压根不经过它。于是服务端发 `duration_ms`，
前端读 `durationMs`。

同一个 payload 里 `error_summary` 也是这样，那个更严重：**跑挂了错误原文整段不显示**，
人只看到一个红点，什么都不知道。
"""
from __future__ import annotations

import json

from app.api.scripts import _sse_done


def _payload_of(sse: str) -> dict:
    assert sse.startswith("event: done\ndata: "), sse[:40]
    assert sse.endswith("\n\n")
    return json.loads(sse[len("event: done\ndata: "):].strip())


def test_耗时字段是驼峰():
    d = _payload_of(_sse_done({"status": "passed", "duration_ms": 13403}))
    assert d["durationMs"] == 13403
    assert "duration_ms" not in d


def test_错误摘要字段是驼峰():
    """跑挂时前端读 errorSummary —— 不驼峰就等于把错误原文吞了。"""
    d = _payload_of(_sse_done({"status": "failed", "error_summary": "元素找不到: #submit"}))
    assert d["errorSummary"] == "元素找不到: #submit"
    assert "error_summary" not in d


def test_事件格式没被改坏():
    sse = _sse_done({"status": "passed"})
    assert sse.startswith("event: done\ndata: ")
    assert sse.endswith("\n\n"), "SSE 事件必须以空行结尾，否则前端不会触发"


def test_截图路径原样保留():
    """路径是字符串不是键，不该被碰。"""
    d = _payload_of(_sse_done({"screenshots": ["/tmp/a_b/shot_1.png"]}))
    assert d["screenshots"] == ["/tmp/a_b/shot_1.png"]


def test_步骤里的响应原文只改外层键不动内容():
    """外层 `response_body` 是我们的字段名，该驼峰；**里面**是被测系统返回的原文，
    一个键都不许动 —— 那是证据，改了就等于伪造。"""
    d = _payload_of(_sse_done({"steps": [
        {"step_name": "登录", "response_body": {"access_token": "x", "user_id": 1}},
    ]}))
    s = d["steps"][0]
    assert s["stepName"] == "登录", "我们的字段照常驼峰"
    assert s["responseBody"] == {"access_token": "x", "user_id": 1}, "对方返回的原文不许动"


def test_本次流量的请求响应体不许被改键():
    """面板要拿它去编排接口场景，键被改过就编排出一个发错请求的场景。"""
    d = _payload_of(_sse_done({"captured_requests": [
        {"url": "/x", "request_body": {"upstream_id": "u1"}},
    ]}))
    assert d["capturedRequests"][0]["request_body"] == {"upstream_id": "u1"}


def test_中文和None不炸():
    d = _payload_of(_sse_done({"status": "failed", "error_summary": None, "steps": []}))
    assert d["errorSummary"] is None


def test_两个流式入口都用了这个helper():
    """钉住调用点 —— 只测 helper 的话，哪个入口漏改了都不会红。
    实测有两条 SSE 路径（TypeScript 和 Python pytest），当初两条都是手写 dumps。"""
    import inspect

    from app.api import scripts
    src = inspect.getsource(scripts)
    assert src.count("_sse_done(") >= 3, "两个 yield 点 + 一处定义，少于 3 说明有入口没改"
    # 不该再有手写的 done 事件
    assert 'yield f"event: done\\ndata: {final}' not in src, "还有手写的 done 事件没走 helper"
