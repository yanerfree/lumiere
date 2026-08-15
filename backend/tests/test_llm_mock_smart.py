"""智能应答（可控假上游的指令契约）。

这些测试守的不是「代码跑得通」，而是几条**错了会让结论整个反过来**的判据：
脱敏模式的行匹配、回显的两个长度、以及「没开智能应答时行为逐字节不变」。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.services import llm_mock_engine as engine
from app.services import llm_mock_smart as smart


def _msg(text: str, role: str = "user") -> dict:
    return {"messages": [{"role": role, "content": text}]}


def _route(**over) -> dict:
    base = {
        "name": "t", "status_code": 200, "response_type": "text", "response_mode": "default",
        "response_body": "原本的正文", "model_mode": "follow_request", "custom_model": None,
        "token_mode": "auto", "custom_prompt_tokens": None, "custom_completion_tokens": None,
        "delay_ms": 0, "finish_reason": "stop", "response_headers": None,
        "stream_mode": "auto", "sse_chunk_size": 6, "sse_chunk_delay_ms": 0,
        "match_enabled": True, "match_rules": [], "tool_calls": None,
        "smart_enabled": True, "smart_role": "auto", "smart_body_marker": None,
    }
    base.update(over)
    return base


async def _collect(agen) -> list[str]:
    return [c async for c in agen]


def _drain(agen) -> list[str]:
    return asyncio.run(_collect(agen))


# ───── 脱敏模式：精确行匹配 ─────

def test_脱敏模式_独立一行才算():
    env = "Guardrail.\nRedact mode: detect_and_redact\nText to check: 甲"
    assert smart.is_redact_mode(env) is True


def test_脱敏模式_系统提示在解释规则时不能误判():
    """★ 这条错了，每个「仅检测」请求都会被当成脱敏模式，结论全反。

    系统提示本身就在解释这条规则，正文里必然出现 detect_and_redact 这个词。
    用子串包含判断的话它永远为真 —— 所以必须精确行匹配。
    """
    env = (
        "Note: Redact mode: detect_and_redact means you must mask PII before returning.\n"
        "Redact mode: detect_only\n"
        "Text to check: 身份证号 11010119900101123X"
    )
    assert "detect_and_redact" in env, "反例本身要含这个词，否则这条测试没在测东西"
    assert smart.is_redact_mode(env) is False


# ───── 回显协议：两个长度分开报 ─────

def test_回显_正文为空时BODY_LEN是0而信封仍然很长():
    """★ 只报一个长度的话，正文为空和「正文很长」长得一模一样，证据就被淹了。"""
    env = "You are a guardrail.\nRedact mode: detect_only\nText to check: "
    _, meta = smart.build_checker_verdict(_msg(env))
    assert meta["checkedLen"] == 0
    assert meta["envelopeLen"] > 30
    assert meta["bodyFrom"] == "marker"


def test_回显_抠不到标记时显式标记而不是静默返回0():
    """定位标记对不上时不能装作正文为空 —— 那正是这套回显要防的事。"""
    _, meta = smart.build_checker_verdict(_msg("请判断：客户身份证号 11010119900101123X"))
    assert meta["bodyFrom"] == "fallback"
    assert meta["checkedLen"] == meta["envelopeLen"] > 0


def test_回显_自定义定位标记():
    env = "prefix\n>>>正文开始<<<甲乙丙"
    _, meta = smart.build_checker_verdict(_msg(env), marker=">>>正文开始<<<")
    assert meta["bodyFrom"] == "marker"
    assert meta["checkedLen"] == 3


def test_回显_reason里两个长度都在():
    verdict, _ = smart.build_checker_verdict(_msg("Text to check: 甲乙丙"))
    reason = json.loads(verdict)["reason"]
    assert "BODY_LEN=" in reason and "ENVELOPE_LEN=" in reason


# ───── 判决表 ─────

@pytest.mark.parametrize("env,verdict,cats", [
    ("Redact mode: detect_only\nText to check: 标记为 VIOLATION", False, ["mock_violation"]),
    ("Redact mode: detect_and_redact\nText to check: 身份证 11010119900101123X", True, ["id_card"]),
    ("Redact mode: detect_only\nText to check: 身份证 11010119900101123X", False, ["id_card"]),
    ("Redact mode: detect_only\nText to check: 今天天气不错", True, []),
])
def test_判决表(env, verdict, cats):
    raw, _ = smart.build_checker_verdict(_msg(env))
    d = json.loads(raw)
    assert d["verdict"] is verdict
    assert d["categories"] == cats


def test_脱敏模式才给脱敏后的内容_且号码真被替换掉():
    raw, _ = smart.build_checker_verdict(
        _msg("Redact mode: detect_and_redact\nText to check: 身份证 11010119900101123X 手机 13800138000"))
    d = json.loads(raw)
    red = d["redacted_content"]
    assert "11010119900101123X" not in red and "13800138000" not in red
    assert smart.ID_CARD_MASK in red and smart.PHONE_MASK in red

    # 仅检测模式不该给脱敏内容 —— 给了的话「网关有没有自己脱敏」就分不清了
    raw2, _ = smart.build_checker_verdict(
        _msg("Redact mode: detect_only\nText to check: 身份证 11010119900101123X"))
    assert "redacted_content" not in json.loads(raw2)


# ───── 指令解析 ─────

@pytest.mark.parametrize("text,mode", [
    ("", None), ("你好", None), ("MODE:HIT", "HIT"), ("前面的话 MODE:PII 后面的话", "PII"),
    ("MODE:UNKNOWN", None), ("MODE:LOOP", "LOOP"), ("MODE:FILTER", "FILTER"),
])
def test_指令解析(text, mode):
    assert smart.parse_directive(text)["mode"] == mode


def test_SAY取到行尾而不是到空格():
    d = smart.parse_directive("请照做\nSAY:你好 世界，这是一句话\n下一行")
    assert d["say"] == "你好 世界，这是一句话"


# ───── 三种入参形状 ─────

def test_入参三形状都能读到指令():
    openai = {"messages": [{"role": "user", "content": "MODE:HIT"}]}
    anthropic = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "前言"}, {"type": "text", "text": "MODE:HIT"}]}]}
    legacy = {"prompt": "MODE:HIT"}
    for body in (openai, anthropic, legacy):
        assert smart.parse_directive(smart.extract_user_text(body))["mode"] == "HIT"


def test_取的是最后一条user消息():
    body = {"messages": [
        {"role": "user", "content": "MODE:PII"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "MODE:HIT"},
    ]}
    assert smart.parse_directive(smart.extract_user_text(body))["mode"] == "HIT"


# ───── 协议形状 / 角色 ─────

@pytest.mark.parametrize("path,shape", [
    ("/v1/chat/completions", "chat"),
    ("/mock/x/v1/chat/completions", "chat"),
    ("/v1/completions", "text"),
    ("/openai/v1/completions?api-version=v1", "text"),
    ("/v1/messages", "anthropic"),
])
def test_协议形状按路径判(path, shape):
    assert smart.detect_shape(path) == shape


def test_chat_completions不能被legacy分支吃掉():
    """/chat/completions 也以 /completions 结尾，判别顺序错了所有 chat 请求都会走成 legacy。"""
    assert smart.detect_shape("/v1/chat/completions") == "chat"


@pytest.mark.parametrize("route_role,path,expect", [
    ("auto", "/mock/v1/chat/completions", "upstream"),
    ("auto", "/mock/checker/v1/chat/completions", "checker"),
    ("auto", "/guard/v1/chat/completions", "checker"),
    ("upstream", "/mock/checker/v1/chat/completions", "upstream"),  # 显式压过路径
    ("checker", "/mock/v1/chat/completions", "checker"),
])
def test_角色判定(route_role, path, expect):
    assert smart.resolve_role({"smart_role": route_role}, path) == expect


# ───── apply_smart 翻译层 ─────

def test_无指令走内置默认正文():
    eff, meta = smart.apply_smart(_route(), _msg("你好"), "/v1/chat/completions")
    assert eff["response_body"] == smart.SMART_DEFAULT_BODY
    assert meta["mode"] is None


def test_PII的敏感信息只在输出里():
    """★ 请求里只有 MODE:PII 四个字。护栏若查输入会判「无 PII」并把号码原样放行。"""
    req = _msg("MODE:PII")
    assert "11010119900101123X" not in json.dumps(req, ensure_ascii=False)
    eff, _ = smart.apply_smart(_route(), req, "/v1/chat/completions")
    assert "11010119900101123X" in eff["response_body"]


def test_FILTER是空正文加content_filter():
    """只清空正文不改 finish_reason 的话，客户端分不出「被过滤」和「模型没话说」。"""
    eff, _ = smart.apply_smart(_route(), _msg("MODE:FILTER"), "/v1/chat/completions")
    assert eff["response_body"] == ""
    assert eff["finish_reason"] == "content_filter"


def test_EMPTY切出零个分片():
    eff, _ = smart.apply_smart(_route(), _msg("MODE:EMPTY"), "/v1/chat/completions")
    assert engine._split_chunks(eff["response_body"], eff["sse_chunk_size"]) == []


def test_DEFY强制流式():
    eff, _ = smart.apply_smart(_route(), {**_msg("MODE:DEFY"), "stream": False}, "/v1/chat/completions")
    assert eff["stream_mode"] == "force_stream"


def test_SLOW非流式按分片数累计延迟():
    """非流式也要慢，否则量不出「全量缓冲把首字延迟推成完整生成耗时」这个降级代价。"""
    eff, _ = smart.apply_smart(_route(sse_chunk_size=6), _msg("MODE:SLOW"), "/v1/chat/completions")
    n = engine._split_chunks(eff["response_body"], 6)
    assert eff["delay_ms"] == smart.SLOW_CHUNK_DELAY_MS * len(n) > 0

    # 流式则不叠 delay_ms（那是首字节前的等待），只把每片间隔调慢
    eff2, _ = smart.apply_smart(_route(), {**_msg("MODE:SLOW"), "stream": True}, "/v1/chat/completions")
    assert eff2["delay_ms"] == 0
    assert eff2["sse_chunk_delay_ms"] == smart.SLOW_CHUNK_DELAY_MS


def test_LOOP第一轮回tool_calls第二轮回终局():
    eff1, m1 = smart.apply_smart(_route(), _msg("MODE:LOOP"), "/v1/chat/completions")
    assert m1["loopStage"] == 1
    assert eff1["response_type"] == "tool_calls" and eff1["finish_reason"] == "tool_calls"
    assert eff1["tool_calls"]

    round2 = {"messages": [
        {"role": "user", "content": "MODE:LOOP"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1"}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
    ]}
    eff2, m2 = smart.apply_smart(_route(), round2, "/v1/chat/completions")
    assert m2["loopStage"] == 2
    assert eff2["response_type"] == "text"
    # 终局要有可拦的东西，否则「护栏有没有介入终局」验不出来
    assert "VIOLATION" in eff2["response_body"]


def test_LOOP第二轮认Anthropic的tool_result():
    body = {"messages": [
        {"role": "user", "content": [{"type": "text", "text": "MODE:LOOP"}]},
        {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
    ]}
    assert smart.loop_stage(body) == 2


def test_checker角色不演MODE场景():
    """护栏检查模型不是被测智能体的上游，它只回判决。"""
    eff, meta = smart.apply_smart(
        _route(smart_role="checker"), _msg("MODE:PII"), "/v1/chat/completions")
    assert meta["mode"] is None
    assert "11010119900101123X" not in eff["response_body"]
    assert json.loads(eff["response_body"])["verdict"] is True


def test_smart_meta记的是网关实际发出的stream():
    """流式降级到底有没有真发生，只能看这一格 —— 记 mock 最终怎么回的就没意义了。"""
    _, meta = smart.apply_smart(
        _route(), {**_msg("MODE:DEFY"), "stream": False}, "/v1/chat/completions")
    assert meta["stream"] is False   # 请求写的 false
    _, meta2 = smart.apply_smart(
        _route(), {**_msg("你好"), "stream": True, "stream_options": {"include_usage": True}},
        "/v1/chat/completions")
    assert meta2["stream"] is True and meta2["includeUsage"] is True


def test_智能应答一律旁路条件应答规则():
    """两套都生效的话，「这次到底是谁决定了响应」就只能靠猜。"""
    r = _route(match_enabled=True, match_rules=[{
        "id": "x", "enabled": True, "field": "prompt", "op": "contains_any",
        "value": ["你好"], "response_body": "规则的回复"}])
    eff, _ = smart.apply_smart(r, _msg("你好"), "/v1/chat/completions")
    assert eff["match_enabled"] is False
    assert eff["response_body"] != "规则的回复"


# ───── 三种协议形状的输出 ─────

def test_三种形状的响应结构各不相同():
    r = _route()
    chat, _ = engine.build_response_json(r, _msg("hi"))
    assert chat["object"] == "chat.completion"
    assert "message" in chat["choices"][0]

    text, _ = engine.build_text_completion_json(r, {"prompt": "hi"})
    assert text["object"] == "text_completion"
    assert "text" in text["choices"][0] and "message" not in text["choices"][0]

    ant, _ = engine.build_anthropic_message_json(r, _msg("hi"))
    assert ant["type"] == "message"
    assert ant["content"][0]["type"] == "text"
    assert ant["stop_reason"] == "end_turn"        # 不叫 finish_reason
    assert "input_tokens" in ant["usage"]          # 不叫 prompt_tokens


def test_anthropic空正文是空content数组():
    ant, _ = engine.build_anthropic_message_json(_route(response_body=""), _msg("hi"))
    assert ant["content"] == []


def test_anthropic流式带event行():
    """Anthropic SDK 按事件名分派，只发 data 它认不出来。"""
    frames = _drain(engine.build_anthropic_stream(_route(response_body="甲乙丙丁"), _msg("hi")))
    joined = "".join(frames)
    for ev in ("message_start", "content_block_start", "content_block_delta",
               "content_block_stop", "message_delta", "message_stop"):
        assert f"event: {ev}\n" in joined


def test_legacy流式用text字段():
    frames = _drain(engine.build_text_completion_stream(_route(response_body="甲乙丙"), {"prompt": "x"}))
    first = json.loads(frames[0].removeprefix("data: ").strip())
    assert "text" in first["choices"][0] and "delta" not in first["choices"][0]
    assert frames[-1].strip() == "data: [DONE]"


# ───── 回归守卫：没开智能应答时行为不能变 ─────

def test_没开智能应答时条件应答照常工作():
    r = _route(smart_enabled=False, match_rules=[{
        "id": "x", "enabled": True, "field": "prompt", "op": "contains_any",
        "value": ["退款"], "response_body": "您的退款已受理"}])
    eff, hit = engine.apply_matched_rule(r, _msg("我要退款"))
    assert hit is not None
    assert eff["response_body"] == "您的退款已受理"


def test_规则可以单独改finish_reason且不影响没填的情况():
    base = _route(finish_reason="stop")
    rule = {"id": "x", "enabled": True, "field": "prompt", "op": "contains_any", "value": ["过滤"]}

    eff, _ = engine.apply_matched_rule({**base, "match_rules": [dict(rule, response_body="")]}, _msg("过滤"))
    assert eff["finish_reason"] == "stop", "没填就得沿用路由的，不能被悄悄改掉"

    eff2, _ = engine.apply_matched_rule(
        {**base, "match_rules": [dict(rule, response_body="", finish_reason="content_filter")]}, _msg("过滤"))
    assert eff2["finish_reason"] == "content_filter"


def test_usage帧仍在流末尾且在DONE之前():
    """少了它流式 token 记 0，很容易当成平台计费缺陷去排查。"""
    frames = _drain(engine.build_response_stream(
        _route(smart_enabled=False, response_body="甲乙丙"),
        {**_msg("hi"), "stream_options": {"include_usage": True}}))
    assert frames[-1].strip() == "data: [DONE]"
    usage_frame = json.loads(frames[-2].removeprefix("data: ").strip())
    assert usage_frame["choices"] == []
    assert usage_frame["usage"]["total_tokens"] > 0


def test_展开成规则只含规则表达得了的那几条():
    rules = smart.expand_to_rules()
    keys = " ".join(json.dumps(r, ensure_ascii=False) for r in rules)
    for expandable in ("MODE:HIT", "MODE:PII", "MODE:EMPTY", "MODE:FILTER", "MODE:DEFY", "SAY:"):
        assert expandable in keys
    # 这两条响应内容依赖请求内容，规则表达不了 —— 混进去只会做出一条假的
    assert "MODE:LOOP" not in keys and "MODE:SLOW" not in keys

    declared = {d["key"]: d["expandable"] for d in smart.DIRECTIVE_CONTRACT}
    assert declared["MODE:LOOP"] is False and declared["MODE:SLOW"] is False


def test_展开出来的规则真的能被引擎命中():
    """展开完是死的就等于没给逃生口。"""
    r = _route(smart_enabled=False, match_enabled=True, match_rules=smart.expand_to_rules())
    eff, hit = engine.apply_matched_rule(r, _msg("MODE:PII"))
    assert hit is not None and "11010119900101123X" in eff["response_body"]

    # SAY 的捕获组要能回填进 ${match.1}。回填发生在响应构建阶段（_resolve_body），
    # 不是命中阶段 —— 只断言 apply_matched_rule 的话，看到的还是没展开的模板串
    body = _msg("SAY:你好世界")
    eff2, _ = engine.apply_matched_rule(r, body)
    assert engine._resolve_body(eff2, body) == "你好世界"

    eff3, _ = engine.apply_matched_rule(r, _msg("MODE:FILTER"))
    assert eff3["response_body"] == "" and eff3["finish_reason"] == "content_filter"
