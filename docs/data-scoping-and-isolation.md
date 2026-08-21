# 数据归属与隔离：MCP Key 管得住工具，管不住数据

**状态：§1–§4 已实现并真跑验证（2026-08-21）。§5 未做，且请先读它 ——
那里有一条「看着该做、其实是假隔离」的，别顺手做了。**

已落地的东西在：`app/main.py` 的 `MCPAuthMiddleware`、`app/mcp/middleware.py` 的
`_OWNER_SQL`、`app/deps/scope.py`、迁移 `zzo0envproj`。封样测试：
`tests/test_mcp_data_scope.py`（24 条）、`tests/test_env_project_scoped.py`（15 条）。

工具范围（哪些 tb_* 露给这把 Key）已经实现且工作正常，在
[`backend/app/mcp/middleware.py`](../backend/app/mcp/middleware.py)。这份文档管的是
**数据范围**：那把 Key 能读到、能改到哪些项目的东西。

---

## 0. 一句话

`mcp_api_keys.project_id` 只决定「哪些工具出现在 tools/list」，**跟数据一点关系没有**。
给 CC 一把 A 项目的 Key，它能列出全部项目、能读能改 B 项目的用例。

而且当下 `MCP_API_KEY` 没设，**不带 Key 也能连**，上面那些照样干。

---

## 1. 现状实测

后端 MCP 监听 **18800**（不是 8756，8756 是主 API）。下面这串不带任何
Authorization 就能跑通：

```bash
SID=$(curl -s -D h.txt -o /dev/null -X POST http://127.0.0.1:18800/mcp/ \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"p","version":"1"}}}';
  grep -i 'mcp-session-id' h.txt | tr -d '\r' | awk '{print $2}')

curl -s -X POST http://127.0.0.1:18800/mcp/ -H "mcp-session-id: $SID" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# → 返回全部 6 个项目
curl -s -X POST http://127.0.0.1:18800/mcp/ -H "mcp-session-id: $SID" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"tb_list_projects","arguments":{}}}'

# → 拿上一步任意项目的 id，照样列得出它的分支，再往下就是用例
curl -s -X POST http://127.0.0.1:18800/mcp/ -H "mcp-session-id: $SID" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"tb_list_branches","arguments":{"project_id":"<别的项目>"}}}'
```

三处根因，各自独立：

| # | 位置 | 问题 |
|---|---|---|
| 1 | [`main.py`](../backend/app/main.py) `MCPAuthMiddleware` | 没有 bearer 且 `MCP_API_KEY` 未设 → **直接放行**。平台监听 `0.0.0.0`，同局域网谁都能连 |
| 2 | [`middleware.py`](../backend/app/mcp/middleware.py) | Key 的 `project_id` 只用来查 `projects.mcp_allowed_tools`。没有 `current_caller_project_id()` 这种东西 |
| 3 | 全部 52 个 tb_* 工具 | `project_id`/`branch_id`/`case_id` 都是**调用方传进来的入参**，直接拿去查库。既不跟 Key 的项目比对，也不走 `require_project_role`/[`deps/scope.py`](../backend/app/deps/scope.py) |

三条都已修（2026-08-21）：① 删掉那条放行分支，`env_key` 降级成可选的额外通道，
不再是"设了才开始检查"的总开关；② `_lookup_key()` 多带出 `project_id`；
③ `on_call_tool` 里按 `_OWNER_SQL` 反查入参归属。

`tb_list_projects` 更直接：`select(Project)` 无 where，描述里还写着
「Claude Code 用于确定要操作的目标项目」—— 等于明确请它自己在 6 个项目里挑。

**HTTP 侧这个坑早就堵过。** `deps/scope.py` 开头那段记着实测结果：
只属于 A 项目的 tester 能 `PUT` 掉 B 项目的用例标题，**真改了数据、靠审计日志才还原回来**。
MCP 侧是同一个坑，而现在写库的主力恰恰是 CC。

**顺带一条：MCP Key 打 `/api/*` 是 401。** `deps/auth.py` 的 `get_current_user`
只认 JWT，压根不查 `mcp_api_keys` 表。这不是隔离生效，是那把 Key 在 REST 上根本不被识别。
别把它当成"接口已经隔离了"的证据。

---

## 2. 风险定性：不在泄露，在污染

这是内网自用平台，「有人偷数据」不是主要威胁。真正会发生的是：

- **CC 挑错项目往里写。** [`middleware.py`](../backend/app/mcp/middleware.py) 开头那段注释
  自己讲清了道理：instructions 里的引导是**软约束、模型不一定听**，所以工具范围做成了硬约束。
  **项目归属现在恰好还是软约束** —— 同一个理由，没往下走一步。
- 库里有个 `tb-self-shared-project`，描述写着「自测链共用的长期项目（只读引用，故意不清理）」。
  CC 手滑往它里面写一批用例，得靠审计日志还原。

**唯一的兜底刚补上：** MCP 侧的审计上下文（`actor_type="mcp"` + Key 名快照）是
2026-08-21 那批改动才加的，之前那段时间 CC 的操作在「操作日志」里操作人是「-」。
所以污染现在**事后追得回来**（能追到是哪台 CC），但那是兜底，不是隔离。

---

## 3. 方案：一道中间件，51 个工具一个都不用改

挂在 `ToolScopeMiddleware.on_call_tool`，跟工具范围校验并排。`_lookup_key()`
本来就带 30s 缓存，多带出一个 `project_id` 不增加查询。

**归属反查表** —— 按入参名反查它属于哪个项目，跟 Key 的 `project_id` 比：

| 入参 | 反查路径 |
|---|---|
| `project_id` | 就是它自己 |
| `branch_id` | `branches.project_id` |
| `case_id` / `case_ids` | `cases.branch_id` → `branches.project_id` |
| `folder_id` | `case_folders.branch_id` → `branches.project_id` |
| `plan_id` | `plans.project_id` |
| `report_id` | `test_reports.project_id`（注意可为 NULL = 历史数据） |
| `scenario_ids` | `api_test_scenarios.branch_id` → `branches.project_id` |
| `env_id` | **等 §4 落地后**才能查（现在 `environments` 没有 project_id） |

不符就当**「不存在」**，照 `deps/scope.py` 的口径 —— 那里写了为什么不返 403：
403 等于告诉对方「这个 id 存在，只是你没权限」，本身就是信息泄露。

另外两处要单独改（它们没有 id 入参，反查表管不到）：

- `tb_list_projects` → 按 Key 的项目过滤。
- `tb_list_environments` → 同上，**§4 落地之后**。

### 存量 Key 的口径

`project_id` 为 NULL 的存量 Key **保持不限制**，跟现有
[`pick_scope()`](../backend/app/mcp/middleware.py) 那条判据一致 ——
「判据是有没有归属项目，不是项目范围真不真」。

**上线前必须先把在用的 Key 归到项目**，否则要么它突然失去所有跨项目能力（如果改成 fail-closed），
要么校验对它形同虚设（保持不限制）。两种都得先确认库里 Key 的 `project_id` 现状：

```sql
select name, key_prefix, project_id, last_used_at from mcp_api_keys where is_active;
```

---

## 4. 环境改项目级（已完成）

### 为什么 —— 数据已经在替你证明

库里 8 个环境和各自的变量数：

| 环境 | 变量数 | 看着像 |
|---|---|---|
| `stoa` | 16 | 项目专属 |
| `uag` | 15 | 项目专属 |
| `测试平台self` | 7 | 项目专属 |
| `api-test-local` | 3 | 项目专属 |
| `testing` | 6 | 种子数据 |
| `production` | 4 | 种子数据 |
| `staging` | 4 | 种子数据 |
| `development` | 4 | 种子数据 |

前四个是**按项目取的名**。大家已经在用「名字里塞项目前缀」手动模拟隔离了 ——
这就是 scoping 放错层的典型信号。

### 三处硬成本

1. **`environments.name` 是全局 unique**，得换成 `(project_id, name)` 复合 unique。
   不改的话两个项目都想有个 `staging` 就撞。
2. **存量 8 条要分配归属。** 四个项目专属的直接归；四条种子数据（共 18 个变量）
   —— **复制到每个项目 / 还是留一份未归属当模板，这个待定，需要拍板。**
3. **引用面要一起收。** `plans.environment_id` 和 `test_reports.environment_id`（两者都可 NULL）
   带 FK 指向 `environments`，改完要防「A 项目的计划挂 B 项目的环境」。正好并进 §3 那张反查表。
   执行链上还有一批只在代码里传 `env_id` 的（`execution_service` / `run_context_service` /
   `variable_service` / `token_service` 等，共 30 多个后端文件 + 13 个前端文件碰 env），
   这些没有 FK 兜着，得逐个走一遍。

### 一并决定的

- **`global_variables` 也改了项目级** —— 迁移 `zzp0gvarproj`。**这里推翻了本文档初稿的判断。**
  初稿说「留全局，它就是所有环境都注入的兜底层」；看数据就知道错了，5 条全是
  **按项目调**的旋钮：`API_TIMEOUT` / `BASE_WAIT` / `LOG_LEVEL` / `RETRY_COUNT` /
  `TEST_LANGUAGE`。尤其 `TEST_LANGUAGE`（"测试跑哪种语言"）是被测系统的属性，
  一个项目跑中文另一个跑英文是常态，全平台一个值根本不够用。

  **语义没变，只换了作用域**：原来是「全平台所有环境的兜底层，环境变量可覆盖」，
  现在是「本项目所有环境的兜底层，环境变量可覆盖」。覆盖关系一个字没动。

  回填是**复制给每个项目**（5×6=30 行），不是归给某一个 —— 改动前所有项目看到的
  都是这 5 条，各存一份才是行为不变的选择；随便归一个会让其余项目凭空丢默认值。

  连带修掉三处会静默串项目的查询：`put_variables` 那句无条件
  `delete(GlobalVariable)`（**任何项目点一次保存就清空全平台**）、
  `build_run_env`（A 项目的执行会被 B 项目的 `TEST_LANGUAGE` 覆盖）、
  `get_merged_variables` 和 `tb_list_global_data`。
- **`notification_channels` 留全局。** 通知渠道是平台设施，不是项目资产。
  这条是真该留的，别跟上面那条搞混。
- **[`api/variables.py`](../backend/app/api/variables.py) 补了项目归属校验。**
  它 19 个端点都有 `get_current_user`（认证没问题），但没有任何项目校验 ——
  改项目级之后不补，「项目级」就只是列表上看着分开了。环境那批路由挪到了
  `/api/projects/{project_id}/environments`，每条挂 `require_project_role`，
  带 `{env_id}` 的还走 `verify_path_scope`（`env_id` 已并进它的归属链）。

### 落地时补漏的一处：**body 里的 env_id**

路径上那两道校验**都管不到请求体**。四处从 body 收 env 的地方原本都没验，
不验的话本项目的执行能挂上别的项目的环境，注进去的是别人的 BASE_URL 和账号：

| 路由 | 参数 |
|---|---|
| `POST …/api-tests/run`（接口场景批量执行） | `envId` |
| `POST …/api-tests/generate`（AI 生成接口场景） | `envId` |
| `POST …/plans`（建计划） | `environmentId` |
| `POST …/execute-adhoc`（批量执行用例） | `envId` |

统一走 `environment_service.assert_env_in_project()`，口径和 `deps/scope.py`
一致（返 404 不返 403）。封样测试会扫所有路由：**新加一个从 body 收 env_id
的路由而忘了验，`test_body里的env_id都验了归属` 会红。**

这里踩过一个只有真跑才暴露的坑：`api_test.py` 原本把 `environment_service`
import 在 `if body.env_id:` **之内**，校验插在它前面就成了 `UnboundLocalError` →
500。结构测试只看得见"字符串在不在"，看不见它能不能跑。
### 新项目的默认数据

环境和全局变量项目化之后，**新建的项目是空的** —— 以前那 4 个环境和 5 个全局变量
是全平台共用的，新项目一进来就有。所以 `create_project` 会铺一份默认
（`app/services/project_defaults.py`，加默认值改这一个文件）：

- 4 个环境：`development` / `testing` / `staging` / `production`
- 5 个全局变量：跟老库那 5 条的键和默认值一致（同一个 key 在不同项目里默认值不该不一样）

**默认环境故意不带任何变量。** 老库那 4 条种子环境带着
`BASE_URL=https://api.example.com`、`ADMIN_PASSWORD=123456` 这类演示值，
照抄给每个新项目等于预埋一份假凭证 —— **假凭证比没凭证更坏**，
它让「忘了填」看起来像「填过了」。环境页上本来就有「常用变量参考」提示该填哪些。

### 压测**没有**跟着改（推翻了本文档初稿的建议）

初稿说「压测零 FK、4 条数据，跟环境一起改成本最低」——**那个判断是错的**，
只看了模型没看它在产品里的位置。压测在 `/tools/load-test`，跟 Mock、代理抓包
同一档，[`App.jsx`](../frontend/src/App.jsx) 的菜单注释自己写着
「Mock 也在这一档：造可控上游本来就是为了让被测系统跑起来，跟压测、抓包是一类事」。
4 条数据全叫「新场景」，是点着玩留下的。

按 §5 同一条理由（真·全局工具别加 project_id）它该留全局。改它等于把页面搬进
项目壳，那是 UI 重构，不是归属修复。

---

## 5. Mock 不改项目级 —— 加 `project_id` 是假隔离

**看着该做，别做。** 理由不是"工作量大"，是**做了也不解决问题**。

Mock 的冲突域是**端口 + path**，不是数据库行的归属。查找就一句：

```python
select(MockRoute).where(MockRoute.path == p)  # ... .first()
```

给行加 `project_id` 不改变任何事：两个项目配同一个 path，照样互相覆盖，
而且 `.first()` 拿到哪条取决于 `sort_order`/`created_at` —— **是偶发的**。
CLAUDE.md 里那句「直接占用 `/v1/chat/completions` 会被拒，你配成 429 别人就跟着挂、还偶发」，
说的就是这个机制。数据库里分开了，运行时还是一张共享路由表。

**而且已经在发生：** 库里 13 条 `mock_routes` 只有 11 个不同 path，重了两组 ——
`/openai/v1/chat/completions`（两条都叫「Azure Chat (api-version=v1)」）和
`/v1/embeddings`（两条都叫「向量 Embeddings」）。`path` 上**连 unique 约束都没有**，
重了没人报错。

所以 Mock 该做的是两件跟隔离无关的事：

1. **`path` 加 unique 约束**（llm `mock_routes`；`api_mock_routes` 按 `(method, path)`）。
2. **把「path 必须带前缀」从文档约定变成服务端强制** —— 按项目/用例派生前缀，
   不带就拒。别指望 CC 自觉，那又是一条软约束。

这两件加起来比加 `project_id` 便宜，解决的是真问题。
`custom_mock_presets`、`grpc/tcp/udp/ws_mock_*` 同理，全部留全局。

---

## 6. 全库归属盘点

按「顶层实体有没有归属」分。子表通过父 id 间接归属的（`api_test_steps`→scenario、
`plan_cases`→plan、`test_report_steps`→report、`skill_versions`→skill、
`scenario_variables`→case、`scripts`/`script_runs`→case）都算已归属，不列。

| 表 | 现状 | 决定 |
|---|---|---|
| `environments` / `environment_variables` | ~~全局~~ → **项目级**（已完成） | 迁移 `zzo0envproj`；页面搬到 `/projects/:projectId/settings/env` |
| `load_test_scenarios` / `_steps` / `_runs` | 全局，零 FK，4 条 | **留全局** —— 它在 `/tools/load-test`，跟 Mock 同一档（§4 末） |
| `global_variables` | ~~全局~~ → **项目级**（已完成） | 迁移 `zzp0gvarproj`，复制给每个项目 |
| `notification_channels` | 全局 | 留全局 |
| `mock_routes` / `api_mock_routes` / `grpc·tcp·udp·ws_mock_*` / `*_mock_logs` / `custom_mock_presets` | 全局 | **留全局**，改 path 约束（§5） |
| `http_requests`（47 条） | 全局，零 FK | 留全局 —— 接口调试草稿箱，无所谓 |
| `ai_global_settings` / `ai_capability_bindings` | 全局 | 留全局；项目级覆盖本来就排在二期 |
| `setup_refs` | 全局，零 FK，**0 条数据，已确认是死代码** | **不动。** `new_setup_refs` 只在 `step_generator.py` 内部产生和返回，**没有任何调用方消费** —— 从没落过库。而 step_generator 属于已封存的平台侧 UI 生成链，删表要动封存代码，白费 |
| `healing_archives` / `failure_tickets` / `case_review_rounds` | → `case_id` | 已归属 |
| `exploratory_findings` | → `exploratory_sessions.project_id` | 已归属 |

---

## 7. 做了什么 / 还剩什么

| # | 事情 | 状态 |
|---|---|---|
| 1 | 堵匿名放行 | ✅ 删掉放行分支；`_deny()` 统一出口；查库失败也 fail closed |
| 2 | MCP 项目归属校验 | ✅ `_OWNER_SQL` 覆盖 14 个参数名，`tb_list_projects` 自己过滤 |
| 3 | 环境改项目级 | ✅ 迁移 + 模型 + 服务 + 路由 + 20 处前端调用 + 页面搬家 |
| 3b | 全局变量改项目级 + 新项目默认数据 | ✅ 迁移 `zzp0gvarproj`；连带修掉 3 处会静默串项目的查询 |
| 3c | 自动化数据页去掉「凭证概览」 | ✅ 跟环境配置是同一份数据，两处显示只会让人不知道该改哪边 |
| 4 | Mock 的 unique 约束 + 强制前缀（§5） | ⬜ 未做，跟隔离解耦 |

### 验证方式（不是"编译过了"）

- **两套测试都跑了**（这一点自己先漏过一次，见下）：
  - `backend/tests/` —— 单测 + 结构封样，1130 条通过。新增
    `test_mcp_data_scope.py` 24 条、`test_env_project_scoped.py` 27 条。
  - 根目录 `tests/` —— 打真接口（`testbench_test` 库）。`tests/api/variables/` 29 条通过，
    其中 12 条是这次新写的跨项目隔离用例。

  ⚠ **两个目录都叫 `tests/`。** 环境项目化时只跑了 `backend/tests/`，1130 全绿，
  而根目录那套有 14 条红在旧路径上 —— 「后端全绿、页面全 404」就是这么来的。
  改路由/改路径必须两套都跑，CLAUDE.md 里已加了命令。
- 反查 SQL **逐条真打库跑过**（14/14）—— 表名写错不会有编译期提示，而这一层是
  fail open 的，写错的症状是"静默失效、每次都放行"。
- 端到端（MCP）：临时建一把 UAG 项目的 Key，in-process 驱动真中间件 + 真库，17 项全过。
  含「拿别的项目的 case_id 改标题」被拒且**事后确认标题没变**、
  「编一个不存在的 UUID」也被拒、「拿别的项目的 env_id 读 BASE_URL/账号」被拒。
- 端到端（HTTP）：四处 body 收 env 的路由逐个打真接口 —— 别的项目的 env_id 一律
  404 ENV_NOT_FOUND，本项目的正常 200；建计划那条**事后确认库里没留下计划**。
- UI：Playwright 走 UAG / 网关管理系统两个项目的环境页 + 旧路径跳转 + 计划页，
  全程零 4xx/5xx。

### 两个已决的问题（原「待拍板」）

- **种子环境不需要复制、也不需要模板。** 每条环境都有唯一归属，回填按**实际引用**
  （谁的 plan/report 在用它）决定，不靠名字猜。`development`/`production`/`staging`/
  `testing` 全部归「测试平台」，因为只有它用过。
  **一条落在兜底分支**：`api-test-local` 从没被引用、名字也对不上任何项目名，
  于是归给了最早建的「测试平台」。看着更像是「API自测项目」的 ——
  要改的话在环境页删掉重建，或直接 update 那一行的 project_id。
- **`setup_refs` 不动。** `new_setup_refs` 只在 `step_generator.py` 内部产生和返回，
  **没有任何调用方消费** —— 从没落过库，表永远 0 行。而 step_generator 属于
  已封存的平台侧 UI 生成链。删表要动封存代码，白费。

## 8. 上线前必须确认

**在用的 Key 都要有归属项目。** `project_id` 为 NULL 的存量 Key **不受数据范围限制**
（跟 `pick_scope()` 那条「判据是有没有归属项目」同一个口径）。查一下：

```sql
select name, key_prefix, project_id, last_used_at from mcp_api_keys where is_active;
```

2026-08-21 实测三把活跃 Key（`uag-cc使用` / `ai-admin项目使用` / `活体全流程-0821`）
都已归属项目，所以这一层对它们是真开着的。
