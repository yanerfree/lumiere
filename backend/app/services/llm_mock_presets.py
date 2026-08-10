"""LLM Mock 预设响应模式定义"""

PRESETS: dict[str, dict] = {
    # ── 正常响应 ──
    "normal_text": {
        "label": "正常 - 文本回复",
        "group": "normal",
        "status_code": 200,
        "finish_reason": "stop",
        "response_type": "text",
        "response_body": "This is a mock response from the LLM Mock service.",
    },
    "normal_tool_calls": {
        "label": "正常 - Tool Calls",
        "group": "normal",
        "status_code": 200,
        "finish_reason": "tool_calls",
        "response_type": "tool_calls",
        "response_body": "",
        "tool_calls": [
            {
                "name": "get_weather",
                "arguments": '{"location": "Beijing", "unit": "celsius"}',
            }
        ],
    },
    "normal_length": {
        "label": "正常 - 截断 (length)",
        "group": "normal",
        "status_code": 200,
        "finish_reason": "length",
        "response_type": "text",
        "response_body": "This response was truncated because it reached the maximum token limit. The content is incomplete and ends mid-sentence, which is typical when the model hits max_tokens. The application should handle this by",
    },
    "normal_content_filter": {
        "label": "正常 - 内容过滤",
        "group": "normal",
        "status_code": 200,
        "finish_reason": "content_filter",
        "response_type": "text",
        "response_body": "",
    },
    "normal_refusal": {
        "label": "正常 - 模型拒绝",
        "group": "normal",
        "status_code": 200,
        "finish_reason": "stop",
        "response_type": "refusal",
        "response_body": "I'm sorry, I can't assist with that request.",
    },
    "normal_tool_calls_truncated": {
        "label": "正常 - Tool Calls 截断",
        "group": "normal",
        "status_code": 200,
        "finish_reason": "length",
        "response_type": "tool_calls",
        "response_body": "",
        "tool_calls": [
            {
                "name": "write_file",
                "arguments": '{"path": "/src/app.js", "content": "import React from \'react\';\nfunction App() {\n  return (\n    <div className=',
            }
        ],
    },

    # ── 向量 Embeddings ──（响应内容留空即按输入文本确定性生成向量）
    "normal_embedding": {
        "label": "正常 - 向量 Embeddings",
        "group": "normal",
        "status_code": 200,
        "finish_reason": "stop",
        "response_type": "embedding",
        "response_body": "",
    },
    "normal_embedding_fixed": {
        "label": "正常 - 向量 (固定向量)",
        "group": "normal",
        "status_code": 200,
        "finish_reason": "stop",
        "response_type": "embedding",
        "response_body": "[0.1, 0.2, 0.3]",
    },

    # ── 网关联调 ──
    # 拿这个 mock 当"假上游"去验网关（护栏 / 脱敏 / fail-closed）时用。
    # 四条都关掉条件应答：规则命中会盖掉下面的 response_body，
    # 而这几个场景的判定恰恰全靠"输出里到底有没有那个串"。
    "gateway_guardrail_hit": {
        "label": "网关 - 护栏命中 (输出含 VIOLATION)",
        "group": "gateway",
        "status_code": 200,
        "finish_reason": "stop",
        "response_type": "text",
        "match_enabled": False,
        "match_rules": [],
        "response_body": "根据内部风控记录，该客户存在异常交易行为，标记为 VIOLATION，建议冻结账户并转人工复核。",
    },
    "gateway_pii_output": {
        "label": "网关 - 输出侧 PII (含身份证号)",
        "group": "gateway",
        "status_code": 200,
        "finish_reason": "stop",
        "response_type": "text",
        "match_enabled": False,
        "match_rules": [],
        # 输入里不放、只在输出里放 —— 这样才能验出"护栏查的是输出而不是输入"
        "response_body": "已为你查到该客户的登记信息：姓名 张三，身份证号 11010119900101123X，联系电话 13800138000。",
    },
    "gateway_fail_closed": {
        "label": "网关 - fail-closed (stream:false 仍返事件流)",
        "group": "gateway",
        "status_code": 200,
        "finish_reason": "stop",
        "response_type": "text",
        "stream_mode": "force_stream",
        "match_enabled": False,
        "match_rules": [],
        "response_body": "上游没有遵守 stream:false 的约定，把整段内容拆成事件流返回了，其中还夹带 VIOLATION 关键词。",
    },
    "gateway_force_json": {
        "label": "网关 - 上游不给流 (stream:true 只回整包)",
        "group": "gateway",
        "status_code": 200,
        "finish_reason": "stop",
        "response_type": "text",
        "stream_mode": "force_json",
        "match_enabled": False,
        "match_rules": [],
        "response_body": "请求要的是流式，上游却一次性返回了完整 JSON —— 用来验网关在拿不到流时会不会挂住或超时。",
    },

    # ── 网关联调 · 指令契约（一键装齐）──
    # 对接方那套假上游是「在 content 里写指令，mock 按指令回」，选这条预设就得到同样的行为，
    # 不用一条条手配。分片大小 6 是硬指标：MODE:HIT 的正文 34 字 → 6 块，
    # 加开头帧/结束帧/[DONE] 正好 9 个 data 分片，网关侧要拿这个数做对照。
    "gateway_contract": {
        "label": "网关 - 指令契约 (MODE:HIT / PII / SAY / EMPTY / DEFY)",
        "group": "gateway",
        "status_code": 200,
        "finish_reason": "stop",
        "response_type": "text",
        "sse_chunk_size": 6,
        "sse_chunk_delay_ms": 20,
        "response_body": "这是默认正文，请求里没写任何 MODE:/SAY: 指令时返回它。",
        "match_enabled": True,
        "match_rules": [
            {
                "id": "contract-hit", "enabled": True, "name": "MODE:HIT 护栏应拦截",
                "field": "prompt", "op": "contains_any", "value": ["MODE:HIT"],
                "response_body": "内部备注：本段包含 VIOLATION 关键词，输出护栏应当拦住它。",
                "status_code": None,
            },
            {
                "id": "contract-pii", "enabled": True, "name": "MODE:PII 输出侧敏感信息",
                "field": "prompt", "op": "contains_any", "value": ["MODE:PII"],
                "response_body": "客户的身份证号是 11010119900101123X，手机 13800138000，请登记入档。",
                "status_code": None,
            },
            {
                "id": "contract-empty", "enabled": True, "name": "MODE:EMPTY 零内容的流",
                "field": "prompt", "op": "contains_any", "value": ["MODE:EMPTY"],
                "response_body": "",
                "status_code": None,
            },
            {
                "id": "contract-defy", "enabled": True, "name": "MODE:DEFY 无视非流式要求硬返流",
                "field": "prompt", "op": "contains_any", "value": ["MODE:DEFY"],
                "response_body": "这是默认正文，请求里没写任何 MODE:/SAY: 指令时返回它。",
                "status_code": None,
                "stream_mode": "force_stream",
            },
            {
                # 放最后：SAY: 是兜底指令，把冒号后面那段原样回显（${match.1} = 正则第一个捕获组）
                "id": "contract-say", "enabled": True, "name": "SAY:xxx 原样回显",
                "field": "prompt", "op": "regex", "value": ["SAY:\\s*(.+?)\\s*$"],
                "response_body": "${match.1}",
                "status_code": None,
            },
        ],
    },

    # ── 客户端错误 4xx ──（只填错误消息，引擎自动包装为 OpenAI 错误格式）
    "error_400_invalid": {
        "label": "400 参数错误",
        "group": "client_error",
        "status_code": 400,
        "response_body": "Invalid value for 'temperature': expected a value between 0 and 2, got 3.5.",
    },
    "error_400_context": {
        "label": "400 context 超限",
        "group": "client_error",
        "status_code": 400,
        "response_body": "This model's maximum context length is 128000 tokens. However, your messages resulted in 130542 tokens. Please reduce the length of the messages or completion.",
    },
    "error_401_invalid_key": {
        "label": "401 无效 Key",
        "group": "client_error",
        "status_code": 401,
        "response_body": "Incorrect API key provided: sk-proj-****xxxx. You can find your API key at https://platform.openai.com/account/api-keys.",
    },
    "error_401_missing_key": {
        "label": "401 缺少 Key",
        "group": "client_error",
        "status_code": 401,
        "response_body": "You didn't provide an API key. You need to provide your API key in an Authorization header using Bearer auth.",
    },
    "error_403_quota": {
        "label": "403 配额用尽",
        "group": "client_error",
        "status_code": 403,
        "response_body": "You exceeded your current quota, please check your plan and billing details.",
    },
    "error_403_region": {
        "label": "403 地区不支持",
        "group": "client_error",
        "status_code": 403,
        "response_body": "Country, region, or territory not supported.",
    },
    "error_404_model": {
        "label": "404 模型不存在",
        "group": "client_error",
        "status_code": 404,
        "response_body": "The model 'gpt-5-turbo' does not exist or you do not have access to it.",
    },
    "error_408_timeout": {
        "label": "408 请求超时",
        "group": "client_error",
        "status_code": 408,
        "response_body": "Request timed out.",
    },
    "error_429_rpm": {
        "label": "429 限频 (RPM)",
        "group": "client_error",
        "status_code": 429,
        "response_body": "Rate limit reached for gpt-4o in organization org-xxxxx on requests per min (RPM): Limit 500, Used 500, Requested 1.",
        "response_headers": {"retry-after-ms": "5000", "retry-after": "5"},
    },
    "error_429_tpm": {
        "label": "429 限频 (TPM)",
        "group": "client_error",
        "status_code": 429,
        "response_body": "Rate limit reached for gpt-4o on tokens per min (TPM): Limit 30000, Used 28500, Requested 2000.",
        "response_headers": {"retry-after-ms": "2000", "retry-after": "2"},
    },

    # ── 服务端错误 5xx ──（只填错误消息，引擎自动包装）
    "error_500": {
        "label": "500 服务器错误",
        "group": "server_error",
        "status_code": 500,
        "response_body": "The server had an error while processing your request. Sorry about that!",
    },
    "error_502": {
        "label": "502 网关错误",
        "group": "server_error",
        "status_code": 502,
        "response_body": "Bad gateway.",
    },
    "error_503": {
        "label": "503 过载",
        "group": "server_error",
        "status_code": 503,
        "response_body": "The engine is currently overloaded, please try again later.",
    },
}


def get_preset(key: str) -> dict | None:
    return PRESETS.get(key)


def list_presets() -> list[dict]:
    result = []
    for key, p in PRESETS.items():
        result.append({"key": key, "label": p["label"], "group": p["group"], "status_code": p.get("status_code", 200)})
    return result
