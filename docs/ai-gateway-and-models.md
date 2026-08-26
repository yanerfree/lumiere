# AI 网关、限流与模型维护（运维/开发必读）

> 这份文档是「踩过的坑 + 怎么做」的唯一事实来源。改 AI 相关代码或换模型前先读这里，别重新踩一遍。
> 最后更新：2026-07-29

## 一、网关是什么

`AI_BASE_URL=http://192.168.51.10:8080/v1`（hoopa 网关）本质是 **Anthropic 原生 Messages API + 绑 Claude Code 凭证**：

- 不带 `User-Agent: claude-cli/*` → `GW-1006 client_not_allowed`
- 带 UA 后还要求 `anthropic-version: 2023-06-01` 头
- 拉模型清单 `GET /models` 需要 `x-api-key` + `anthropic-version` + claude-cli UA（只给 Bearer 会 403）

## 二、三条通道（429 的根源就在这张表）

网关对 **SDK 直连**限流，对**真 claude CLI 客户端**给正常配额。所以 429 **不是配额用光，是通道选错**。

| 通道 | 实现 | 会不会 429 |
|---|---|---|
| 真 claude CLI 直驱 | `app/services/ai/cli_agent.py` → `claude --print`（`GATEWAY_BASE` 不带 `/v1`） | 不会 |
| claude-proxy | `claude-proxy/index.mjs`，监听 **38210**，spawn 真 CLI，对内讲 OpenAI `/v1/chat/completions`（支持 stream + tools） | 不会 |
| SDK 直连 `/v1` | `app/services/ai/llm_client.py`（**全部文本类模块走这条**） | **会** |

实测佐证（同一时刻、同一模型）：HTTP `/v1/messages` 返回 `429 [hoopa] the upstream provider is temporarily rate-limiting`，而 `claude --print --model <同一模型>` rc=0 正常返回。

## 三、429 怎么绕（已实现，别删）

全在 `app/services/ai/llm_client.py`，两层缺一不可：

1. **退避重试**：429 + 5xx 重试 4 次（首发 + 3），优先尊重 `Retry-After`，否则指数退避 + 抖动（抖动是为了防多任务同时重试再次撞限流）。非流式和流式都覆盖。
2. **降级到 CLI 通道**：重试仍 429 → 打 claude-proxy。地址取 `settings.ai_proxy_base_url or ai_ui_base_url`（`.env` 不配也生效，因为 `AI_UI_BASE_URL` 已指向 38210）。`provider == "anthropic"` 不降级（proxy 只讲 OpenAI 协议）。

两个必须知道的细节：

- **降级通道单独放宽超时**（`_PROXY_TIMEOUT = 600`）：proxy 每次 spawn 真 CLI 冷启（实测非流式 ~36s，大 prompt 更久），沿用主路 `ai_timeout_seconds`（默认 120）会把兜底也拖超时，**等于没兜底**。
- **流式只在首字节前重试**：已经开始吐 delta 就不能重试，否则重复输出。

**为什么必须做在 `llm_client` 这一层**：`app/services/scenario_gen/llm_structured.py` 对 `LLMError` 是 `raise` 不重试（注释写着「网络/鉴权错误直接抛」），一个 429 会把整条场景建模 / 用例展开打死。**不要把重试挪到调用方。**

**怎么验证**：起一个恒返 `429` + `Retry-After: 1` 的本地桩当 `base_url`，断言三件事——① 桩被打满 4 次后降级到 proxy 并拿到真实回答 ② 流式同样 ③ 把兜底通道清空后必须老实抛 429、不能吞异常。

> 历史坑：`.env` 里曾注释「仅偶发限流→已加重试」，但那个 `max_retries=5` 只存在于旧 langgraph 引擎 `mcp_agent.py`，文本主路当时是裸奔的。**别信注释，去看代码。**

## 四、新模型怎么维护进来

**主路零代码**：模型下拉是 `GET /api/ai-capabilities/models` 动态代理网关 `/models`，新模型上线自动出现。换模型是页面/DB 操作（「AI 服务配置 → AI 能力→模型」，接口 `PUT /api/ai-capabilities/bindings/{id}`，内置档位只允许改 model），**不用改代码、不用发版**。

只有这几处是硬编码，**网关拉不到清单时才会用到**，别忘了同步：

| 位置 | 作用 |
|---|---|
| `app/api/ai_capabilities.py` → `_PRESET_MODELS` | 拉不到 `/models` 时的兜底清单，**最容易过期** |
| `app/services/ai_capabilities.py` → `CATEGORY_META[*].defaultModel` / `recommend` | 面板展示与推荐语（真正的播种在 alembic 迁移里） |
| `app/config.py` → `ai_model` | `.env` 兜底默认 |
| `app/services/ai/cli_agent.py` / `mcp_agent.py` | 模型兜底链末端 |
| `app/services/llm_mock_manager.py` | LLM Mock 的假模型清单。chat 部分是装饰性的；**embedding 部分不是** —— 被测网关要在 `/v1/models` 里看到 embedding 模型，才认这个 Provider 能做语义缓存 |

`app/api/ai_config.py` 里连通性探针固定用 haiku 发 `"hi"`，是最省的选择，不用跟着换。

### 红线：接口路径只认裸模型 ID

`claude-opus-5[1m]` 这种长上下文后缀写法**只有 Claude Code CLI 客户端自己认**（它在客户端解析）。打到 `/v1/messages` 会 **404 `not_found_error`**（对照：裸 ID 拿到 429 说明 ID 合法）。而且 Opus-5 本身 1M 上下文就是默认值，**不需要任何后缀**。

**不要把 `[1m]` 填进模型档位。**

## 五、UI 脚本生成用哪个模型（实测数据，2026-07-29）

同一条用例（`TC-SVC-00001` 创建并删除项目，打 Lumiere 自己的 5173）、同一环境，只换模型，共 10 次真实生成：

| 模型 | 通过率 | 均耗时 | 耗时区间 | 单价（输入/输出 per MTok） |
|---|---|---|---|---|
| `claude-sonnet-4-6` | **1/2** | 315s | — | $3 / $15 |
| **`claude-sonnet-5`** ← 当前档位 | **4/4** | **178s** | 138–209s（稳） | $3 / $15 |
| `claude-opus-5` | 4/4 | 246s | 148–414s（抖 2.4 倍） | $5 / $25 |

结论：**默认用 `claude-sonnet-5`**。opus-5 能过但均耗时 +38%、单价 +67%、抖动大，只在 sonnet-5 反复失败的硬用例上临时升级。**不要因为「新旗舰更强」就往 `ui_script` 档位上切 opus。** Haiku 会导致工具调用循环失败，不要选。

## 六、长驻服务依赖

| 服务 | 端口 | 谁需要它 |
|---|---|---|
| claude-proxy | 38210 | 429 降级通道；旧 langgraph 引擎 |
| playwright-mcp | 38931 | UI 脚本生成（真 CLI 直驱 MCP）。host 只认 `localhost` |

启动：`deploy/start-ai-services.sh`（幂等，含健康检查）。**proxy 挂了 → 限流降级无处可去，只剩重试。**

后端必须跑 **8756** 端口，否则前端全 502、看起来像服务挂了。

## 七、已知遗留

- **UI 脚本 flaky 的真瓶颈不在模型**：失败案例根因是生成的脚本对 **antd Popconfirm 动画**不设防——`getByRole('button', { name: 'OK' })` 能解析到元素，但一直 `element is not stable`，点击重试到 120s 超时。修 SKILL 的选择器纪律（等动画结束 / `force: true`）比换模型收益大。**未做。**
- **探索期数据不会被清理**：生成阶段 agent 在被测系统里真实造的数据（如 `dogfood_*` 项目）不受脚本 `cleanup` 管辖（cleanup 只在脚本运行时生效），会残留，需要手工清。
- 两个长驻服务仍是 `nohup` 临时进程，**没有 systemd / compose 托管**。
