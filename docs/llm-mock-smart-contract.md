# LLM Mock 智能应答 —— 可控假上游的指令契约

## 这是什么，为什么要有

被测系统是 AI 网关。验它的护栏 / 脱敏 / fail-closed / 计费统计，都需要一个**行为可精确控制的假上游**。
用真上游：慢、费钱、不确定，挂了还分不清是网关的锅还是模型的锅。

关键设计是**行为由请求控制**：场景开关写在请求正文里（`MODE:PII`、`SAY:你好`），不写在服务端配置里。
差别不是风格问题 —— 配置在服务端的话，每换一个场景都要改配置、等下发、重来一遍，**对照实验根本做不起来**。

## 开 / 关

一条路由上只有这一个「按请求内容决定回什么」的开关：

- **关着** —— 老老实实的静态 mock，所有请求都回路由上配的那段响应内容。
- **开着** —— 本模块接管 **响应内容 / 状态码 / 响应类型 / 结束原因 / 响应模式 / 响应流式 / 延迟**，
  这些由请求里的指令决定，页面上跟着**隐藏**（不是置灰）—— 摆一堆改了不生效的框只会误导人。

**开着时这几样指令管不着、照常生效，所以必须留在页面上**（藏了就是反过来的坑：
生效了但你看不见也改不了）：

| 仍生效 | 为什么不能藏 |
|---|---|
| 几字一片（`sse_chunk_size`） | 分片数本身就是被验证的指标 —— 整段一次吐出会掩盖护栏在分片边界上的问题 |
| SSE 间隔（`sse_chunk_delay_ms`） | 同上。唯一例外是 `MODE:SLOW`，它把间隔顶成 250ms 好和对接方脚本对齐 |
| Token 模式 / 自定义 token | **测网关的计费与配额统计算得对不对**，靠的就是自己指定用量 |
| 模型模式 / 自定义模型 | 测模型映射改写 |
| 路径 / 方法 | 协议形状和角色按路径判 |

> ⚠ 两段历史，别再走回去：
> ① `smart_response` 曾经是个黑盒 bool（关键词和响应写死在引擎里、页面上看不见改不了），
>    被 `n8g9h0i1j2k3` 拆成了可见的 `match_rules` 规则表。
> ② 规则表和本模块功能重叠 —— 一条路由上两套「按请求内容决定回什么」，
>    「这次到底是谁决定了响应」只能靠猜 —— 已在 `zz6dropmr` 删掉，只留智能应答。
>
> 所以这一版必须守住「不是黑盒」：页面右侧的指令契约面板列全了它认哪些指令、各自返回什么，
> 数据来自后端 `GET /smart-contract`（单一真源），不在 JSX 里另抄一份。

## 指令表（写在最后一条 user 消息里）

| 指令 | 行为 | 为什么需要它 |
|---|---|---|
| 不带指令 | 内置的干净长正文 | 对照实验的基线；够长能切多片，且保证不误触护栏 |
| `SAY:<文本>` | 原样回显冒号后面那段（取到**行尾**） | 精确控制输出，做「只改一个变量」的对照 |
| `MODE:HIT` | 输出含 `VIOLATION` | 不依赖大模型的确定性对照，先排除「是不是模型判飘了」 |
| `MODE:PII` | 输出含身份证号+手机号 | **请求里只有四个字**，敏感数据只在输出 —— 护栏若查输入会判「无 PII」并原样放行 |
| `MODE:EMPTY` | 零内容事件流（只有角色帧+结束帧） | 这是**合法形态不是异常**，网关不该当错误处理 |
| `MODE:FILTER` | 空回复 + `finish_reason=content_filter` | 上游侧内容过滤的形态；只清空正文不改 finish_reason，那个形态就是假的 |
| `MODE:DEFY` | 无视 `stream:false` 照样回事件流 | 验网关的 fail-closed 守卫 |
| `MODE:SLOW` | 每片 250ms，**非流式也按分片数累计** | 非流式不慢的话，量不出「全量缓冲把首字延迟推成完整生成耗时」这个降级代价 |
| `MODE:LOOP` | 第一轮回 `tool_calls`（**工具名取自请求的 `tools`**，优先听 `tool_choice`），收到 `role=tool` 后回终局（终局含 VIOLATION） | 网关把中间迭代强制成非流式、只有终局是流式的，护栏是否介入终局只能这样验 |

> ⚠ LOOP 的工具名**不能写死**：网关是拿模型返回的工具名去真执行的（POST 给 MCP 执行端点）。
> 名字不在请求的 `tools` 里，执行端点报错，网关把 "tool execution failed" 当工具结果塞回模型 ——
> loop 照样转两轮，迭代计数 / 逐轮日志 / 终局是否流式都还能测，但**真实的工具执行链路
> （MCP 调用 → 结果回填 → 工具结果缓存）测不了**。入参按工具自己的 schema 只填 `required`，
> 多填可能撞上 `additionalProperties:false`。日志里 `smartMeta.loopTool` 回显用了哪个名字。

同时出现 `SAY:` 和 `MODE:` 时 SAY 优先。协议形状按**路径**判，入参三种写法都能读到指令
（OpenAI 字符串 `content` / Anthropic block 数组 / legacy `prompt`）。

## 护栏检查模型（checker 角色）

角色下拉选「护栏检查模型」，或路径里带 `/checker`、`/guard`。它不演场景，只回判决：

```json
{
  "verdict": true,
  "reason": "[MOCK-CHECKER] BODY_LEN=42 ENVELOPE_LEN=863 BODY_FROM=marker REDACT=on BODY_HEAD=客户身份证号是…",
  "redacted_content": "客户身份证号是 ***ID_CARD***",
  "categories": ["id_card"]
}
```

**为什么两个长度要分开报**：平台把正文包在提示模板里发过来，模板本身几百字。
只报 `ENVELOPE_LEN` 的话，**正文为空时它仍然是个大数字**，「护栏到底拿没拿到正文」这个证据就被淹了。

**抠不到定位标记时不静默返回 0**：整个信封当正文，并标 `BODY_FROM=fallback`。
默认认 `Text to check:` / `待检文本:` / `Content to check:` / `Text:`，模板不一样就在路由上填「待检正文定位标记」。

判决表：

| 待检文本 | verdict | categories | redacted_content |
|---|---|---|---|
| 含 `VIOLATION` | false | `mock_violation` | — |
| 含身份证号 + 脱敏模式 | true | `id_card` | 替换为 `***ID_CARD***` |
| 含身份证号 + 仅检测 | false | `id_card` | — |
| 其余 | true | — | — |

> ⚠ **判「是不是脱敏模式」必须精确行匹配** `Redact mode: detect_and_redact`，不能用子串包含 ——
> 系统提示本身就在解释这条规则，用包含会把每个「仅检测」请求都误认成脱敏模式，**结论全反**。
> 实现见 `llm_mock_smart._REDACT_LINE_RE`，反例测试见 `test_脱敏模式_系统提示在解释规则时不能误判`。

## 证据在哪看

请求日志详情里的「智能应答判定」块，以及 MCP `tb_llm_mock_requests` 返回的 `smartMeta`：

- `mode` —— 命中了哪条指令
- `stream` —— **网关实际发出的值**（不是 mock 最终怎么回的）。流式降级有没有真发生，只能看它
- `hasStreamOptions` / `includeUsage` / `loopStage`
- checker 角色另有 `checkedLen` / `envelopeLen` / `bodyFrom` / `redactMode` / `verdict` / `categories`
- `aborted` —— 客户端中途断连。**护栏拦截就是这个形态**（网关判定要拦，直接掐掉与上游的连接），
  这种请求照样留日志，响应体末尾会标「流未发完」，据此能查出「被拦下来时上游已经发出去多少片」。

> 实现上有个坑：断连时当前任务正在被取消，**不能在 `finally` 里直接 `await` 数据库** ——
> 取消会把 asyncpg 连接掐在半路，坏连接回到池子里，之后别的请求全报 `connection is closed`
> （实测踩过，整个后端跟着挂）。所以那条日志是甩给独立任务写的，见 `_spawn_log_task`。

## 排障端点（mock 端口上，不鉴权）

给「在平台外面用 curl 跑对照实验」的人自助查，不用登录页面、也不用 MCP：

| 端点 | 用途 |
|---|---|
| `GET /__log?limit=50&path=…` | 最近若干条请求的**摘要**：`stream` / `hasStreamOptions` / `includeUsage` + 整个 `smart` 判定块 |
| `POST /__reset?path=…` | 清请求记录。断言「上游只收到 N 次」之前先清，否则上一轮的记录会让断言**假过** |
| `GET /health` | 探活 |

> ⚠ `__log` **只回解析后的摘要，不回请求头和完整报文**。这个端口不鉴权，而请求头里恰恰是
> 最敏感的东西 —— 网关注入的上游 API Key 就在 `Authorization` 里，摊在开放端口上等于把凭据发出去。
> 要看完整报文走平台「请求日志」页或 MCP `tb_llm_mock_requests`，那两条路都要登录/授权。

`__log` 里的 `stream` 记的是**网关实际发出的值**（请求体里的 `stream`），不是 mock 最终回的形态 ——
**验流式降级只能看这一格**：客户端传 `true`、上游收到 `false` 才叫降级生效。
拿 `MODE:DEFY` 就能自证：发 `stream:false`，mock 故意返事件流，日志里仍是 `stream=false`。

## 探活

`GET /health` → `{"status":"ok","service":"llm-mock","port":28100}`。
它**不进请求日志** —— 否则「上游只应收到 1 次请求」这类断言会被探活污染。
另有 `GET /v1/models`（网关建 Provider 时会探测，不实现连 Provider 都创建不出来）。

## 想要「自定义关键词 → 自定义回复」怎么办

没有了 —— 这是删掉条件应答规则表时一并放弃的能力。指令是一份**与对接方共享的固定契约**，
改了两边就对不上、对照实验不成立，所以它不做成可编辑的。
真需要一句特定的话，用 `SAY:<文本>` 让它原样回显；需要一条固定不变的响应，
就关掉智能应答，把那段话填进「响应内容」。

## 相关代码

| 位置 | 作用 |
|---|---|
| `backend/app/services/llm_mock_smart.py` | 指令解析、护栏回显、`apply_smart` 翻译层、契约表 |
| `backend/app/services/llm_mock_engine.py` | 三种协议形状的响应构建（chat / text_completion / Anthropic） |
| `backend/app/services/llm_mock_manager.py` | 接线：智能应答开着就调 `apply_smart`，再按形状选 builder |
| `backend/app/api/llm_mock.py` `GET /smart-contract` | 契约的**单一真源**，前端的指令契约面板从这里取 |
| `backend/tests/test_llm_mock_smart.py` | 51 条，重点守脱敏行匹配、两个长度、以及「没开智能应答时就是静态响应」 |

`apply_smart` 的实现取向：**不自己造响应**，而是把指令翻译成现有引擎已经吃的字段
（`response_body` / `finish_reason` / `response_type` / `stream_mode` / `delay_ms` …），让现有链路照常跑。
流式切片、usage 帧、响应头、Token 估算一行都不用重写，也不会长出两条会慢慢走偏的响应生成路径。
