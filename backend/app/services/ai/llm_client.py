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
_PROXY_TIMEOUT = 600.0


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


@dataclass
class LLMResponse:
    content: str = ""
    finish_reason: str = "stop"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""


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
    return {
        "model": model or (config.model if config else settings.ai_model),
        "messages": messages,
        "max_tokens": max_tokens or (config.max_tokens if config else settings.ai_max_tokens),
        "temperature": temperature if temperature is not None else (config.temperature if config else settings.ai_temperature),
        "stream": stream,
    }


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

    body: dict = {
        "model": model or (config.model if config else settings.ai_model),
        "messages": chat_messages,
        "max_tokens": max_tokens or (config.max_tokens if config else settings.ai_max_tokens),
        "temperature": temperature if temperature is not None else (config.temperature if config else settings.ai_temperature),
        "stream": stream,
    }
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


def _get_timeout(*, config=None) -> int:
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
) -> httpx.Response:
    """POST + 限流退避重试 + 429 耗尽后降级到 claude-proxy CLI 通道。返回 200 响应或抛 LLMError。"""
    last: httpx.Response | None = None
    for attempt in range(_MAX_ATTEMPTS):
        resp = await client.post(endpoint, json=body, headers=headers)
        if resp.status_code == 200:
            return resp
        last = resp
        if resp.status_code not in _RETRY_STATUS or attempt == _MAX_ATTEMPTS - 1:
            break
        delay = _retry_delay(resp, attempt)
        logger.warning(
            "LLM %s，%.1fs 后重试 (%d/%d) %s", resp.status_code, delay,
            attempt + 1, _MAX_ATTEMPTS - 1, endpoint,
        )
        await asyncio.sleep(delay)

    # 429 重试耗尽 → 走 CLI 通道（proxy 只讲 OpenAI 协议，anthropic provider 不降级）
    if last is not None and last.status_code == 429 and provider != "anthropic":
        fb = _proxy_endpoint()
        if fb and fb != endpoint:
            logger.warning("网关持续限流，降级到 CLI 通道: %s", fb)
            resp = await client.post(fb, json=body, headers=headers, timeout=_PROXY_TIMEOUT)
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
) -> LLMResponse:
    provider = _get_provider(config=config)
    if provider == "anthropic":
        body = _build_anthropic_body(messages, model=model, max_tokens=max_tokens, temperature=temperature, config=config)
    else:
        body = _build_openai_body(messages, model=model, max_tokens=max_tokens, temperature=temperature, config=config)

    headers = {**_build_headers(config=config), **_get_extra_headers(config=config)}
    endpoint = _get_endpoint(config=config)

    async with httpx.AsyncClient(timeout=_get_timeout(config=config)) as client:
        resp = await _post_with_retry(client, endpoint, body, headers, provider=provider)
        data = resp.json()

    if provider == "anthropic":
        content = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")
        return LLMResponse(
            content=content,
            finish_reason=data.get("stop_reason", "end_turn"),
            prompt_tokens=data.get("usage", {}).get("input_tokens", 0),
            completion_tokens=data.get("usage", {}).get("output_tokens", 0),
            model=data.get("model", ""),
        )

    choice = data.get("choices", [{}])[0]
    usage = data.get("usage", {})
    return LLMResponse(
        content=choice.get("message", {}).get("content", ""),
        finish_reason=choice.get("finish_reason", "stop"),
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        model=data.get("model", ""),
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
                if status in _RETRY_STATUS and attempt < _MAX_ATTEMPTS - 1:
                    retry_delay = _retry_delay(resp, attempt)

            if retry_delay is not None:
                attempt += 1
                logger.warning(
                    "LLM 流式 %s，%.1fs 后重试 (%d/%d) %s",
                    status, retry_delay, attempt, _MAX_ATTEMPTS - 1, target,
                )
                await asyncio.sleep(retry_delay)
                continue

            if status == 429 and provider != "anthropic" and not proxy_tried:
                fb = _proxy_endpoint()
                if fb and fb != target:
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
