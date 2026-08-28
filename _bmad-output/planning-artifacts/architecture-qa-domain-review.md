---
stepsCompleted: ['step-01-init', 'step-02-context', 'step-04-decisions', 'step-06-structure', 'step-07-validation']
workflowType: 'architecture'
scope: 'scoped-adr'
inputDocuments:
  - _bmad-output/planning-artifacts/prd-qa-domain-review.md
  - _bmad-output/planning-artifacts/prd-qa-domain-review-HANDOFF.md
  - docs/qa-repo-readonly-catalog.md
  - docs/cc-platform-loop-spec.md
  - CLAUDE.md
classification:
  projectType: 'web-app'
  domain: 'devtools-qa'
  complexity: 'high'
  projectContext: 'brownfield'
---

# 架构决策：QA 对账 · 域级 AI 评审（重做）

> **这份文档只定 HANDOFF §7 没定的那些结构性问题。**
> 算法、判据、五类缺口的定义、`evidence` 匹配规则、`env_gaps` 三档 —— 全在
> `prd-qa-domain-review-HANDOFF.md` §7（Q1 A–J / Q2 A–J），**本文不复述、不改写**。
> 复述会长出第二个版本，然后两份互相矛盾，而没人知道该信哪个。

## 0. 为什么这份是 scoped 的（这条要留痕）

BMAD 的 `create-architecture` 是给「选框架 / 选数据库 / 定分层」那种场景设计的。
本项目是 brownfield，技术栈早定死了（FastAPI + asyncpg + arq + React/antd + Playwright），
而 §7 已经把算法层设计完了。照全流程再产一份 50KB 架构文档，产出会是 §7 的转述 ——
**转述是本次要修的三个洞里的一种（洞二：转述听起来永远合理）**，不该由架构文档自己再犯一次。

所以本文只回答四类 §7 确实没答的问题：

| 类 | 问题 | 决策 |
|---|---|---|
| 模块边界 | 新增的三块代码放哪、能不能单测 | AD-2 |
| 可执行性缺口 | 爬虫脚本怎么入库、门禁怎么复用 | **AD-3（§7 在这里字面上做不到）** |
| 运行时拓扑 | arq 怎么分片、job 怎么注册、状态机 | AD-4 |
| 一致性手段 | 前后端常量重复能不能自动护住 | **AD-5（对 PRD NFR-9 的加强）** |

另有数据模型与只读落点两节（AD-6 / AD-7），是把 §7 的表设计翻译成迁移与索引。

---

## AD-1 · 现有文件不再长大

**决策**：`backend/app/services/qa_catalog_review.py` 现在 1214 行，本次新增的三块
**一行都不加进去**，各自独立模块。

**理由**（不是"文件太长"这种审美理由）：

1. `evidence` 回验必须是**纯函数、零 IO、零模型**。放进 1214 行那个文件里，
   它的单测就得拖着 `llm_client`、`QaCatalogReview`、session 一起起来 ——
   而 HANDOFF §9 B 组有 12 条测试要打它，其中 3 条是哨兵。**测不动的哨兵等于没有哨兵。**
2. 三方对账（G1–G5）是**集合运算**，输入是三个 dict/set，输出是五个列表。
   它跟"调模型评一个域"没有任何共享状态。混在一起会让人误以为对账也要过模型 ——
   而 §7 Q2-A 的全部价值就在于它不过模型。
3. 爬虫要进 arq worker，生命周期跟 `execute()` 那条后台任务不同（分片、可重放、有产物表）。

**新增模块**：

| 模块 | 性质 | 依赖 | 谁调它 |
|---|---|---|---|
| `app/services/qa_evidence_check.py` | 纯函数 | `re` | `qa_catalog_review._one()` |
| `app/services/qa_coverage_reconcile.py` | 纯函数 | `branch_diff_service.normalize_path` | `qa_catalog_review.run_review()` |
| `app/services/qa_page_survey.py` | 编排 + 落库 | session、arq、engine | arq worker |
| `app/engine/surveys/qa_page_survey_crawl.py` | Playwright 脚本 | pytest+playwright | 沙箱执行器 |

`qa_catalog_review.py` 只增**调用点**和 `coverage` 字典里的新键。

---

## AD-2 · `evidence` 回验的调用位置：`_one` 内，且必须能被测出来

§7 D 已经定了「落在 `_one` 里、`parse_result` 之后」并给了硬理由（`part` 只在 `_one` 作用域里）。
本文补一条**结构上的保证**：

**决策**：回验函数签名不许接受"全域脚本"。

```python
def check_evidence(gaps: list[dict], batch_scripts: list[dict]) -> tuple[list[dict], dict]:
    """batch_scripts 是**这一批**的脚本。想传全域进来得改签名 —— 那一改就会被 review 看见。"""
```

**理由**：§9 第 17 条哨兵（`test_回验必须在合并之前`）靠的是"A 批结论引用只存在于 B 批的正文
⇒ 判 unmatched"。但如果函数签名长得像 `check_evidence(gaps, scripts)`，
后人把 merge 之后的全量 `scripts` 传进去，**类型检查、单测形状全都过得去**，
只有那一条哨兵会红 —— 而它红的时候看起来像"测试写得太严"。
把参数名钉成 `batch_scripts` 并在 docstring 里写明，是让**代码本身**说出这个约束。

---

## AD-3 · 爬虫脚本怎么入库 —— §7 这里字面上做不到

**§7 Q2-B 写的是**：「回推：CC → 平台，脚本正文入库，沿用 `lum_sync_ui_script` 门禁
（硬拦写死地址与凭据）」。

**核过之后：这条执行不了。** `lum_sync_ui_script(case_id, content)` 的 `case_id` 是必填，
而它落的是**某条用例的「UI 测试」页签**（`scenario_variables.case_id NOT NULL` 那条约束的同族）。
页面枚举爬虫**没有用例可绑** —— 它不验任何一条用例，它产的是事实账本。
硬造一条假用例来挂它，等于在用例库里埋一条永远不该被执行、也没有预期结果的用例。

**决策**：

1. 爬虫脚本**作为仓内文件**落 `backend/app/engine/surveys/qa_page_survey_crawl.py`，
   走正常 code review，不走 MCP 回推通道。
   —— 它是平台自己的确定性工具，不是某个项目的测试资产。
2. `lum_sync_ui_script` 的**门禁逻辑**（硬拦写死服务地址与凭据）提成共享校验函数
   `app/services/ui_script_guard.py::assert_no_hardcoded_endpoint_or_secret(content)`，
   由 MCP 工具和本模块的一条封样测试**各自调用**。
   —— 复用的是判据，不是那个工具。§7 的意图（别让地址和凭据写死进脚本）完全保留。
3. 封样测试：`test_爬虫脚本里不许写死地址和凭据` 直接对源码文件跑那个校验函数。

**这条要写进 HANDOFF 的勘误**，否则下一个人会照着 §7 去调一个要 `case_id` 的工具，
然后被迫造假用例 —— 那是比不做更坏的结果。

---

## AD-4 · arq 分片拓扑

**§7 Q2-H 定了「必须分片」，但没定分几片、怎么注册、状态怎么收。** 本文定。

### 硬事实（核过）

```
backend/app/engine/worker.py:15   functions = [run_git_sync, run_automated_execution]
backend/app/engine/worker.py:17   max_jobs = 6
backend/app/engine/worker.py:18   job_timeout = 600
```

**`functions` 是白名单** —— 新 job 不加进这个列表，enqueue 之后**永远不执行、也不报错**，
在 redis 里躺着。这是 §7 完全没提的一步，而它的失败形态正是本次要修的那一类：
静默、无痕、看起来像"跑了但没结果"。

### 决策：每角色一片，survey 行做汇总

| | |
|---|---|
| 分片单位 | **角色**（1 主爬 + 5 浅扫 = 6 片） |
| 为什么不按域 | 域是**对账**的单位，不是爬取的单位。一次登录要能扫多个域的页面，按域分片会让同一个角色反复登录 6 次 —— 而登录本身是写操作（§7 Q2-I 风险 1），登录次数是要报给 QA 的账本项 |
| 单片预算 | 主爬 5–7 min < 600s，5 个浅扫每片 1–2 min。**都在 job_timeout 内** |
| `max_jobs=6` 的冲突 | 6 片刚好占满 worker。**必须限并发**：爬取片用 `Semaphore(2)`，理由同 `BATCH_CONCURRENCY=3` —— 6 个浏览器一起打被测环境，那是别人的机器 |
| 汇总 | 每片结束更新 `qa_page_survey` 的账本计数（原子 `UPDATE ... SET x = x + n`），最后一片把 `status` 推到终态 |

### 状态机（与 `QaCatalogReview.STATUSES` 同构，多一个终态）

```
pending → running → done
                  ↘ partial   ← 有片失败，但产物可用（对账降级，removed 一律 unknown）
                  ↘ failed    ← 一片都没成
                  ↘ dirty     ← L5 自检不等（爬前爬后 total 变了）
```

**`partial` 和 `dirty` 必须是独立终态，不能塞进 `done` 加个 flag。**
理由和 `batchesFailed` 是同一个：「少爬了一片」和「爬完了没问题」在页面上不能长得一样。
`dirty` 尤其不能降级成警告 —— 它意味着**我们可能动了别人的数据**，那是要人来看的。

---

## AD-5 · 前后端常量重复：有自动手段，PRD 那条判断可以推翻

**PRD NFR-9 写的是**：`AXES`/`DIM_KEYS`/`DIM_SPEC`/`DIM_SINCE` 前端是复制品，
「**没有任何自动手段护得住** ⇒ 进上线检查单」。

**这条我不同意，而且有具体做法。** 根因不是"常量天生要抄两份"，是
**`to_dict()` 不发 dims，所以前端只能自己重算 rollup**。
核过（2026-08-28，行号已从 HANDOFF §6 写的那组漂移）：`QaCatalog.jsx` 的 `AXES`:147 /
`DIM_KEYS`:164 / `DIM_SINCE`:169 / `dimRollup()`:175，渲染点 `:1252`。
把根因去掉，复制品就没有存在理由了。

**决策**：`to_dict()` **增发**（不删任何现有键，NFR-8 的形状约束不破）：

```python
"dims": dim_rollup(r.result),      # 后端算好的 rollup，前端直接渲染
"dimSpec": DIM_SPEC,               # 这条结论按哪一版口径评的
"axes": _axes_meta(),              # [{key, name, dims:[{key,name,why,since}]}]
```

前端改成**读 `dims`**；`AXES`/`DIM_KEYS`/`DIM_SINCE` 三份复制品删掉。
存量结论（`result` 里没有新键）由后端 `dim_rollup` 统一渲染 `?` —— 这个逻辑本来就在后端。

**保留一条前端兜底**：`dims` 缺失时（旧后端 + 新前端，CLAUDE.md 里那个"新前端 + 旧后端"的
经典症状）走现有的本地重算路径，并在抽屉顶上显示一行「后端未提供维度口径，本页数字为前端重算」。
—— 不兜底的话，后端没重启会渲染成一片假的 `?`，而那正是 CLAUDE.md 反复警告的那种坑。

**收益**：NFR-9 从「靠人记得看检查单」变成「后端加子项，前端自动跟上」。
`DIM_SPEC` 升版不再需要手工镜像 —— §7 Q2-G 那条 ⚠ 和 §9 第 30 条的前提随之解除。

**代价，写明**：`to_dict()` 的 payload 变大（24 个域 × 一份 rollup）。
**调用点已查，不是假设**：`api/qa_catalog.py` 有 5 处调 `to_dict`，其中
**`:251` 是列表接口**（`[to_dict(r) for r in rows]`）—— 历史结论列表一次能出几十行。
⇒ 签名改成 `to_dict(r, *, with_dims: bool = False)`，只有 `:218 / :230 / :280 / :298`
那四处详情接口传 `True`。**默认 `False` 是故意的**：漏传只会让详情页退到前端兜底
（看得见的降级），而默认 `True` 漏改列表接口是看不见的膨胀。

---

## AD-6 · 新表：迁移与索引

沿用 `models/qa_catalog_review.py` 那次「单独建表并写明理由」的先例，**不复用 `review_batches`**。

```
qa_page_survey            一次爬取一行
  id, project_id, env_id, env_name
  build_fingerprint, route_table_hash, roles(jsonb), status
  ledger(jsonb)      ← pagesPlanned/Visited/Failed/EmptyState, rolesPlanned/LoggedIn,
                        controlsFound, requestsObserved, writesAborted, loginCount, truncated
  started_at, finished_at, error
  UNIQUE (project_id, env_id, build_fingerprint, started_at)
  INDEX  (project_id, env_id, status)

qa_page_survey_item       每个可操作项一行 ← diff 的单位
  id, survey_id → qa_page_survey(id) ON DELETE CASCADE
  key            ← page_path + anchor（§7 Q2-C 定的）
  page_path, page_title, anchor, anchor_kind
  label, control_type, state            ← present/enabled/reachable
  roles_visible(jsonb), endpoints(jsonb)
  first_seen_survey_id, last_seen_survey_id
  UNIQUE (survey_id, key)
  INDEX  (project_id, page_path)        ← 对账按域取页，要能按 page_path 扫
  INDEX  (key)                          ← 两趟 diff 按 key 对齐
```

**`ledger` 用 jsonb 不用列**：账本项会随实现增长（§7 已经列了 10 项，落地肯定还会加），
每加一项一次迁移不现实。**但 `status` 必须是列** —— 它要进 WHERE 和索引。

**`UNIQUE (survey_id, key)` 是硬约束不是优化**：`key` 重复意味着 anchor 推断塌了
（比如整页都退化成 text 锚点、两个按钮同名），那时候 diff 会变成噪声源。
让它在写入时就炸，比在 diff 结果里表现成"新增 40 项"好查。

---

## AD-7 · 只读五层的代码落点

§7 Q2-F 定了五层是什么。本文定它们各自写在哪，以及**哪一层可以被单测**。

| 层 | 落点 | 可单测？ |
|---|---|---|
| L1 网络 abort | `app/engine/pw_conftest.py` 新增 `readonly_guard` fixture（`context.route("**/*")`） | ✅ 纯函数化判定：`is_write_request(method, url, allowlist)` 单独可测 |
| L2 不点写控件 | `surveys/qa_page_survey_crawl.py` 的动作词典 | ✅ `classify_control(label, role)` 单独可测 |
| L3 只读账号 | 编排层选角色 | ✅ 断言主爬用的是 `qa-auditor` |
| L4 凭证 drop | `qa_page_survey.py` 落库前，~~复用 `_mask_deep`~~ **按 HAR 形状 drop**（下方勘误） | ✅ 造一份带 Authorization 的 HAR ⇒ 库里搜不到 |
| L5 爬前爬后自检 | 编排层，不等 ⇒ `status='dirty'` | ✅ 桩两个不同的 total ⇒ dirty |

**决策：五层的判定逻辑全部提成纯函数**，Playwright fixture 只做"调用判定 + abort"。
理由：fixture 里的逻辑要起浏览器才能测，实际上就是不会被测。
NFR-1 说"单测每层各一条"，只有先纯函数化才写得出来。

**勘误（2026-08-29，S6.2 落地时实测）——上表 L4 那格原来写错了：**

- **`_mask_deep` 救不了 HAR。** 它按**键名**脱敏，而 HAR 把头名放在
  `{"name": "Authorization", "value": "Bearer …"}` 的**值**里 ——
  对这个形状结构性失明。实测：一份带三个凭证头的 HAR 喂进去，
  `Bearer …` / `session=…` / `Set-Cookie` **原样三个全在**。
  改为 `qa_survey_guard.drop_credentials()`：先按 HAR 形状整条剔除，
  `_mask_deep` 那套按键名的规则只留作兜底。
- 顺带记一条**反直觉**的：把深度上限从 12 改回 `_mask_deep` 的 6，
  **凭证不会漏**（到底了返回 `"…"` 不是原对象），漏的是**证据** ——
  整个 `request` 塌成省略号，而 HAR 是失败分类唯一的网络证据来源。
- **L1 的 fixture 不能放进 `pw_conftest.py`。** 那份 conftest 是**所有** UI 脚本
  共用的，普通用例脚本合法地做写操作，无条件挂上 `readonly_guard` 会把它们全 abort。
  ~~爬虫用自己那份 conftest（S6.3）。~~ **S6.3 实做时也没给它单独的 conftest**：
  爬虫不是 pytest 脚本、是 arq 任务，为它造一份 pytest 装置等于造一份**不会被执行**
  的装置 —— 只读保护看着有、实际没有。改成直接
  `context.route("**/*", make_readonly_guard(ledger))`，接线本身由
  `test_route_一定挂上了` 钉住（漏掉那行 = 整趟裸奔且不报任何错）。
- **`state` 这一版只产 `present` / `enabled`，不产 `reachable`**（上面第 216 行列了三档）。
  `reachable` 要真点进去才知道，这一版不点；把 `enabled` 当 `reachable` 写就是
  **把没验证过的事记成验证过了**，宁可少一档（`test_不许出现_reachable` 钉住）。
- L3 的判定也提成了纯函数（`pick_main_crawl_role` / `shallow_scan_roles`），
  不然它只是一句注释 —— 而它是五层里唯一由**对方系统**兜底的一层。

---

## AD-8 · 增量缓存键

§7 Q2-H 定了三档增量。本文把键写死，避免实现时各写一套：

```
survey 缓存键   = (project_id, env_id, build_fingerprint)
对账缓存键      = survey 缓存键 + route_table_hash + qa_commit_sha
```

- QA 仓 commit 变 ⇒ 只有对账键变 ⇒ **不爬**，只重算（秒级）
- `route_table_hash` 变 ⇒ 只重算 R 侧与 G2
- `build_fingerprint` 变 ⇒ 重爬（首次必须整站）

**产物必须写明用的是哪一趟爬取 + 时间 + 指纹**，沿用 `to_markdown()` 里
「这份结论靠得住吗」那一节的做法。§7 原话：**复用缓存却不说，就是把陈旧事实伪装成新鲜结论。**

---

## 对 PRD / HANDOFF 的三处修订（这一节是本文的净产出）

| # | 出处 | 原文 | 修订 | 为什么 |
|---|---|---|---|---|
| M1 | HANDOFF §7 Q2-B | 爬虫脚本「沿用 `lum_sync_ui_script` 门禁」 | 脚本落仓内文件；门禁**逻辑**提成共享函数各自调用 | 那个工具 `case_id` 必填，爬虫无用例可绑；照做要造假用例 |
| M2 | PRD NFR-9 | 前后端常量「没有任何自动手段护得住」 | `to_dict()` 增发 `dims`/`dimSpec`/`axes`，前端删复制品 | 根因是 `to_dict()` 不发 dims；去掉根因就不需要检查单 |
| M3 | HANDOFF §7 Q2-H | 「必须分片」 | 每角色一片 + `worker.py::functions` **必须注册** | 不注册的 job 静默不执行 —— 正是本次要修的失败形态 |

M2 会让 §9 第 30 条测试（`test_维度口径变了DIM_SPEC必须跟着升`）**从"只能断后端半边"变成能断全场**，
以及解除 §7 Q2-G 那条 ⚠。

---

## 落地顺序（在 §7 Q1-H 六步之上补新增的那几块）

§7 Q1-H 的 0–5 步不动。新增块插在其后：

| 步 | 内容 | 依赖 |
|---|---|---|
| 0–3 | §7 Q1-H 原样（测量 / 三态 / 去上限 / evidence 回验） | — |
| 4 | `env_gaps` 分档（独立） | — |
| 5 | `to_dict()` 发 dims + 前端删复制品（**AD-5**） | 无（可提前，且它让第 8 步变简单） |
| 6 | 爬虫脚本 + 五层纯函数 + 新表迁移（**AD-3 / AD-6 / AD-7**） | — |
| 7 | arq 分片 + 注册 + 状态机（**AD-4**） | 6 |
| 8 | 三方对账 + dim 白名单 + 删 `nextUp` + `DIM_SPEC=3` | 7、5 |

**AD-5 提到第 5 步而不是压在最后**：它减少第 8 步要同时改的地方（前端那份复制品），
而第 8 步是唯一一步同时动后端分类法、前端抽屉和 MCP 契约的 —— 那一步越薄越好。
