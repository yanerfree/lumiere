"""LLM Mock 引擎 — 路由匹配 + 响应生成 + SSE 流式 + Token 估算 + 向量 (Embeddings)

「按请求内容决定回什么」不在这里 —— 那是 llm_mock_smart 的事。本模块只管：
给定一条（可能已被智能应答改写过的）路由配置，把响应按目标协议形状拼出来。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import re
import struct
import time
import uuid
import string
import random
from functools import lru_cache
from typing import AsyncIterator


RANDOM_RESPONSES: list[str] = [
    "你好！有什么我可以帮助你的吗？",
    "好的，我来帮你处理这个问题。请稍等片刻。",
    "根据我的分析，这个方案是可行的。建议你按照以下步骤操作：首先确认需求，然后制定计划，最后逐步执行。",
    "这是一个很好的问题。简单来说，这个概念的核心在于通过抽象化来降低系统复杂度，同时保持足够的灵活性。",
    "感谢你的提问！以下是我的建议：\n\n1. 先明确目标和约束条件\n2. 评估现有资源和可用方案\n3. 选择最优方案并制定实施计划\n4. 执行并持续监控效果",
    "I'd be happy to help you with that. Based on the information provided, here's my analysis and recommendation.",
    "让我来总结一下要点：\n- 第一，数据完整性需要保障\n- 第二，性能指标要满足 SLA 要求\n- 第三，安全合规是底线\n\n如果还有其他问题，随时可以问我。",
    "这个问题涉及多个方面。从技术角度看，推荐使用微服务架构来解耦各模块；从业务角度看，需要优先保证核心流程的稳定性。",
    "当然可以！这里是一个示例代码：\n\n```python\ndef hello(name):\n    return f\"Hello, {name}!\"\n\nresult = hello(\"World\")\nprint(result)\n```\n\n希望这对你有帮助。",
    "经过仔细分析，我认为有以下几个关键因素需要考虑：响应时间、吞吐量、错误率和资源利用率。建议从这几个维度建立监控体系。",
    "你好，这个任务我已经理解了。预计需要以下资源和时间来完成。如果有任何调整，请随时告知。",
    "这是一个常见的场景。通常的做法是先进行充分的测试，然后灰度发布，观察一段时间后再全量上线。",
    "非常抱歉，我无法直接执行这个操作，但我可以为你提供详细的操作指南和注意事项。",
    "好的，让我换一种方式来解释：想象一下你在整理一个大型图书馆——你需要先建立分类体系，然后按类别整理，最后建立索引方便查找。软件架构设计也是类似的道理。",
    "处理完成！结果显示一切正常，所有测试用例均已通过。详细报告如下...",
]


def _gen_completion_id() -> str:
    chars = string.ascii_letters + string.digits
    suffix = "".join(random.choices(chars, k=29))
    return f"chatcmpl-{suffix}"


def _gen_call_id() -> str:
    chars = string.ascii_letters + string.digits
    suffix = "".join(random.choices(chars, k=24))
    return f"call_{suffix}"


def _gen_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:24]}"


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    non_ascii = len(text) - ascii_chars
    return max(1, int(ascii_chars / 4 + non_ascii / 1.5))


def _resolve_template(template: str, request_body: dict) -> str:
    model = request_body.get("model", "gpt-4o")
    template = template.replace("${request.model}", model)
    messages = request_body.get("messages", [])
    if messages:
        last_content = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                c = m.get("content", "")
                last_content = c if isinstance(c, str) else str(c)
                break
        template = template.replace("${request.messages[-1].content}", last_content)
        template = template.replace("${request.messages.length}", str(len(messages)))
    template = template.replace("${random.uuid}", uuid.uuid4().hex)
    template = template.replace("${timestamp}", str(int(time.time())))
    return template


_ERROR_MAP: dict[int, tuple[str, str | None]] = {
    400: ("invalid_request_error", "invalid_request"),
    401: ("invalid_request_error", "invalid_api_key"),
    403: ("insufficient_quota", "insufficient_quota"),
    404: ("invalid_request_error", "model_not_found"),
    408: ("timeout", "request_timeout"),
    429: ("requests", "rate_limit_exceeded"),
}


def _error_meta(status_code: int) -> tuple[str, str | None]:
    return _ERROR_MAP.get(status_code, ("server_error", "server_error" if status_code >= 500 else None))


def _resolve_body(route: dict, request_body: dict) -> str:
    mode = route.get("response_mode", "default")
    if mode == "random":
        raw = random.choice(RANDOM_RESPONSES)
    else:
        raw = route["response_body"]
    return _resolve_template(raw, request_body)


def build_response_json(route: dict, request_body: dict) -> tuple[dict, dict]:
    """构建非流式 Chat Completion 响应。返回 (response_body, extra_headers)"""
    completion_id = _gen_completion_id()
    created = int(time.time())
    req_model = request_body.get("model", "gpt-4o")
    resp_model = req_model if route["model_mode"] == "follow_request" else (route.get("custom_model") or req_model)

    status_code = route["status_code"]
    if status_code >= 400:
        body_text = _resolve_body(route, request_body)
        try:
            body = json.loads(body_text)
        except (json.JSONDecodeError, TypeError):
            err_type, err_code = _error_meta(status_code)
            body = {"error": {"message": body_text, "type": err_type, "param": None, "code": err_code}}
        return body, _build_headers(route, completion_id)

    response_type = route.get("response_type", "text")
    content = None
    refusal = None
    tool_calls_out = None

    if response_type == "refusal":
        refusal = _resolve_body(route, request_body)
    elif response_type == "tool_calls":
        tool_calls_cfg = route.get("tool_calls") or []
        tool_calls_out = []
        for tc in tool_calls_cfg:
            tool_calls_out.append({
                "id": _gen_call_id(),
                "type": "function",
                "function": {
                    "name": tc.get("name", "unknown"),
                    "arguments": tc.get("arguments", "{}"),
                },
            })
    else:
        content = _resolve_body(route, request_body)

    # Token 计算
    prompt_text = json.dumps(request_body.get("messages", []))
    if route["token_mode"] == "custom":
        prompt_tokens = route.get("custom_prompt_tokens") or 0
        completion_tokens = route.get("custom_completion_tokens") or 0
    else:
        prompt_tokens = estimate_tokens(prompt_text)
        completion_tokens = estimate_tokens(content or refusal or json.dumps(tool_calls_out or []))

    message: dict = {"role": "assistant", "content": content, "refusal": refusal, "annotations": []}
    if tool_calls_out:
        message["tool_calls"] = tool_calls_out
    finish_reason = route.get("finish_reason", "stop")

    body = {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": resp_model,
        "system_fingerprint": "fp_mock_v1",
        "choices": [
            {
                "index": 0,
                "message": message,
                "logprobs": None,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {"cached_tokens": 0, "audio_tokens": 0},
            "completion_tokens_details": {
                "reasoning_tokens": 0,
                "audio_tokens": 0,
                "accepted_prediction_tokens": 0,
                "rejected_prediction_tokens": 0,
            },
        },
        "service_tier": "default",
    }
    return body, _build_headers(route, completion_id)


def _split_chunks(text: str, size: int) -> list[str]:
    """按 size 个字符切块。空串切出 0 块 —— 对应「零内容的流」（只有开头帧和结束帧）。"""
    n = max(1, int(size or 1))
    return [text[i:i + n] for i in range(0, len(text), n)]


async def build_response_stream(route: dict, request_body: dict) -> AsyncIterator[str]:
    """构建 SSE 流式 Chat Completion 响应。yield 每一行 SSE data"""
    completion_id = _gen_completion_id()
    created = int(time.time())
    req_model = request_body.get("model", "gpt-4o")
    resp_model = req_model if route["model_mode"] == "follow_request" else (route.get("custom_model") or req_model)
    finish_reason = route.get("finish_reason", "stop")
    chunk_delay = route.get("sse_chunk_delay_ms", 50) / 1000.0
    chunk_size = route.get("sse_chunk_size") or 1

    include_usage = False
    stream_opts = request_body.get("stream_options")
    if isinstance(stream_opts, dict):
        include_usage = stream_opts.get("include_usage", False)

    def _chunk(delta: dict, fr: str | None = None, usage: dict | None = None, choices_empty: bool = False) -> str:
        choices = [] if choices_empty else [{"index": 0, "delta": delta, "logprobs": None, "finish_reason": fr}]
        obj = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": resp_model,
            "system_fingerprint": "fp_mock_v1",
            "choices": choices,
        }
        if usage is not None:
            obj["usage"] = usage
        else:
            obj["usage"] = None
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    response_type = route.get("response_type", "text")

    if response_type == "tool_calls":
        # 流式 Tool Calls
        yield _chunk({"role": "assistant", "content": None, "tool_calls": None}, None)
        tool_calls_cfg = route.get("tool_calls") or []
        for idx, tc in enumerate(tool_calls_cfg):
            call_id = _gen_call_id()
            name = tc.get("name", "unknown")
            args = tc.get("arguments", "{}")
            # 第一个 chunk: id + name + type
            yield _chunk({"tool_calls": [{"index": idx, "id": call_id, "type": "function", "function": {"name": name, "arguments": ""}}]}, None)
            await asyncio.sleep(chunk_delay)
            # arguments 分块
            chunk_size = max(5, len(args) // 4)
            for i in range(0, len(args), chunk_size):
                frag = args[i:i + chunk_size]
                yield _chunk({"tool_calls": [{"index": idx, "function": {"arguments": frag}}]}, None)
                await asyncio.sleep(chunk_delay)
        # finish
        yield _chunk({}, finish_reason)
    elif response_type == "refusal":
        content_text = _resolve_body(route, request_body)
        yield _chunk({"role": "assistant", "refusal": ""}, None)
        for piece in _split_chunks(content_text, chunk_size):
            yield _chunk({"refusal": piece}, None)
            await asyncio.sleep(chunk_delay)
        yield _chunk({}, finish_reason)
    else:
        content_text = _resolve_body(route, request_body)
        # 第一个 chunk: role
        yield _chunk({"role": "assistant", "content": ""}, None)
        # 正文按 sse_chunk_size 切块 —— 空正文就一块都不发（"零内容的流"）
        for piece in _split_chunks(content_text, chunk_size):
            yield _chunk({"content": piece}, None)
            await asyncio.sleep(chunk_delay)
        # finish_reason chunk
        yield _chunk({}, finish_reason)

    # usage chunk
    if include_usage:
        prompt_text = json.dumps(request_body.get("messages", []))
        if route["token_mode"] == "custom":
            pt = route.get("custom_prompt_tokens") or 0
            ct = route.get("custom_completion_tokens") or 0
        else:
            ct_text = route["response_body"] if response_type == "text" else ""
            pt = estimate_tokens(prompt_text)
            ct = estimate_tokens(ct_text)
        usage_obj = {
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": pt + ct,
        }
        yield _chunk({}, None, usage=usage_obj, choices_empty=True)

    yield "data: [DONE]\n\n"


# ───── 另两种协议形状（legacy completions / Anthropic messages） ─────
# 只有智能应答按路径判出来时才会走这里，普通路由一律还是 chat.completion —— 零影响。
# 为什么要有：客户端 SDK 认形状不认内容，形状不对时报的错跟网关自己的 bug 长得一样，
# 排查时分不开。

def _resp_model(route: dict, request_body: dict, default: str) -> str:
    req_model = request_body.get("model", default)
    return req_model if route["model_mode"] == "follow_request" else (route.get("custom_model") or req_model)


def _prompt_source(request_body: dict):
    """auto 估算用的输入取样 —— 五种协议的入参字段不一样，取到哪个用哪个。"""
    if not isinstance(request_body, dict):
        return ""
    for k in ("messages", "prompt", "contents", "input"):
        v = request_body.get(k)
        if v:
            return v
    return ""


def _usage_pair(route: dict, request_body: dict, out_text: str) -> tuple[int, int]:
    if route["token_mode"] == "custom":
        return (route.get("custom_prompt_tokens") or 0, route.get("custom_completion_tokens") or 0)
    prompt_text = json.dumps(_prompt_source(request_body), ensure_ascii=False)
    return (estimate_tokens(prompt_text), estimate_tokens(out_text))


def build_text_completion_json(route: dict, request_body: dict) -> tuple[dict, dict]:
    """legacy /v1/completions —— object=text_completion，choice 用 `text` 而不是 message/delta。"""
    completion_id = _gen_completion_id().replace("chatcmpl-", "cmpl-")
    content = _resolve_body(route, request_body)
    pt, ct = _usage_pair(route, request_body, content)
    body = {
        "id": completion_id,
        "object": "text_completion",
        "created": int(time.time()),
        "model": _resp_model(route, request_body, "gpt-3.5-turbo-instruct"),
        "choices": [{"index": 0, "text": content, "logprobs": None,
                     "finish_reason": route.get("finish_reason", "stop")}],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct},
    }
    return body, _build_headers(route, completion_id)


async def build_text_completion_stream(route: dict, request_body: dict) -> AsyncIterator[str]:
    completion_id = _gen_completion_id().replace("chatcmpl-", "cmpl-")
    created = int(time.time())
    model = _resp_model(route, request_body, "gpt-3.5-turbo-instruct")
    chunk_delay = route.get("sse_chunk_delay_ms", 50) / 1000.0
    content = _resolve_body(route, request_body)

    def _frame(text: str, fr: str | None) -> str:
        obj = {"id": completion_id, "object": "text_completion", "created": created, "model": model,
               "choices": [{"index": 0, "text": text, "logprobs": None, "finish_reason": fr}]}
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    for piece in _split_chunks(content, route.get("sse_chunk_size") or 1):
        yield _frame(piece, None)
        await asyncio.sleep(chunk_delay)
    yield _frame("", route.get("finish_reason", "stop"))
    yield "data: [DONE]\n\n"


# OpenAI 的 finish_reason → Anthropic 的 stop_reason
_ANTHROPIC_STOP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "refusal",
}


def build_anthropic_message_json(route: dict, request_body: dict) -> tuple[dict, dict]:
    """Anthropic /v1/messages —— content 是 block 数组，停止原因叫 stop_reason。"""
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    response_type = route.get("response_type", "text")
    content_blocks: list[dict] = []
    out_text = ""

    if response_type == "tool_calls":
        for tc in route.get("tool_calls") or []:
            try:
                args = json.loads(tc.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {"_raw": tc.get("arguments")}
            content_blocks.append({"type": "tool_use", "id": _gen_call_id().replace("call_", "toolu_"),
                                   "name": tc.get("name", "unknown"), "input": args})
        out_text = json.dumps(content_blocks, ensure_ascii=False)
    else:
        out_text = _resolve_body(route, request_body)
        # 空正文对应零内容响应：content 是空数组，不是一个空 text block
        if out_text:
            content_blocks.append({"type": "text", "text": out_text})

    pt, ct = _usage_pair(route, request_body, out_text)
    body = {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": _resp_model(route, request_body, "claude-sonnet-5"),
        "content": content_blocks,
        "stop_reason": _ANTHROPIC_STOP.get(route.get("finish_reason", "stop"), "end_turn"),
        "stop_sequence": None,
        "usage": {"input_tokens": pt, "output_tokens": ct},
    }
    return body, _build_headers(route, msg_id)


async def build_anthropic_stream(route: dict, request_body: dict) -> AsyncIterator[str]:
    """Anthropic 事件流：message_start → content_block_* → message_delta → message_stop。

    每帧都要带 `event:` 行 —— Anthropic SDK 是按事件名分派的，只发 data 它认不出来。
    """
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    model = _resp_model(route, request_body, "claude-sonnet-5")
    chunk_delay = route.get("sse_chunk_delay_ms", 50) / 1000.0
    content = _resolve_body(route, request_body)
    pt, ct = _usage_pair(route, request_body, content)

    def _ev(name: str, payload: dict) -> str:
        return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    yield _ev("message_start", {"type": "message_start", "message": {
        "id": msg_id, "type": "message", "role": "assistant", "model": model,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": pt, "output_tokens": 0}}})
    yield _ev("content_block_start", {"type": "content_block_start", "index": 0,
                                      "content_block": {"type": "text", "text": ""}})
    for piece in _split_chunks(content, route.get("sse_chunk_size") or 1):
        yield _ev("content_block_delta", {"type": "content_block_delta", "index": 0,
                                          "delta": {"type": "text_delta", "text": piece}})
        await asyncio.sleep(chunk_delay)
    yield _ev("content_block_stop", {"type": "content_block_stop", "index": 0})
    yield _ev("message_delta", {"type": "message_delta", "delta": {
        "stop_reason": _ANTHROPIC_STOP.get(route.get("finish_reason", "stop"), "end_turn"),
        "stop_sequence": None}, "usage": {"output_tokens": ct}})
    yield _ev("message_stop", {"type": "message_stop"})


# ───── Gemini generateContent ─────
# 回复在 candidates[].content.parts[]，用量叫 usageMetadata，停止原因大写。

_GEMINI_FINISH = {"stop": "STOP", "length": "MAX_TOKENS", "content_filter": "SAFETY", "tool_calls": "STOP"}
_GEMINI_STATUS = {400: "INVALID_ARGUMENT", 401: "UNAUTHENTICATED", 403: "PERMISSION_DENIED",
                  404: "NOT_FOUND", 408: "DEADLINE_EXCEEDED", 429: "RESOURCE_EXHAUSTED"}


def _gemini_usage(pt: int, ct: int) -> dict:
    return {"promptTokenCount": pt, "candidatesTokenCount": ct, "totalTokenCount": pt + ct}


def _gemini_error(route: dict, request_body: dict, status_code: int) -> dict:
    body_text = _resolve_body(route, request_body)
    try:
        parsed = json.loads(body_text)
        if isinstance(parsed, dict) and "error" in parsed:
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return {"error": {"code": status_code, "message": body_text,
                      "status": _GEMINI_STATUS.get(status_code, "INTERNAL")}}


def build_gemini_json(route: dict, request_body: dict) -> tuple[dict, dict]:
    """Gemini generateContent 非流式。"""
    status_code = route["status_code"]
    model = _resp_model(route, request_body, "gemini-1.5-pro")
    if status_code >= 400:
        return _gemini_error(route, request_body, status_code), _build_headers(route, "gemini")

    response_type = route.get("response_type", "text")
    parts: list[dict] = []
    out_text = ""
    if response_type == "tool_calls":
        for tc in route.get("tool_calls") or []:
            try:
                args = json.loads(tc.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {"_raw": tc.get("arguments")}
            parts.append({"functionCall": {"name": tc.get("name", "unknown"), "args": args}})
        out_text = json.dumps(parts, ensure_ascii=False)
    else:
        out_text = _resolve_body(route, request_body)
        if out_text:
            parts.append({"text": out_text})

    pt, ct = _usage_pair(route, request_body, out_text)
    body = {
        "candidates": [{
            "content": {"role": "model", "parts": parts},
            "finishReason": _GEMINI_FINISH.get(route.get("finish_reason", "stop"), "STOP"),
            "index": 0,
            "safetyRatings": [],
        }],
        "usageMetadata": _gemini_usage(pt, ct),
        "modelVersion": model,
    }
    return body, _build_headers(route, "gemini")


async def build_gemini_stream(route: dict, request_body: dict) -> AsyncIterator[str]:
    """Gemini streamGenerateContent（alt=sse）：每帧是一个完整 GenerateContentResponse，
    `data: ` 前缀、无 event 行、无 [DONE]，用量只在最后一帧带。"""
    model = _resp_model(route, request_body, "gemini-1.5-pro")
    chunk_delay = route.get("sse_chunk_delay_ms", 50) / 1000.0
    content = _resolve_body(route, request_body)
    pt, ct = _usage_pair(route, request_body, content)
    fr = _GEMINI_FINISH.get(route.get("finish_reason", "stop"), "STOP")

    def _frame(parts: list[dict], finish: str | None, usage: dict | None) -> str:
        cand: dict = {"content": {"role": "model", "parts": parts}, "index": 0}
        if finish:
            cand["finishReason"] = finish
        obj: dict = {"candidates": [cand], "modelVersion": model}
        if usage is not None:
            obj["usageMetadata"] = usage
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    pieces = _split_chunks(content, route.get("sse_chunk_size") or 1)
    if not pieces:
        # 零内容：只发一帧带 finishReason + usage
        yield _frame([], fr, _gemini_usage(pt, ct))
        return
    for i, piece in enumerate(pieces):
        last = i == len(pieces) - 1
        yield _frame([{"text": piece}], fr if last else None,
                     _gemini_usage(pt, ct) if last else None)
        await asyncio.sleep(chunk_delay)


# ───── Ollama 原生 API ─────
# /api/chat → message 对象；/api/generate → response 字段。
# 流式是 NDJSON（一行一个 JSON、无 data: 前缀、无 [DONE]），最后一行 done=true 带用量。

_OLLAMA_DONE_REASON = {"stop": "stop", "length": "length", "content_filter": "stop", "tool_calls": "stop"}


def _ollama_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime())


def _ollama_counts(pt: int, ct: int) -> dict:
    return {"total_duration": 1_000_000, "load_duration": 0,
            "prompt_eval_count": pt, "prompt_eval_duration": 0,
            "eval_count": ct, "eval_duration": 0}


def _is_ollama_generate(path: str) -> bool:
    return path.split("?", 1)[0].rstrip("/").lower().endswith("/api/generate")


def build_ollama_json(route: dict, request_body: dict, path: str) -> tuple[dict, dict]:
    """Ollama 原生非流式。"""
    status_code = route["status_code"]
    if status_code >= 400:
        body_text = _resolve_body(route, request_body)
        try:
            parsed = json.loads(body_text)
            if isinstance(parsed, dict) and "error" in parsed:
                return parsed, _build_headers(route, "ollama")
        except (json.JSONDecodeError, TypeError):
            pass
        return {"error": body_text}, _build_headers(route, "ollama")

    is_generate = _is_ollama_generate(path)
    model = _resp_model(route, request_body, "llama3")
    content = _resolve_body(route, request_body)
    pt, ct = _usage_pair(route, request_body, content)
    done_reason = _OLLAMA_DONE_REASON.get(route.get("finish_reason", "stop"), "stop")
    base = {"model": model, "created_at": _ollama_now()}
    if is_generate:
        body = {**base, "response": content, "done": True, "done_reason": done_reason, **_ollama_counts(pt, ct)}
    else:
        message: dict = {"role": "assistant", "content": content}
        if route.get("response_type") == "tool_calls":
            calls = []
            for tc in route.get("tool_calls") or []:
                try:
                    args = json.loads(tc.get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {"_raw": tc.get("arguments")}
                calls.append({"function": {"name": tc.get("name", "unknown"), "arguments": args}})
            if calls:
                message["tool_calls"] = calls
                message["content"] = ""
        body = {**base, "message": message, "done": True, "done_reason": done_reason, **_ollama_counts(pt, ct)}
    return body, _build_headers(route, "ollama")


async def build_ollama_stream(route: dict, request_body: dict, path: str) -> AsyncIterator[str]:
    """Ollama 流式：NDJSON。"""
    is_generate = _is_ollama_generate(path)
    model = _resp_model(route, request_body, "llama3")
    chunk_delay = route.get("sse_chunk_delay_ms", 50) / 1000.0
    content = _resolve_body(route, request_body)
    pt, ct = _usage_pair(route, request_body, content)
    done_reason = _OLLAMA_DONE_REASON.get(route.get("finish_reason", "stop"), "stop")

    def _line(obj: dict) -> str:
        return json.dumps(obj, ensure_ascii=False) + "\n"

    def _piece_frame(piece: str) -> dict:
        base = {"model": model, "created_at": _ollama_now()}
        if is_generate:
            return {**base, "response": piece, "done": False}
        return {**base, "message": {"role": "assistant", "content": piece}, "done": False}

    for piece in _split_chunks(content, route.get("sse_chunk_size") or 1):
        yield _line(_piece_frame(piece))
        await asyncio.sleep(chunk_delay)
    tail = {"model": model, "created_at": _ollama_now(), "done": True, "done_reason": done_reason,
            **_ollama_counts(pt, ct)}
    if is_generate:
        tail["response"] = ""
    else:
        tail["message"] = {"role": "assistant", "content": ""}
    yield _line(tail)


# ───── OpenAI Responses API ─────
# object=response，产出在 output[]，用量叫 input_tokens/output_tokens；流式是命名事件。

def _responses_status(finish_reason: str) -> tuple[str, dict | None]:
    if finish_reason == "length":
        return "incomplete", {"reason": "max_output_tokens"}
    if finish_reason == "content_filter":
        return "incomplete", {"reason": "content_filter"}
    return "completed", None


def build_responses_json(route: dict, request_body: dict) -> tuple[dict, dict]:
    """OpenAI Responses 非流式。"""
    resp_id = f"resp_{uuid.uuid4().hex[:24]}"
    status_code = route["status_code"]
    model = _resp_model(route, request_body, "gpt-4o")
    if status_code >= 400:
        body_text = _resolve_body(route, request_body)
        try:
            body = json.loads(body_text)
        except (json.JSONDecodeError, TypeError):
            err_type, err_code = _error_meta(status_code)
            body = {"error": {"message": body_text, "type": err_type, "param": None, "code": err_code}}
        return body, _build_headers(route, resp_id)

    response_type = route.get("response_type", "text")
    output: list[dict] = []
    out_text = ""
    if response_type == "tool_calls":
        for tc in route.get("tool_calls") or []:
            output.append({
                "type": "function_call",
                "id": f"fc_{uuid.uuid4().hex[:24]}",
                "call_id": _gen_call_id(),
                "name": tc.get("name", "unknown"),
                "arguments": tc.get("arguments", "{}"),
                "status": "completed",
            })
        out_text = json.dumps([o.get("arguments") for o in output], ensure_ascii=False)
    else:
        out_text = _resolve_body(route, request_body)
        content_blocks = []
        if out_text:
            content_blocks.append({"type": "output_text", "text": out_text, "annotations": []})
        output.append({
            "type": "message",
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "status": "completed",
            "role": "assistant",
            "content": content_blocks,
        })

    pt, ct = _usage_pair(route, request_body, out_text)
    status, incomplete = _responses_status(route.get("finish_reason", "stop"))
    body = {
        "id": resp_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": model,
        "output": output,
        "usage": {"input_tokens": pt, "output_tokens": ct, "total_tokens": pt + ct},
        "incomplete_details": incomplete,
        "error": None,
    }
    return body, _build_headers(route, resp_id)


async def build_responses_stream(route: dict, request_body: dict) -> AsyncIterator[str]:
    """OpenAI Responses 事件流：命名事件（response.created / response.output_text.delta /
    response.completed），每帧带 event 行 —— SDK 按事件名分派。"""
    resp_id = f"resp_{uuid.uuid4().hex[:24]}"
    model = _resp_model(route, request_body, "gpt-4o")
    chunk_delay = route.get("sse_chunk_delay_ms", 50) / 1000.0
    content = _resolve_body(route, request_body)
    pt, ct = _usage_pair(route, request_body, content)
    status, incomplete = _responses_status(route.get("finish_reason", "stop"))
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"

    def _ev(name: str, payload: dict) -> str:
        payload = {"type": name, **payload}
        return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def _response_obj(st: str, output: list[dict]) -> dict:
        done = st not in ("in_progress",)
        return {"id": resp_id, "object": "response", "created_at": int(time.time()),
                "status": st, "model": model, "output": output,
                "usage": ({"input_tokens": pt, "output_tokens": ct, "total_tokens": pt + ct} if done else None),
                "incomplete_details": (incomplete if done else None), "error": None}

    yield _ev("response.created", {"response": _response_obj("in_progress", [])})
    yield _ev("response.output_item.added", {"output_index": 0, "item": {
        "type": "message", "id": msg_id, "status": "in_progress", "role": "assistant", "content": []}})
    yield _ev("response.content_part.added", {"item_id": msg_id, "output_index": 0, "content_index": 0,
                                              "part": {"type": "output_text", "text": "", "annotations": []}})
    for piece in _split_chunks(content, route.get("sse_chunk_size") or 1):
        yield _ev("response.output_text.delta", {"item_id": msg_id, "output_index": 0,
                                                 "content_index": 0, "delta": piece})
        await asyncio.sleep(chunk_delay)
    yield _ev("response.output_text.done", {"item_id": msg_id, "output_index": 0,
                                            "content_index": 0, "text": content})
    final_item = {"type": "message", "id": msg_id, "status": "completed", "role": "assistant",
                  "content": ([{"type": "output_text", "text": content, "annotations": []}] if content else [])}
    yield _ev("response.output_item.done", {"output_index": 0, "item": final_item})
    final_event = "response.completed" if status == "completed" else "response.incomplete"
    yield _ev(final_event, {"response": _response_obj(status, [final_item])})


# ───── 向量 (Embeddings) ─────

# 各家 embedding 模型的原生维度 —— 请求没带 dimensions 时按模型名推断
EMBEDDING_MODEL_DIMS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "text-embedding-v3": 1024,
    "text-embedding-v2": 1536,
    "text-embedding-v1": 1536,
    "embedding-3": 2048,
    "embedding-2": 1024,
    "bge-m3": 1024,
    "bge-large-zh-v1.5": 1024,
}
DEFAULT_EMBEDDING_DIM = 1536
MAX_EMBEDDING_DIM = 4096
# 密集分量的权重 —— 只为了让向量稠密好看，太大会稀释相似度
_DENSE_WEIGHT = 0.3

_CJK_START, _CJK_END = "一", "鿿"
_TOKEN_RE = re.compile(rf"[a-z0-9]+|[{_CJK_START}-{_CJK_END}]")


# ───── 路径通配匹配 ─────
# Azure OpenAI 把「部署名」塞在路径里（/openai/deployments/gpt-4o-mini/chat/completions），
# 一个部署配一条路由不现实，所以路由路径支持通配：
#   *  匹配一段里的任意字符（不跨 /）
#   ** 匹配任意层级（跨 /）
# 路由路径里写了 ?query 的话只按 ? 前面的部分匹配 —— 直接把 Azure 那种带 api-version 的整条 URL
# 粘进来也能命中，不然就是一个查不出原因的 404。

@lru_cache(maxsize=256)
def _compile_path_pattern(pattern: str) -> re.Pattern:
    body = pattern.split("?", 1)[0]
    out = []
    for part in re.split(r"(\*\*|\*)", body):
        if part == "**":
            out.append(".*")
        elif part == "*":
            out.append("[^/]*")
        elif part:
            out.append(re.escape(part))
    return re.compile("^" + "".join(out) + "$")


def has_wildcard(pattern: str) -> bool:
    return "*" in pattern.split("?", 1)[0]


def path_matches(pattern: str, path: str) -> bool:
    return _compile_path_pattern(pattern).fullmatch(path) is not None


def is_embeddings_route(route: dict, path: str) -> bool:
    """判断这次请求要不要按 embeddings 格式回。
    显式配置 response_type=embedding 优先；路径以 /embeddings 结尾也算（兼容用户手建的路由 / 前缀兜底路由）。
    """
    if route.get("response_type") == "embedding":
        return True
    return path.rstrip("/").endswith("/embeddings")


def collect_embedding_inputs(raw) -> list[str]:
    """把 OpenAI 的 input 字段拍平成文本列表。
    支持 str / list[str] / list[int](token ids) / list[list[int]]。
    """
    if raw is None:
        return [""]
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (int, float)):
        return [str(raw)]
    if isinstance(raw, list):
        if not raw:
            return [""]
        # list[int] —— 整条当成一个输入（token ids）
        if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in raw):
            return [",".join(str(x) for x in raw)]
        out = []
        for item in raw:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, list):
                out.append(",".join(str(x) for x in item))
            else:
                out.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
        return out or [""]
    return [json.dumps(raw, ensure_ascii=False, sort_keys=True)]


def resolve_embedding_dim(request_body: dict, model: str) -> int:
    """维度优先级：请求 dimensions > 模型名推断 > 默认 1536。"""
    requested = request_body.get("dimensions")
    if isinstance(requested, (int, float)) and not isinstance(requested, bool):
        dim = int(requested)
        if dim > 0:
            return min(dim, MAX_EMBEDDING_DIM)
    return EMBEDDING_MODEL_DIMS.get(model, DEFAULT_EMBEDDING_DIM)


def _tokenize_for_embedding(text: str) -> list[str]:
    """英文按词、中文按单字 + 相邻双字 —— 让"相似文本"在向量上也相似。"""
    tokens = _TOKEN_RE.findall(text.lower())
    cjk = [t for t in tokens if len(t) == 1 and _CJK_START <= t <= _CJK_END]
    bigrams = [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]
    return tokens + bigrams


def _dense_component(text: str, dim: int) -> list[float]:
    """整段文本的密集分量 —— 让向量每一维都有值，看起来像真模型的输出，而不是一串 0。
    shake_256 是可变长摘要，一次就能铺满任意维度。
    """
    raw = hashlib.shake_256(text.encode("utf-8")).digest(dim * 2)
    ints = struct.unpack(f"<{dim}h", raw)
    norm = math.sqrt(sum(i * i for i in ints)) or 1.0
    return [i / norm for i in ints]


def semantic_vector(text: str, dim: int) -> list[float]:
    """确定性语义向量：同一段文本永远得到同一个向量，相似文本余弦相似度高、无关文本接近正交。
    这样网关的语义缓存既能测出"命中"，也能测出"未命中"——全返回同一个假向量的话任何两个 prompt 都会 100% 相似。
    用 hashlib 而不是内置 hash()，避免 PYTHONHASHSEED 让向量跨进程漂移。

    骨架是按 token 做特征哈希（决定相似度），再叠一层权重很小的全文密集分量（只负责让向量稠密，
    不同文本之间近似正交，不会把相似度搅乱）。
    """
    tokens = _tokenize_for_embedding(text)
    if not tokens:
        # 空输入也要给个稳定的非零向量，否则余弦相似度算不出来
        tokens = ["\x00empty"]

    sparse = [0.0] * dim
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        h = int.from_bytes(digest, "big")
        sparse[h % dim] += 1.0 if (h >> 63) & 1 else -1.0

    sparse_norm = math.sqrt(sum(v * v for v in sparse)) or 1.0
    dense = _dense_component(text, dim)
    vec = [s / sparse_norm + _DENSE_WEIGHT * d for s, d in zip(sparse, dense)]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


# ───── SIM 指令：让测试者精确钉住两个 prompt 的余弦相似度 ─────
# 为什么要它：上面那套按 token 哈希的相似度本质上是「数重合的字」——
#   · 同义换说法（「今天几号」/「今天是几号」）字面差一个字，相似度就掉到默认阈值 0.95 以下，
#     正路（该命中的缓存）在默认配置下压根测不出命中；
#   · 反义只差一个字（「删除用户」/「不要删除用户」）字面几乎一样，相似度反而很高，
#     于是把语义相反的答案当缓存命中给出去 —— 这是语义缓存最危险的错，字面相似度根本测不到。
# SIM 指令绕开字面：相似度只由 key + score 决定，跟句子长什么样无关。
#   在发给网关的 prompt 里写   SIM:<key>          → 该 key 的锚向量本身（自相似 1.0，同 key 无 score 必命中）
#                            SIM:<key>@0.96     → 与该 key 锚向量余弦恰为 0.96 的向量
# 于是：同义命中 = 两句都写 SIM:q1（1.0）；卡边界 = SIM:q1 对 SIM:q1@0.94（未命中）/@0.96（命中）；
#       反义危险 = SIM:del@0.10 强制拉低，哪怕字面几乎一样。
_SIM_RE = re.compile(r"SIM:([A-Za-z0-9_.\-]+)(?:@(1(?:\.0+)?|0?\.\d+|0|1))?")


def _unit_from_seed(seed: str, dim: int) -> list[float]:
    """由种子确定性生成一个单位向量（稠密、近似各向同性）。同一种子跨进程恒定。"""
    raw = hashlib.shake_256(seed.encode("utf-8")).digest(dim * 2)
    ints = struct.unpack(f"<{dim}h", raw)
    norm = math.sqrt(sum(i * i for i in ints)) or 1.0
    return [i / norm for i in ints]


def parse_sim_directive(text: str) -> tuple[str, float] | None:
    """从输入文本里解析 SIM 指令，返回 (key, score) 或 None。score 缺省 = 1.0。"""
    if not text:
        return None
    m = _SIM_RE.search(text)
    if not m:
        return None
    score = float(m.group(2)) if m.group(2) else 1.0
    return m.group(1), max(0.0, min(1.0, score))


def controlled_vector(key: str, score: float, dim: int) -> list[float]:
    """构造一个与「key 的锚向量」余弦相似度恰为 score 的向量。
    锚向量只由 key 决定，所以同 key、都不带 score 的两个输入必然 1.0 相似（缓存必命中）；
    带 score 的那个则被精确放到离锚 arccos(score) 的角度上。
    做法：b = score·a + sqrt(1-score²)·u，其中 u 是与 a 正交的确定性单位向量（Gram-Schmidt）。
    此时 a·b = score（a·a=1、a·u=0），且 |b|=1。
    """
    a = _unit_from_seed(f"sim-anchor:{key}", dim)
    if score >= 1.0:
        return [round(v, 6) for v in a]
    # 另取一个种子向量，减掉它在 a 上的分量，得到与 a 正交的单位向量
    r = _unit_from_seed(f"sim-ortho:{key}", dim)
    dot = sum(ai * ri for ai, ri in zip(a, r))
    u = [ri - dot * ai for ri, ai in zip(r, a)]
    un = math.sqrt(sum(x * x for x in u)) or 1.0
    u = [x / un for x in u]
    coeff = math.sqrt(max(0.0, 1.0 - score * score))
    b = [score * ai + coeff * ui for ai, ui in zip(a, u)]
    bn = math.sqrt(sum(x * x for x in b)) or 1.0
    return [round(x / bn, 6) for x in b]


def _random_vector(dim: int) -> list[float]:
    vec = [random.gauss(0, 1) for _ in range(dim)]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def _fixed_vector_from_body(response_body: str | None) -> list[float] | None:
    """响应内容里写了个纯数字 JSON 数组，就原样当向量用（逃生口，比如固定 [0.1, 0.2, 0.3]）。"""
    if not response_body or not response_body.strip().startswith("["):
        return None
    try:
        parsed = json.loads(response_body)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(parsed, list) and parsed and all(
        isinstance(x, (int, float)) and not isinstance(x, bool) for x in parsed
    ):
        return [float(x) for x in parsed]
    return None


def _encode_embedding(vec: list[float], encoding_format: str) -> list[float] | str:
    """openai-python 默认就是 base64 拿向量，这里必须支持，否则 SDK 侧解不开。"""
    if encoding_format == "base64":
        return base64.b64encode(struct.pack(f"<{len(vec)}f", *vec)).decode("ascii")
    return vec


def build_embeddings_response(route: dict, request_body: dict) -> tuple[dict, dict]:
    """构建 OpenAI 兼容的 Embeddings 响应。返回 (response_body, extra_headers)"""
    headers = _build_headers(route, "")

    status_code = route["status_code"]
    if status_code >= 400:
        body_text = _resolve_template(route.get("response_body") or "", request_body)
        try:
            body = json.loads(body_text)
        except (json.JSONDecodeError, TypeError):
            err_type, err_code = _error_meta(status_code)
            body = {"error": {"message": body_text, "type": err_type, "param": None, "code": err_code}}
        return body, headers

    req_model = request_body.get("model") or "text-embedding-3-small"
    resp_model = req_model if route["model_mode"] == "follow_request" else (route.get("custom_model") or req_model)

    inputs = collect_embedding_inputs(request_body.get("input"))
    fixed = _fixed_vector_from_body(route.get("response_body"))
    dim = len(fixed) if fixed else resolve_embedding_dim(request_body, resp_model)

    encoding_format = request_body.get("encoding_format")
    encoding_format = encoding_format if encoding_format in ("float", "base64") else "float"

    random_mode = route.get("response_mode") == "random"

    data = []
    for idx, text in enumerate(inputs):
        sim = None if fixed else parse_sim_directive(text)
        if sim:
            # 显式指令优先于路由级 random —— SIM 是这次请求里明写的意图，比路由开关更具体
            vec = controlled_vector(sim[0], sim[1], dim)
        elif fixed:
            vec = fixed
        elif random_mode:
            vec = _random_vector(dim)
        else:
            vec = semantic_vector(text, dim)
        data.append({
            "object": "embedding",
            "index": idx,
            "embedding": _encode_embedding(vec, encoding_format),
        })

    if route["token_mode"] == "custom":
        prompt_tokens = route.get("custom_prompt_tokens") or 0
    else:
        prompt_tokens = sum(estimate_tokens(t) for t in inputs)

    body = {
        "object": "list",
        "data": data,
        "model": resp_model,
        "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
    }
    return body, headers


def compact_embeddings_for_log(body: dict, keep: int = 8) -> dict:
    """给请求日志用的瘦身版：向量只留前几维。
    原样存的话 1536 个浮点数会把日志撑爆、还会被截断成解析不了的半截 JSON。
    """
    data = body.get("data")
    if not isinstance(data, list):
        return body
    slim = []
    for item in data:
        if not isinstance(item, dict):
            slim.append(item)
            continue
        vec = item.get("embedding")
        if isinstance(vec, list) and len(vec) > keep:
            item = {**item, "embedding": vec[:keep] + [f"...共 {len(vec)} 维"]}
        elif isinstance(vec, str) and len(vec) > 64:
            item = {**item, "embedding": f"{vec[:64]}... (base64, 共 {len(vec)} 字符)"}
        slim.append(item)
    return {**body, "data": slim}


def _build_headers(route: dict, request_id_or_completion_id: str) -> dict:
    headers = {
        "x-request-id": _gen_request_id(),
        "openai-processing-ms": str(route.get("delay_ms", 0)),
        "openai-version": "2024-06-01",
        "x-ratelimit-limit-requests": "10000",
        "x-ratelimit-limit-tokens": "2000000",
        "x-ratelimit-remaining-requests": "9999",
        "x-ratelimit-remaining-tokens": "1999500",
        "x-ratelimit-reset-requests": "6ms",
        "x-ratelimit-reset-tokens": "15ms",
    }
    custom = route.get("response_headers")
    if isinstance(custom, dict):
        headers.update(custom)
    return headers
