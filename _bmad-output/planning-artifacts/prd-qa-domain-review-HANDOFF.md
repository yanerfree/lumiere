# 交接：QA 对账 · 域级 AI 评审（重做）

> 写给**接着做这件事的人**。看完这一份就能接着干，不用回看聊天记录。
> 2026-08-28 · 交接自 Claude Code 会话

---

## 1. 一句话

QA 对账页面上那个「域级 AI 评审」已经在跑了，但**它自己说不清有没有漏、判据没人验、覆盖面是猜的**。
这次把这三样修掉。PRD 在 `_bmad-output/planning-artifacts/prd-qa-domain-review.md`
（愿景/判据/旅程 + 功能与非功能需求 + Epic 拆分都写完了）；
**两个设计问题的方案在本文 §7，可以直接照着开工。**

## 2. 做到哪了

| | |
|---|---|
| PRD | 愿景/执行摘要/成功判据 S1–S6/用户旅程/领域约束 + **FR/NFR + Epic 拆分**，都在 PRD 文件里 |
| 设计 | **两个设计问题都有完整方案**（本文 §7 Q1 A–J、Q2 A–J），关键断言我逐条核过属实 |
| 代码 | **一行没动**。这是纯规划阶段 |
| BMAD 说明 | 前 5 步走的是正规 `/bmad-create-prd` 流程；**Step 6–12 是我压缩着一次写完的，没走完整交互**（用户要求尽快交接）。要按 BMAD 正规流程重走，改前言 `stepsCompleted` 回退即可 |
| 下一步该干嘛 | 照 §7 Q1-H 的六步顺序开工，**第一步是 Step 0 测量**（`max_tokens` 现在够不够）—— 那个数不测出来，§9-E 那条测试写不了 |

**注意**：PRD 的默认输出名 `prd.md` 被平台主 PRD（v3.5）占着，所以这份单独叫
`prd-qa-domain-review.md`，符合本仓「一功能一文件」的惯例。

## 3. 已经定死的，别重开

1. **不做评分。** 现有六维加权分，同一条用例两次能给出 86 和 78。抖动的数当不了闸门，
   也没法照着改。结论只留「这个域要不要停下来处理」。
2. **QA 仓永远只读**，也**不要求对方仓库加任何字段/文件/CI 钩子**。
   （`app/services/qa_catalog.py` 只允许 `show`/`ls-tree`/`rev-parse`/`log`/`grep`/`symbolic-ref`，
   `backend/tests/test_qa_catalog_parse.py` 有封样测试扫源码，出现写子命令直接红。）
3. **`env_gaps()` 是纯代码，绝不过模型**；环境变量**只取键名，绝不取值** —— 值里是真凭证。
4. **三个出口不变**：页面抽屉 / Markdown 导出 / MCP `lum_get_qa_review`。只改内容质量，不改形状。
5. 顶层维度就是人认得的那三块：**覆盖面 / 场景设置 / 断言**。别换成实现视角的黑话
   （上一轮换过一次，用户当场说「你写的都是什么维度」「我咋看不懂」）。

## 4. 三个洞（这是要修的东西，附实测数据）

### 洞一：说不清有没有漏
`parse_result` 的 `_rows()` 取 `[:6]`（`qa_catalog_review.py:460`），prompt 里也写「每一项最多 6 条」（`:335`）。
实测某域 `batches:5`、`scriptGaps:30` —— **正好 6×5，上限是打满的**。
被丢掉多少条，代码里查不出来。**「说不清有没有漏」比「漏了」更糟。**

⚠ **但 6 条上限不是唯一的墙，这一点我一开始判错了。** `_one` 传的是 `max_tokens=2400`
（`:677`），6 条 scriptGaps + brief + summary 本来就在 2400 附近。
**只删 `[:6]` 和提示词那行、不抬 `max_tokens`，结果不是多出结论，而是 JSON 被截在半截 →
`parse_result` 抛 ValueError → 整批进 `batchesFailed`** —— 从「悄悄丢 N 条」变成「整批 12 份脚本一条都没有」，比现状更差。三处必须同一次落地。

⚠ 另外：`30 = 6×5` 是**一个样本，不是一条铁律**。`_one` 虽然传了 `temperature=0`，但
`llm_client._NO_SAMPLING_PARAMS`（`llm_client.py:42`）对 `claude-opus-5`/`claude-sonnet-5`
会把采样参数**静默摘掉**（这两个模型收到采样参数直接 400）。**这套评审本来就不是确定性的。**
方向不变（上限确实在咬），但别拿"输出确定所以 30 稳定"当论据。

### 洞二：判据没人验
每条 finding 带 `evidence`，页面和文档都写着「从脚本正文原样抄」，读的人靠它 grep 定位。
**代码从来不检查这句话是不是真的。**

手工验过的基线（可以当回归集）：30 条里 **22 条正文逐字命中、7 条跨不相邻行拼接（每行都真）、
1 条 reflow、0 条编造**。结论碰巧是对的，但没有任何东西保证它对。

⚠ **踩过的坑**：第一版校验用朴素精确匹配，误报了 8/30（27%）。
**必须逐行比对 + 空白归一化**（`re.sub(r"\s+", " ", …)`），否则造出一堆假警报。

### 洞三：覆盖面是猜的
`coverage` 维度回答「这个域还缺什么场景」，事实来源是**没有** —— 模型看着清单编。
要换成：真登录被测系统，逐页枚举用户能看见能点的东西，再和场景清单对账。

### 洞四：**已经在线上的一个活体反例**（我原来不知道，设计评审挖出来的）

`split_batches` 在 `if len(out) >= MAX_BATCHES: break` 处**静默丢掉剩下的脚本**（`:568`），
而 `coverage.scriptsRead = len(scripts)`（`:736`）数的是**从 git 读出来的份数**，不是**进了某一批的份数**。

于是一个超过 8 批的域，页面和 Markdown 照样渲染「这一趟读了 **N 份脚本的正文**」（`:934`），
**而其中一部分模型根本没见过。**

`:1082` 那处自证只比了 `scriptsTotal > scriptsRead`，拦不住这一种。

**这就是这套评审存在的理由本身的一个反例，而且已经在线上。**
修法一行：`coverage` 增 `scriptsBatched = sum(len(b) for b in batches)`，
`scriptsBatched < scriptsRead` 时那句「全读了」改成警告。

### 附带一条：`env_gaps()` 误报
它拿脚本里的变量名和平台环境键名硬比，名字不同就报缺。
实测环境里有 `ADMIN_PASSWORD`/`PLATADMIN_PASSWORD` 等 **7 组角色账号**，它照样报「缺 PASSWORD」。
这是误报，不是缺口。

## 5. 被测环境（这条我一开始判错过，别再判错）

我曾经写「缺被测环境地址和账号，覆盖面检查做不了」—— **错的，账号早就配在平台里**。

- 位置：Lumiere → 项目 **UAG** → 项目配置 → 环境配置 → **`uag-138:3000`**
- 前端 `http://192.168.51.138:3000`（已 curl 验证 200）
- 登录 `POST /api/auth/login` —— **没有 v1**，写成 `/api/v1/auth/login` 会 401（备注里写着，别踩）
- 网关 `http://192.168.51.138:8000`
- 7 组角色账号：`admin`(super_admin) / `qa-platadmin` / `qa-auditor`(只读) /
  `qa-ta-lead` / `qa-ta-user` / `qa-tb-lead`(跨团队越权对照)，密码同一个，**值在平台环境配置里，不写在这**
- admin 登录已实测拿到 super_admin token

**教训**：动手前先看平台的环境配置（页面 / `lum_list_environments`），别凭印象说"没有"。

## 6. 代码落点（`backend/app/services/qa_catalog_review.py`，1213 行）

| 行 | 是什么 | 这次要动吗 |
|---|---|---|
| 54–59 | 五个上限常量 + `BATCH_CONCURRENCY=3` | **要**：条数上限、max_tokens、批次数三者耦合 |
| 216 | `env_gaps()` 纯代码 | **要**：修误报 |
| 271–385 | `_SYSTEM` prompt（九个 `dim` 键 + 「每一项最多 6 条」在 335 行） | **要** |
| 386 | `build_payload()` —— 只放变量**名**进 prompt | 不动（红线） |
| 440–481 | `parse_result()` —— `[:6]` 在 460 | **要**：加 evidence 回验 |
| 535 / 555 | `take_scripts()` / `split_batches()` | 可能要 |
| 586 | `merge_results()` —— 取最坏结论、按 `_gap_key` 去重 | 可能要 |
| 612 | `_MERGE_SYSTEM` —— 二次 pass，只看结论不看代码 | 可能要 |
| 659–742 | `run_review()` —— `return_exceptions=True` / `batchesFailed` / `dimSpec` | **要** |
| 806–809 | `DIM_SPEC = 2` / `DIM_SINCE` | **要**：加子项必须 `DIM_SPEC += 1` |
| 1106 | `to_dict()` —— **不发 dims** | **要**（见下） |
| 1127 | `execute()` | 视情况 |

**一个已知的隐患**：前端 `frontend/src/pages/qa/QaCatalog.jsx` 自己抄了一份
`AXES`(142) / `DIM_KEYS`(162) / `DIM_SPEC` / `DIM_SINCE`，**从原始 result 重算 rollup**（1236 行），
因为 `to_dict()` 不发 dims。注释写着「必须和后端逐字一致」，**但没有任何测试拦这件事**。
加子项时前后端不同步 → 页面和 Markdown 导出会给出不同的数字。

## 7. 还没定的（两个设计问题）

两个设计问题**都已经做完方案（下面全文）**。两份方案的每条关键断言我都自己回仓库/回
被测环境核过 —— Q1 推翻了我自己两个判断、挖出一个线上缺陷（洞四）；Q2 推翻了我
「覆盖面必须靠模型猜」的前提。**没核过的地方我在原地标了「未验证」，别当结论用。**

### Q1 · 全量匹配 + evidence 回验 —— **已有方案，关键断言我逐条核过属实**

**A. 完整性三态，不是布尔**（这是「能自证没漏」的落点）

三个证据源，`_one` 里全都拿得到，**一个新依赖都不用加**：

| 证据 | 来源 | 现状 |
|---|---|---|
| `finish_reason ∈ {length, max_tokens}` | `LLMResponse.finish_reason` | **已经有，`_one` 直接扔了** |
| `completion_tokens >= max_tokens - ε` | `LLMResponse.completion_tokens` | 已经有，也扔了 |
| JSON 顶层闭合 | `parse_result` | 已隐含 |

任一服务端事实命中 → `truncated`；两个都拿不到（CLI 降级通道很可能就是这样）→ `unknown`；
都拿到且都没命中 → `complete`。

**`unknown` 的渲染必须和 `complete` 明确不同。** 这是整个方案最容易被后人"简化成布尔"的地方，
要有专门的封样测试。

**B. `max_tokens` 定多少 —— 它不是自由变量**

`llm_client.complete` 是非流式，超时取能力位的 `timeout_seconds`（默认 120s）。
**只抬 `max_tokens` 不抬 `timeout_seconds`，只是把「截断」换成「读超时」。**

定法（照抄本文件既有规矩：先量再定，把量到的数写进注释）：
1. **Step 0（不落代码）**：MCP 域（47 份）临时去掉条数上限、`max_tokens=16000` 跑一趟，
   记 **单批最大 `completion_tokens`** 和 **单批墙钟耗时**。
2. `MAX_OUTPUT_TOKENS` = 实测最大 × 1.5，向上取千位。
3. 能力位 `timeout_seconds` = 实测耗时 × 2.5。**这是 DB 配置改动，要写进上线步骤** ——
   代码合了配置没动，第一趟就红。
4. **不要为此上流式。** 抬 timeout 就够了，改流式要重写累积和解析。

**在测到之前把数写死 = 又造一个拍脑袋的常量。**

**C. 批内第二趟：条件续跑，不做无条件二遍**

无条件跑两趟 = 24 域 × 8 批成本翻倍，而绝大多数批本来没到上限。
判定 ≠ `complete` 才续跑一轮（`MAX_CONTINUATIONS = 1`），同一批脚本，
user 消息追加「已报过的」**只给 `id + path + oneLine`**（约 30 token/条，
给全了续跑的输入又把预算吃掉一半）。续完仍截断 → 落 `residualTruncated` 并在页面明说。

> ⚠ 设计评审自己也不确定这一条值不值得建：如果 Step 0 测完 `max_tokens≈8000` 后没有任何一批
> 报 `truncated`，续跑就是会腐烂的死代码。**建议第 1 步只做检测就先发**，攒到真有
> `truncated` 样本再建。光是检测已经满足治理要求 ——「说不清有没有漏」变成「这批没说完，我说了」。

**D. evidence 回验：落在 `_one` 里，`parse_result` 之后 `return` 之前**

**不是 merge 之后**，理由是硬的：`part`（这一批的正文）只在 `_one` 作用域里。
放到 merge 之后只能拿**全域脚本**当草堆，于是「第 3 批的结论引用了第 5 批脚本里的一句」
会判成通过 —— **而那正是最该抓的一种漂移**（模型在没有正文的情况下编出了一句恰好存在的代码）。
正文已在内存（`take_scripts` 读过），**QA 仓一次都不用再碰**。

匹配算法（对应我手工验出的三类）：
```
haystack = re.sub(r"\s+", " ", 整份脚本正文)      # 换行压成空格 → 覆盖「重排」那 1 条
needles  = [归一化(l) for l in evidence.splitlines() if l.strip()]
```
逐行子串测试 → 全中 `verbatim`（**跨行拼接那 7 条自动落这里**）／部分中 `partial`／
一条不中先在**本批其它脚本**里找：找到 `wrong_path`（判据真、路径错，跟编造是两回事）、
仍找不到 `unmatched`；空 `empty`；**归一化后短于 8 字符落 `too_short`**
（`fi`/`done`/`}` 在任何脚本里都命中，算通过等于把这道检查变成橡皮图章）。

**处置选「打标记」，不删不降级**：
- 删 → 丢掉多少不可知，「一条没删」和「删了 8 条」页面上一模一样，正是要禁的形状
- 降 severity → `severity` 是「对仓库多糟」不是「我多确信」，两个正交的轴合成一个，
  还会污染 `_SEV_RANK` 排序
- 打标记 → 条目全留、可数、`evidence` 搜不到本身成为一条关于评审质量的信号

配套三处**必须一起改**，否则等于没改：① `_merge_payload` 的预置数要在回验**之后**算
（否则 brief 的数和页面列出的条数打架）；② `to_markdown` 那句「✅ 每条都能十秒内被否掉」
从**无条件承诺**改成**实测陈述**（「30 条里 29 条 grep 得到，1 条搜不到，已标出」）——
**这个模块最不该有的就是一句自己没验过的承诺**；③ MCP `format=json` 每行带 `evidenceCheck`，
QA 那边的 Claude Code 是照 evidence 去 grep 的。

**E. 该删什么 —— 我原来的判断被推翻了一半**

- **`nextUp` 删**，但理由要换：不是"没用"，是**分批模式下它算错** —— 每批拿到完整场景清单
  但只有部分脚本，各产一份全域优先级，再按批次号拼接，**拼出来的顺序没有意义**。
  且它做的事（按 P/R 排序）代码能确定性地做，撞上本文件自己的规矩「数和排序不许问模型」。
  **爆破半径 5 处**：`parse_result`/`merge_results`、`to_markdown`、
  `app/mcp/tools/qa_catalog.py:44` + `app/mcp/__init__.py`（**这是对外契约**）、
  `QaCatalog.jsx:1424`(empty 计算含它)+`:1506`、`docs/qa-repo-readonly-catalog.md:286`。
  **做法**：停止生成+停止渲染，MCP `format=json` 保留 `"nextUp": []` 一个周期再摘键。
- **`coverage` 维度不整删 —— 我判错了一半。** 我的理由对 `scriptGaps` 完全成立
  （scriptGaps 锚在一份**确实声明了某场景**的脚本上，「清单里没这条」不可能是它的属性，
  而且这种条目**产不出 evidence**）；对 `catalogGaps` **完全不成立** —— 那是它唯一的归宿，
  删了「缺了删除后的越权访问」只能硬塞进 `shape`，比现状更糟。
  **改成按数组分白名单**：`scriptGaps` 八个合法 dim（不含 coverage），
  `catalogGaps` 是 `{coverage, grain, shape}`，越界 coerce 并记**代码算的** `dimCoerced` 计数
  （否则 coerce 自己成了新的静默行为）。动分类法必须 `DIM_SPEC` 升到 3 + 补 `DIM_SINCE`
  + **手工镜像前端那份复制品**。

**F. 顺手要收的两处（同类问题）**

- `_gap_key` 每字段截 60 字符（`:582`）。今天每批 ≤6 条无害；放开到 30+ 条后，
  同一份脚本上两条不同结论只要 `problem` 前 60 字相同就**静默合并**。
  而各批脚本本来不相交，这个去重对 `scriptGaps` 几乎不干活却在制造风险 →
  **`catalogGaps` 保持严格去重**（每批都看全量清单，真会重），**`scriptGaps` 放宽**。
- `_rows` 里 `str(v)[:600]` 对 `evidence` 是**从行中间切断**，回验必然判 `partial` →
  改成按行边界截，或截了就记 `evidenceTruncated`。

**G. `env_gaps` 修法：不删，改分档**（纯代码，只用键名）

危害不在那条假阳本身，在于**它跟 `UAG_APIKEY`/`PSQL_DSN` 这两个真缺口用同样的置信度并排显示** ——
一条响亮的假阳会让人把整列当噪音，两个真阳一起被无视。

三档：`absent`（真没有）／`ambiguous`（存在**角色前缀家族**：把 env_key 按 `_` 切段收集后段拼接，
`ADMIN_PASSWORD`→`PASSWORD`，命中就降级并列出那 7 个真键名）／`declared_only`。

**两个护栏，少一个就会把真阳一起吃掉**：① **按下划线分段匹配，不用 `endswith` 裸子串**
（本文件在 `_DYNAMIC_SUFFIX_RE` 注释里已经因为同一个原因被咬过一次，把真缺口整族吃掉过）；
② **尾段长度 ≥5 才参与家族匹配** —— 保住 `PSQL_DSN`(DSN=3)。
提示词只喂 `absent`，`ambiguous` 单列并注明「名字对不上，不是真缺，不许由它推出任何覆盖结论」。

**H. 落地顺序**（每步单独可上线、单独有价值）

| 步 | 内容 |
|---|---|
| 0 | **测量**（B 的 Step 0），不落代码 —— 后面两个常量都等它 |
| 1 | 完整性三态 + `coverage` 新字段 + `scriptsBatched` —— 不改结论内容，先让「说不清」看得见 |
| 2 | 去上限：提示词那行 + `[:6]` + `max_tokens` + 能力位 `timeout_seconds`，**必须同批** |
| 3 | evidence 回验 + 标记 + 计数 + 三处渲染/契约 |
| 4 | `env_gaps` 分档（独立，随时可插） |
| 5 | dim 白名单 + 删 `nextUp` + `DIM_SPEC` 升版（都动前端抽屉和 MCP 契约，合成一次发布） |

**I. 一条会挡路的存量测试**

`tests/test_qa_catalog_review.py:285` `test_每一项最多留六条`（断言 `len == 6`）
**必须改成它的反面**。这是唯一一条会阻止改动落地的存量测试。

**J. 还没验证的一件事**

`stop_reason` 经 claude-proxy(:38210) CLI 降级通道之后**是否还诚实**，代码里看不出来。
这正是坚持三态 + `completion_tokens` 交叉校验的原因。
想确定：强制走 proxy 发一次 `max_tokens=64`，看回来的 `finish_reason` 和 `usage`，一次实验就够。

### Q2 · 覆盖面枚举 —— **已有方案；核心结论是「这件事大半不需要模型」**

#### Q2-A 最重要的一条：覆盖面是集合运算，不是判断题

挖出来一条此前没被利用的事实链，**我已经在 138 上实测验证**：

```
GET http://192.168.51.138:3000/api/docs/routes    ← 公开、无需 JWT、只读
实测 2026-08-28：200，77KB，98 个 group / 655 条路由
（网关 :8000 上这条是 404 —— 别打错端口）
```

而 QA 清单 `docs/test-scenario-catalog.md` 的域码表**第三列就是 API 组名**（已核实原文）：

```
| `MCP` | MCP 能力 | MCP-Tools, MCP-Upstreams, MCP-Permissions, MCP-Import, ... |
| `POL` | 策略与审批 | Policy-Rules, Approvals |
| `PUB` | 对外公共 API | **按路径前缀 `/api/public/v1/*` 划定（18 条）**，外加 ... |
```

于是这条链**每一跳都是纯代码**：

```
页面控件 →(HAR 观测)→ 实际请求 →(normalize_path)→ 路径模板
        →(路由表 group)→ API 组 →(域码表第三列)→ 域码
        →(清单场景行)→ 场景 ID →(@scenario 头 + 脚本正文 URL)→ 有没有脚本真打过
```

**新的 `coverage` 主体是一张三方账本，不是一次模型调用。** 这直接兑现了「纯代码能判的
绝不给模型」。第一个、也是最小的改动：`qa_catalog.py:44` 的 `_DOMAIN_RE` 现在
**只捕获 code + name，把第三列丢掉了**（我核过源码，属实）——它是 group→域码 映射的唯一来源。

#### Q2-B 谁来跑：平台跑；但第一版由 CC 写、CC 首跑

**先纠正一个前提**（我原来也搞错了）：`cc-platform-loop-spec.md` 红线 1 封的是
**AI 驱动的 UI 脚本生成**（playwright-mcp :38931 + `cli_agent.py`），理由是
① 复杂用例 12 分钟 ② 探索阶段真在被测系统建数据且不清理 ③ 只量过 2 条。
**被封的不是 Playwright 运行时。** 平台今天有一套活着的**确定性**浏览器执行能力：
`backend/.venv` 的 pytest+playwright、`~/.cache/ms-playwright` 三个 chromium、沙箱执行器、
HAR 采集，以及 `backend/app/engine/pw_conftest.py`（我核过）里**钉死的
viewport 1280×720 + locale**、成对 `<X>_USERNAME`/`<X>_PASSWORD` 自动成角色、
`LOGIN_URL` 默认 `/api/auth/login`（正好对上「没有 v1」那条备注）。

判据：**逐页枚举的产物是可重放的事实，不是一次性推理结果 —— 归平台。**
更硬的理由：**viewport 和 locale 会改变「用户能看见什么」**（菜单窄屏折叠、按钮进
overflow、中英文案不同）。爬虫跑在各人开发机上，两次 diff 出来的「新增/消失」大半是
屏幕宽度造成的噪声。平台把这两个变量钉死了，CC 的机器没有。

| 阶段 | 谁 | 产物 |
|---|---|---|
| 探路（哪些页面进得去、菜单结构、空列表怎么处理） | CC 本地浏览器，一次性 | 一份**确定性爬虫脚本** |
| 回推 | CC → 平台 | 脚本正文入库，沿用 `lum_sync_ui_script` 门禁（硬拦写死地址与凭据） |
| 每次执行 | 平台 arq worker | survey 产物 + 完整性账本 |
| 对账出结论 | 平台（纯代码为主） | review 的 `coverage` 维度 |

**红线 1 的三条理由本设计一条都不触碰**（爬虫是固定脚本、没有模型在环；只读拦截、
不点写控件、不建数据）。**这段论证必须写进 `docs/qa-repo-readonly-catalog.md`** ——
否则下一个人看到「平台又在跑浏览器」会当成红线复活、整个推翻。

#### Q2-C 枚举产物：一张可 diff 的表 + 一本完整性账本

新建表（**不复用 `review_batches`**，沿用 `models/qa_catalog_review.py` 那次「单独建表
并写明理由」的先例）：

- **`qa_page_survey`**（一次爬取一行）：`project_id`/`env_id`/`env_name`、
  `build_fingerprint`（index.html 的 asset hash）、`route_table_hash`、`roles[]`、
  `status`（与 `QaCatalogReview.STATUSES` 同构），**账本**：
  `pagesPlanned / pagesVisited / pagesFailed / pagesEmptyState / rolesPlanned /
  rolesLoggedIn / controlsFound / requestsObserved / writesAborted / truncated`
- **`qa_page_survey_item`**（每个可操作项一行，这是 diff 的单位）：
  `page_path`（归一化路由模板 `/agents/{id}`）、`page_title`、
  **`key` = `page_path` + `anchor`**、`anchor`/`anchor_kind`（直接复用
  `ui_selector_render.infer_kind`：testid>id>role>semantic>structure>text>style）、
  `label`（控件文案**逐字**，evidence 用）、`control_type`、
  **`state`（present/enabled/reachable 三态）**、`roles_visible[]`、
  `endpoints[]`（`{method, path_template, source}`）、
  `first_seen_survey_id`/`last_seen_survey_id`

**anchor 优先 testid 不只是稳定性偏好**：拿文案当 key，每次 UI 文案微调都会产出
一堆假的「新增+消失」。

**diff 语义里最要紧的一条：`removed` 默认不是结论，是待定。**
「没走到这个页面」和「这个功能没了」在产物上长得一模一样 —— 跟 `run_review()` 里
`batchesFailed` 要治的是同一个病。该页 `pagesFailed` 或 `pagesEmptyState` ⇒
该页所有 item 的 diff 一律降级 `unknown`，**不进 removed**。

**两个白捡的副产品（这是「别重复造」的正解）**：
- 爬到的 testid 就是 `lum_upsert_selectors` 登记表的原料。表里没有 → 新建
  `status='active'` 来源标 `crawl`；只能靠 text/style 定位 → 登记 `status='gap'`+`gap_note`，
  自动进 `lum_next_duty` 的「待补 testid」队列；**已有且人工登记过的绝不覆盖**，
  只记一条「爬到的与登记不符」进待整改。
- `services/review/checkup.py` 的 `observed_actions` 今天靠 CC 手抄、上限 40 条 ——
  改成不传时按模块/域**从 survey 表读**。这是复用，不是重复。
- **不复用 `lum_proxy_capture`**：它是全局 mitm，混着 Vite 热更新噪声（实测 156 条里
  只有 9 条 `/api/`），且与页面导航没有时序对应；Playwright HAR 天生按 context 分角色、
  按导航分段，`engine/har.py` 的 `parse_har_dir` 已处理多 context 分文件。
  **这条取舍要写进文档**，免得下一轮又有人接一遍代理。

#### Q2-D 角色：并集是产物，矩阵是结论，越权是候选不是观测

1. **产物层取并集** —— item 表全项目唯一，`roles_visible[]` 是多值字段。
   同一个「审批通过」按钮按角色存 6 份，会让 diff 变成 6 倍噪声，而它其实是**一个**功能点。
2. **可见性矩阵纯代码** —— `item × role → 见/不见/未探测`。
   **「未探测」必须是独立第三态**：某角色登录失败或某页没走到，不能渲染成「该角色看不见」。
3. **覆盖面按并集算、按角色标注** —— 任一角色能看见，它就是产品功能面的一部分，
   QA 就该有场景；角色只是缺口的属性，不改变缺口成不成立。

**越权不进枚举产物。** 「qa-tb-lead 能不能看到 team A 的东西」是**断言**不是观测。
塞进来会有两个坏结果：① 要求爬虫主动尝试越权 —— 那就不再是只读枚举了；
② 「没观测到越权」会被误读成「没有越权漏洞」，**这是最毒的一种假绿**。
正确位置：矩阵算完后纯代码产一份**越权候选清单**（「只有 A 队 team_admin 可见，
但同为 team_admin 的 B 队也可见」这类矩阵内部不一致），标 `SEC 域候选`，
并显式写明「由矩阵推导，不是实测越权结果」。

**成本剪枝同时也是安全剪枝**：主爬只用只读角色 `qa-auditor` 全站深爬（只读账号
在服务端就没有写能力，是 §Q2-E 之外的第四道保险）；其余 5 角色只做可见性差分
（登录 → 菜单/首页/各域列表页首屏 → 记存在与否 → 登出，不进详情、不翻页、不点任何东西）。
6× 压到 ≈2×。**代价必须写明**：auditor 看不到的创建类入口，其端点观测只能由有权限
角色的浅层遍历补，深度必然不如主爬 —— 这是有意识的取舍，不是遗漏。

#### Q2-E 对账：端点是唯一的连接键，五类缺口全由代码算

三个事实源（全部只读）：**R** 路由表（`/api/docs/routes`）、**P** 页面枚举（HAR）、
**Q** QA 仓（已有的 `clone --bare` + `git show`）。
Q 侧端点抽取沿用 `env_gaps()` 的套路：纯正则扫 `$API/`、`$BFF/`、`curl` 行 ——
**宁可漏报不可误报**，抽不出来的记进账本 `endpointsUnextracted`，**不当成「没打过」**。

归一化复用 `branch_diff_service.py:54` 的 `normalize_path`，三处坑必须处理：
group 名**大小写 + 单复数**归一（域码表自己就警告过 2.1.1→2.2.0 改过写法，
按字面比对会凭空多出 7 个新组）；**`PUB` 是唯一按路径前缀定义的域**且**故意与
TEM/PRV/AGT/MCP 重叠**，`Root` 组同属 SMK/MCP/SEC ⇒ **「端点→域码」是一对多，
数据结构必须是集合，写成 dict 单值会静默丢域**。

| 类 | 定义 | 含义 | blame |
|---|---|---|---|
| **G1** | ∈P ∧ ∈R ∧ ∉Q | 页面上点得到、清单里一条场景都没有 —— **最硬的缺口** | catalog |
| **G2** | ∈R ∧ ∉P ∧ ∉Q | 端点在、页面到不了、也没人测 | catalog |
| **G3** | ∈P ∧ 清单认领了该域 ∧ 无脚本打过该端点 | **认领了没兑现** | script |
| **G4** | ∈P ∧ 控件无任何请求 | 纯前端行为（排序/筛选/弹窗） | 需判断 |
| **G5** | present 但 `enabled=false`，点了既无请求也无路由 | 死按钮/flag 关掉/未实现 | 情报，不是缺口 |

G1/G2/G3 完全由代码算出，每条自带可 grep 的锚点。
**边界要写死**：QA 自己的 `check-route-drift.sh` 判的是「路由表 vs 基线 csv」，
只能发现端点新增；本设计新增的是**页面维度和角色维度**。
**如果实现出来只剩 G2，那就是一个更慢的 route-drift，必须推翻重做。**

模型只做三件事，每件都必须给逐字锚点、拿不出源文锚点的结论直接丢弃：
① G1 每条一句「该补什么场景」+ 建议 P/R/层；② G4 这个纯前端控件值不值得测；③ 域级小结。

产物形状对齐消费方动作：G1/G2 直接渲染成**可粘贴的清单表行**
（`| \`POL-27\` | 审批列表批量驳回 | P1 | 6 | ui | ⬜ |`），编号取该域 max+1
（清单明确「一经分配永不复用」，**不填空洞**）。渠道不变，仍是
HTTP export / 抽屉复制 / `lum_get_qa_review` —— **只能是他来拉，不是我们推**。

#### Q2-F 只读的强制手段（五层，不靠嘱咐）

| 层 | 手段 | 拦住什么 |
|---|---|---|
| **L1 网络** | Playwright `route()`：method ∉ {GET,HEAD,OPTIONS} ⇒ `route.abort()`；例外只有**精确 URL allowlist**（`POST {origin}/api/auth/login`，可选 logout） | 任何写请求落地 |
| **L2 行为** | 默认档**根本不点写控件**：白名单只点 nav/tab/pagination/filter/detail-link；按动作词典（删除/移除/保存/提交/新建/审批/驳回/禁用/启用/重置/导入…）识别的只登记存在性 | 确认框后的二段写 |
| **L3 账号** | 主爬用只读角色 `qa-auditor`，服务端就写不动 | L1/L2 的漏网 |
| **L4 数据** | HAR 落库前 **drop**（不是脱敏）`Authorization`/`Cookie`/`Set-Cookie` —— HAR 里的 token 是完整可用凭证；body 不入库，再过 `_mask_deep`。凭证只从环境变量注入，产物只存**角色名** | 凭证进库/进日志/进 prompt |
| **L5 自检** | 爬前爬后各拉一次若干列表页的 total，不等就把本次 survey 标 `dirty` 并报警 | 前四层全漏网 |

另需一个**探边档**（默认关，须显式开）：在一次性 context 里点写控件，靠 L1 abort 拿到
method+path，拿到即销毁 context，产物 `source=aborted`。
**默认关是因为 abort 会让前端进错误态、污染后续页面。**

**控件 → 端点这条边是整个设计的软肋**，因为只读枚举天然观测不到写操作。
三条路只许走前两条：✅`observed`（HAR 真观测到）、✅`aborted`（L1 拦下但拿到了
method+path —— 拦截既是闸门又是事实来源，这是最划算的一处）、
⚠️`static`（前端源码静态提取，**只在构建指纹匹配时采信**且永久标记 source）、
❌**模型推断：禁止**，宁可让 `endpoints` 为空。为空的写控件**按页面归域**
（该页 GET 命中的 group 所属域）—— 保守近似，但它是代码推的、可复现的。

#### Q2-G 域适用性 —— 这条漏掉，新维度上线第一天就废

24 个域里有相当一部分**根本不经过前端页面**：`GW`（Kong :8000 数据面）、`NFR`、
`PUB`、`SEC`（Internal/WebSocket/鉴权矩阵）、`SMK` 的一部分。
拿页面枚举去评这些域，会**系统性地报「这个域缺口巨大」**，其实只是页面上本来就看不到。
所以**每个域必须声明本维度是否适用**，不适用的标 `notApplicable` 并写原因，
**不给 0 分、不进 rollup 分母**。

维度键的处置：**`DIM_SPEC` 升到 3**，新增键在 `DIM_SINCE` 里标 3；
**`coverage` 键名保留、定义换掉**（仍是「清单里就没有这条场景」，但产出方式从猜变成 G1），
新增 `claimed`(G3) / `blind`(G2)。**改名比换定义更危险 —— 改名会让存量结论静默错位。**
⚠ 前端 `QaCatalog.jsx` 的 `AXES`/`DIM_SINCE`/`dimRollup` 是复制品，两处同改，
漏一处渲染就错位（§9 第 30 条说明了为什么这条只能靠上线检查单）。

#### Q2-H 耗时与增量

- 主爬（auditor）~65 页 × 4–6s ≈ **5–7 分钟**；5 角色浅扫 ≈ **4–6 分钟**；
  对账纯代码**秒级**；模型按域批 ≈ **1–3 分钟**。
- **硬约束：`backend/app/engine/worker.py:18` 的 `job_timeout = 600`（我核过）。
  全量一趟必然超 ⇒ 必须分片**（每角色一个 job 或每域一个 job，survey 行做汇总）。
  这不是优化，是能不能跑起来的问题。
- 增量三档：**QA 仓 commit 变了 ⇒ 完全不爬**（survey 按
  `(project_id, env_id, build_fingerprint)` 缓存，只重跑对账，秒级）；
  `route_table_hash` 变了 ⇒ 只重算 R 侧和 G2；`build_fingerprint` 变了 ⇒ 重爬
  （可按域，**首次必须整站**）。
  产物里必须写明**这次用的是哪一趟爬取、爬取时间、指纹** ——
  沿用 `to_markdown()` 里「这份结论靠得住吗」那一节的做法。
  **复用缓存却不说，就是把陈旧事实伪装成新鲜结论。**

#### Q2-I 风险，按我判断排序

1. **爬虫本身会污染 QA 的断言（最容易漏、后果最难归因）。** 6 个角色真登录，会在被测
   系统留下**会话和审计日志**。`OBS` 域 Audit、`SEC` 域审计链的用例若断言「审计条数」
   「最近登录记录」，我们正好污染它们 —— **而且只读拦截拦不住，因为登录本身就是写**。
   需要：爬取账号与 QA 用例账号分离、爬取与回归执行队列互斥（至少错开时段）、
   产物里记录本次产生的登录次数供 QA 排查。表现是 QA 的用例时红时绿，**像 flaky**。
2. **域适用性（Q2-G）不做 ⇒ 上线即废。**
3. **前端源码不能当事实源。** `/home/dreamer/agw/default/web` 是本地 worktree，
   旁边还有 `/home/dreamer/agw/fixbugs-v3/web`，**和 :3000 上跑的构建是否一致完全未验证**；
   且 `src/lib/navigation-config.ts` 的 `Role` 联合类型是
   `"super_admin"|"team_admin"|"team_member"|"auditor"` —— **没有 `platform_admin`**，
   而环境里 `qa-platadmin` 就是 platform_admin。**二者必有一个过时。**
   静态源码只能当**对照**，采信条件是构建指纹匹配。
4. ~~路由表端点是否可达~~ —— **我已实测：138:3000 上 200 / 98 组 / 655 条，此风险解除。**
   但退化方案仍要写：不可达时用 HAR 观测自建**局部**路由表，并在结论里显式声明
   「本轮无路由表，G2 类缺口未验证」，**而不是静默少算一类**。
   另：QA 仓缓存里 `docs/api-routes.csv` **取不到**（`git show` 报 path 不存在），
   好在域码表自带 group 列不依赖它 —— 但这说明**缓存 ref 可能滞后**，
   需按 `_pick_ref` 那个已知问题复核。
5. **控件→端点这条边观测不到写操作。** 处理是「宁可空着，按页面保守归域」。
   风险在于实现时有人图省事让模型补这条边 —— **那就把「猜」从场景层挪到了端点层，
   还更隐蔽，因为它看起来像事实。**
6. **红线 1 的观感风险**（见 Q2-B，必须在 docs 里留显式论证）。
7. **一个你可能高估的收益：G3 会一次性爆出很多条**，因为脚本正文 URL 抽取必然不完备
   （变量拼接、helper 封装）。所以 G3 默认严重度应低于 G1，且**必须在结论里带
   `endpointsUnextracted` 计数** —— 否则第一版喷出一片「你们没兑现」，
   然后 QA 那边合理地不再看这份报告。

#### Q2-J 落点（除 §6 那张表之外）

| 文件 | 改什么 |
|---|---|
| `backend/app/services/qa_catalog.py:44` | `_DOMAIN_RE` 扩到第三列（group 列表）—— **group→域码 映射的唯一来源** |
| `backend/app/engine/pw_conftest.py` | 多角色登录 fixture 已有；**只读 route 拦截层（L1）加在这里** |
| `backend/app/services/branch_diff_service.py:54` | `normalize_path()` 复用，对账的归一化连接键 |
| `backend/app/engine/har.py` | `parse_har_dir` 已处理多 context 分文件，直接用 |
| `backend/app/engine/worker.py:18` | `job_timeout=600` ⇒ 分片 |
| `frontend/src/pages/qa/QaCatalog.jsx` | `AXES`/`DIM_SINCE`/`dimRollup` 复制品，必须一字不差同改 |

## 8. 验证怎么做

```bash
# 单测/结构封样，~12s
cd backend && .venv/bin/python -m pytest tests/test_qa_catalog_review.py -q      # 现有 78 条

# API/E2E —— DATABASE_URL 必须独占，这套的 db_session 收尾会 drop_all
cd /home/dreamer/lumiere && DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/lumiere_test_<你的名字>' \
  backend/.venv/bin/python -m pytest tests/ -q

# 改完后端必须重启（故意不带 --reload）
bash deploy/restart-backend.sh
```

**两套 `tests/` 都要跑**，根目录那套打的是真接口。
**临时脚本的 cwd 必须是 `backend/`**，否则静默丢 `.env`，429 降级通道消失。

## 9. 要新增的封样测试

★ = 哨兵：它抓的是**代码测试抓不到的那一半**，或者是某个决定被后人"简化掉"时唯一会红的那条。

**A 组 · 上限被悄悄改回来**
1. `test_一批三十条结论一条都不许丢` —— **替换** `:285` 那条 `test_每一项最多留六条`
2. ★ `test_提示词里不许再有条数上限` —— 断言 `"每一项最多" not in _SYSTEM`
   （只删 `[:6]` 不删提示词那行，上限照样在；写窄一点别误伤 brief 的「points 最多 3 条」）
3. `test_parse_result不再按固定条数切片` —— 扫源码不许有 `[:6]`（本文件既有惯例）
4. `test_满额那批要标成没说完` / 5. `test_completion_tokens撞上限也算没说完`
   （守 CLI 通道谎报 finish_reason 那条路）
6. ★ `test_两个凭据都拿不到时是说不清不是说完了` —— **断言 `unknown != complete`**。
   三态被简化成布尔时唯一会红的测试
7. `test_续跑一轮之后仍没说完要留痕` / 8. `test_续跑不重复已报过的`
9. `test_批数封顶丢掉的脚本要报出来` —— `scriptsBatched < scriptsRead` 时 markdown
   不再出现「全读了」（**洞四的封样**）

**B 组 · evidence 回验被绕开**
10. `test_原样抄的判据认得出来`
11. ★ `test_跨行拼接的判据也算数` —— 两条**非相邻**真实行拼成一段 ⇒ 通过。
    **任何"整块 exact match"的实现在这条上必红** —— 这就是防那 27% 假阳的哨兵
12. `test_换行重排的判据也算数`（手工验出的那 1 条）
13. `test_编出来的判据要标出来` / 14. `test_判据是真的但路径写错要分得出来`
15. `test_太短的判据不算验过`（`fi`/`done`）
16. `test_搜不到的判据不许被删掉` —— 守住「打标记不删除」这个决定
17. ★ `test_回验必须在合并之前` —— A 批的结论引用一句**只存在于 B 批脚本**里的正文 ⇒
    判 unmatched。**回验一旦被挪到 merge 之后，这条必红**
18. `test_没有判据的标成empty不标成搜不到`
19. `test_核验计数进覆盖率块且brief的数跟它对得上`
20. `test_MCP的json每行带核验结论`
21. `test_页面那句承诺跟着核验结果走` —— 防那句自己没验过的承诺漂回来

**C 组 · `env_gaps`**
22. `test_角色前缀的同名变量不算真缺`（7 组 `*_PASSWORD` ⇒ ambiguous）
23. ★ `test_两个真缺口不许被家族匹配吃掉` —— `UAG_APIKEY`/`PSQL_DSN` 仍是 `absent`。
    **整组里最重要的一条**：修误报最容易的翻车方式就是把真阳一起修掉
24. `test_短尾段不参与家族匹配` / 25. `test_家族匹配按下划线分段不按结尾子串`
    （`SERVICE_TOKEN` 不许被 `VICE_TOKEN` 命中）
26. `test_动态后缀在声明分支也要放过`（`wanted` 分支缺豁免，改起来免费）
27. 扩 `test_变量值一个字节都不外传`

**D 组 · 维度与 nextUp**
28. `test_scriptGaps不许落coverage维度`（coerce 且计入 `dimCoerced`）
29. `test_catalogGaps还能落coverage维度`
30. `test_维度口径变了DIM_SPEC必须跟着升` —— **后端只能断后端这半边；
    前端那份复制品没有任何自动手段护得住，只能写进上线检查单**
31. `test_不再产出nextUp` + `test_旧结论里带nextUp时页面不崩`

**E · 还差一条不能现在写的**
`MAX_OUTPUT_TOKENS` 该断言「装得下实测最多的那一批」（对标现有的
`test_单份上限装得下实测最大的脚本`），但**这个数现在还不存在** —— 必须先做 Step 0 测量。

**F · 稳定性实测**（不是单测，是一次性实验）
同一个域跑两次，findings 交集 ≥ 70%。**这条从来没测过。**
注意 `_NO_SAMPLING_PARAMS` 会摘掉 `temperature=0`，所以**这套评审本来就不确定** ——
先上标记和计数，攒一个月 tally 再定任何阈值，别现在就据 30 条一个样本定阈值。

---

## 10. 勘误（写于本文之后，**与正文冲突时以本节为准**）

本文写在动代码之前。后面走 BMAD 的架构决策、Epic 0 实测和 Epic 1/2 落地，推翻了正文里的七处。
**按正文原样照做会踩坑的，全在这儿。**

### 10.1 §4「洞四」的定性错了：机制是真的，「线上正在谎报」不成立

正文说 `split_batches` 撞 `MAX_BATCHES` 会静默丢脚本、页面照样写「全读了」——
**机制描述完全正确**，`break` 确实在丢，`scriptsRead` 确实数的是从 git 读到的份数，
而且那个 `break` 违反了它自己上面三行的 docstring。

不成立的是「**已经在害人**」。S0.3 实测（2026-08-28，全部 38 个域）：
**一个域都没撞上限**；而且按常量算**撞不上** ——
8 批需要 `7 × (90_000 - 18_000) = 504_000` 字节，而 `TOTAL_SCRIPT_BYTES` 封在 `480_000`。
贪心装箱下每批至少装到 `BATCH_SCRIPT_BYTES - MAX_SCRIPT_BYTES` 才会开新批，
所以**总预算根本喂不出第 8 批**。这是结构性的不可达，不是"目前碰巧没超"。

⚠ **这不是"洞四不用修"**，而是**修的重点要挪**：
从「丢了要报出来」（给一个不会发生的事件加仪表）
挪到「**把『为什么现在不会发生』变成一句会红的断言**」（★#9b）
外加删掉那个不省钱只留坑的 `break`（S1.2b）。
今天不丢是因为预算恰好比封顶紧 —— 谁把 `TOTAL_SCRIPT_BYTES` 调过 504_000
而没动 `MAX_BATCHES`，静默丢就当场开始，**而在 ★#9b 之前没有任何东西会告诉他**。
（MCP 域脚本数已从 47 长到 49，调大预算不是假想的改动。）

⚠⚠ 更该记的是**为什么会错**：正文把「洞四是活体缺陷」当成了不用验的依据 ——
而这个模块要治的病，恰恰是「结论看起来有据、依据其实没验过」。**它自己差点犯第二次。**

### 10.2 §4 之外还有一个**比洞四严重**的活缺陷（每次评审都在发生）

`merge_results` 的**跨批去重一条都没生效**。实测（UAG/`MCP`，六批）：
喂进 `scriptGaps=36 / catalogGaps=19 / nextUp=18` ⇒ 合并后 **36 / 19 / 18，去重 0 条**。
`nextUp` 那 18 行其实只有 3 件事，页面渲染成编号优先级清单后
`MCP-76` 占第 1/4/7/10/13/16 位。根因：`_gap_key` 后两段用自由文本
（`problem`/`why` 截 60 字），模型换个措辞就是一个新键。
**跟洞四的区别：洞四潜伏，这条每一趟都在发生。**
→ 落 Epic 9，且带出一条 Epic 9 之外的欠账：`catalogGaps` 的键也得换。

### 10.3 §7 Q1 系列：三态**不能**靠改 `LLMResponse` 默认值来做

正文的方案是把 `finish_reason` / `completion_tokens` 的默认值改掉以区分"没报"。
**不够。** S0.2 实测：claude-proxy 那条 CLI 降级通道**键全在、值全是编的**
（`usage` 三项恒 0、`finish_reason` 恒 `"stop"`、连 `max_tokens=64` 都不理会，
照样返回 1891 字符）。改默认值区分不出「通道没报」和「通道报了个常量假值」。

实现改成：新增 `LLMResponse.reported: frozenset[str]`，由 `complete()` 按原始 JSON 填；
判据是 **`prompt_tokens == 0` 而 prompt 非空**（一个**可证伪**的假值 —— 输入就摆在那儿，
不可能 0 个 token；12/12 命中）。`reported` 的默认值**必须是空集**。

⚠ 且 S0.2 那一趟网关额度耗尽，**12/12 次调用全走 CLI 通道** ——
也就是说 `unknown` 是**主路**不是边角情况，S1.3 的渲染要按主路设计。

### 10.4 §7 的三处修订（来自架构决策文档，M1–M3 原表在那份文档末节）

| # | 正文出处 | 正文原话 | 改成 | 为什么 |
|---|---|---|---|---|
| M1 | §7 Q2-B | 爬虫脚本「沿用 `lum_sync_ui_script` 门禁」 | 脚本落仓内文件；门禁**逻辑**提成共享函数各自调用 | 那个工具 `case_id` 必填，爬虫无用例可绑；照做要造假用例 |
| M2 | PRD NFR-9 | 前后端常量「没有任何自动手段护得住」 | `to_dict()` 增发 `dims`/`dimSpec`/`axes`，前端删复制品 | 根因是 `to_dict()` 不发 dims；去掉根因就不需要检查单 |
| M3 | §7 Q2-H | 「必须分片」 | 每角色一片 + `worker.py::functions` **必须注册** | 不注册的 job 静默不执行 —— 正是本次要修的失败形态 |

M2 一并解除 §7 Q2-G 那条 ⚠，并让 §9 第 30 条测试从「只能断后端半边」变成能断全场。

### 10.5 §8「验证怎么做」少了两条会让人白跑一天的

- **Bash 工具的 cwd 在两次调用之间不保证保留。** 临时脚本 cwd 必须是 `backend/`
  （否则静默丢 `.env`、429 降级通道消失），所以**每条命令都要自带 `cd …/backend &&`**，
  不能"先 cd 一次后面接着用"。本次靠这个漏掉一次 ⇒ 12 发全 429 且不降级，**全程无提示**。
- **光把 endpoint 旁路到 claude-proxy 不够**：`_get_timeout()` 取的是能力位的
  `timeout_seconds`（现值 120s），而 proxy 正常走的是模块里写死的 `_PROXY_TIMEOUT = 600`。
  只换 endpoint 不换 timeout ⇒ **6 批整整齐齐全在 120.1s 超时**。

### 10.6 §5「能力位 `timeout_seconds`」这个东西**不存在**（Epic 2 落地时查证）

正文有四处这么写：line 152/153、line 159 的第 3 条、line 251 的 Epic 2 一行、
line 654–655。**能力位上没有超时**：`ai_capability_bindings` 表只有
`key/label/category/model/module_keys/sort_order` —— 它**只管选模型**。
超时在 `ai_provider_configs.timeout_seconds`（「AI 服务配置」里的**服务**那一层），
`resolve_ai_config()` 从服务上取，跟 `capability=` 参数无关。

所以正文第 3 条那句「这是 DB 配置改动，要写进上线步骤」照做会卡住：
去能力位页面找不到那个输入框。而**改对了地方也不该改** —— 现网
系统默认「公司网关-Opus」`timeout_seconds = 120`，用例生成 / 六维评审 / 模块体检
全走它，为域评审拧到 1020 等于让每一个卡死的 AI 请求都多等十五分钟才报错。

**已落的做法**：`llm_client.complete()` 加可选 `timeout` 参数（不传 = 原样走服务配置，
别的调用方零影响），`run_review` 自己传 `MIN_TIMEOUT_SECONDS = 1020`。
④ 于是从一条上线步骤变成代码里的常量，**上线步骤这条整个没了**。
正文 line 161「不要为此上流式」仍然成立，理由不变。

⚠ 补一条落地时才发现的：**④ 有两条腿**。主路之外还有 429 降级到 claude-proxy 的
那一跳，它写死 `_PROXY_TIMEOUT = 600s`，而写满 10000 token 按实测要 ~633s ——
只放宽主路，长请求会「平时好好的、一到限流时段整批挂」。
已把 `_PROXY_TIMEOUT` 改成**下限**（`max(600, 调用方要的)`）。
细节和为什么这条最难查，记在 epics 文档的 E2-5。

### 10.7 §Q2-H / §附录表里的 `job_timeout = 600` 对这个模块不适用

正文 line 460 写「硬约束：`worker.py:18` 的 `job_timeout = 600`（我核过）」，
line 508 的文件表里也照抄了一遍「⇒ 分片」。**域评审根本不过 arq。**
`app/api/qa_catalog.py` 的 `spawn()` 是一个裸 `asyncio.create_task`，跑在 API 进程里；
`app/engine/worker.py` 的 `functions = [run_git_sync, run_automated_execution]`
两个都不是它。所以 600 秒这个数**不构成域评审的墙钟约束**，别照它推分片。

⚠ 但正文那两处不是全错 —— **Epic 7 的爬虫要是走 arq，那 600 秒对它就是真的**。
这条只否掉"域评审受它约束"，没否掉 Q2-H 对爬虫的分片结论。

⚠ 另一件顺带查出来的、**本次不修**的事：`asyncio.create_task` 起的活
会被后端重启直接杀掉，而 `finish()` 只在异常分支里跑 ——
**一次重启就能让一条评审永远卡在 `running`，没有任何东西会去收尸。**
（记在 epics 文档 Epic 1 的 S0.1 结论三里，明确划在 Epic 2 范围外。）

另：本次新增的实施就绪偏差 **B-1 ~ B-5** 与三条环境约束 **C-1 ~ C-3**
写在 `implementation-readiness-qa-domain-review.md`，**开工前那份要连着这份一起读**。

