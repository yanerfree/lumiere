"""LLM Mock 引擎 — 路由匹配 + 响应生成 + SSE 流式 + Token 估算 + 向量 (Embeddings)"""
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


_AI_CASE_KEYWORDS = ("测试用例", "JSON 数组", "test case", "测试设计", "设计测试用例")

_MOCK_CASES_JSON = json.dumps([
    {
        "title": "正常创建-必填字段完整",
        "type": "api",
        "priority": "P0",
        "preconditions": "已登录，具有创建权限",
        "steps": [
            {"action": "发送 POST 请求，body 包含所有必填字段", "expected": "返回 201，响应包含新建资源的 id"},
            {"action": "查询新建资源详情", "expected": "返回 200，数据与提交一致"}
        ],
        "expected_result": "资源创建成功，数据完整",
        "module": "${request.model}",
        "submodule": None,
        "tags": ["正向", "CRUD"]
    },
    {
        "title": "异常-缺少必填字段",
        "type": "api",
        "priority": "P0",
        "preconditions": "已登录",
        "steps": [
            {"action": "发送 POST 请求，body 缺少必填字段", "expected": "返回 400/422，提示缺少必填字段"}
        ],
        "expected_result": "拒绝创建，返回明确的错误提示",
        "module": "${request.model}",
        "submodule": None,
        "tags": ["异常", "参数校验"]
    },
    {
        "title": "异常-重复数据唯一性校验",
        "type": "api",
        "priority": "P0",
        "preconditions": "数据库中已存在相同唯一键的记录",
        "steps": [
            {"action": "发送 POST 请求，body 包含已存在的唯一键值", "expected": "返回 409 或 400，提示数据重复"}
        ],
        "expected_result": "拒绝重复创建",
        "module": "${request.model}",
        "submodule": None,
        "tags": ["异常", "业务规则"]
    },
    {
        "title": "边界值-字段长度上限",
        "type": "api",
        "priority": "P1",
        "preconditions": "已登录",
        "steps": [
            {"action": "发送 POST 请求，某字段值达到长度上限", "expected": "返回 201 或明确的长度限制错误"},
            {"action": "发送 POST 请求，某字段值超过长度上限", "expected": "返回 400，提示超出长度"}
        ],
        "expected_result": "边界值内正常处理，超出时有明确提示",
        "module": "${request.model}",
        "submodule": None,
        "tags": ["边界值"]
    },
    {
        "title": "权限校验-未登录访问",
        "type": "api",
        "priority": "P1",
        "preconditions": "未登录（无 Token）",
        "steps": [
            {"action": "不带 Authorization 头发送请求", "expected": "返回 401 Unauthorized"}
        ],
        "expected_result": "未认证时拒绝访问",
        "module": "${request.model}",
        "submodule": None,
        "tags": ["权限", "安全"]
    }
], ensure_ascii=False, indent=2)


def _detect_smart_response(request_body: dict) -> str | None:
    messages = request_body.get("messages", [])
    text = " ".join(m.get("content", "") for m in messages if isinstance(m.get("content"), str))
    for kw in _AI_CASE_KEYWORDS:
        if kw in text:
            return _resolve_template(_MOCK_CASES_JSON, request_body)
    return None


def _resolve_body(route: dict, request_body: dict) -> str:
    # 智能应答会**盖掉**路由配的 response_body。做护栏/脱敏这类"输出里必须有某个串"的验证时，
    # prompt 一旦蹭到 _AI_CASE_KEYWORDS 就会拿到一段用例 JSON，判定直接失真 —— 所以给了开关。
    if route.get("smart_response", True):
        smart = _detect_smart_response(request_body)
        if smart:
            return smart
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


async def build_response_stream(route: dict, request_body: dict) -> AsyncIterator[str]:
    """构建 SSE 流式 Chat Completion 响应。yield 每一行 SSE data"""
    completion_id = _gen_completion_id()
    created = int(time.time())
    req_model = request_body.get("model", "gpt-4o")
    resp_model = req_model if route["model_mode"] == "follow_request" else (route.get("custom_model") or req_model)
    finish_reason = route.get("finish_reason", "stop")
    chunk_delay = route.get("sse_chunk_delay_ms", 50) / 1000.0

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
        for ch in content_text:
            yield _chunk({"refusal": ch}, None)
            await asyncio.sleep(chunk_delay)
        yield _chunk({}, finish_reason)
    else:
        content_text = _resolve_body(route, request_body)
        # 第一个 chunk: role
        yield _chunk({"role": "assistant", "content": ""}, None)
        # 内容逐字
        for ch in content_text:
            yield _chunk({"content": ch}, None)
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
        if fixed:
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
