# 两个待做需求：反馈「范围」列 + Key 级工具范围

**状态：已分析、结论已定、代码一行没动**（2026-09-02）。这份文档是给「后面有空再做」
那一刻的自己看的 —— 动手前整篇读完，尤其两节「别这么写」和需求二的那条**上线前必查**。
文档里所有数字都是当天在 `lumiere` 库和 `TOOL_CATALOG` 上真跑出来的，不是估的；
真动手时**要重跑一遍**，因为这两条结论的前提都是「今天恰好没有冲突数据」。

---

## 需求一：CC 反馈一眼看出「是哪一块的问题」

> 原话：想增加一下是哪个模块的问题，比如是 QA 仓评审结论的问题、还是 UI 脚本回推、
> 还是什么的问题，可以一眼看出来问题的反馈范围。

### 1.1 现状：范围信息**存在**，但既不成型也看不见

页面（`frontend/src/pages/settings/CCFeedback.jsx`）今天唯一的范围线索是「来源」列里那行
小字 `<code>{r.toolName}</code>`，在表格最右边，和 source 标签、项目名挤在 230px 里。
它能查，但不能「一眼看出」—— 而且下面这组数据说明，光靠它也拼不出范围。

56 条反馈按 `tool_name` 分组（实测）：

| tool_name | 条数 |
|---|---|
| `lum_sync_orchestrated_scenario` | 8 |
| `lum_review_case` / `lum_update_case` | 各 6 |
| `lum_check_deliverable` | 4 |
| `lum_run_api_test` | 3 |
| `lum_add_project_note` / `lum_check_assertion_bite` | 各 2 |
| 其余 25 个值 | 各 1 |

**关键事实：56 条全都填了 `tool_name`，但只有 38 条填的是注册工具名，另外 18 条（32%）是自由文本。**
自由文本长这样：`AI 评审规则文案`、`AI 评审（mustFix 输出）`、`接口场景执行器`、
`断言执行（type=status / operator=in）`、`hardcodeWarnings（回推校验）`、`覆盖统计`、
`执行报告`，还有 3 条是 `A / B` 的组合（`lum_add_project_note / ai_review`）。

这不是 CC 填错了 —— 它撞到的**确实**不是某一个工具，是一块子系统。工具名这一列
承载不了「范围」，因为**有些范围压根没有对应的工具**。

### 1.2 为什么不能复用 `TOOL_CATALOG.category`（最省事的那条路，是错的）

`TOOL_CATALOG` 有 15 个 category，看着现成。但那是**货架分类**（「这个工具该摆在哪一格、
我去哪儿找它」），不是**故障域**（「坏掉的是哪个子系统」）。两者最大的一处错位正好
落在最大的一撮反馈上：

- `lum_review_case` 的 category 是「用例·手工步骤」。
- 而这 6 条反馈说的全是 **AI 评审判据/文案**的毛病 —— 和用例增删改一点关系没有。

按 category 归类，AI 评审这一块（下面会看到它是最大的一块）会被整块塞进「用例」，
而这正是要看的那件事。**这种错不报错**：页面照样有一列、照样有饼图，只是指错了地方。

同理，纯派生（不落库、按 `tool_name` 现场映射）也不行：自由文本那 18 条只能落「其它」，
而它们恰恰是含金量最高的一撮 —— 一个人肯手写「AI 评审规则文案」，说明他很清楚
自己在说哪一块。

### 1.3 按内容人工聚类，实际长这样

| 域码 | 中文 | 现有条数 | 主要来源 |
|---|---|---|---|
| `ai_review` | AI 评审 | **15** | `lum_review_case` 6 + 自由文本 8 + slash 组合 1 |
| `sync` | 回推入库与校验 | 12 | `sync_orchestrated_scenario` 9、`sync_ui_script`、`hardcodeWarnings`、`upsert_scenario_variables` |
| `case` | 用例读写 | 8 | `update_case` 6、`get_case`、`request_deprecate` |
| `gate` | 交付门禁与体检 | 7 | `check_deliverable` 4、`check_assertion_bite` 2、`check_env_hygiene` |
| `api_run` | 接口场景执行 | 5 | `run_api_test` 3、执行器、断言执行 |
| `report` | 执行报告与覆盖统计 | 4 | 执行报告、执行结果状态、覆盖统计 |
| `note` | 项目须知 | 2 | `add_project_note` |
| `spec` | 接入规范与工具描述 | 1 | `get_sync_spec` |
| `apidoc` | 接口库 | 1 | `list_api_tests` |
| `diff` | 版本对账 | 1 | `apply_endpoint_diff / 覆盖对齐` |

合计 56。**`ai_review` 一块占 27%，而它今天在页面上是散成 9 个不同 `tool_name` 的。**
这一条就是需求本身的证据：不加这一列，最该被看见的那块正好最看不见。

另外三个域今天 0 条，但要一起建，因为它们是这条通道**明确覆盖**的范围，
0 条本身是信息（「这块没人报过」和「这块不在范围里」不是一回事）：
`qa_review`（QA 仓对账结论）、`ui_script`（UI 脚本执行/渲染，区别于 `sync` 的入库）、
`env`（环境/变量/全局数据）。加上 `other`，一共 14 档。

> 用户举的两个例子正好一个已有一个没有：「UI 脚本回推」= `sync`（12 条，最大之一），
> 「QA 仓评审结论」= `qa_review`（0 条）。这也说明档位不能只从存量数据里长出来。

### 1.4 方案

**加一列 `area`（`String(24)`，可空，带索引）**，取值就是上表那 14 档。

**`NULL` 和 `other` 必须分开**，和 `decided_by` 的 NULL 同一个口径：
`NULL` = 还没人判过它属于哪块；`other` = 判过了，确实不属于任何一块。
合成一个的话，「没判」会永久伪装成「判过了没归属」，而这一列的价值全在能筛。

**谁来填 —— 三层，和这张表既有的分工一致（AI 判、人兜底）：**

1. **上报时给个默认**：`tool_name` → area 的静态映射（只对注册工具名生效，
   写在 `app/services/cc_feedback_service.py` 里一张 dict）。命中就落，
   不命中留 `NULL`。**不要在这里做关键词猜测** —— 「AI 评审规则文案」猜得中，
   「执行结果状态」猜不中，而猜错的那半没有任何地方会报错。
2. **AI 分诊落最终值**：`_HANDLE_PROMPT` 的 JSON schema 加一个 `"area"` 字段 +
   14 档清单。它本来就读工具描述和 `inspect.getsource`（`_platform_facts`），
   判这个比判 category 容易得多。判不出来回 `null`，别硬凑。
3. **人能改**：抽屉里的处置表单加一个下拉，和 category/severity 并列。

**页面（`CCFeedback.jsx`）：**

- 「范围」列排在**标题右边**（第二列），不是最右；`tool_name` 留在「来源」列不动 ——
  两者是「哪一块」和「哪个工具」，不互相替代。
- 顶部加一排按域的**计数筛选块**（复用现有 summary 那排的样式）。
  **一列文字只做到"能查"，能"一眼看出"的载体是这排计数。** 需求要的是后者。
- 列表接口 `GET /api/cc-feedback` 加 `area` 过滤参数，`summary` 加 `byArea`。

**MCP 侧：** `brief()` 加 `area` / `areaLabel`，回音（`lum_list_my_feedback`、
`lum_next_duty` 的「平台反馈有回音」队列）跟着带上 —— CC 那边也能按块看自己报了些什么。

### 1.5 三个坑（都属于「写错了不报错」那一类）

1. **`area` 绝不能进 `fingerprint_of()`。**
   指纹现在只有 `(tool_name, 归一化标题)` 两样，函数里已经写明为什么不掺正文。
   掺 `area` 会有两个后果：同一件事在改了域之后变成两行（归并失效），
   以及 **`wont_fix` 短路失效** —— 那是这条通道最要紧的行为，
   而它失效的表现是「反馈变多了」，看起来完全正常。
2. **一条只留一个主域，别做成多选。**
   有 3 条 slash 组合确实跨两块，多选之后各域计数加起来 ≠ 总数，
   顶部那排计数块就不能用来做筛选（点进去看到的和数字对不上）。
   跨块的按「坏在哪」选主域 —— `lum_add_project_note / ai_review` 主域是 `ai_review`。
3. **回填 56 条时，规则匹配不上的留 `NULL`，别一把塞 `other`。**
   塞了之后 AI 分诊那一层就永远不会再碰它们（它只填空的），
   等于用一次性回填把 32% 的数据钉死在错误值上。

### 1.6 顺带白捡的一件事（不在原需求里，做不做另说）

`_siblings_for()` 今天按 **`tool_name` 精确相等**取判重候选。于是那 8 条
`AI 评审规则 xxx`（名字个个不同）**互相都不是候选** —— 最该判重的一撮，
恰好是判重能力为零的一撮。有了 `area` 之后，候选集可以放宽成「同域 + 同类」，
判重能力提升的正好是自由文本那一块。

### 1.7 改动清单

| 文件 | 改什么 |
|---|---|
| `backend/alembic/versions/<新>_cc_feedback_area.py` | 加列 + 索引 + 按规则回填（匹配不上留 NULL） |
| `backend/app/models/cc_feedback.py` | `AREAS` / `AREA_LABEL` 常量 + `area` 列，docstring 里写清 NULL vs other |
| `backend/app/services/cc_feedback_service.py` | `_TOOL_AREA` 映射；`report()` 落默认值；`brief()` 出 `area`/`areaLabel`；`list_feedback()` 加过滤 + `byArea`；`triage()` 收 `area`；`_HANDLE_PROMPT` 加字段 |
| `backend/app/api/cc_feedback.py` | 列表加 `area` query；triage body 收 `area` |
| `backend/app/mcp/tools/feedback.py` | `report_feedback` 加可选 `area`（**不进指纹**，描述里写明） |
| `frontend/src/pages/settings/CCFeedback.jsx` | 范围列（第二列）+ 顶部计数筛选 + 抽屉里的下拉 |
| `backend/tests/test_cc_feedback_gates.py` | 加：`area` 不影响指纹（改域后仍归并）、NULL≠other、AI 回 null 时不落 other |
| `tests/api/cc_feedback/test_cc_feedback_flow.py` | 加：按域筛 + `byArea` 计数 |

工作量：半天。

---

## 需求二：建 MCP Key 时选工具范围（一把 Key 干一件活）

> 原话：创建 mcpkey 的时候可以选择授权的工具范围，不再是项目整体授权，
> 这样某一个 key 就是比较干净的干某个活，免得工具太多调错，或者导致上下文过多。

### 2.1 结论：可行，而且**能力已经在库里躺着**

`mcp_api_keys.allowed_tools` 这一列**存在**，创建/PATCH 接口**已经收**这个字段，
`_validate_tools()` 已经在挡拼错的工具名，`invalidate_scope_cache(key_hash)` 已经能按
单把 Key 清缓存。2026-08-10 的迁移 `w7e8f9a0b1c2` 把范围挪到了项目级，这一列退成遗留
（注释：只对 `project_id` 为 NULL 的存量 Key 生效），页面不再暴露入口。

**所以这件事不是"加个功能"，是"把一个被降级的维度重新启用"。** 真正要改的只有一处
判据（`pick_scope`）和一处交互（建 Key 弹窗）。

### 2.2 但当年挪走的理由是**真的**，别直接回滚

`docs/cc-platform-loop-spec.md` 里那一节记着：范围是「**这个项目允许 CC 干哪些活**」，
不是「这把钥匙允许干哪些活」——同一个项目发五把 Key，范围本来就该一样；
做成 Key 级之后，每换一次范围就要多发一把 Key，Key 会按范围的排列组合增殖。

这条今天仍然成立。所以**不是二选一，是两层**：项目范围是**天花板**（这个项目允许干什么），
Key 范围是**可选的收窄**（这把钥匙这次干哪件活）。天花板那层的语义一个字不动。

### 2.3 为什么现在做值得：项目级范围今天几乎没在收窄任何东西

实测：

- 3 把活跃 Key，**全部**归属项目，遗留 `allowed_tools` **全空**。
- 两个真实项目 `UAG` / `网关管理系统` 的范围都是 **59/63**；另外 3 个项目是 0（不限制）。

**59/63 等于没收窄。** 也就是说「省上下文」这件事，项目那一层客观上没做到 ——
用户说的「干净」只能在 Key 这一层拿到。

工具描述的体积实测（`name + description`，63 个工具共 **22,909 字符**）：

| 档位 | 工具数 | 字符 | 占全量 |
|---|---|---|---|
| `fullloop` 全链路 | 58 | 21,096 | 92% |
| `live` 活体回推 | 39 | 15,176 | 66% |
| `uiscript` UI 脚本 | 28 | 11,122 | 48% |
| `triage` 失败归因 | 15 | 4,968 | 21% |
| `regression` 回归执行 | 18 | 4,791 | 20% |
| `mocks` | 9 | 3,149 | 13% |
| `apidoc` 接口库 | 9 | 2,759 | 12% |
| `skill` | 6 | 2,294 | 10% |
| `qareview` QA 对账 | 4 | 1,943 | 8% |

一把只干归因的 Key ≈ 全量的 21%，**每轮省下约 1.8 万字符**。
（另有约 1.07 万字符的 server instructions 是固定开销，不随范围变 —— 别把省下的量算大。）

### 2.4 方案：两层取交集，Key 只能更窄

```
生效范围 = 项目范围 ∩ Key 范围
  Key 范围 NULL  → 跟随项目（默认，也是今天所有 Key 的状态）
  项目范围 NULL  → 天花板不限，生效范围 = Key 范围
  两个都 NULL    → 不限制
```

四种组合和今天行为的对比：

| 项目范围 | Key 范围 | 今天 | 新口径 | 变了吗 |
|---|---|---|---|---|
| 有 | NULL | 项目 | 项目 | 否 |
| NULL | NULL | 不限 | 不限 | 否 |
| NULL | 有 | **不限**（`project_id` 非空就不看遗留列） | Key | **是** |
| 有 | 有 | **项目**（遗留列被忽略） | 交集 | **是** |

后两行今天各 0 条 —— 见下面那条必查。

### 2.5 两处「别这么写」

1. **`key_scope or project_scope` —— 和现有那条注释里的坑是同一个坑的镜像。**
   `pick_scope` 现在写着「写成 `project_scope or legacy_scope` 是最自然也最错的写法」。
   反过来一样错：Key 范围为空时掉回项目范围**看着对**，但它把 `None`（跟随项目）
   和 `[]`（一个都不给）混成一回事。
   ⚠ **`[]` 今天就等于"不限制"**：`return [...] if raw else None` —— 空列表是 falsy，
   直接返回 `None`。方向完全反了，而且一个字都不会报错。
   所以新实现里 `[]` 必须要么**拒收**（发一把 0 工具的 Key 是无意义的，
   清空走 `reset_tools` 三态），要么显式当成"空集合"处理，**不能落进 `or` 里**。
2. **交集只能收窄，不能反向扩。**
   Key 上写了项目范围里没有的工具 → **丢掉**，并且页面要显示
   「有 N 个被项目范围挡住了」。不能显示成这把 Key 有它 ——
   那会变成「页面说有、实际调不到」，排查时第一反应一定是去查 MCP 连接。

### 2.6 上线前必查（否则是一次静默的权限变更）

```sql
select count(*) from mcp_api_keys
 where project_id is not null and jsonb_typeof(allowed_tools) = 'array';
```

**2026-09-02 实测 = 0**，所以今天改口径不会让任何一把 Key 的行为发生变化。
非 0 就意味着有 Key 的遗留范围**今天正被忽略**，新口径下会**突然生效** ——
那把 Key 会莫名其妙少掉一批工具。迁移里要么清掉那些遗留值，要么落成人工确认，
**不能默默启用**。

> 顺带一条踩过的：`allowed_tools` 里存的可能是 jsonb 的 `'null'` 标量（不是 SQL NULL、
> 也不是数组），所以任何判空都得用 `jsonb_typeof(...) = 'array'`，
> `IS NOT NULL` 会漏，`jsonb_array_length()` 直接报 `cannot get array length of a scalar`。

### 2.7 交互：照抄已定稿的那套，别再走一遍四次弯路

`docs/cc-platform-loop-spec.md` 记着**四次失败的交互尝试**（二选一卡片 / 勾选藏在模式
后面 / 42 条说明铺开 / 下拉单选），结论是：**「活」是主角，工具明细是结果，默认整块收起**。
Key 这边直接复用，不要另设一套：

- 建 Key 弹窗默认 **「跟随项目范围（推荐）」**。
- 展开 →「这把钥匙只干这几件活」→ 复用 `MCPTools.jsx` 里那组 ActivityCard 多选
  （数据走已有的 `GET /api/mcp-keys/profiles`）。
- 工具明细默认收起；选完显示「生效 N / 63」，以及被项目范围挡掉的那几个。
- Key 列表每行显示「N / 63 · 跟随项目」或「N / 63 · 本 Key 收窄」。
- **落库存展开后的工具名，不存档位名**（纪律 2，已有，别破）。

### 2.8 还得说清的三件事

1. **工具范围不是权限边界。** 数据范围仍然只由 `project_id` 决定，游客非 GET 闸门、
   项目角色校验都不受影响。窄 Key 的收益是「少挑错 + 省上下文」，**不是隔离** ——
   别在页面上把它写成安全特性，那会让人以为发一把窄 Key 就等于降权。
2. **名单过期的问题会翻倍。** 项目那页已经有 `staleProfiles` 提示
   （覆盖 ≥70% 但不满 ⇒「平台新增了 N 个工具，本项目的范围还没跟上」+ 一键补齐）。
   Key 级同样会过期，而且**交集让它更隐蔽**：项目补齐了、Key 没补，
   看起来像"项目已经修好了"。同一套提示要做到 Key 上。
3. **缓存**：改 Key 用 `invalidate_scope_cache(key.key_hash)`（已有，精确清）；
   改项目范围仍然**全清**（一个项目 N 把 Key，key_hash 在那儿拿不到 ——
   `test_改项目范围要清缓存_而且是全清` 盯着这条，别顺手"优化"成精确清）。

### 2.9 改动清单

| 文件 | 改什么 |
|---|---|
| `backend/app/mcp/middleware.py` | 重写 `pick_scope`：None 感知的交集；`[]` 单独处理；docstring 写上 2.5 两条 |
| `backend/app/models/mcp_api_key.py` | `allowed_tools` 的「遗留」注释改成「Key 级收窄」，写清 NULL = 跟随项目 |
| `backend/app/api/mcp_keys.py` | POST 收 `allowed_tools`（已有，去掉前端不传的约束即可）；PATCH 去掉「绑项目就清 `allowed_tools`」那一句；返回带上生效范围和被挡掉的工具 |
| `backend/alembic/versions/<新>_key_level_scope.py` | 无 DDL；跑 2.6 那条检查，非 0 就落人工确认（或数据清理） |
| `frontend/src/pages/settings/MCPTools.jsx` | 建 Key 弹窗加范围选择（默认跟随项目）；Key 列表加生效范围列；per-key 过期提示 |
| `backend/tests/test_project_mcp_scope.py` | 改 `test_解析判据是有没有归属项目`（判据变了）；补：交集只收窄、`[]` 不等于不限制、Key 越界工具被丢、改 Key 只清自己那条缓存 |

工作量：一天。和需求一互不依赖，谁先做都行。
