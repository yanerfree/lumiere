"""智能应答 —— 可控假上游的指令契约。

被测系统是 AI 网关。要验它的护栏 / 脱敏 / fail-closed / 计费统计，就需要一个**行为由
请求精确控制**的假上游：场景开关写在请求正文里（`MODE:PII`、`SAY:你好`），而不是写在
服务端配置里。差别不是风格问题 —— 配置在服务端的话，每换一个场景都要改配置、等下发、
重来一遍，**对照实验根本做不起来**。

## 开 / 关

关着：路由是一条老老实实的静态 mock，所有请求都回它配的 response_body。
开着：本模块接管，响应内容 / 状态码 / finish_reason / 流式全部由请求里的指令决定，
页面上那些配置也就跟着隐藏了 —— 显示一堆改了不生效的框只会误导人。

（曾经还有一套 match_rules 条件应答干类似的事，跟这个重复：一条路由上两套
「按请求内容决定回什么」，"这次到底是谁决定了响应"只能靠猜，已在 zz6dropmr 删掉。）

## 实现取向

`apply_smart` **不自己造响应**，而是把指令翻译成现有引擎已经吃的那些字段
（response_body / finish_reason / response_type / stream_mode / delay_ms …），
然后让现有链路照常跑。好处是流式切片、usage 帧、响应头、Token 估算这些一行都不用重写，
也不会出现「两条并行的响应生成路径慢慢长歪」。
"""
from __future__ import annotations

import json
import re

# ───── 内置正文 ─────

# 不带任何指令时返回它。三个要求：够长（能切出多片，便于观察分片边界）、
# 干净（保证不误触护栏，否则「无指令」这条基线本身就不可信）、无敏感信息。
SMART_DEFAULT_BODY = (
    "这是一段用于联调的默认回复。它不包含任何敏感信息，也不含任何会触发内容护栏的关键词，"
    "因此可以作为对照实验的基线：如果这条请求都被拦下来了，那问题一定在网关侧而不在上游内容。"
    "正文特意写得长一些，好让流式分片切出足够多的片段，方便观察分片边界上的处理是否正确。"
)

# 输出侧敏感信息。**请求里只有 MODE:PII 四个字，身份证号和手机号只出现在这里** ——
# 这是一票否决式对照：护栏若查的是输入，它会判「无 PII」并把号码原样放行。
SMART_PII_BODY = (
    "已为你查到该客户的登记信息：姓名 张三，身份证号 11010119900101123X，"
    "联系电话 13800138000，请登记入档。"
)

# 含 VIOLATION 关键词。不依赖大模型的确定性对照 —— 先用它排除「是不是模型判飘了」。
SMART_HIT_BODY = (
    "根据内部风控记录，该客户存在异常交易行为，已标记为 VIOLATION，"
    "建议冻结账户并转人工复核。"
)

# MODE:LOOP 的终局正文。带 VIOLATION 是故意的：网关会把中间迭代强制成非流式、
# 只有终局是流式的，「护栏到底有没有介入终局」只能靠终局里有没有可拦的东西来验。
SMART_LOOP_FINAL_BODY = (
    "工具已返回查询结果。综合来看该账户存在 VIOLATION 风险，建议立即冻结并转人工复核。"
)

# 请求里没带 tools 时的兜底工具名
SMART_LOOP_TOOL_CALLS = [
    {"name": "query_risk_profile", "arguments": '{"customer_id":"C10086","scope":"full"}'}
]

# 按 JSON Schema 的类型造占位值
_ARG_PLACEHOLDER = {"string": "mock", "integer": 1, "number": 1, "boolean": True, "array": [], "object": {}}


def _mock_arguments(schema: dict | None) -> str:
    """按工具自己的参数 schema 造一份最小入参。

    只填 required 的：多填可能撞上 additionalProperties:false，反而调不通。
    """
    if not isinstance(schema, dict):
        return "{}"
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    out: dict = {}
    for k in required:
        if not isinstance(k, str):
            continue
        spec = props.get(k) if isinstance(props.get(k), dict) else {}
        enum = spec.get("enum")
        if isinstance(enum, list) and enum:
            out[k] = enum[0]
        else:
            out[k] = _ARG_PLACEHOLDER.get(spec.get("type"), "mock")
    return json.dumps(out, ensure_ascii=False)


def loop_tool_calls(body: dict) -> list[dict]:
    """MODE:LOOP 第一轮要回的 tool_calls。

    **工具名必须取自请求**，不能写死。网关是拿模型返回的工具名去**真执行**的：
    名字不在请求的 tools 里，执行端点直接报错，网关把 "tool execution failed"
    当成工具结果塞回给模型 —— loop 照样转两轮，迭代计数、逐轮日志、终局是否流式
    都还能测，但**真实的工具执行链路（MCP 调用 → 结果回填 → 工具结果缓存）测不了**。
    对接方实测反馈的问题。

    优先级：tool_choice 指名的 > tools 里第一个 > 内置兜底名。
    """
    if not isinstance(body, dict):
        return [dict(tc) for tc in SMART_LOOP_TOOL_CALLS]

    tools = body.get("tools") if isinstance(body.get("tools"), list) else []

    def _spec(t) -> tuple[str, dict | None] | None:
        if not isinstance(t, dict):
            return None
        fn = t.get("function")
        if isinstance(fn, dict) and isinstance(fn.get("name"), str) and fn["name"]:
            return fn["name"], fn.get("parameters")
        # Anthropic 的形状：{name, input_schema}
        if isinstance(t.get("name"), str) and t["name"]:
            return t["name"], t.get("input_schema")
        return None

    # tool_choice 指名了某个工具就用它 —— 网关会拿这个来验「模型有没有听话」
    choice = body.get("tool_choice")
    wanted = None
    if isinstance(choice, dict):
        cf = choice.get("function")
        if isinstance(cf, dict) and isinstance(cf.get("name"), str):
            wanted = cf["name"]
        elif isinstance(choice.get("name"), str):
            wanted = choice["name"]
    if wanted:
        for t in tools:
            sp = _spec(t)
            if sp and sp[0] == wanted:
                return [{"name": sp[0], "arguments": _mock_arguments(sp[1])}]
        return [{"name": wanted, "arguments": "{}"}]

    for t in tools:
        sp = _spec(t)
        if sp:
            return [{"name": sp[0], "arguments": _mock_arguments(sp[1])}]

    return [dict(tc) for tc in SMART_LOOP_TOOL_CALLS]

# MODE:SLOW 每片固定 250ms。固定值而不是沿用路由配置，是为了和对接方那份脚本对得上 ——
# 两边分片计时不一致的话，「全量缓冲把首字延迟推成完整生成耗时」这个降级代价量不出来。
SLOW_CHUNK_DELAY_MS = 250

# 回显里正文开头截多少字
BODY_HEAD_LEN = 60


# ───── 指令 ─────

# 已知指令。SAY: 单独处理（要取冒号后面那段），其余是纯开关。
SMART_MODES = ("PII", "HIT", "LOOP", "DEFY", "SLOW", "EMPTY", "FILTER")

_SAY_RE = re.compile(r"SAY:[ \t]*(.*)")
_MODE_RE = re.compile(r"MODE:([A-Z_]+)")


def parse_directive(text: str) -> dict:
    """从文本里解析场景开关。返回 {"mode": str|None, "say": str|None, "raw": str|None}

    SAY: 取到**行尾**（不是到空格）—— 要回显的正文里通常带空格和标点。
    同时出现 SAY: 和 MODE: 时 SAY 优先：它是「精确控制输出」那条路，语义更强。
    """
    if not text:
        return {"mode": None, "say": None, "raw": None}

    m = _SAY_RE.search(text)
    if m:
        said = m.group(1).strip()
        return {"mode": "SAY", "say": said, "raw": f"SAY:{said}"}

    for mm in _MODE_RE.finditer(text):
        mode = mm.group(1)
        if mode in SMART_MODES:
            return {"mode": mode, "say": None, "raw": f"MODE:{mode}"}

    return {"mode": None, "say": None, "raw": None}


# ───── 入参形状（取「最后一条 user 文本」这一件事，三种形状都要能取） ─────

def _block_text(content) -> str:
    """Anthropic 的 content 是 block 数组，拼接各 block 的 text。"""
    parts = []
    for b in content:
        if isinstance(b, dict):
            t = b.get("text")
            if isinstance(t, str):
                parts.append(t)
            elif b.get("type") == "tool_result":
                c = b.get("content")
                if isinstance(c, str):
                    parts.append(c)
                elif isinstance(c, list):
                    parts.append(_block_text(c))
        elif isinstance(b, str):
            parts.append(b)
    return "\n".join(parts)


def message_text(message) -> str:
    """一条消息的文本，兼容字符串 content 与 block 数组 content。"""
    if not isinstance(message, dict):
        return ""
    c = message.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return _block_text(c)
    if c is None:
        return ""
    return json.dumps(c, ensure_ascii=False)


def extract_user_text(body: dict) -> str:
    """最后一条 user 消息的文本。三种入参形状：

      messages[].content 是字符串       → OpenAI
      messages[].content 是 block 数组  → Anthropic
      body.prompt 是字符串              → legacy completions

    没有 user 消息时退回最后一条消息 —— 有些客户端把指令塞在 system 里，
    直接返回空串的话整个契约在那种客户端上就是静默失效。
    """
    if not isinstance(body, dict):
        return ""
    messages = body.get("messages")
    if isinstance(messages, list) and messages:
        for m in reversed(messages):
            if isinstance(m, dict) and m.get("role") == "user":
                return message_text(m)
        return message_text(messages[-1])
    prompt = body.get("prompt")
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, list):
        return "\n".join(str(p) for p in prompt)
    return ""


def all_text(body: dict) -> str:
    """整个请求里的全部文本（含 system）。护栏信封的正文定位要在这里面找。"""
    if not isinstance(body, dict):
        return ""
    messages = body.get("messages")
    if isinstance(messages, list) and messages:
        return "\n".join(message_text(m) for m in messages)
    prompt = body.get("prompt")
    return prompt if isinstance(prompt, str) else ""


def loop_stage(body: dict) -> int:
    """Agent Loop 的第几轮。有 role=tool 消息（OpenAI）或 tool_result block（Anthropic）→ 第 2 轮。"""
    if not isinstance(body, dict):
        return 1
    for m in body.get("messages") or []:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "tool" or m.get("tool_call_id"):
            return 2
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    return 2
    return 1


# ───── 协议形状 / 角色 ─────

def detect_shape(path: str) -> str:
    """按路径判协议形状：chat（chat.completion）/ text（text_completion）/ anthropic（message）。

    判别顺序要紧：/chat/completions 必须在 /completions 之前判掉，
    否则 legacy 那条分支会把所有 chat 请求都吃了。
    """
    p = (path or "").split("?", 1)[0].rstrip("/")
    if p.endswith("/messages"):
        return "anthropic"
    if p.endswith("/chat/completions"):
        return "chat"
    if p.endswith("/completions"):
        return "text"
    return "chat"


# 路径里出现这些片段，auto 角色就判成护栏检查模型
_CHECKER_HINTS = ("/checker", "/check", "/guard", "/guardrail", "/moderation")


def resolve_role(route: dict, path: str) -> str:
    """upstream（被测智能体的上游）还是 checker（网关护栏调用的检查模型）。

    显式配置优先。auto 时按路径判 —— 不靠端口分流（平台有路由表，一条路由一个角色，
    比起端口更自然），也不硬要求某个前缀。
    """
    explicit = (route or {}).get("smart_role") or "auto"
    if explicit in ("upstream", "checker"):
        return explicit
    p = (path or "").lower()
    return "checker" if any(h in p for h in _CHECKER_HINTS) else "upstream"


# ───── 护栏检查模型的回显协议 ─────

# 平台/网关把正文包在提示模板里发过来（`…\nText to check: <正文>`）。默认认这几种写法，
# 都不匹配时整个信封当正文并标 bodyFrom=fallback —— **不静默返回 0**。
# 静默返回 0 正是这套回显要防的事：正文为空和「没抠到」会长得一模一样。
DEFAULT_BODY_MARKERS = (
    "Text to check:",
    "待检文本:",
    "待检文本：",
    "Content to check:",
    "Text:",
)

# ⚠ 真踩过的坑：判断「是不是脱敏模式」必须用**精确行匹配**，不能用子串包含 ——
# 系统提示本身就在解释这条规则（"Redact mode: detect_and_redact means …"），
# 用子串会把每个「仅检测」请求都误认成脱敏模式，结论全反。
_REDACT_LINE_RE = re.compile(r"^\s*Redact mode:\s*detect_and_redact\s*$", re.MULTILINE)

# 身份证号（18 位，末位可能是 X）/ 手机号
_ID_CARD_RE = re.compile(r"\b\d{17}[\dXx]\b")
_PHONE_RE = re.compile(r"\b1[3-9]\d{9}\b")

ID_CARD_MASK = "***ID_CARD***"
PHONE_MASK = "***PHONE***"


def is_redact_mode(envelope: str) -> bool:
    """脱敏模式（detect_and_redact）还是仅检测。见上面那条注释里的坑。"""
    return bool(_REDACT_LINE_RE.search(envelope or ""))


def extract_checked_body(envelope: str, marker: str | None = None) -> tuple[str, str]:
    """从信封里抠出待检正文。返回 (正文, 来源标记)

    来源标记：marker=按定位标记抠到的 / fallback=没抠到，整个信封当正文了。
    fallback 会一路带到日志里 —— 「护栏到底拿没拿到正文」这个证据不能被淹掉。
    """
    text = envelope or ""
    markers = [marker.strip()] if (marker and marker.strip()) else list(DEFAULT_BODY_MARKERS)
    for mk in markers:
        idx = text.find(mk)
        if idx >= 0:
            return text[idx + len(mk):].strip(), "marker"
    return text, "fallback"


def build_checker_verdict(body: dict, marker: str | None = None) -> tuple[str, dict]:
    """护栏检查模型的回显式判决。返回 (verdict_json 字符串, meta)

    为什么要回显两个长度：平台把正文包在提示模板里发过来，模板本身几百字。
    **只报信封长度的话，正文为空时它仍然是个大数字**，「护栏到底拿没拿到正文」
    这个证据就被淹了 —— 所以 BODY_LEN 和 ENVELOPE_LEN 必须分开报。

    判决表：
      含 VIOLATION           → verdict=false, categories=[mock_violation]
      含身份证号 + 脱敏模式    → verdict=true,  categories=[id_card], 给 redacted_content
      含身份证号 + 仅检测      → verdict=false, categories=[id_card]
      其余                    → verdict=true
    """
    envelope = all_text(body)
    checked, body_from = extract_checked_body(envelope, marker)

    redact = is_redact_mode(envelope)
    has_violation = "VIOLATION" in checked
    has_id_card = bool(_ID_CARD_RE.search(checked))
    has_phone = bool(_PHONE_RE.search(checked))

    categories: list[str] = []
    redacted: str | None = None

    if has_violation:
        verdict = False
        categories = ["mock_violation"]
    elif has_id_card:
        categories = ["id_card"]
        if has_phone:
            categories.append("phone")
        if redact:
            verdict = True
            redacted = _PHONE_RE.sub(PHONE_MASK, _ID_CARD_RE.sub(ID_CARD_MASK, checked))
        else:
            verdict = False
    else:
        verdict = True

    reason = (
        f"[MOCK-CHECKER] BODY_LEN={len(checked)} ENVELOPE_LEN={len(envelope)} "
        f"BODY_FROM={body_from} REDACT={'on' if redact else 'off'} "
        f"BODY_HEAD={checked[:BODY_HEAD_LEN]}"
    )

    payload: dict = {"verdict": verdict, "reason": reason}
    if redacted is not None:
        payload["redacted_content"] = redacted
    payload["categories"] = categories

    meta = {
        "checkedLen": len(checked),
        "envelopeLen": len(envelope),
        "bodyFrom": body_from,
        "redactMode": redact,
        "verdict": verdict,
        "categories": categories,
        "bodyHead": checked[:BODY_HEAD_LEN],
    }
    return json.dumps(payload, ensure_ascii=False), meta


# ───── 翻译层：指令 → 现有引擎吃的字段 ─────

def _chunk_count(text: str, chunk_size: int) -> int:
    n = max(1, int(chunk_size or 1))
    return (len(text) + n - 1) // n


def apply_smart(route: dict, request_body: dict, path: str) -> tuple[dict, dict]:
    """把请求里的指令翻译成一份被覆盖过的路由副本，后续流程照常跑。

    返回 (生效路由, smart_meta)。生效路由里多一个 `_smart_shape` 供 manager 选 builder。
    """
    eff = dict(route)
    shape = detect_shape(path)
    role = resolve_role(route, path)
    user_text = extract_user_text(request_body)
    directive = parse_directive(user_text)
    mode = directive["mode"]

    stream_opts = request_body.get("stream_options") if isinstance(request_body, dict) else None
    meta: dict = {
        "role": role,
        "shape": shape,
        "mode": mode,
        "directive": directive["raw"],
        # 记的是**网关实际发出的值**，不是 mock 最终怎么回的 —— 用来验流式降级真的发生了没有
        "stream": bool(isinstance(request_body, dict) and request_body.get("stream")),
        "hasStreamOptions": isinstance(stream_opts, dict),
        "includeUsage": bool(isinstance(stream_opts, dict) and stream_opts.get("include_usage")),
        "userTextHead": (user_text or "")[:BODY_HEAD_LEN],
    }

    # 智能应答接管：路由上的静态响应配置一概不生效
    eff["response_mode"] = "default"
    eff["response_type"] = "text"
    eff["finish_reason"] = "stop"
    eff["status_code"] = 200
    eff["stream_mode"] = "auto"
    eff["delay_ms"] = 0

    # ── 护栏检查模型：只回判决，忽略所有 MODE（它不是被测智能体的上游，不该演场景）──
    if role == "checker":
        verdict_json, cmeta = build_checker_verdict(request_body, route.get("smart_body_marker"))
        eff["response_body"] = verdict_json
        # 判决要被网关解析，切碎了没意义，而且流式 JSON 更难排查
        eff["stream_mode"] = "force_json"
        meta.update(cmeta)
        meta["mode"] = None
        eff["_smart_shape"] = shape
        return eff, meta

    # ── 被测智能体的上游 ──
    body = SMART_DEFAULT_BODY

    if mode == "SAY":
        body = directive["say"] or ""
    elif mode == "PII":
        body = SMART_PII_BODY
    elif mode == "HIT":
        body = SMART_HIT_BODY
    elif mode == "EMPTY":
        # 零内容的事件流（只有角色帧 + 结束帧）。这是**合法形态不是异常**，
        # 网关不该当错误处理 —— 现有 _split_chunks("") 正好切出 0 片。
        body = ""
    elif mode == "FILTER":
        body = ""
        eff["finish_reason"] = "content_filter"
    elif mode == "DEFY":
        # 无视 stream=false，照样回事件流 —— 验网关的 fail-closed 守卫
        eff["stream_mode"] = "force_stream"
    elif mode == "SLOW":
        eff["sse_chunk_delay_ms"] = SLOW_CHUNK_DELAY_MS
        # 非流式也要慢，否则量不出「全量缓冲把首字延迟推成完整生成耗时」这个降级代价。
        # 复用 manager 已有的 delay_ms sleep，不用改 manager。
        if not (isinstance(request_body, dict) and request_body.get("stream")):
            eff["delay_ms"] = SLOW_CHUNK_DELAY_MS * _chunk_count(body, route.get("sse_chunk_size") or 1)
    elif mode == "LOOP":
        stage = loop_stage(request_body)
        meta["loopStage"] = stage
        if stage == 1:
            eff["response_type"] = "tool_calls"
            eff["finish_reason"] = "tool_calls"
            eff["tool_calls"] = loop_tool_calls(request_body)
            body = ""
            # 回显用了哪个工具名、是不是从请求里取的 —— 拿它断言「网关执行的是不是同一个工具」
            meta["loopTool"] = eff["tool_calls"][0]["name"]
            meta["loopToolFromRequest"] = bool(
                isinstance(request_body, dict) and request_body.get("tools"))
        else:
            body = SMART_LOOP_FINAL_BODY

    eff["response_body"] = body
    eff["_smart_shape"] = shape
    return eff, meta


# ───── 页面用：指令契约表 ─────
# 前端的「指令契约面板」从这里取，不在 JSX 里再抄一份 ——
# 抄两份的话，改了一边忘了另一边，页面上写的和引擎实际干的就不是一回事了。

DIRECTIVE_CONTRACT: list[dict] = [
    {"key": "", "label": "不带指令",
     "effect": "返回内置的干净长正文，作为对照实验的基线"},
    {"key": "SAY:<文本>", "label": "SAY",
     "effect": "原样回显冒号后面那段（取到行尾），用来只改一个变量做对照"},
    {"key": "MODE:HIT", "label": "HIT",
     "effect": "输出含 VIOLATION 关键词，不依赖大模型的确定性对照"},
    {"key": "MODE:PII", "label": "PII",
     "effect": "输出含身份证号+手机号（请求里没有），验护栏查的是输出而不是输入"},
    {"key": "MODE:EMPTY", "label": "EMPTY",
     "effect": "零内容事件流（只有角色帧+结束帧）—— 合法形态，网关不该当错误"},
    {"key": "MODE:FILTER", "label": "FILTER",
     "effect": "空回复 + finish_reason=content_filter，上游侧内容过滤的形态"},
    {"key": "MODE:DEFY", "label": "DEFY",
     "effect": "无视 stream=false 照样回事件流，验网关 fail-closed"},
    {"key": "MODE:SLOW", "label": "SLOW",
     "effect": f"每片 sleep {SLOW_CHUNK_DELAY_MS}ms，非流式也按分片数累计 —— 量「全量缓冲把首字延迟推成完整生成耗时」这个降级代价"},
    {"key": "MODE:LOOP", "label": "LOOP",
     "effect": "第一轮回 tool_calls，收到 role=tool 后回终局（终局含 VIOLATION，好验护栏有没有介入终局）"},
]
