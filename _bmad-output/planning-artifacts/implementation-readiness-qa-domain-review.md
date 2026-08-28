---
version: 0.1
date: 2026-08-28
status: ready-with-notes
feature: qa-domain-review-redo
relatedPRD: prd-qa-domain-review.md
relatedHandoff: prd-qa-domain-review-HANDOFF.md
relatedArch: architecture-qa-domain-review.md
relatedEpics: epics-qa-domain-review.md
---

# 实施就绪检查 — QA 对账 · 域级 AI 评审（重做）

**这一步不是签字仪式，是把 epics 里引用的每个文件、行号、符号真去核一遍。**
理由很具体：HANDOFF §6 引用的前端行号已经漂了（`AXES` 142→147、rollup 1236→1252），
而**一份行号错了的交接文档，会让接手的人在错的地方读代码，然后得出错的结论** ——
这跟本模块要治的病是同一个。

结论：**可以开工**。下面是核出来的东西，分三类。

---

## A. 核过属实（不再复核，直接照用）

`backend/app/services/qa_catalog_review.py`：

| 符号 / 常量 | 行 | 用在哪个 story |
|---|---|---|
| `MAX_SCENARIOS = 200` / `MAX_SCRIPTS = 60` / `MAX_SCRIPT_BYTES = 18_000` | 53–55 | 背景 |
| `BATCH_SCRIPT_BYTES = 90_000` / `MAX_BATCHES = 8` | 57–58 | S0.3 / S1.2 |
| `_DYNAMIC_SUFFIX_RE` | 95 | S5.2 ①（被同一个原因咬过一次的那处注释） |
| `parse_result` / `_rows` | 440 / 458 | S2.1 ① |
| `_rows` 里 `[:6]` | 460 | S2.1 ①（**唯一的目标**，见 B-2） |
| `_rows` 里 `str(v)[:600]` | 462 | S2.3 |
| `split_batches` | 555 | S1.2 |
| `_SEV_RANK` | 577 | S3.4（降 severity 会污染它） |
| `_gap_key` | 581 | S2.2 |
| `_merge_payload` | 632 | S3.5 ① |
| `"scriptsRead": len(scripts)` / `"batches"` | 736 / 738 | **洞四根因** |
| `DIM_SPEC = 2` / `DIM_SINCE` | 806 / 809 | S8.3 |
| `dim_rollup` | 814 | S10.1 |
| `to_dict` | 1106 | S10.1 |
| 提示词「每一项最多 6 条」 | 335 | S2.1 ② |
| 提示词「`points` 最多 3 条」 | 618 | S2.1 ②**的误伤对象，别删** |

`backend/tests/test_qa_catalog_review.py`：
`test_每一项最多留六条`@**285**（S2.1 要**替换**它）、
`test_单份上限装得下实测最大的脚本`@**584**（#E 的对标写法）。

复用点全部存在：`branch_diff_service.normalize_path`@54、
`ui_selector_render.infer_kind`@125、`review/checkup.py` 的 `observed_actions`@146/159
（**现有 `[:40]` 上限确认在 159 行** —— S6.5 要替掉的正是这个手抄上限）、
`qa_catalog.py::_DOMAIN_RE`@44、`engine/worker.py::functions`@15（`job_timeout = 600`@18）、
`engine/pw_conftest.py`、`engine/har.py`。

`api/qa_catalog.py` 的 `to_dict` 五个调用点：**218 / 230 / 251 / 280 / 298**，
其中 **`:251` 确认是列表**（`[to_dict(r) for r in rows]`）—— S10.1 的「默认关」是对的。

`QaCatalog.jsx`：`AXES`@**147**、`DIM_KEYS`@**164**、`DIM_SINCE`@**169**、`dimRollup`@**175**。

---

## B. 核出来的偏差（已改进 epics，记在这里防回退）

**B-1 · `to_markdown` 两处行号 HANDOFF 说的是 933/1089，实际是 934/1088。**
顺带发现 **`:1082` 已经在做 `scriptsTotal > scriptsRead` 的条件渲染** ——
也就是说同一个病，`split_batches` 这一半漏了、`MAX_SCRIPTS` 那一半治对了。
**S1.2 照 `:1082` 的写法抄就行，不用自己设计。**
（这条本身是个提示：洞四不是没人想过，是想过一半。）

**B-2 · 封样测试 #3「扫源码不许再有 `[:6]`」按字面写会出错。**
文件里有**三处** `[:6]`：
- `:460` `for x in (data.get(key) or [])[:6]` —— **结论条数上限，是目标**
- `:252` `hits[name] = srcs[:6]` —— env_gaps 每个变量留几条引用位置，**该留**
- `:1026` `str(g["evidence"]).splitlines()[:6]` —— markdown 里 evidence 显示几行，**该留**

按字面做整文件子串扫描，结果只有两种：永远红，或者有人为了让它绿把后两处也删了。
**#3 必须断言 `_rows` 函数体内不再有切片。**
HANDOFF 已经给 #2 写了「写窄一点别误伤」，#3 漏了同一句 —— 现在补上了。

**B-3 · `nextUp` 的爆破半径 HANDOFF 说 5 处，实际 9 处。**
关键差别在 MCP 那边是**三处而不是一处**：
`mcp/tools/qa_catalog.py:44`（工具描述）、**`:135`（真正的 payload）**、
`mcp/__init__.py:886`（工具描述）。
**只改两处描述不改 `:135` 的 payload，等于没改，而且看起来像改了。**

**B-4 · 洞四不是线上缺陷，是潜伏缺陷 —— 这条是本次唯一一处必须改掉的交接结论。**
S0.3 跑了两个仓全部 38 个域：**撞 `MAX_BATCHES` 的域 0 个**，每域「进批 == 读到」。
且按常量算**撞不上**：每个闭合批 `used > BATCH_SCRIPT_BYTES - MAX_SCRIPT_BYTES = 72_000`，
凑 8 批要前 7 批 `> 504_000`，而 `take_scripts` 把合计钉在 `TOTAL_SCRIPT_BYTES = 480_000`。
**`break` 是死代码**（解析推导 + 20000 次随机搜索最大 7 批 + 定向构造最坏情况，三种方式一致）。

机制本身是真的，`break` 也确实违反了它自己上面三行的 docstring
（「切的是调用，不是内容 —— 每一份脚本都会被读到」）。**改的是定性，不是要不要修。**
重点跟着挪了：原方案给一个不会发生的事件加仪表（S1.2「丢了要报」），
现在改成①**把「为什么现在不会发生」写成会红的断言**（新 ★#9b：
`(MAX_BATCHES-1)*(BATCH_SCRIPT_BYTES-MAX_SCRIPT_BYTES) >= TOTAL_SCRIPT_BYTES`）
②**删掉那个不省钱只留坑的 `break`**（新 S1.2b）。

为什么这不是"没事了"：`TOTAL_SCRIPT_BYTES` 调大过 504_000 而没动 `MAX_BATCHES`，
静默丢当场开始，**而现在没有任何东西会告诉他**。MCP 域脚本数已从 HANDOFF 记的 47 长到 49 ——
调大预算不是假想改动。**今天不丢靠的是两个常量恰好的比例，而这个比例没写在任何地方。**

顺带更正两个实测数：UAG/`MCP` 域现在是 **80 场景 / 49 份脚本 / 6 批**（HANDOFF 记 75/47）。

---

**B-5 · Epic 0 跑完发现一个**比洞四严重**的活缺陷：跨批去重一条都没生效。**
实测（UAG/`MCP`，2026-08-28）：六批喂进 `scriptGaps=36 / catalogGaps=19 / nextUp=18`，
`merge_results` 之后 **36 / 19 / 18，去重 0 条**。`nextUp` 那 18 行只有 3 件事
（`MCP-76`/`77`/`79`），页面上渲染成编号优先级清单，`MCP-76` 占第 1/4/7/10/13/16 位。
根因是 `_gap_key` 后两段用自由文本（`problem`/`why` 截 60 字），换个措辞就是新键。
**跟洞四的区别：洞四是潜伏的（S0.3 证明够不着），这条是每一次评审都在发生的。**
详情、原始数据与三条推论写在 epics 的「Epic 0 的副产物」一节（副-A ~ 副-E）。
**它不改开工顺序**（仍是 Epic 0 → S1.2），但它把 **Epic 9 从"可做可不做"变成了有实测支撑**，
且新增一条 Epic 9 之外的欠账：`catalogGaps` 的键也得换（Epic 9 只删 `nextUp`）。

---

## C. 开工前必须知道的三个约束（不是缺陷，是环境事实）

**C-1 · worktree 里没有 `.venv`。**
`.claude/worktrees/qa-domain-review-impl/backend/.venv` 不存在（venv 不受 git 跟踪）。
跑测试和临时脚本用主检出那个解释器：`/home/dreamer/lumiere/backend/.venv/bin/python`。
**Epic 0 的临时脚本 cwd 仍必须是 `backend/`**（否则静默丢 `.env`，429 降级通道消失）——
这两条合起来意味着：**cwd 用 worktree 的 `backend/`，解释器用主检出的**。
⚠ **2026-08-28 实测补充：Bash 工具的 cwd 在两次调用之间不保留**（harness 会
`Shell cwd was reset`）。所以不能"先 cd 一次后面接着用"，**每条命令都要自带
`cd /…/qa-domain-review-impl/backend &&`**。本次就是靠这个漏掉一次，12 发全 429
且不降级 —— 症状正如 CLAUDE.md 所写：**静默**，没有任何一行提示 `.env` 没加载。
⚠ 还有一处同源的坑：光把 endpoint 旁路到 claude-proxy **不够**，
`_get_timeout()` 取的是能力位的 `timeout_seconds`（现值 120s），
而 proxy 正常走的是模块里写死的 `_PROXY_TIMEOUT = 600`。
只换 endpoint 不换 timeout ⇒ **6 批整整齐齐全在 120.1s 超时**。

**C-2 · `job_timeout = 600` 是 worker 全局的**，不是每 job 的。
Q2-H 已经据此定了分片方案，但要看清余量：主爬 5–7 分钟 = 300–420s，
**离 600s 只剩 30% 余量**。S7.8 的分片是「能不能跑起来」的前提，不是优化；
真跑超了**不许调 `job_timeout`**（那会一起放松 git_sync 和 execution 的超时），
只能再切细。
⚠ **S0.1 实测把这条从"Epic 7 的事"变成了"Epic 2 的事"**：域评审自己就已经在射程内。
单批墙钟实测 **237 – 404s**；`BATCH_CONCURRENCY = 3` 时 6 批要跑两波，
两波之和轻易过 600s。而 Epic 2 要把 `max_tokens` 从 2400 提到 10000，
**输出变长 ⇒ 单批更慢**，这个 600 会先于任何别的东西撞上。
撞上的表现是**整个域的评审直接没了**（job 被杀），不是"慢一点"。
⇒ Epic 2 的 AC 里必须带一条：提 `max_tokens` 的同一批改动里，
要么把域评审也分片，要么先量一趟提上限之后的单批墙钟。

**C-3 · 两套 `tests/`，且根目录那套要独占 `DATABASE_URL`。**
Epic 6/7 加迁移和新表 ⇒ **必须跑根目录那套**（打真接口）。
库名用自己的，别用默认 `lumiere_test`（它收尾 `drop_all`，共用会互删表，
报出来是几十上百条假 red 而代码一行没错）。

---

## 开工顺序

按 epics 文末那张图。**第一件事是 Epic 0**（不落代码，产两个数），
第一件**落代码**的事是 **S1.2** —— 它修的是唯一一个已经在线上的缺陷，
且可以单独发。
