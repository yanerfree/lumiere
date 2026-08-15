# LLM Mock 智能应答 —— 可控假上游的指令契约

## 这是什么，为什么要有

被测系统是 AI 网关。验它的护栏 / 脱敏 / fail-closed / 计费统计，都需要一个**行为可精确控制的假上游**。
用真上游：慢、费钱、不确定，挂了还分不清是网关的锅还是模型的锅。

关键设计是**行为由请求控制**：场景开关写在请求正文里（`MODE:PII`、`SAY:你好`），不写在服务端配置里。
差别不是风格问题 —— 配置在服务端的话，每换一个场景都要改配置、等下发、重来一遍，**对照实验根本做不起来**。

## 跟「条件应答规则表」怎么分工

规则表是默认路径，可见可编辑，能覆盖大部分「问什么答什么」。
但它的响应体是**静态串**，下面这几样表达不了，才归智能应答：

| 做不到的 | 为什么 |
|---|---|
| 护栏回显判决 | 响应里要带「本次待检正文有多长、开头是什么」，必须现算 |
| `MODE:LOOP` | 要跨轮判断（消息里有没有 `role=tool`） |
| `MODE:SLOW` | 非流式也要按分片数累计延迟 |
| 三种协议形状 | `/completions` 用 `text`、`/messages` 是 Anthropic 事件序列 |

**一条路由只能二选一**：智能应答开着时规则表整个不参与匹配。
两套都生效的话，「这次到底是谁决定了响应」就只能靠猜。

> ⚠ 历史：`smart_response` 这个名字曾经是个黑盒 bool（关键词和响应写死在引擎里、页面上看不见改不了），
> 被 `n8g9h0i1j2k3` 拆成了现在的规则表。这一版不是它的回归 ——
> 页面上有指令契约面板（看得见它会干什么），还有「展开成规则」按钮能把控制权拿回来。

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
| `MODE:LOOP` | 第一轮回 `tool_calls`，收到 `role=tool` 后回终局（终局含 VIOLATION） | 网关把中间迭代强制成非流式、只有终局是流式的，护栏是否介入终局只能这样验 |

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

## 逃生口：展开成规则

不想被固定契约绑着，就点「展开成规则」：把 `SAY` / `HIT` / `PII` / `EMPTY` / `FILTER` / `DEFY`
这 6 条落地成普通的条件应答规则并关掉智能应答，从此可看可改可删。
`LOOP` / `SLOW` / 护栏回显展开不了（响应内容依赖请求内容）—— 弹窗会明说，要它们就重新打开智能应答。

## 相关代码

| 位置 | 作用 |
|---|---|
| `backend/app/services/llm_mock_smart.py` | 指令解析、护栏回显、`apply_smart` 翻译层、契约表 |
| `backend/app/services/llm_mock_engine.py` | 三种协议形状的响应构建（chat / text_completion / Anthropic） |
| `backend/app/services/llm_mock_manager.py` | 接线：智能应答开着就旁路条件应答，按形状选 builder |
| `backend/app/api/llm_mock.py` `GET /smart-contract` | 契约的**单一真源**，前端指令面板和「展开成规则」都从这里取 |
| `backend/tests/test_llm_mock_smart.py` | 52 条，重点守脱敏行匹配、两个长度、以及「没开智能应答时行为不变」 |

`apply_smart` 的实现取向：**不自己造响应**，而是把指令翻译成现有引擎已经吃的字段
（`response_body` / `finish_reason` / `response_type` / `stream_mode` / `delay_ms` …），让现有链路照常跑。
流式切片、usage 帧、响应头、Token 估算一行都不用重写，也不会长出两条会慢慢走偏的响应生成路径。
