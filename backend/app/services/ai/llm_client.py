"""LLM 客户端 — 基于 httpx 的流式/非流式调用，支持 OpenAI 兼容和 Anthropic API

限流(429)处理，两层，缺一不可：
1. 退避重试：公司网关对 SDK 直连是瞬时限流(GW-2006/hoopa)，退避几秒基本就过。
2. 降级到 CLI 通道：重试仍 429 → 打 claude-proxy(:38210)，它把真 claude CLI 包成
   OpenAI 接口，配额按 CLI 客户端算、不受网关对 SDK 的限流影响（实测：同一时刻
   SDK 直连 429，CLI 路径 rc=0 正常返回）。
不要把这两层删掉换成"让调用方自己重试"——llm_structured 对 LLMError 是直接抛不重试的，
一个 429 会把整条场景建模/用例展开打死。

背景/实测数据/验证方法见 docs/ai-gateway-and-models.md。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 值得重试的状态码：429 限流 + 网关侧瞬时故障
_RETRY_STATUS = {429, 500, 502, 503, 504, 529}
_MAX_ATTEMPTS = 4          # 首发 + 3 次重试
_BACKOFF_BASE = 1.8
_BACKOFF_CAP = 30.0
# 降级通道专用超时：proxy 每次要冷启真 CLI（实测非流式 ~36s），大 prompt 更久，
# 沿用主路的 ai_timeout_seconds(默认120) 会把兜底也拖超时，等于没兜底。
# ⚠ 这是**下限不是定值**：调用方自己带了更长的 timeout，兜底这一跳得跟着放宽。
# 否则出现一种很难查的偏科 —— 主路给够了时间，一被限流降级就卡死在 600s。
# 而降级恰恰发生在网关最忙的时候，也就是**最慢的那些请求**上。
_PROXY_TIMEOUT = 600.0


# 新一代模型不再接受采样参数,发了直接 400(网关原话:"`temperature` is deprecated for this model.")。
# 实测:claude-sonnet-5 带 temperature → 400,去掉后 → 请求合法(仅可能被限流)。
# 覆盖 5 系(opus/sonnet/fable/mythos)与 opus-4-7/4-8;老模型(haiku-4-5、sonnet-4-6 等)仍接受。
_NO_SAMPLING_PARAMS = re.compile(
    r"claude-(?:opus|sonnet|fable|mythos)-5|claude-opus-4-(?:7|8)", re.I
)


def _supports_temperature(model: str | None) -> bool:
    return not _NO_SAMPLING_PARAMS.search(model or "")


def _has_proxy_channel(current_endpoint: str, provider: str) -> bool:
    """有没有可用的 CLI 降级通道(proxy 只讲 OpenAI 协议,anthropic provider 不适用)。"""
    if provider == "anthropic":
        return False
    fb = _proxy_endpoint()
    return bool(fb) and fb != current_endpoint


def _drop_rejected_param(body: dict, err_text: str) -> str | None:
    """网关报「某参数不支持」时,从请求体里摘掉它(就地改),返回被摘掉的参数名。

    _NO_SAMPLING_PARAMS 漏掉新模型时靠这里兜住,避免又一次全线 400。
    """
    for p in ("temperature", "top_p", "top_k"):
        if p in body and p in (err_text or ""):
            body.pop(p, None)
            return p
    return None


def _proxy_endpoint() -> str:
    """限流兜底通道的 chat/completions 地址（claude-proxy，真 CLI 包成 OpenAI 接口）。"""
    base = (settings.ai_proxy_base_url or settings.ai_ui_base_url or "").rstrip("/")
    return f"{base}/chat/completions" if base else ""


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    """优先尊重 Retry-After，否则指数退避 + 抖动（抖动防多任务同时重试再次撞限流）。"""
    ra = resp.headers.get("retry-after")
    if ra:
        try:
            return min(float(ra), _BACKOFF_CAP)
        except ValueError:
            pass
    return min(_BACKOFF_BASE ** attempt + random.uniform(0, 0.5), _BACKOFF_CAP)


@dataclass
class StreamChunk:
    delta: str = ""
    finish_reason: str | None = None


# 元数据的三项。`reported` 里有哪几项，就只有哪几项能拿去下结论。
_META_ALL = frozenset({"finish_reason", "prompt_tokens", "completion_tokens"})


@dataclass
class LLMResponse:
    content: str = ""
    finish_reason: str = "stop"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    # 这条通道**确实报了**哪几项元数据（见 `_meta_reported`）。
    # 空集 = 一项都不可信。默认空集是故意的：**没填就是不知道**，
    # 而上面那几个字段的默认值（"stop" / 0）长得跟"模型正常说完了、
    # 输入 0 个 token"一模一样 —— 谁拿默认值当事实，谁就会把"没测到"
    # 渲染成"测过了没问题"，那恰好是这个平台要治的病。
    reported: frozenset[str] = frozenset()


def _meta_reported(usage: dict, *, in_key: str, prompt_chars: int) -> frozenset[str]:
    """通道报的元数据里，哪几项算数。

    判据**不是"键在不在"**。claude-proxy 那条 CLI 降级通道 `usage` 三项恒 0、
    `finish_reason` 恒 `"stop"`，键全在、值全是编的（2026-08-28 实测 12/12 次调用，
    连 `max_tokens=64` 都不理会，照样返回 1891 字符）。

    单看一次响应分不出「模型正好写了 0 个 token」和「通道压根不数」——
    但 `prompt_tokens == 0` 而 prompt 非空是**可证伪的假值**：输入就摆在那儿，
    不可能 0 个 token。这一项一假，同一条通道同一个响应里报的另外两项一起不算数。
    """
    if prompt_chars > 0 and int(usage.get(in_key) or 0) <= 0:
        return frozenset()
    return _META_ALL


class LLMError(Exception):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


def _build_headers(*, config=None) -> dict[str, str]:
    headers: dict[str, str] = {
        "User-Agent": "claude-cli/1.0",
    }
    auth_token = config.auth_token if config else settings.ai_auth_token
    api_key = config.api_key if config else settings.ai_api_key
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _build_openai_body(
    messages: list[dict],
    *,
    stream: bool = False,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    config=None,
) -> dict:
    mdl = model or (config.model if config else settings.ai_model)
    body = {
        "model": mdl,
        "messages": messages,
        "max_tokens": max_tokens or (config.max_tokens if config else settings.ai_max_tokens),
        "stream": stream,
    }
    if _supports_temperature(mdl):
        body["temperature"] = (
            temperature if temperature is not None
            else (config.temperature if config else settings.ai_temperature)
        )
    return body


def _build_anthropic_body(
    messages: list[dict],
    *,
    stream: bool = False,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    config=None,
) -> dict:
    system_parts = []
    chat_messages = []
    for m in messages:
        if m["role"] == "system":
            system_parts.append(m["content"])
        else:
            chat_messages.append({"role": m["role"], "content": m["content"]})

    mdl = model or (config.model if config else settings.ai_model)
    body: dict = {
        "model": mdl,
        "messages": chat_messages,
        "max_tokens": max_tokens or (config.max_tokens if config else settings.ai_max_tokens),
        "stream": stream,
    }
    if _supports_temperature(mdl):
        body["temperature"] = (
            temperature if temperature is not None
            else (config.temperature if config else settings.ai_temperature)
        )
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    return body


def _get_endpoint(*, config=None) -> str:
    provider = config.provider if config else settings.ai_provider
    base_url = config.base_url if config else settings.ai_base_url
    base = base_url.rstrip("/")
    if provider == "anthropic":
        return f"{base}/messages" if base else "https://api.anthropic.com/v1/messages"
    return f"{base}/chat/completions"


def _get_extra_headers(*, config=None) -> dict[str, str]:
    provider = config.provider if config else settings.ai_provider
    if provider == "anthropic":
        h = {"anthropic-version": "2023-06-01", "content-type": "application/json"}
        api_key = config.api_key if config else settings.ai_api_key
        if api_key:
            h["x-api-key"] = api_key
        return h
    return {"content-type": "application/json"}


def _proxy_timeout(requested: float | None = None) -> float:
    """降级那一跳等多久：`_PROXY_TIMEOUT` 打底，调用方要得更长就听它的。

    别写成"降级就用 600" —— 那样**提高 max_tokens 的人只改对了一半**：
    主路放宽了、兜底没有，于是长请求在正常时段跑得通、一限流就整批挂掉。
    """
    if isinstance(requested, (int, float)) and not isinstance(requested, bool):
        return max(_PROXY_TIMEOUT, float(requested))
    return _PROXY_TIMEOUT


def _get_timeout(*, config=None, override: int | None = None) -> int:
    """这次调用等多久。`override` 是**调用方按自己这一次的形状**要的秒数。

    默认那条路（服务配置里的 `timeout_seconds`，现值 120）是**全平台共用**的：
    改大它，用例生成、评审、体检全都跟着变成十几分钟才超时 —— 一个卡死的请求
    从"两分钟后报错"变成"十七分钟没反应"。所以**一次调用要更长，就自己带过来**，
    别去把公共的那个数拧大。
    """
    # bool 是 int 的子类：`override=True` 会一路走到 httpx，变成**1 秒**超时 ——
    # 比不传更坏，因为它看起来像"传了"。认不出来的一律当没传。
    if isinstance(override, int) and not isinstance(override, bool) and override > 0:
        return override
    return config.timeout_seconds if config else settings.ai_timeout_seconds


def _get_provider(*, config=None) -> str:
    return config.provider if config else settings.ai_provider


async def _post_with_retry(
    client: httpx.AsyncClient,
    endpoint: str,
    body: dict,
    headers: dict,
    *,
    provider: str,
    timeout: float | None = None,
) -> httpx.Response:
    """POST + 限流退避重试 + 429 耗尽后降级到 claude-proxy CLI 通道。返回 200 响应或抛 LLMError。

    `timeout` 是调用方为**这一次**要的秒数（主路已经设在 client 上了，这里只用来
    决定降级那一跳给多久）—— 见 `_proxy_timeout()`。
    """
    last: httpx.Response | None = None
    for attempt in range(_MAX_ATTEMPTS):
        resp = await client.post(endpoint, json=body, headers=headers)
        if resp.status_code == 200:
            return resp
        last = resp
        # 429 且有 CLI 通道可用 → 立刻降级,不做长退避。
        # 网关对非 Haiku 模型是**持续**限流(不是瞬时):实测退避 3×30s 后仍 429,总耗时 ~98s
        # 才靠降级成功;而 proxy 配额独立、几秒就回。干等纯属浪费。
        if resp.status_code == 429 and _has_proxy_channel(endpoint, provider):
            break
        if resp.status_code not in _RETRY_STATUS or attempt == _MAX_ATTEMPTS - 1:
            break
        delay = _retry_delay(resp, attempt)
        logger.warning(
            "LLM %s，%.1fs 后重试 (%d/%d) %s", resp.status_code, delay,
            attempt + 1, _MAX_ATTEMPTS - 1, endpoint,
        )
        await asyncio.sleep(delay)

    # 400 且是「参数不被该模型接受」→ 摘掉参数重试一次(正则漏网时的兜底)
    if last is not None and last.status_code == 400:
        dropped = _drop_rejected_param(body, last.text)
        if dropped:
            logger.warning("模型 %s 不接受 %s，去掉后重试", body.get("model"), dropped)
            resp = await client.post(endpoint, json=body, headers=headers)
            if resp.status_code == 200:
                return resp
            last = resp

    # 429 重试耗尽 → 走 CLI 通道（proxy 只讲 OpenAI 协议，anthropic provider 不降级）
    if last is not None and last.status_code == 429:
        fb = _proxy_endpoint()
        if _has_proxy_channel(endpoint, provider):
            logger.warning("网关限流，降级到 CLI 通道: %s", fb)
            resp = await client.post(
                fb, json=body, headers=headers, timeout=_proxy_timeout(timeout))
            if resp.status_code == 200:
                return resp
            last = resp

    status = last.status_code if last is not None else 0
    text = last.text[:500] if last is not None else ""
    raise LLMError(f"LLM API error: {status} {text}", status)


async def complete(
    messages: list[dict],
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    config=None,
    timeout: int | None = None,
) -> LLMResponse:
    provider = _get_provider(config=config)
    if provider == "anthropic":
        body = _build_anthropic_body(messages, model=model, max_tokens=max_tokens, temperature=temperature, config=config)
    else:
        body = _build_openai_body(messages, model=model, max_tokens=max_tokens, temperature=temperature, config=config)

    headers = {**_build_headers(config=config), **_get_extra_headers(config=config)}
    endpoint = _get_endpoint(config=config)
    prompt_chars = sum(len(m.get("content") or "") for m in messages)

    resolved_timeout = _get_timeout(config=config, override=timeout)
    async with httpx.AsyncClient(timeout=resolved_timeout) as client:
        resp = await _post_with_retry(
            client, endpoint, body, headers,
            provider=provider, timeout=resolved_timeout)
        data = resp.json()

    if provider == "anthropic":
        content = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")
        usage = data.get("usage") or {}
        return LLMResponse(
            content=content,
            finish_reason=data.get("stop_reason", "end_turn"),
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            model=data.get("model", ""),
            reported=_meta_reported(usage, in_key="input_tokens", prompt_chars=prompt_chars),
        )

    choice = data.get("choices", [{}])[0]
    usage = data.get("usage") or {}
    return LLMResponse(
        content=choice.get("message", {}).get("content", ""),
        finish_reason=choice.get("finish_reason", "stop"),
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        model=data.get("model", ""),
        reported=_meta_reported(usage, in_key="prompt_tokens", prompt_chars=prompt_chars),
    )


async def stream(
    messages: list[dict],
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    config=None,
) -> AsyncIterator[StreamChunk]:
    provider = _get_provider(config=config)
    if provider == "anthropic":
        body = _build_anthropic_body(messages, stream=True, model=model, max_tokens=max_tokens, temperature=temperature, config=config)
    else:
        body = _build_openai_body(messages, stream=True, model=model, max_tokens=max_tokens, temperature=temperature, config=config)

    headers = {**_build_headers(config=config), **_get_extra_headers(config=config)}
    endpoint = _get_endpoint(config=config)

    async with httpx.AsyncClient(timeout=_get_timeout(config=config)) as client:
        target = endpoint
        attempt = 0
        proxy_tried = False
        while True:
            status, error_body, retry_delay = 0, "", None
            # 降级到 proxy 后放宽超时（真 CLI 冷启慢），主路仍用配置值
            req_kwargs = {"timeout": _PROXY_TIMEOUT} if target != endpoint else {}
            async with client.stream("POST", target, json=body, headers=headers, **req_kwargs) as resp:
                if resp.status_code == 200:
                    async for chunk in _iter_sse(resp, provider=provider):
                        yield chunk
                    return
                # 首字节前失败才重试；已开始吐 delta 就不能重试（会重复输出）
                status = resp.status_code
                error_body = (await resp.aread()).decode("utf-8", errors="replace")[:500]
                skip_backoff = status == 429 and _has_proxy_channel(target, provider)
                if status in _RETRY_STATUS and attempt < _MAX_ATTEMPTS - 1 and not skip_backoff:
                    retry_delay = _retry_delay(resp, attempt)

            if retry_delay is not None:
                attempt += 1
                logger.warning(
                    "LLM 流式 %s，%.1fs 后重试 (%d/%d) %s",
                    status, retry_delay, attempt, _MAX_ATTEMPTS - 1, target,
                )
                await asyncio.sleep(retry_delay)
                continue

            # 400 参数不被接受 → 摘掉重试(同上,流式也要有)
            if status == 400 and _drop_rejected_param(body, error_body):
                logger.warning("模型 %s 不接受该采样参数，去掉后重试流式", body.get("model"))
                attempt = 0
                continue

            if status == 429 and not proxy_tried:
                fb = _proxy_endpoint()
                if _has_proxy_channel(target, provider):
                    proxy_tried, target, attempt = True, fb, 0
                    logger.warning("网关持续限流，流式降级到 CLI 通道: %s", fb)
                    continue

            raise LLMError(f"LLM API error: {status} {error_body}", status)


async def _iter_sse(resp: httpx.Response, *, provider: str) -> AsyncIterator[StreamChunk]:
    """把 SSE 字节流切成 StreamChunk（手动分行，兼容大 chunk）。"""
    buffer = ""
    async for raw_bytes in resp.aiter_bytes():
        buffer += raw_bytes.decode("utf-8", errors="replace")
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line or line.startswith(":"):
                continue
            if line == "data: [DONE]":
                return
            if not line.startswith("data: "):
                continue
            payload = line[6:]

            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                logger.warning("Failed to parse SSE chunk: %s", payload[:200])
                continue

            chunk = _parse_stream_chunk(data, provider=provider)
            if chunk.delta or chunk.finish_reason:
                yield chunk


def _parse_stream_chunk(data: dict, *, provider: str | None = None) -> StreamChunk:
    p = provider or settings.ai_provider
    if p == "anthropic":
        event_type = data.get("type", "")
        if event_type == "content_block_delta":
            delta = data.get("delta", {})
            return StreamChunk(delta=delta.get("text", ""))
        if event_type == "message_delta":
            return StreamChunk(finish_reason=data.get("delta", {}).get("stop_reason"))
        return StreamChunk()

    choices = data.get("choices", [])
    if not choices:
        return StreamChunk()
    choice = choices[0]
    delta = choice.get("delta", {})
    return StreamChunk(
        delta=delta.get("content", "") or "",
        finish_reason=choice.get("finish_reason"),
    )
