# 版本升级：分支对账（端点反查）与用例复用

> 2026-08-21 定稿。来源：与用户逐条对齐的讨论 + 代码库实测核查（每条实测都标了文件行号，
> 不是推测）。上游边界规则见 [cc-platform-loop-spec.md](cc-platform-loop-spec.md) §0。
>
> 这份文档的作用是**防走偏**。「接口反查」这件事最容易做成"平台定时扫描 + 自动修用例"，
> 那是错的方向，理由见 §1 和 §2。

---

## 0. 要解决的问题

v1.0 的用例、接口场景、UI 脚本都写好、跑过、审过了。v2.0 出来，在平台上建新分支、
勾选用例复制过来 —— **然后呢？**

现在的答案是「人拿眼睛比对」：翻 changelog、翻代码、翻用例，猜哪些还能用。
比不全、比不准，换一个会话/换一个人就得从头再来。

要的答案是：**一次对账，把这批用例分成三堆 —— 照抄 / 要改 / 该废，然后 CC 自己干完，
AI 审完，人只在两个地方出现。**

---

## 1. 边界：为什么反查必须由 CC 发起

反查需要两半数据，**平台只有一半**：

| | 谁手里有 | 是什么 |
|---|---|---|
| 用例依赖了哪些端点、哪些字段 | **平台** | 每个步骤都存了 `method` + `url` + 断言字段路径 + 期望状态码，全量可算 |
| v2.0 到底改了什么 | **CC** | 在开发者本机的代码仓库里，`git diff v1.0..v2.0` 才看得到 |

**影响清单 = 这两半求交集。** 由此推出三条，都是纪律不是偏好：

1. **平台单独产不出影响清单** —— 分支复制那一刻还没人告诉它 v2.0 改了什么。
   所以「复制完自动出清单」这件事在原理上不成立，别去做。
2. **不做定时扫、不做每次执行后扫、不做每次回推后扫。** 一个版本对一次账。
   平台反复扫自己那一半，产不出任何新信息，只产噪音。
3. **平台不推，CC 拉。** MCP 是 CC 发问、平台回答，反过来没有通道。所以清单落进平台后
   挂到 `tb_next_duty` 队列，CC 每轮开工问一句就知道干到哪了 ——
   **清单必须落平台，不能留在 CC 的上下文里**：它一关会话就没了，续不上。

---

## 2. 三条红线

### 红线 1 · 反查全链路只读，只写清单和标签

`tb_list_branch_endpoints` / `tb_apply_endpoint_diff` 及其页面**不得有任何路径**能改
`steps` / 断言 / `review_status` / 三维状态。改用例还是走 `tb_update_case`、
`tb_sync_orchestrated_scenario`，一条条过原有门禁。

**为什么**：这批用例是 v1.0 审过的成果。一个"自动帮你改"的工具改坏了，没人看得出来 ——
它改的正是断言，而断言坏了的表现就是**变绿**。

### 红线 2 · 预期按 v2.0 的需求写，不是按 v2.0 的实测抄

版本升级时最顺手的错误：打开 v2.0 跑一遍，看它现在返回什么，照着改预期，用例立刻变绿。
**那不是测试，那是把 v2.0 的实现抄了一遍** —— v2.0 引入的 bug 会被固化成"预期"，
而且步骤、接口场景、UI 脚本三份产物同源，会一致地一起错，全绿，没人看得出来。

做法照 MCP 侧那条既有规则（读需求 → 读实现+实测 → 自己比对）：一致就按它写并落款；
不一致**预期按需求写**，让它红，提 `product_defect` 让人拍板；需求没覆盖先自己判，
判不出来带着判断去问人。

### 红线 3 · 假废弃比假绿更毒

**"我在页面上找不到" ≠ "这个功能没了"。** 入口挪到二级菜单、改名、拆成两个页面，
在 UI 上都长得像"没了"。

一条用例被误废，那块功能就**再没人测了，而且永远不报错** —— 没有任何信号会说
"这里本来该有覆盖"。所以废弃审核的探测必须正反两面都过，探不出来一律落人（§6）。

---

## 3. 完整流程（每一步的实际调用）

### 阶段 A · 对账（CC 一轮跑完，一个用例都不动）

触发：你在复制窗口里复制那句提示语，粘到 CC 终端。分支名平台自己填，两个 git 版本号
留占位由你补 —— **平台不知道 v1.0/v2.0 对应哪两个 tag，这是唯一需要你给的信息**。

```
tb_list_projects()                          → project_id
tb_list_branches(project_id)                → branch_id (v2.0)
tb_list_project_notes(project_id)           → 先读坑，别重踩
tb_list_cases(branch_id, pending_only=true) → 这批用例各欠哪几维

【新】tb_list_branch_endpoints(branch_id)
      → {method, url, 断言字段路径, 期望状态码} → 用例编号 / 步骤名 / 断言序号

（本机）git diff v1.0.0..v2.0.0 + 看改动的 router / schema
      → 改了哪些 url、哪些响应字段、新增了哪些状态值

（CC 求交集）

【新】tb_apply_endpoint_diff(branch_id, changes=[
        {url:"/subscriptions/provider", method:"POST", kind:"removed"},
        {url:"/provider-unified", method:"POST", kind:"field_changed",
         detail:"响应去掉 quota，新增 quotaDetail.limit"},
        {url:"/approvals/{id}", method:"PATCH", kind:"new_state",
         detail:"新增 status=suspended"},
      ])
      → 落清单：命中 N 条（该废 / 要改），未命中 = 照抄堆
      → 命中的那些，预期落款打回「待重新确认」
      → 清单进 tb_next_duty 队列
```

### 阶段 B · 要改堆（每条都是这一串，CC 自己转）

```
tb_next_duty(branch_id)                → 「待处理接口变动 N 条」，取第一条
tb_get_case(case_id)
tb_list_api_tests(branch_id) → tb_get_api_test(scenario_id)
                                       → 清单指的是哪一步

（本机）读 v2.0 的 PRD / docs / OpenAPI / 代码校验分支   ← 红线 2
tb_get_sync_spec(kind='api_scenario')
tb_list_global_data(project_id, env_id, probe=true)

tb_update_case(case_id, steps=[...], expected_result="...",
               expected_confirmed_note="依据 v2.0 docs/xx.md §3.5 + 实测确认")
tb_upsert_scenario_variables(case_id, variables=[...])      # 有新变量才调
tb_sync_orchestrated_scenario(..., source_case_id=case_id, mode='patch',
                              steps=[只改动的那几步])

tb_run_api_test(scenario_ids, env_id)
tb_check_assertion_bite(case_id, skip_steps="操作:提交审批", env_id)

# target_level=full 才有这三步
tb_render_ui_script(case_id, lang='zh', env_id) → 本机改+调
tb_sync_ui_script(case_id, content="...")
tb_run_ui_script(case_id, env_id)
```

跑红了才走：

```
tb_get_ui_script_result(case_id)        → 截图路径 + 流量 + run_id + 现象初判
tb_submit_analysis(run_id, cause=..., ...)
   # 脚本/用例过期/环境/数据/flaky → CC 自己改，不等人
   # product_defect → 要 liveVerified + codeRefs + issue 三样齐
```

### 阶段 C · 送审（CC 自己送，被打回自己改）

```
tb_review_case(case_id, run_first=true, env_id)   → 六维 + mustFix
   → 不过 → 回阶段 B → 再送（平台记着第几轮、上次说了什么）
tb_check_deliverable(case_id)                     → blockers / risks / notes
```

### 阶段 D · 照抄堆（两次调用，不是建计划）

```
tb_run_api_test(scenario_ids="id1,...,idN", env_id)   # 一次调用跑完，DEBUG 口径
tb_run_ui_scripts_batch(case_ids="...", env_id)       # full 的那些，REGRESSION 口径
```

**内容没变也必须在 v2.0 上真跑一遍** —— "接口签名没变、底层行为变了"只有这一跑抓得到。
跑红的当场退回要改堆（说明对账漏了）。

⚠️ **不要用 `tb_create_plan` + `tb_run_plan` 推状态** —— 计划执行路径不写维度状态，
见 §7.1。计划是「全部审完之后出正式回归报告」用的。

### 阶段 E · 你验收（一次看完）

```
tb_check_branch(branch_id)          → 可交付 / 有阻塞 / 有脆弱点 / 待人审
tb_list_pending_confirm(project_id) → CC 认为是 v2.0 缺陷、等你拍板的
tb_check_env_hygiene(project_id, branch_id)
tb_add_project_note(project_id, ...)   # CC 收尾写回这一轮的坑
```

### 你在整条链上出现三次

复制分支 → CC 判不出预期时回答它 → 验收和拍板缺陷。中间的改、跑、审、返工循环全闭环。

---

## 4. 状态流转

现有四层状态，**这次不新增任何状态字段**。"照抄 / 要改 / 该废"是清单 + 标签，不是状态 ——
状态由执行事实推进，"要不要改"是判断，不是事实。

```
lifecycle_status  草稿 / 完成 / 废弃        自动推进（废弃只有人/AI 审能设）
manual_status     草稿 / 调试中 / 完成      手工步骤写了就是完成
api_status        草稿 / 调试中 / 完成      执行事实推
ui_status         草稿 / 调试中 / 完成      执行事实推
review_status     待提审 / 待审 / 通过 / 打回
```

**「完成」= CC 调试完了、提交待审核**，不是"审核通过了"。三维按 `target_level` 全部
completed 时，`sync_review_status`（`script_run_service.py:245`）一次性写掉两个：
`review_status=待审` **且** `lifecycle_status=完成`。审核通过之后 lifecycle 不再变。

三堆的状态线：

| | 状态线 | 谁给「通过」 |
|---|---|---|
| **照抄**（未命中） | 草稿 → 跑绿 → 完成/待审 → 通过 | 系统自动，四条件全过（§5） |
| **要改**（命中） | 草稿 → CC 改+跑 → 完成/待审 → 通过 | AI 审，打回就回炉 |
| **该废**（端点没了） | 草稿 → CC 提请 → 待废审 → 废弃 | AI 探测批准；探不出落人；驳回退回「要改」 |

对账**不改任何状态**，只打标签和落清单。

---

## 5. 照抄堆自动过审：四条件 + 撤销

未被清单命中的那些，**不再走 AI 六维审**，四个条件全过即 `review_status=通过`、
`decidedBy=system`，理由自动写「内容与 v1.0 逐字一致、v1.0 已审通过、v2.0 实测跑绿且断言有效」：

1. **未被对账清单命中**
2. **内容与源分支逐字一致** —— 平台自己比 `steps` / 接口场景 / UI 脚本正文，不听 CC 声明
3. **v2.0 上跑绿**
4. **断言咬得住**（`tb_check_assertion_bite` 过）

**为什么这个论证闭合**：清单命中的是「端点变了 / 字段变了 / 新增了状态值」。一条用例
**没被命中**就意味着它碰的接口和字段 v2.0 全没动、新增的东西也不在它身上 ——
那 v1.0 那次审核的结论在 v2.0 上仍然成立，再审是拿同一份内容问同一个问题。

三条防线：

- **内容一变就作废。** CC 改了任何一个字（包括标题），条件 2 不成立，降级成必须 AI 审。
  这个判定是机械的（比内容指纹），不靠自觉。
- **清单重算能撤销。** CC 后来补交漏掉的 changes，重算时新命中的用例 ——
  **包括已自动过审的** —— 撤回待审，理由写「对账补充后命中，原自动过审失效」。
  自动过审的全部合法性来自"未命中"，命中了就得作废。
- **条件 3、4 不能免。** 这两条治的是 diff 看不出来的行为变化。

---

## 6. 废弃审核

### 字段（跟 review 同形）

```
deprecate_status   NULL / requested / approved / rejected
deprecate_reason   JSONB {reason, evidence, requestedBy, requestedAt,
                          decision, decidedBy, decidedAt, note}
```

`lifecycle_status = deprecated` 只在 `deprecate_status = approved` 时才落。

### 三个入口

- **CC 提请** —— 独立工具 `tb_request_deprecate(case_id, reason, evidence)`。
  **不塞进 `tb_update_case`**：塞进去会被顺手带过（改标题时把用例一起废了），
  而且这里要硬校验证据。
- **人确认** —— 列表页一列「待废审」徽标，点开确认/驳回 + 看得见理由和证据；
  详情页顶部提示条同样两个按钮。**一条一条点，不做批量。**
- **AI 审** —— 合进 `tb_review_case`，不新开工具。这条用例有待决废弃请求时，
  它不审六维，改审「该不该废」。理由：CC 每轮本来就对每条调它，合进去不用判断调哪个；
  而且审一条要废的用例的六维质量本身没意义。

### AI 审的判据：必须实际探测，正反两面

原来的审核问「这条验得对不对」，废弃审核问「**这个场景在 v2.0 上还存不存在**」。
不许靠读代码或听 CC 转述定论，要真去被测系统上探：

- **正面**：老入口/老端点真的没了 —— UI 上走到那个位置看在不在；接口上打那个 url 看是不是 404/410
- **反面**：功能没被搬到别处 —— 改名、挪菜单、拆页面

三态结论：

| 结论 | 处置 |
|---|---|
| 确认没了（正反都过） | 批准废弃，直接生效，留痕 `decidedBy=ai` |
| 还在（在别处找到了） | 驳回 → 进 `tb_next_duty`：「这是要改，不是要废」 |
| **探不出来**（进不去页面 / 权限不够 / 页面报错） | **落人，不许自己拍** |

AI 批准直接生效的依据：废弃可逆（撤销回草稿）+ 全程留痕 + 有上面三条门槛。
"一条一条确认"这个前提保住了，只是确认人可以是 AI。

---

## 7. 代码库现状核查（2026-08-21 实测）

### 7.1 计划执行路径不写维度状态 —— 现存的洞

`tb_create_plan` + `tb_run_plan` 走 `app/engine/tasks/execution.py:290`，只调了
`record_run`（记执行、进通过率、出报告），**没调 `apply_case_status`**。
所以走计划跑，哪怕全绿，`api_status` / `ui_status` 一动不动，用例永远进不了待审 ——
你会看到一份 100% 通过的报告和一批还是草稿的用例。

**这是漏，不是设计**：`apply_case_status` 的 docstring 自己写着「只有 regression
（**计划**/批量回归）失败才是真信号」，它就是按"计划会调我"写的。
同一平台里 adhoc 批量（`adhoc_execution.py:420`）调了，计划没调。

→ 待做 1：计划执行路径补上 `apply_case_status`。

### 7.2 spec 级用例复制完就假显示「完成 + 待审」

`_copy_cases`（`branch_copy_service.py:176`）对每条复制出来的用例调 `sync_manual_status`，
而它自己又调了 `sync_review_status`（`case_service.py:38`）。连锁结果实测：

```
target_level=spec      → lifecycle=done     manual=completed review=pending
target_level=spec_api  → lifecycle=draft    manual=completed review=None
target_level=full      → lifecycle=draft    manual=completed review=None
```

**只承诺手工步骤的用例，一复制过来就显示"完成、待审"，而它在 v2.0 上一次都没验过。**
这正是「没跑过也说通过了」，只是它不从 `review_status` 那个门进来。

纪律是：**新版本上没验过就不能算完成**，手工步骤也一样（v2.0 的页面可能根本不那么走了）。

→ 待做 2：复制出来的用例，`lifecycle_status` / `review_status` 强制置回草稿/待提审。

### 7.3 `deprecated` 不被任何地方排除

实查 `tb_list_cases` / `tb_check_branch` / `tb_check_deliverable` / plan：
**没有一处过滤 `lifecycle_status='deprecated'`**。现在废掉一条用例，它照样进待办队列、
照样进回归、照样算进通过率分母。这个洞不补，废弃审核做出来也没用。

→ 待做 7：待办队列、交付门禁、批量回归、通过率统计全部排除 deprecated；
但 `tb_list_cases` 显式传状态时要查得到（不然废了就再也找不着，撤销都撤不了）。

### 7.4 `Case` 没记「我是从哪条复制来的」

只有 `case_code` / `tea_id` 能跨分支对同一条；`_copy_cases` 算出的 `case_map` 用完就扔。
所以 §5 条件 2（内容逐字一致）没法机械判定，只能靠 CC 自己声明"我没改" —— 等于没有防线。

→ 待做 3：复制时记来源用例 id + 内容指纹。

### 7.5 审核结论不随复制过来 —— 这是对的，保留

`_copy_cases` 的构造里没有 `review_status` / `review_reason`，`CaseReviewRound` 历史
也不复制，`automation_status="pending"`，执行记录不带。
**结论：v2.0 分支上每条用例都是"没跑过、没审过"，符合纪律，不改。**

`target_level` 和预期落款（`expected_confirmed_*`）**跟着复制**（代码里有理由：
不带过去每条都要人把依据重填一遍）。但落款在版本升级时会过期 ——
所以 §3 阶段 A 里，**命中清单的用例其预期落款自动打回「待重新确认」**。
CC 改了步骤或预期的话平台本来会自动清掉它，这里补的是"需求变了、步骤没变"那种漏网。

---

## 8. 待做清单与顺序

| # | 事项 | 备注 |
|---|---|---|
| 1 | 计划执行路径补 `apply_case_status` | §7.1，现存洞，影响日常，最先修 |
| 2 | 复制状态修复（lifecycle/review 置回草稿） | §7.2，和 3 一起改 |
| 3 | 复制时记来源用例 + 内容指纹 | §7.4，是 §5 条件 2 的前提 |
| 4 | 复制窗口的可复制提示语（分支名自动填，git 版本号留占位） | §3 阶段 A |
| 5 | `tb_list_branch_endpoints` + `tb_apply_endpoint_diff` | 全套的前提 |
| 6 | 照抄堆自动过审（四条件 + 撤销机制） | §5 |
| 7 | 废弃审核（字段/三入口/探测判据） + `deprecated` 各处排除 | §6 + §7.3 |

---

## 9. 延后的，及理由

| 延后项 | 为什么先不做 |
|---|---|
| **被测版本戳** | 用处是"提醒你该对账了"，但对账时机很清楚（建完新分支就这一次），不需要系统提醒。等出现"总忘了对账"再加。 |
| **覆盖分母（功能点表）** | 稳定 ID + 优先级 + 状态的功能点表要人长期维护，维护不动就变成第二份过期文档。承认"通过率没有分母"这个问题存在，但这个解法太重。 |
| **契约漂移探测**（拿断言字段路径去真接口探一遍） | 投入大、打不中危险方向。"路径没了"本来就会让断言转红（红了有人看）；真正漏的"系统新增了状态值/分支"，这套探测**探不出来**。 |
