"""LLM Mock 内置路由套件 —— 开箱即用的那一批。

为什么要有这东西
────────────────
在这之前，LLM Mock 里只有人一条条手建的路由。新人打开页面看到的是一串裸路径，
「哪条是正常的、我要测限流该填哪个、Anthropic 形状有没有」全靠问人；
而想测一个新场景就得先弄懂十几个配置项。内置套件把「这个 mock 支持的能力」
一条一条摆出来，**路径用各家官方那个写法**，复制访问地址填进被测网关就能跑，
不用改任何配置。

三条纪律
────────
① **路径是官方写法**（`/v1/chat/completions`、`/v1/messages`、`/api/chat` …）。
   场景变体用「场景前缀 + 官方尾巴」（`/429/v1/chat/completions`）——
   引擎的后缀匹配和各家 SDK 的 base_url 拼接规则都吃这种写法，所以填 base_url
   时把前缀带上即可，SDK 那边一个字不用改。
② **只新增、只补元信息，绝不覆盖行为，绝不删任何路由。**
   已经存在的同名路径（比如库里早就有的 `/v1/chat/completions`）只会被贴上
   「内置」标签收编进分组，它的状态码/响应体/延迟一个字都不动 —— 那些是别人
   正在用的东西，启动时悄悄改掉它，表现是「昨天好好的用例今天红了」，
   而这种红查不到原因。
③ 行为字段**建好之后就不再跟着代码走**。解锁改过的内置路由，下次重启不会被
   冲回去；要拿新定义就把那条删掉重启。（反过来做的话，「我明明改了」和
   「它自己变回去了」会变成一个没人能复现的怪事。）

purpose / usage_hint 是**写死的文案**，不是前端从配置反推的句子。反推那版
对「正常文本响应」这类配置只能给同一句兜底话，几十条长得一模一样，等于没写。
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_mock import MockRoute

logger = logging.getLogger(__name__)

# 分组显示名。顺序就是页面上从上到下的顺序。
CATEGORY_LABELS: dict[str, str] = {
    "protocol": "内置 · 协议基线",
    "shape": "内置 · 响应形态",
    "error": "内置 · 故障与限流",
    "smart": "内置 · 智能应答",
    "auth": "内置 · 认证",
}

_BASE_TIP = "把被测网关的上游地址填成这条路由上方那串「完整访问地址」即可，不用改配置。"

# 各家官方路径的正常应答。model 跟随请求，所以填什么模型都能通。
_PROTOCOL: list[dict] = [
    {
        "name": "OpenAI Chat Completions",
        "path": "/v1/chat/completions",
        "purpose": "OpenAI 对话接口的正常应答 —— 联调基线，先验通路走不走得通。",
        "usage_hint": (
            f"{_BASE_TIP}\n"
            "DeepSeek / Qwen / 智谱 / Moonshot 这些「OpenAI 兼容」的厂商也走这条，"
            "它们各自的前缀（如 /compatible-mode/v1/chat/completions）会被后缀匹配兜住。\n"
            "请求里 stream: true 就返事件流，false 就返整包 —— 跟着请求走。"
        ),
        "response_body": "lumiere mock · OpenAI 协议应答",
        "preset_mode": "normal_text",
    },
    {
        "name": "OpenAI Completions（老版补全）",
        "path": "/v1/completions",
        "purpose": "老版文本补全接口的正常应答 —— 验网关认不认这套老形状。",
        "usage_hint": f"{_BASE_TIP}\n回的是 text_completion 形状（choices[].text），不是 message。",
        "response_body": "lumiere mock · OpenAI legacy Completions 协议应答",
        "preset_mode": "normal_text",
    },
    {
        "name": "OpenAI Responses",
        "path": "/v1/responses",
        "purpose": "OpenAI 新版 Responses 接口的正常应答。",
        "usage_hint": f"{_BASE_TIP}\n回的是 response 对象（output[].content[]），流式是 response.* 系列事件。",
        "response_body": "lumiere mock · OpenAI Responses 协议应答",
        "preset_mode": "normal_text",
    },
    {
        "name": "OpenAI Embeddings（向量）",
        "path": "/v1/embeddings",
        "purpose": "向量接口 —— 测语义缓存命中/未命中，同样的文本永远给同一条向量。",
        "usage_hint": (
            f"{_BASE_TIP}\n"
            "向量按输入文本算出来，不是随机数：同一句话两次拿到的向量一模一样，"
            "意思相近的两句话余弦相似度也高 —— 缓存该不该命中因此是可预期的。\n"
            "维度跟随请求的 dimensions 字段。"
        ),
        "response_type": "embedding",
        "response_body": "",
        "preset_mode": "normal_embedding",
    },
    {
        "name": "模型列表",
        "method": "GET",
        "path": "/v1/models",
        "purpose": "返回这个 mock 支持的全部模型 —— 网关探测「这个上游有哪些模型」时要的就是它。",
        "usage_hint": (
            "浏览器直接打开这条完整访问地址就能看到清单，也可以点顶栏的「内置模型」。\n"
            "覆盖 OpenAI / DeepSeek / 通义千问 / 智谱 / Anthropic / Moonshot / BAAI，"
            "含各家的 embedding 模型 —— 网关要在这里看到 embedding 才认这个上游能做语义缓存。\n"
            "带前缀也认：/429/v1/models 一样返回同一份清单，所以场景路由不用各配一条。\n"
            "这条由服务直接应答，页面上改它的响应体不生效（清单跟着平台版本走）。"
        ),
        "response_body": "由服务直接应答：返回内置模型清单（顶栏「内置模型」看到的就是这份）。",
    },
    {
        "name": "Anthropic Messages",
        "path": "/v1/messages",
        "purpose": "Claude 的 messages 接口正常应答 —— 验网关的 Anthropic 形状。",
        "usage_hint": (
            f"{_BASE_TIP}\n"
            "回的是 message 对象（content[].text + stop_reason），流式是 message_start / "
            "content_block_delta 这套事件，和 OpenAI 那套完全不一样。"
        ),
        "response_body": "lumiere mock · Anthropic Messages 协议应答",
        "preset_mode": "normal_text",
    },
    {
        "name": "Gemini generateContent",
        "path": "/v1beta/models/*:generateContent",
        "purpose": "Gemini 的整包应答 —— 验网关的 Google 形状。",
        "usage_hint": (
            f"{_BASE_TIP}\n"
            "路径里的 * 是模型名，一条路由吃所有模型（/v1beta/models/gemini-2.0-flash:generateContent 会命中）。\n"
            "回的是 candidates[].content.parts[]，用量字段叫 usageMetadata。"
        ),
        "response_body": "lumiere mock · Gemini generateContent 协议应答",
        "preset_mode": "normal_text",
    },
    {
        "name": "Gemini streamGenerateContent",
        "path": "/v1beta/models/*:streamGenerateContent",
        "purpose": "Gemini 的流式应答。",
        "usage_hint": f"{_BASE_TIP}\n不管请求怎么写都按流返回 —— Google 这个接口本身就是流式专用。",
        "response_body": "lumiere mock · Gemini streamGenerateContent 协议应答",
        "preset_mode": "normal_text",
    },
    {
        "name": "Ollama chat",
        "path": "/api/chat",
        "purpose": "Ollama 本地模型的对话接口 —— 验网关认不认本地部署那套形状。",
        "usage_hint": (
            f"{_BASE_TIP}\n"
            "Ollama 不带 /v1 前缀，流式是一行一个 JSON（NDJSON），不是 SSE。"
        ),
        "response_body": "lumiere mock · Ollama /api/chat 协议应答",
        "preset_mode": "normal_text",
    },
    {
        "name": "Ollama generate",
        "path": "/api/generate",
        "purpose": "Ollama 的单轮生成接口。",
        "usage_hint": f"{_BASE_TIP}\n回的字段是 response（不是 message），其余同 /api/chat。",
        "response_body": "lumiere mock · Ollama /api/generate 协议应答",
        "preset_mode": "normal_text",
    },
    {
        "name": "Azure OpenAI 部署",
        "path": "/openai/deployments/*/chat/completions",
        "purpose": "Azure 把部署名塞在路径里的那种形状 —— 一条路由吃所有部署名。",
        "usage_hint": (
            f"{_BASE_TIP}\n"
            "路径里的 * 是部署名，/openai/deployments/my-gpt4o/chat/completions 会命中。\n"
            "Azure 的 api-version 查询串不影响匹配。"
        ),
        "response_body": "lumiere mock · Azure OpenAI 部署形状应答",
        "preset_mode": "normal_text",
    },
]

# 正常 200，但内容形态各不相同 —— 这些是最容易在网关里被漏处理的分支。
_SHAPE: list[dict] = [
    {
        "name": "工具调用 tool_calls",
        "path": "/tool-calls/v1/chat/completions",
        "purpose": "上游返回函数调用而不是文字 —— 验网关解析得出工具名和参数。",
        "usage_hint": (
            f"{_BASE_TIP}\n"
            "回的是一条 get_weather 调用，finish_reason=tool_calls、content 为空。\n"
            "常见坑：网关只读 content，遇到工具调用就当成「上游返回了空内容」。"
        ),
        "response_type": "tool_calls",
        "finish_reason": "tool_calls",
        "response_body": "",
        "tool_calls": [{"name": "get_weather", "arguments": '{"location": "Beijing", "unit": "celsius"}'}],
        "preset_mode": "normal_tool_calls",
    },
    {
        "name": "截断 length",
        "path": "/length/v1/chat/completions",
        "purpose": "顶到 token 上限被截断 —— 验网关有没有提示用户「这句话没说完」。",
        "usage_hint": f"{_BASE_TIP}\nfinish_reason=length，正文停在半句上。别把它当成正常完成。",
        "finish_reason": "length",
        "response_body": (
            "This response was truncated because it reached the maximum token limit. "
            "The content is incomplete and ends mid-sentence, which is typical when the model hits max_tokens. "
            "The application should handle this by"
        ),
        "preset_mode": "normal_length",
    },
    {
        "name": "内容过滤 content_filter",
        "path": "/content-filter/v1/chat/completions",
        "purpose": "被上游安全策略拦下 —— 200 但正文是空的，验网关别把它当成功。",
        "usage_hint": f"{_BASE_TIP}\n状态码 200、finish_reason=content_filter、content 为空 —— 三个条件一起看才判得准。",
        "finish_reason": "content_filter",
        "response_body": "",
        "preset_mode": "normal_content_filter",
    },
    {
        "name": "模型拒答 refusal",
        "path": "/refusal/v1/chat/completions",
        "purpose": "模型明确拒绝回答 —— 拒绝理由在 refusal 字段里，不在 content 里。",
        "usage_hint": f"{_BASE_TIP}\n只读 content 的网关会拿到 null，页面上表现为「回了个空」。",
        "response_type": "refusal",
        "response_body": "I'm sorry, I can't assist with that request.",
        "preset_mode": "normal_refusal",
    },
    {
        "name": "上游硬返流（不认 stream:false）",
        "path": "/force-stream/v1/chat/completions",
        "purpose": "请求要整包，上游偏返事件流 —— 验网关的 fail-closed（护栏拿不到完整正文时该拦住）。",
        "usage_hint": (
            f"{_BASE_TIP}\n"
            "把请求写成 stream:false 发过来，这里照样返事件流。\n"
            "正文里夹了 VIOLATION 关键词，配合护栏看它到底拦没拦。"
        ),
        "stream_mode": "force_stream",
        "response_body": "上游没有遵守 stream:false 的约定，把整段内容拆成事件流返回了，其中还夹带 VIOLATION 关键词。",
        "preset_mode": "gateway_fail_closed",
    },
    {
        "name": "上游不给流（只回整包）",
        "path": "/force-json/v1/chat/completions",
        "purpose": "请求要流式，上游只给整包 —— 验网关会不会挂住或超时。",
        "usage_hint": f"{_BASE_TIP}\n把请求写成 stream:true 发过来，这里仍然一次性返回完整 JSON。",
        "stream_mode": "force_json",
        "response_body": "请求要的是流式，上游却一次性返回了完整 JSON —— 用来验网关在拿不到流时会不会挂住或超时。",
        "preset_mode": "gateway_force_json",
    },
    {
        "name": "输出侧敏感信息",
        "path": "/pii/v1/chat/completions",
        "purpose": "敏感信息只出现在**输出**里 —— 验护栏查的是输出而不是输入。",
        "usage_hint": (
            f"{_BASE_TIP}\n"
            "请求里干干净净，回复里有身份证号和手机号。只查输入的护栏会全放行。"
        ),
        "response_body": "已为你查到该客户的登记信息：姓名 张三，身份证号 11010119900101123X，联系电话 13800138000。",
        "preset_mode": "gateway_pii_output",
    },
]

# 各类失败。路径前缀就是状态码，一眼认得出。
_ERROR: list[dict] = [
    {
        "name": "400 参数错误",
        "path": "/400/v1/chat/completions",
        "status_code": 400,
        "purpose": "请求参数不合法 —— 验网关把上游的报错原样透出来还是吞掉。",
        "response_body": "Invalid value for 'temperature': expected a value between 0 and 2, got 3.5.",
        "preset_mode": "error_400_invalid",
    },
    {
        "name": "400 上下文超限",
        "path": "/400-context/v1/chat/completions",
        "status_code": 400,
        "purpose": "消息太长顶爆上下文 —— 验网关有没有给出「换个短点的问法」这类提示。",
        "response_body": (
            "This model's maximum context length is 128000 tokens. However, your messages resulted in "
            "130542 tokens. Please reduce the length of the messages or completion."
        ),
        "preset_mode": "error_400_context",
    },
    {
        "name": "401 无效 Key",
        "path": "/401/v1/chat/completions",
        "status_code": 401,
        "purpose": "上游说密钥不对 —— 验网关是提示「配置有问题」还是傻乎乎地重试。",
        "response_body": "Incorrect API key provided: sk-proj-****xxxx. You can find your API key at https://platform.openai.com/account/api-keys.",
        "preset_mode": "error_401_invalid_key",
    },
    {
        "name": "403 配额用尽",
        "path": "/403/v1/chat/completions",
        "status_code": 403,
        "purpose": "余额/配额用完 —— 这种错重试多少次都不会好，验网关别死磕。",
        "response_body": "You exceeded your current quota, please check your plan and billing details.",
        "preset_mode": "error_403_quota",
    },
    {
        "name": "404 模型不存在",
        "path": "/404/v1/chat/completions",
        "status_code": 404,
        "purpose": "模型名写错或没权限 —— 验网关的模型映射配错了会是什么表现。",
        "response_body": "The model 'gpt-5-turbo' does not exist or you do not have access to it.",
        "preset_mode": "error_404_model",
    },
    {
        "name": "408 请求超时",
        "path": "/408/v1/chat/completions",
        "status_code": 408,
        "purpose": "上游自己判超时并回 408（不是卡住不回）—— 验网关区不区分这两种。",
        "response_body": "Request timed out.",
        "preset_mode": "error_408_timeout",
    },
    {
        "name": "429 限频（带 Retry-After）",
        "path": "/429/v1/chat/completions",
        "status_code": 429,
        "purpose": "被限流 —— 验网关的重试 / 退避 / 降级，最常用的一条。",
        "usage_hint": (
            f"{_BASE_TIP}\n"
            "响应头带了 retry-after: 5 和 retry-after-ms: 5000 —— 看网关是按它等，"
            "还是自己拍脑袋定间隔（后者会把上游打得更狠）。"
        ),
        "response_body": "Rate limit reached for gpt-4o in organization org-xxxxx on requests per min (RPM): Limit 500, Used 500, Requested 1.",
        "response_headers": {"retry-after-ms": "5000", "retry-after": "5"},
        "preset_mode": "error_429_rpm",
    },
    {
        "name": "500 服务器错误",
        "path": "/500/v1/chat/completions",
        "status_code": 500,
        "purpose": "上游内部错 —— 验网关的容错和重试，也是测熔断最常用的一条。",
        "usage_hint": f"{_BASE_TIP}\n连着打它就能把网关的熔断器打开，然后看熔断之后的表现。",
        "response_body": "The server had an error while processing your request. Sorry about that!",
        "preset_mode": "error_500",
    },
    {
        "name": "502 网关错误",
        "path": "/502/v1/chat/completions",
        "status_code": 502,
        "purpose": "上游前面那层网关坏了 —— 验错误码有没有被原样透传。",
        "response_body": "Bad gateway.",
        "preset_mode": "error_502",
    },
    {
        "name": "503 过载",
        "path": "/503/v1/chat/completions",
        "status_code": 503,
        "purpose": "上游过载 —— 这种是该重试的，验网关分不分得清它和 403。",
        "response_body": "The engine is currently overloaded, please try again later.",
        "preset_mode": "error_503",
    },
    {
        "name": "慢上游（5 秒）",
        "path": "/slow/v1/chat/completions",
        "purpose": "上游拖了 5 秒才回 —— 验网关的超时阈值和等待时的表现。",
        "usage_hint": (
            f"{_BASE_TIP}\n"
            "内容是正常的，只是慢。网关超时设得比 5 秒短就会先断开，"
            "这时候看它是报超时还是无声无息。\n"
            "要更慢的自己复制一条改延迟即可（内置的这条是锁的）。"
        ),
        "delay_ms": 5000,
        "response_body": "上游花了 5 秒才回，内容本身是正常的 —— 用来验网关的超时阈值。",
        "preset_mode": "normal_text",
    },
]

_SMART_TIP = (
    "行为由请求正文里的指令决定，一条路由演所有场景 —— 页面上那些状态码/响应体配置对它不生效。\n"
    "在 messages 里写：SAY:你好 原样回显 / MODE:HIT 输出含 VIOLATION / MODE:PII 输出带敏感信息 / "
    "MODE:EMPTY 零内容流 / MODE:FILTER 空回复+content_filter / MODE:DEFY 无视 stream:false 硬返流 / "
    "MODE:SLOW 每片 250ms / MODE:LOOP 先工具调用再终局。"
)

_SMART: list[dict] = [
    {
        "name": "智能上游（OpenAI 形状）",
        "path": "/smart/v1/chat/completions",
        "purpose": "一条路由按请求里的指令演多种场景 —— 不用为每个场景各配一条 mock。",
        "usage_hint": f"{_BASE_TIP}\n{_SMART_TIP}",
        "smart_enabled": True,
        "smart_role": "upstream",
        "response_body": "",
    },
    {
        "name": "智能上游（Anthropic 形状）",
        "path": "/smart/v1/messages",
        "purpose": "同上，但回 Anthropic 的 message 形状。",
        "usage_hint": f"{_BASE_TIP}\n{_SMART_TIP}",
        "smart_enabled": True,
        "smart_role": "upstream",
        "response_body": "",
    },
    {
        "name": "护栏检查模型",
        "path": "/checker/v1/chat/completions",
        "purpose": "冒充网关护栏调用的那个「AI 审核」模型 —— 看网关到底把什么喂给了它。",
        "usage_hint": (
            f"{_BASE_TIP}（填在网关的**护栏/审核模型**那一栏，不是主模型）\n"
            "它把收到的待检正文长度和开头回显出来，记在请求日志的智能应答信息里。\n"
            "网关只截了前 200 字就送去审核这类问题，只有在这里看得出来。"
        ),
        "smart_enabled": True,
        "smart_role": "checker",
        "response_body": "",
    },
]

_AUTH: list[dict] = [
    {
        "name": "Bearer Token 校验",
        "path": "/auth/v1/chat/completions",
        "purpose": "只有带对 Token 才放行 —— 验网关有没有把上游密钥正确注入。",
        "usage_hint": (
            f"{_BASE_TIP}\n"
            "上游密钥填 lumiere-mock-token。填错或不填，这里直接回 401 —— "
            "「网关到底有没有把密钥带上去」这件事在下游是看不出来的，只能在这里验。"
        ),
        "auth_type": "bearer",
        "auth_config": {"token": "lumiere-mock-token"},
        "response_body": "认证通过：网关把正确的上游密钥带上来了。",
        "preset_mode": "normal_text",
    },
]

_GROUPS: list[tuple[str, list[dict]]] = [
    ("protocol", _PROTOCOL),
    ("shape", _SHAPE),
    ("error", _ERROR),
    ("smart", _SMART),
    ("auth", _AUTH),
]

# 内置路由排在自建之前：sort_order 用负数占位，自建的是 0 起步的。
_SORT_BASE = -1000

# 行为字段（建一次就不再跟着代码走），和元信息字段（每次启动刷成最新）分开。
_BEHAVIOUR_DEFAULTS: dict = {
    "enabled": True,
    "delay_ms": 0,
    # 流式分片：默认一次 6 个字符、间隔 20ms。库默认是「一个字符一片」——
    # 那会把一句话切成几十片，既不像真上游，也让「分片数」这种被验证的指标失真。
    "sse_chunk_size": 6,
    "sse_chunk_delay_ms": 20,
    "status_code": 200,
    "finish_reason": "stop",
    "response_type": "text",
    "response_body": "",
    "tool_calls": None,
    "stream_mode": "auto",
    "response_headers": None,
    "smart_enabled": False,
    "smart_role": "auto",
    "auth_type": "none",
    "auth_config": None,
    "preset_mode": None,
    "method": "POST",
}
_META_FIELDS = ("name", "category", "purpose", "usage_hint", "sort_order", "builtin", "locked")


def builtin_specs() -> list[dict]:
    """展平成一张有序清单，每条自带 category 和 sort_order。"""
    out: list[dict] = []
    for category, items in _GROUPS:
        for spec in items:
            row = dict(_BEHAVIOUR_DEFAULTS)
            row.update(spec)
            row["method"] = str(row["method"]).upper()
            row["category"] = category
            row["builtin"] = True
            row["locked"] = True
            row["sort_order"] = _SORT_BASE + len(out)
            row.setdefault("usage_hint", _BASE_TIP)
            out.append(row)
    return out


async def ensure_builtin_routes(session: AsyncSession) -> dict[str, int]:
    """幂等落地内置套件。返回 {created, adopted, refreshed}。

    · 库里没有这条 (method, path) → 按定义新建（锁定）
    · 已有且 builtin=True        → 只刷元信息（名称/分组/用途/说明/排序），行为不动
    · 已有但 builtin=False       → 只贴元信息收编进内置组，**行为一个字不改**
                                   （那是别人正在用的路由，启动时悄悄改掉它，
                                     表现是「昨天好好的用例今天红了」，还查不到原因）
    任何情况下都不删除路由。
    """
    specs = builtin_specs()
    existing = {
        (r.method.upper(), r.path): r
        for r in (await session.execute(select(MockRoute))).scalars().all()
    }
    stats = {"created": 0, "adopted": 0, "refreshed": 0}

    for spec in specs:
        key = (spec["method"], spec["path"])
        row = existing.get(key)
        if row is None:
            session.add(MockRoute(**{k: v for k, v in spec.items()}))
            stats["created"] += 1
            continue
        was_builtin = bool(row.builtin)
        for field in _META_FIELDS:
            setattr(row, field, spec[field])
        stats["refreshed" if was_builtin else "adopted"] += 1

    await session.flush()
    return stats


async def seed_builtin_routes() -> dict[str, int] | None:
    """启动时调用的包装：自己开会话、自己提交、出错只记日志不拦启动。

    失败不能把服务拖住 —— 少几条示例路由是小事，起不来是大事。
    """
    from app.deps.db import async_session_factory

    try:
        async with async_session_factory() as session:
            stats = await ensure_builtin_routes(session)
            await session.commit()
        if stats["created"] or stats["adopted"]:
            logger.info(
                "LLM Mock 内置路由：新建 %d 条、收编 %d 条、刷新 %d 条",
                stats["created"], stats["adopted"], stats["refreshed"],
            )
        return stats
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM Mock 内置路由落地失败（不影响服务启动）: %s", e)
        return None


async def count_builtin(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count(MockRoute.id)).where(MockRoute.builtin.is_(True))
    ) or 0
