---
version: 0.1
date: 2026-08-28
status: draft
feature: qa-domain-review-redo
relatedPRD: prd-qa-domain-review.md
relatedHandoff: prd-qa-domain-review-HANDOFF.md
relatedArch: architecture-qa-domain-review.md
---

# Epics & Stories — QA 对账 · 域级 AI 评审（重做）

**编号沿用 PRD Step 11 的 Epic 0–9**（PRD 是契约，不重编号）。
架构文档新增的那一块落 **Epic 10**，但它的**执行顺序在 Epic 8 之前** —— 见文末顺序表。

每条 story 都挂着 **FR 编号**和 **HANDOFF §9 的封样测试编号**。
**★ = 哨兵测试**：某个决定被后人"简化掉"时唯一会红的那条。**没挂上测试的 story 不算写完。**

---

## Epic 0 — 量一下现在够不够（不落代码）

**这一步不产代码，产两个数。** 没有这两个数，Epic 2 的 `MAX_OUTPUT_TOKENS`
和能力位 `timeout_seconds` 就只能拍脑袋 —— 而 `qa_catalog_review.py` 的既有规矩是
「先量再定，把量到的数写进注释」（那份文件里已经有一段"2026-08-27 真去量了一遍"）。

- **S0.1** MCP 域（47 份脚本）临时去掉 `_rows` 的 `[:6]` 与提示词那行，`max_tokens=16000`
  跑一趟，记录：**单批最大 `completion_tokens`**、**单批墙钟耗时**、每批 `finish_reason`。
  AC：两个数写进 `qa_catalog_review.py` 常量区注释，照抄现有那段实测注释的写法。
  ⚠ 临时脚本 **cwd 必须是 `backend/`**，否则静默丢 `.env`，429 降级通道消失。
- **S0.2**（HANDOFF §7 J）强制走 claude-proxy(:38210) 发一次 `max_tokens=64`，
  看回来的 `finish_reason` 和 `usage` **还诚不诚实**。
  AC：结论写进 Epic 1 的三态注释里。**这一次实验决定 `unknown` 那一档有多常见** ——
  如果 CLI 通道两个凭据都不给，那 `unknown` 就是常态而不是边角料，S1.3 的渲染就得当主路设计。
- **S0.3** 顺带记洞四的现状：跑一遍 24 个域，统计**有几个域真的撞了 `MAX_BATCHES=8`**
  （那些域**现在正在线上谎报"全读了"**）。
  AC：一个域码列表。**这是洞四的爆破半径，S1.2 修完要拿它逐个验。**

---

## Epic 1 — 完整性三态 + 洞四（**线上已有缺陷**）

FR-1-2 / FR-1-4。**先只做检测，不做续跑**（续跑是 Epic 4，且条件启动）。
这一步不改任何结论内容，只让「说不清有没有漏」变成看得见。

- **S1.1** `_one` 停止丢弃 `LLMResponse` 的 `finish_reason` / `completion_tokens`，
  产出每批 `completeness ∈ {complete, truncated, unknown}`。
  判定：任一服务端事实命中 ⇒ `truncated`；两个凭据都拿不到 ⇒ `unknown`；都拿到且都没命中 ⇒ `complete`。
  AC + 测试：**#4** 满额那批标 truncated、**#5** `completion_tokens` 撞上限也算没说完、
  **★#6** `test_两个凭据都拿不到时是说不清不是说完了`（**断言 `unknown != complete`** ——
  三态被简化成布尔时唯一会红的那条）。
- **S1.2** `coverage` 增 `scriptsBatched = sum(len(b) for b in batches)`；
  `to_markdown` 两处**无条件断言**改成条件渲染：`:934`「这一趟读了 **N 份脚本的正文**」
  和 `:1088`「**N 份全读了**，不是抽了几份」（行号 2026-08-28 复核；
  另有 `:1082` 已经在做 `scriptsTotal > scriptsRead` 的条件渲染 ——
  **照它的写法抄**，那是同一个病的另一半，已经治对了一半）。
  `scriptsBatched < scriptsRead` ⇒ 换成警告，**写清差了几份**。
  AC + 测试：**★#9** `test_批数封顶丢掉的脚本要报出来`。
  **验收要拿 S0.3 那个域码列表真跑**：那几个域的 markdown 必须不再出现"全读了"。
- **S1.3** `completeness` 汇总进 `coverage`（`batchesIncomplete: [批次号]`）并进 `to_markdown`
  的「这份结论靠得住吗」那一节。**`unknown` 的渲染必须和 `complete` 明确不同。**
  AC：存量结论（没有 `completeness` 字段）渲染成"这一版口径没记"，**不渲染成 complete**。

> **洞四是本次唯一一个已经在线上的缺陷，而它正是这个模块存在的理由要抓的那类错 ——
> 静默、无痕、页面上和正常情况长得一模一样。它自己犯了一次。**
> 所以 **S1.2 优先级高于本 Epic 其余部分，可以单独发**。

---

## Epic 2 — 去上限（**三处必须同批**）

FR-1-1。依赖 Epic 1（**先能说清没漏，再放条数**）和 Epic 0（要那个数）。

- **S2.1** 四处**同一次**落地，少一处比现状更差：
  ① `_rows` 的 `[:6]` 删掉；② `_SYSTEM` 里「每一项最多 6 条」删掉；
  ③ `max_tokens` 从 2400 抬到 `MAX_OUTPUT_TOKENS`（= S0.1 实测最大 × 1.5，向上取千位）；
  ④ 能力位 `timeout_seconds`（= 实测耗时 × 2.5）—— **这是 DB 配置改动，必须写进上线步骤**。
  AC + 测试：**#1** `test_一批三十条结论一条都不许丢`（**替换**存量的
  `test_每一项最多留六条`，在 `backend/tests/test_qa_catalog_review.py:285` ——
  那是唯一一条会阻止本改动落地的存量测试）；
  **★#2** `test_提示词里不许再有条数上限`（断言 `"每一项最多" not in _SYSTEM`；
  **写窄一点，别误伤 brief 的「points 最多 3 条」** —— 那条是有意的）；
  **#3** 扫源码不许再有 `[:6]` —— ⚠ **这条必须写窄，否则要么永远红要么删错东西**：
  `qa_catalog_review.py` 里有**三处** `[:6]`，只有 `:460`（`_rows` 里那个）是目标；
  `:252` 的 `hits[name] = srcs[:6]`（env_gaps 每个变量留几条引用位置）和
  `:1026` 的 `splitlines()[:6]`（markdown 里 evidence 显示几行）**都该留**。
  写法：断言 `_rows` 函数体内不再有切片，别对整个文件做子串扫描；
  **#E** `test_MAX_OUTPUT_TOKENS装得下实测最多的那一批`（§9 的 E 条 ——
  对标现有的 `test_单份上限装得下实测最大的脚本`。**这条在 Epic 0 之前写不出来**，
  因为那个数还不存在；Epic 0 一交它就必须补上，否则「实测定值」这个约定下一次
  改动就会退化成拍脑袋）。
  ⚠ **只删 ①② 不做 ③ 的结果不是多出结论，而是 JSON 被截在半截 ⇒ `parse_result` 抛
  ValueError ⇒ 整批 12 份脚本一条结论都没有。比现状更差。**
- **S2.2** `_gap_key` 放宽（HANDOFF §7 F）：`scriptGaps` 不再按 `problem` 前 60 字去重
  （各批脚本本来不相交，这个去重几乎不干活，却在制造静默合并），
  `catalogGaps` **保持严格去重**（每批都看全量清单，真会重）。
  AC：造两条 `problem` 前 60 字相同、后文不同的 scriptGaps ⇒ 两条都在。
- **S2.3** `_rows` 里 `str(v)[:600]` 对 `evidence` 是**从行中间切断**（回验必然判 partial）⇒
  改成按行边界截，或截了就记 `evidenceTruncated`。
  AC：一条 700 字符的多行 evidence 截完仍是完整行。
  **这条必须排在 Epic 3 之前**，否则回验一上线就冒一片假 partial，
  然后第一反应会是"回验不准"而去放松回验 —— 修错地方。

---

## Epic 3 — evidence 回验

FR-2-1 ~ FR-2-4。新模块 `app/services/qa_evidence_check.py`（架构 AD-1 / AD-2）。

- **S3.1** 纯函数 `check_evidence(gaps, batch_scripts)` —— **参数名钉成 `batch_scripts`**
  （架构 AD-2：让代码本身说出"不许传全域"这个约束）。
  匹配：`haystack = re.sub(r"\s+", " ", 正文)`，`needles` 逐行归一化后做子串测试。
  AC + 测试：**#10** 原样抄认得出来、**★#11** `test_跨行拼接的判据也算数`
  （两条**非相邻**真实行拼成一段 ⇒ 通过。**任何"整块 exact match"的实现在这条上必红** ——
  这就是防那 27% 假阳的哨兵）、**#12** 换行重排也算数。
- **S3.2** 六态 `evidenceCheck ∈ {verbatim, stitched, reflowed, unmatched, wrong-path, empty}`
  ＋ `too_short` 兜底（归一化后 < 8 字符 ⇒ 不算验过；`fi` / `done` / `}` 在任何脚本里都命中，
  算通过等于把这道检查变成橡皮图章）。
  AC + 测试：**#13** 编出来的要标出来、**#14** 判据真但路径写错要分得出来（跟编造是两回事）、
  **#15** 太短的不算验过、**#18** 没判据标 `empty` 不标 `unmatched`。
- **S3.3** 调用点落在 `_one` 内、`parse_result` 之后 `return` 之前 —— **不是 merge 之后。**
  AC + 测试：**★#17** `test_回验必须在合并之前` —— A 批结论引用一句**只存在于 B 批脚本**
  里的正文 ⇒ 判 `unmatched`。**回验一旦被挪到 merge 之后，这条必红。**
- **S3.4** 处置**只打标记，不删、不降 severity**。
  AC + 测试：**#16** 搜不到的判据仍在输出里，只是带标记。
  > 删 ⇒ 丢了多少不可知，"一条没删"和"删了 8 条"页面上一模一样，**正是本模块要禁的形状**；
  > 降 severity ⇒ `severity` 是"对仓库多糟"，不是"我多确信"，两个正交轴合成一个
  > 还会污染 `_SEV_RANK` 的排序。
- **S3.5** 三处渲染/契约**必须一起改**，否则等于没改：
  ① `_merge_payload` 的预置数在回验**之后**算（否则 brief 的数和页面列的条数打架）；
  ② `to_markdown` 那句「✅ 每条都能十秒内被否掉」从**无条件承诺**改成**实测陈述**
  （「30 条里 29 条 grep 得到，1 条搜不到，已标出」）；
  ③ MCP `format=json` 每行带 `evidenceCheck`（QA 那边的 Claude Code 是照 `evidence` 去 grep 的，
  它需要知道哪条不值得 grep）。
  AC + 测试：**#19** 计数进覆盖率块且 brief 的数对得上、**#20** MCP json 每行带结论、
  **#21** `test_页面那句承诺跟着核验结果走`。
  > **这个模块最不该有的，就是一句自己没验过的承诺。**

---

## Epic 4 — 条件续跑（**条件启动，默认不做**）

FR-1-3。依赖 Epic 1。

- **S4.1** 判定 ≠ `complete` 才续跑一轮（`MAX_CONTINUATIONS = 1`），同一批脚本，
  user 消息追加「已报过的」**只给 `id + path + oneLine`**（约 30 token/条；
  给全了续跑的输入又把预算吃掉一半）。续完仍截断 ⇒ 落 `residualTruncated` 并在页面明说。
  AC + 测试：**#7** 续跑后仍没说完要留痕、**#8** 续跑不重复已报过的。

> **启动条件（这是判断，不是拖延）**：Epic 0/1 跑完后，若**没有任何一批**报 `truncated`，
> 本 Epic **不建** —— 那就是会腐烂的死代码。
> 光是 Epic 1 的检测已经满足治理要求：「说不清有没有漏」已经变成「这批没说完，我说了」。
> 决策依据 = S0.1 + S1.1 上线后一周的 `batchesIncomplete` tally。

---

## Epic 5 — `env_gaps` 三档（独立，可并行，随时可插）

FR-4-1 ~ FR-4-4。**纯代码，只取键名，不过模型**（红线：变量值是真凭证）。

- **S5.1** 三档 `absent` / `ambiguous` / `satisfied`：按 `_` 切段收集后段拼接做家族匹配
  （`ADMIN_PASSWORD` → `PASSWORD`），命中就降级并列出那 7 个真键名。
  提示词**只喂 `absent`**；`ambiguous` 单列并注明「名字对不上，不是真缺，
  **不许由它推出任何覆盖结论**」。
  AC + 测试：**#22** 7 组 `*_PASSWORD` ⇒ `ambiguous`。
- **S5.2** 两个护栏，**少一个就会把真阳一起吃掉**：
  ① 按下划线分段匹配，**不用 `endswith` 裸子串**（`qa_catalog_review.py` 的
  `_DYNAMIC_SUFFIX_RE` 注释里已经因为同一个原因被咬过一次，把真缺口整族吃掉过）；
  ② 尾段长度 ≥5 才参与家族匹配。
  AC + 测试：**★#23** `test_两个真缺口不许被家族匹配吃掉`（`UAG_APIKEY` / `PSQL_DSN`
  仍 `absent` —— **整组里最重要的一条**：修误报最容易的翻车方式，
  就是把真阳一起修掉，而且修掉之后页面变干净，看着像修好了）；
  **#24** 短尾段不参与（`DSN` 长度 3，保住 `PSQL_DSN`）；
  **#25** `SERVICE_TOKEN` 不许被 `VICE_TOKEN` 命中。
- **S5.3** **#26** `test_动态后缀在声明分支也要放过`（`wanted` 分支缺豁免，改起来免费）；
  **#27** 扩 `test_变量值一个字节都不外传`。
  AC：`ambiguous` 那一档列出的是**键名**，一个值都没有 —— 提示词里、日志里、页面上都没有。

---

## Epic 6 — 页面枚举爬虫

FR-3-3 + NFR-1 五层只读。架构 AD-3 / AD-6 / AD-7。**可与 Epic 1–5 并行。**

- **S6.1** 新表迁移 `qa_page_survey` + `qa_page_survey_item`（字段/索引见架构 AD-6）。
  AC：迁移可升可降；`UNIQUE (survey_id, key)` 在写入重复 key 时**炸**，不静默去重
  —— 重复 key 意味着锚点推断塌了，那是要看见的。
- **S6.2** 五层只读的**判定逻辑全部提成纯函数**（架构 AD-7）：
  `is_write_request(method, url, allowlist)` / `classify_control(label, role)` /
  HAR 落库前 **drop（不是脱敏）** `Authorization` / `Cookie` / `Set-Cookie`
  （HAR 里的 token 是**完整可用凭证**，body 一概不落库，产物只留角色名）/
  爬前爬后 total 自检。
  AC + 测试：五层各一条单测；L5 前后不等 ⇒ survey 标 `dirty`。
  **fixture 里只做"调用判定 + abort"** —— 写在 fixture 里的逻辑要起浏览器才能测，
  实际上就是不会被测。
- **S6.3** 爬虫脚本 `app/engine/surveys/qa_page_survey_crawl.py`，**仓内文件、走 code review**
  （架构 AD-3：`lum_sync_ui_script` 要 `case_id`，爬虫无用例可绑，
  照 §7 Q2-B 字面做就得造一条假用例）。
  门禁逻辑提成 `app/services/ui_script_guard.py::assert_no_hardcoded_endpoint_or_secret(content)`，
  MCP 工具与本模块各自调用。
  AC + 测试：`test_爬虫脚本里不许写死地址和凭据` 直接对源码文件跑那个校验函数。
- **S6.4** item 的 `anchor` 优先 testid（复用 `ui_selector_render.infer_kind`）。
  **diff 语义**：该页 `pagesFailed` 或 `pagesEmptyState` ⇒ 该页所有 item 的 diff
  一律降级 `unknown`，**不进 `removed`**。
  AC：两趟 diff 稳定（同一构建指纹跑两趟，`added` / `removed` 均为空）。
  > 「没走到这个页面」和「这个功能没了」在产物上长得一模一样 ——
  > 跟 `batchesFailed` 要治的是同一个病。
- **S6.5** 两个白捡的副产品：爬到的 testid 进 `lum_upsert_selectors`
  （**已有且人工登记过的绝不覆盖**，只记一条「爬到的与登记不符」进待整改；
  只能靠 text/style 定位的登记 `status='gap'`）；
  `services/review/checkup.py` 的 `observed_actions` 不传时**从 survey 表读**
  （今天靠 CC 手抄、上限 40 条）。
  AC：两条都是复用现有出口，**不新增第二份数据**。

---

## Epic 7 — 三方对账

FR-3-1 / FR-3-2 / FR-3-4 / FR-3-6 / FR-3-7。依赖 Epic 6。
新模块 `app/services/qa_coverage_reconcile.py`，**纯集合运算，不过模型。**

- **S7.1** `qa_catalog.py:44` 的 `_DOMAIN_RE` 扩到**第三列**（group 列表）——
  **它是 group→域码 映射的唯一来源**，现在只捕获 code + name，把第三列丢了（已核）。
  AC + 测试：`PUB` 与 `TEM/PRV/AGT/MCP` 重叠时不丢域 ——
  **映射必须是集合，写成 dict 单值会静默丢域。**
- **S7.2** 拉 `GET {BFF}/api/docs/routes` 建路由表（138:3000 实测 200 / 98 组 / 655 条）。
  **不可达时显式声明「本轮无路由表，G2 未验证」**，不静默少算一类缺口。
  AC + 测试：桩 404 ⇒ 结论里有声明且 G2 为 `notVerified`。
- **S7.3** 归一化复用 `branch_diff_service.normalize_path`，三处坑：
  group 名**大小写 + 单复数**归一（域码表自己警告过 2.1.1→2.2.0 改过写法，
  按字面比对会**凭空多出 7 个新组**）；`PUB` 按路径前缀定义且**故意与 TEM/PRV/AGT/MCP 重叠**；
  `Root` 组同属 SMK/MCP/SEC。
  AC：一对多映射，集合类型。
- **S7.4** 五类缺口 G1–G5 **由纯代码算出**，每条自带可 grep 的锚点。
  Q 侧端点抽取**宁可漏报不可误报**，抽不出来记进账本 `endpointsUnextracted`，
  **不当成「没打过」**。
  AC + 测试：三方账本造桩 ⇒ 五类各命中一条。
  ⚠ **边界写死**：QA 自己的 `check-route-drift.sh` 判的是「路由表 vs 基线 csv」，
  只发现端点新增；本设计新增的是**页面维度和角色维度**。
  **如果实现出来只剩 G2，那就是一个更慢的 route-drift，必须推翻重做。**
  ⚠ G3 默认严重度低于 G1，且**必须带 `endpointsUnextracted` 计数** —— 否则第一版
  喷出一片「你们没兑现」，然后 QA 那边合理地不再看这份报告。
- **S7.5** FR-3-6 **每个域声明本维度是否适用**，不适用标 `notApplicable`，
  **不给 0 分、不进 rollup 分母**。
  AC + 测试：`GW` / `NFR` / `PUB` / `SEC` 不产生假缺口。
  > **这条漏掉，新维度上线第一天就废** —— 会系统性地报「这个域缺口巨大」，
  > 其实只是那个域的功能页面上本来就看不到。
- **S7.6** FR-3-7 G1/G2 渲染成**可直接粘贴的清单表行**，编号取该域 max+1
  （**不填空洞**，清单明确「一经分配永不复用」）。
  AC + 测试：编号不复用已分配值。
- **S7.7** 角色：**产物层取并集**（`roles_visible[]` 多值），可见性矩阵纯代码算，
  「未探测」是**独立第三态**。**越权不进枚举产物** —— 矩阵算完后纯代码产一份越权候选清单，
  标 `SEC 域候选`，并显式写明「由矩阵推导，不是实测越权结果」。
  AC：「没观测到越权」在**任何出口**上都不渲染成「没有越权漏洞」。
  > 那是最毒的一种假绿。

- **S7.8** arq 分片（一角色一片：1 个深度只读爬 + 5 个浅爬，`Semaphore(2)`）
  + **在 `worker.py::functions` 注册** + 状态机 `pending → running → done / partial / failed / dirty`
  （`partial` 与 `dirty` 是**独立终态**）+ 增量缓存键（架构 AD-4 / AD-8）。
  AC + 测试：**未注册的 job enqueue 之后永远不执行、也不报错**（`functions` 是白名单）
  ⇒ 加一条封样测试断言 survey 函数在 `functions` 里。

---

## Epic 8 — 维度换血

FR-3-5 / FR-3-8 + NFR-9。依赖 Epic 7 **与 Epic 10**。

- **S8.1** FR-3-5 模型只做三件**带锚点的判断题**（G1 该补什么场景 / G4 值不值得测 / 域级小结），
  **拿不出源文锚点的结论直接丢弃**。
  AC + 测试：模型输出无锚点 ⇒ 被丢。
  ⚠ **风险**：实现时有人会图省事，让模型补「控件→端点」那条边 ——
  **那就把「猜」从场景层挪到了端点层，还更隐蔽，因为它看起来像事实。**
  三条边只许走 `observed` / `aborted`；`static` 只在构建指纹匹配时采信且**永久标记 source**；
  **模型推断禁止**，宁可 `endpoints` 为空。
- **S8.2** dim 按数组分白名单：`scriptGaps` 八个合法 dim（**不含 `coverage`**），
  `catalogGaps` 是 `{coverage, grain, shape}`，越界 coerce 并记**代码算的** `dimCoerced` 计数
  （否则 coerce 自己就成了新的静默行为）。
  AC + 测试：**#28** `scriptGaps` 不许落 `coverage`（coerce 且计入 `dimCoerced`）、
  **#29** `catalogGaps` 还能落 `coverage`。
  > `coverage` 维度**不整删** —— 对 `scriptGaps` 该删的理由完全成立，
  > 对 `catalogGaps` 完全不成立：那是它唯一的归宿，删了
  > 「缺了删除后的越权访问」只能硬塞进 `shape`，**比现状更糟**。
- **S8.3** `DIM_SPEC` 升到 3，新增键在 `DIM_SINCE` 标 3；
  **`coverage` 键名保留、定义换掉**（仍是「清单里就没有这条场景」，产出方式从猜变成 G1），
  新增 `claimed`(G3) / `blind`(G2)。
  AC + 测试：**★#30** `test_维度口径变了DIM_SPEC必须跟着升`；
  存量结论的新键渲染 `?` **不渲染 0**。
  > **改名比换定义更危险 —— 改名会让存量结论静默错位。**
  > 有了 Epic 10，这条测试从「只能断后端半边」变成能断全场。

---

## Epic 9 — 删 `nextUp`（任意时点）

NFR-8。

- **S9.1** 停止生成 + 停止渲染；MCP `format=json` 保留 `"nextUp": []` **一个周期**再摘键。
  **爆破半径逐个核过（2026-08-28），共 9 处**：
  `qa_catalog_review.py` 的 `:379`（提示词里的 JSON 样例）、`:479`（`_rows("nextUp")`）、
  `:593` + `:599`（merge 的空壳与循环）、`:652`–`:654`（`to_markdown`）、`:1067`；
  **MCP 三处**——`app/mcp/tools/qa_catalog.py:44`（工具描述）+ `:135`（真正的 payload）
  + `app/mcp/__init__.py:886`（工具描述）**这是对外契约，改描述不改 payload 等于没改**；
  前端两处 —— `QaCatalog.jsx:1424`（empty 计算含它）+ `:1506`（渲染点）；
  文档 `docs/qa-repo-readonly-catalog.md:286`。
  AC + 测试：**#31** `test_不再产出nextUp` + `test_旧结论里带nextUp时页面不崩`。
  > 删的理由要换：不是"没用"，而是**分批模式下它算错** —— 每批拿到完整场景清单
  > 但只有部分脚本，各产一份全域优先级，再按批次号拼接，**拼出来的顺序没有意义**。
  > 且它做的事（按 P/R 排序）代码能确定性地做，
  > 撞上 `qa_catalog_review.py` 自己的规矩「数和排序不许问模型」。

---

## Epic 10 — 后端发维度口径（架构 AD-5；**编号在后，顺序在前**）

替代 PRD NFR-9 的「进上线检查单」—— NFR-9 说这类前后端常量重复
「没有任何自动手段护得住」，根因其实是 `to_dict()` 不发维度，逼前端自己重算 rollup。
**独立，无依赖，越早做越省事。**

- **S10.1** `to_dict(r, *, with_dims: bool = False)` 增发 `dims` / `dimSpec` / `axes`；
  `app/api/qa_catalog.py` 只有 `:218 / :230 / :280 / :298` 四处**详情**传 `True`，
  **`:251` 列表接口不传**（已核：那是 `[to_dict(r) for r in rows]`，一次能出几十行）。
  AC：列表接口 payload 不变大。**默认关**是故意的 —— 忘了传是"少个字段"，
  而不是"列表接口悄悄胖了十倍"。
- **S10.2** 前端改**读 `dims`**，删掉 `QaCatalog.jsx` 的
  `AXES`:147 / `DIM_KEYS`:164 / `DIM_SINCE`:169 三份复制品
  （行号 2026-08-28 复核，HANDOFF §6 里那组已漂）。
  **保留一条兜底**：`dims` 缺失时走现有本地重算，并在抽屉顶上显示一行
  「后端未提供维度口径，本页数字为前端重算」。
  AC：**新前端 + 旧后端**（CLAUDE.md 里那个经典症状）不渲染成一片假的 `?`，
  而是显式说明降级 —— 那正是这个模块要治的病，自己身上更不能有。

---

## 建议开发顺序

```
Epic 0（测量，先做，不落代码）
  ├─ Epic 1（三态 + 洞四）        ← S1.2 可单独发，它修的是线上缺陷
  │    └─ Epic 2（去上限）
  │         └─ Epic 3（evidence 回验）   ← S2.3 必须排在它之前
  │              └─ Epic 4（条件续跑，条件启动）
  ├─ Epic 5（env_gaps，独立并行）
  ├─ Epic 10（后端发 dims，独立并行，越早越好）
  ├─ Epic 9（删 nextUp，任意时点）
  └─ Epic 6（爬虫，独立并行）
       └─ Epic 7（三方对账）
            └─ Epic 8（维度换血）  ← 依赖 7 与 10
```

**关键路径两条，可并行**：`0 → 1 → 2 → 3`（治理线）和 `6 → 7 → 8`（覆盖面线）。
Epic 5 / 9 / 10 随时插。

**Epic 8 是唯一一步同时动后端分类法、前端抽屉和 MCP 契约的** ——
先做 Epic 10 能让它变薄，这也是把 Epic 10 提前的唯一理由。

## 验收

24 个域重跑一遍，PRD 的 **S1–S6 全部可测量地满足**。

**另有 §9 的 F 条 —— 不是单测，是一次性实验，别漏**：
同一个域连跑两次，findings 交集 ≥ 70%。**这条从来没测过**，所以它既可能一次就过，
也可能暴露出这套评审的抖动大到结论不可用 —— 后者要在 Epic 3 之后、Epic 8 之前跑出来，
因为 Epic 8 要拿这些 findings 去改分类法。
S6 见 NFR-6 的告诫：`_NO_SAMPLING_PARAMS` 会摘掉 `temperature=0`，
**这套评审本来就不确定** —— 先上标记和计数，攒一个月 tally 再定阈值，
别现在就据一个样本定。

**两套 `tests/` 都要跑**（`backend/tests` 与根目录 `tests`），
根目录那套打的是真接口，且 `DATABASE_URL` 必须独占（它收尾 `drop_all`）。
