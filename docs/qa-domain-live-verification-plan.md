# QA 域评审：从「读代码猜」升级到「真跑页面对流量」

**状态：已分析、结论已定、代码一行没动**（2026-09-02）。这份文档是给「后面有空再做」
那一刻的自己看的 —— 动手前整篇读完，尤其 §1（一条已确认写错的口径）和 §4（被否掉的
省事写法）。文档里所有数字都是当天在 `lumiere` 库和 QA 仓 bare 缓存上真跑出来的，
不是估的；真动手时**要重跑一遍**（QA 仓一直在动）。

复现所有数字的命令都写在正文里，可以直接抄。

---

## §1 先纠一条口径错误：只读有两个，只有一个是真的

| | 是不是硬约束 | 出处 |
|---|---|---|
| QA **仓库**只读 | **真的，不动** | `CLAUDE.md` 硬规则；`services/qa_catalog.py` 白名单 |
| 被测**环境**只读 | **不是。写错了** | `services/qa_survey_guard.py:3` |

### 1.1 环境那条只读的全部理由，只有一句话

```python
# backend/app/services/qa_survey_guard.py:3
爬的是**别人的测试环境**。这五层不是「尽量别写」，是「写不出去」：
```

**订正（2026-09-02）：本节初稿说「它引用的 `AD-7` 在仓里根本不存在」，那句是错的。**
`AD-7` 在 `_bmad-output/planning-artifacts/architecture-qa-domain-review.md:233`
（`## AD-7 · 只读五层的代码落点`），有正文、还带一节 2026-08-29 的实测勘误。
初稿的 grep 只扫了 `docs/ backend/app/`，把 `_bmad-output/` 漏在范围外了：

```bash
grep -rn "AD-7" docs/ backend/app/        # ← 初稿用的，漏了 _bmad-output/
grep -rn "AD-7" . | grep -v /.venv/       # ← 正确的：正文在 _bmad-output/
```

**但这不救那句理由。** 读了 AD-7 和它上游的 `Q2-F 只读的强制手段` 之后更清楚：
那两节从头到尾没拿「环境归谁」当过判据 —— L2 挡的是「确认框后的二段写」，
探边档默认关的理由是「abort 会让前端进错误态、污染后续页面」，
L4 drop 凭证的理由是「HAR 里的 token 是完整可用凭证」。全是**无向枚举的力学**，
在我们自己的环境上一字不差地成立。「爬的是别人的测试环境」是**贴在正确工程上的错标签**：
守卫该留，标签该换 —— 照着这个标签往下推，就会推出「被测环境不能写」这条不存在的规矩。

### 1.2 三条反证

**① 我们自己跑 UI 脚本，零写守卫。**

```bash
grep -rn "is_write_request|route.abort|readonly" \
  backend/app/engine/pw_conftest.py backend/app/engine/executor.py \
  backend/app/engine/ts_runner.py | wc -l
# 0
```

`services/review/residue.py` 是**事后审核项**不是禁令，开头原文：
「正常脚本跑完就该自己收拾干净，留垃圾只有两种可能 —— 脚本没写清理，或者删不掉」。

**② 那个环境本来就是拿来写的 —— 他自己的套件一直在写。**

```bash
# 在 backend/.qa-repos/<project_id>.git 里
for f in $(git ls-tree -r HEAD --name-only | grep -E '^(api|scenarios)/.*\.sh$'); do
  git show HEAD:$f; done > /tmp/allsh.txt
grep -oE 'api_(json|json_code|code|once)[[:space:]]+[A-Z]+' /tmp/allsh.txt \
  | awk '{print $NF}' | sort | uniq -c | sort -rn
#   159 POST   119 PUT   115 GET   107 DELETE   23 PATCH
```

去重端点里有 `DELETE /teams/{id}`、`DELETE /providers/{id}`、
`POST /approvals/{id}/approve`。**那个环境就是给这么用的。**

**③ 用户的定性**：它是给我们测试、给我们审核用的测试环境，不一定是别人跑代码的环境。

### 1.3 但有一个区分**该留下**：只读绑在「无向」上，不绑在「谁的环境」上

真正让 `SAFE_TO_CLICK = ("read",)`（`qa_survey_guard.py:78`）成立的理由不是"别人的
环境"，是**它不知道那个按钮会干什么**：

- **枚举是无向的**：走到一页，看见一个不认识的控件，要当场决定点不点。
  在**我们自己**的环境上无向乱点，点到「删除团队」一样是灾难。
  所以 `unknown` 不点这条**保留**，理由改写成「无向探索」。
- **UI 脚本是有向的**：我们写的，知道点什么，自带清理，`residue.py` 事后查账。
  这里不需要守卫，也从来没有过。

**推论（这条改变了上一版的计划）：不给爬虫开写权限，写操作走 UI 脚本。**
两条路各干各的，用的都是现成机器。「要不要给爬虫开写」是个问错了的问题。

### 1.4 动手时要一并改的文字

**`AD-7` 的引用全部保留**（正文在 `_bmad-output/`，见 §1.1 订正）。要改的是**理由那句**：
`qa_survey_guard.py:3`（「爬的是别人的测试环境」）、`qa_page_survey_crawl.py:7`（同句）、
`models/qa_page_survey.py:14`（「这张表不会让平台往被测环境写任何东西」）
一律重写成「无向枚举不点不认识的控件 —— 因为它不知道自己造了什么、也清理不掉」，
并写明**这是爬虫的约束，不是「被测环境只读」这条规矩**。
**不改的话下一个人会照着它再推一遍同样的错结论。**

✅ **已改**（2026-09-02，用户当场定「测试环境可以操作，代码仓库不许操作」后）：
上述三处 docstring 已重写，`CLAUDE.md` 与 `qa-repo-readonly-catalog.md` 的口径同步更正。

---

## §2 数据：为什么纯只读到不了八九十分

```bash
norm(){ sed -E 's/\$\{[^}]+\}/{id}/g; s/\$[A-Za-z_][A-Za-z0-9_]*/{id}/g; s/"//g'; }
W=$(grep -oE 'api_(json|json_code|code|once)[[:space:]]+(POST|PUT|PATCH|DELETE)[[:space:]]+"[^"]+"' \
      /tmp/allsh.txt | sed -E 's/api_[a-z_]+ +//' | norm | sort -u | wc -l)
R=$(grep -oE 'api_get(_as)?[[:space:]]+"[^"]+"' /tmp/allsh.txt \
      | sed -E 's/api_get(_as)? +//' | norm | sort -u | wc -l)
echo "读 $R 个 / 写 $W 个"
```

| | 数 |
|---|---|
| 调用处 | 读 440 / 写 408 —— **写占一半** |
| 去重端点 | 读 146 / 写 99 / **合计 245** |

**只读枚举的天花板 = 146/245 = 60%。** 写的 99 个端点结构上碰不到。
要八九十分，必须写。

但 99 个写端点**不等于 99 条脚本**：一条「MCP 审批链」走一趟就打掉
`POST /approvals/{id}/approve` + `POST /agents/{id}/mcp-permissions` + 清理的 `DELETE`。
他自己的 `scenarios/mcp/builtin-approval-to-call.sh` 就是这个形状。

场景分布也是重的（按 `@scenario` 前缀统计 153 条脚本）：

```
MCP 59 · TEM 16 · AUT 14 · PCR 8 · RTE 7 · RES 6 · FIN 6 · SEC 5 · GW 5 · 其余 27
```

- 前 5 个域 = 104/153 = **68%**
- 前 8 个域 = 121/153 = **79%**

---

## §3 「他没有步骤」是真的，但他有三份结构化数据

```bash
for f in $(git ls-tree -r HEAD --name-only | grep -E '^(api|scenarios)/.*\.sh$'); do
  git show HEAD:$f | head -25 | grep -oE '^#[[:space:]]*@[a-zA-Z-]+'; done | sort | uniq -c
#   153 @tier   153 @scenario   14 @known-bug
```

确实没有步骤字段。但另外三份很值钱，**不用我们猜**：

### ① `docs/qa/gates/ui-domain-patterns.tsv` —— 域码 → 页面，他自己维护的

```
tem  team|member-budget|budget-tree|owner-search   ★ 域码 tem ≠ 产品词 team（testid 111 / 页面 2）
mcp  mcp|skill|tool                                MCP 能力，含 Skill Hub（testid 164 / 页面 14）
grd  guardrail|regex|sensitive                     ★ 原来命中 0（testid 45 / 页面 3）
gw   NO-UI                                         数据面网关。控制台上没这个面
```

**「这个域覆盖哪些页面」他写好了**，还带实测的 testid 数和页面数，`NO-UI` 显式声明。
这张表是被一次静默失效逼出来的（他自己写在表头）：闸门 7 曾用域码做子串匹配，
`tem` 匹配不到 `team`，报 **PASS**，实际 65 个 testid 只碰过 10 个。

### ② `ui/support/selectors.ts` —— 14 个页面路由 + 145 个 testid

```ts
export const routes = { login:'/login', dashboard:'/', providers:'/providers',
  agents:'/agents', teams:'/teams', mcpHub:'/mcp-tools',
  mcpCallLogs:'/monitoring/mcp-call-logs', credentials:'/personal-credentials',
  agentDetail:(id)=>`/agents/${id}`, mcpToolDetail:(id)=>`/mcp-tools/${id}`,
  teamDetail:(id)=>`/teams/${id}` }
```

**顶部有一句话，是批 1 的全部理由**：

> ⚠️ 建仓时**没有可访问的 UAG 控制台**，以上选择器全部来自**源码阅读**，
> 未在真实浏览器里验证过。

`routes` 里只有 2 行注明「2026-08-25 在真实控制台核对过」，其余 12 个是从源码推的。

### ③ 17 条 Playwright spec，全带 `@scenario`

`ui/playwright.config.ts` + `ui/node_modules` 都在仓里。域分布
aut 2 / mcp 7 / obs 1 / prv 1 / tem 7。

**注意这个倾斜：153 条 shell 场景 vs 17 条 UI 场景，MCP 是 59:7。**
UI 这一维他基本是空的 —— 这本身就是三边对账会报出来的最大一块缺口。

---

## §4 被否掉的省事写法：「倒推他在页面上怎么操作」

用户原话提到「倒推出他在页面怎么操作的」。**这一步不做**，理由：

他的 bash **根本不经过页面** —— `api_get "/agents/${ID}"` 直接打 BFF。
给一个没有 UI 语义的东西编一段 UI 语义，编出来的东西**没有判据能验对错**，
编错了不报错。这正是本平台反复要防的形状（同 `qa_catalog_review` 里
`unmet`/`blind` 禁止模型编的那条）。

**正确方向是反过来：不从脚本推页面，从页面推脚本。**
页面上点了 X、发出 `POST /a/b` → 去 245 个端点里查有没有人打过 `POST /a/b`。
集合比对，纯代码，跟 `services/review/traffic_diff.py` 的 `norm()` 同一套归一化。

顺带一条已确认的好消息 —— **不需要任何跨层映射**：

```sh
# QA 仓 config/env.sh
BFF="${BFF:-http://127.0.0.1:4000}"
API="${API:-${BFF}/api/v1}"          # 脚本打这个
WEB_URL="${WEB_URL:-http://127.0.0.1:3000}"   # 页面
```

`api_get()` 拼的是 `${API}${path}` —— **他打的就是页面在打的那个 BFF**。
（用户确认：QA 仓的要求就是这样，不是这样就是他写错了。）

---

## §5 已经建好、但一次没跑过的东西

```bash
psql -d lumiere -tAc "select count(*) from qa_page_surveys"        # 0
psql -d lumiere -tAc "select count(*) from qa_page_survey_items"   # 0
grep -l survey backend/app/api/*.py | wc -l                        # 0  ← 没有 HTTP 入口
grep -rl survey frontend/src --include=*.jsx | wc -l               # 0  ← 没有前端入口
```

| 文件 | 行 | 干什么 |
|---|---:|---|
| `engine/surveys/qa_page_survey_crawl.py` | 362 | Playwright 走页面、抓 HAR、抽端点。**这就是"爬虫"，没有第二套技术** |
| `services/qa_survey_guard.py` | 285 | 只读五层判定，纯函数 |
| `services/qa_page_survey.py` | 190 | 两趟产物做差，unknown 压倒 removed/added |
| `services/qa_route_table.py` | 212 | 拉 `GET {BFF}/api/docs/routes`（R 边）。**别打到网关 :8000** |
| `services/qa_coverage_reconcile.py` | 678 | P/R/Q 三边纯集合运算 |
| `services/qa_survey_byproducts.py` | 270 | 锚点 → `project_selectors`；`observed_actions` → 模块体检 |
| `services/review/traffic_diff.py` | 148 | **四方对比的判据，现成的**（见下） |

外加 `backend/tests/` 下 10 个测试文件（`test_qa_page_survey_*`、
`test_qa_coverage_reconcile*`、`test_qa_route_table`、`test_qa_survey_*`）。

**缺的只有接线**：HTTP 入口、前端入口、`qa_route_table` 的落库表。

### `traffic_diff.py` 是最关键的一块，它已经在用例管理那边跑了

它的 docstring 里那个例子就是这套机制抓到的：

> 页面打开订阅管理时调的是 `/api/v1/subscriptions/provider-unified`，
> 而它 22 条接口场景全用 `/api/v1/subscriptions/provider`。
> 后者确实存在、返回 200，所以**用例一直是绿的**；但页面根本不用它。

> 判据全是确定的（URL 集合比对、条数比对），不需要模型。

用例管理侧的完整链路（**QA 侧要复用的就是这条**）：

```
engine/pw_conftest.py:73   record_har_path        → 录浏览器流量
engine/ts_runner.py:206    parse_har(...)         → captured_requests
services/review/traffic_diff.py                   → 四方比对
```

---

## §6 计划：三批，第二批到位就是八九十分

### 批 1 · 一趟只读跑通全链（估 60 分）

Playwright 开 `routes` 里那 14 个路由，一次拿三样：

1. **145 个选择器活体验证** —— 报 `命中1 / 命中0 / 命中多个`。
   这是他自己文档里承认没验过的那件事，产出**对他直接可用**（所有 spec 都
   `import { sel } from '../../support/selectors'`，报出来的是 `sel.teams.totalBadge`
   这种能直接 grep 的键）。
2. **P 边**（页面真调的 GET）从 HAR 抽。
3. **控件账本**（页面上有哪些可操作项）→ `qa_page_survey_items`。

同时补两个纯代码解析器：

- **Q 边**：静态抽 245 个调用点（`api_get "/path"` / `api_json POST "/path"`），
  `${VAR}` → `{id}` 用 `traffic_diff._IDLIKE` 同一条正则。跟 `env_gaps()` 同一类，**不问模型**。
- **R 边**：现成的 `qa_route_table.py`。

然后三边比对（`qa_coverage_reconcile.py`）：

| | 结论 |
|---|---|
| P ∖ Q | 页面上在用、他一条没测 ← **这就是「覆盖全不全」** |
| Q ∖ R | 幽灵端点：打了个不存在的，拿 404 当"被拒"断言 |
| P ∩ Q | 对上了，这部分可信 |

**这一批不碰写，价值已经独立成立** —— 选择器报告他直接能用，不用等批 2。

**它能给他一样他自己拿不到的东西**：他的闸门 7 分母是**源码 grep 出的 testid**，
我们的分母是**运行时真实渲染出来的控件**。差集两头都有意义 ——
源码里有但页面不渲染的（条件渲染 / flag 关了 / 权限藏了）在他那边白占分母、
覆盖率不可解释；页面上有但 grep 不到的（动态拼 testid、第三方组件）
在他那边**根本不进分母**，那是真缺口而他看不见。

### 批 2 · 写操作 UI 脚本（估到 85–90 分）

按域场景数排，写 **8–12 条** Playwright 脚本，走用例管理那套现成的：

```
lum_sync_ui_script → lum_run_ui_script → HAR → traffic_diff
```

覆盖顺序 **MCP → TEM → AUT → PCR → RTE → FIN**（§2 的分布），
每条**自带清理**（`residue.py` 会查账；删不掉就是被测系统的 bug，那本身也是一条发现）。

**剩下 10~15% 不追**：尾巴上是一次性的 `POST /admin-users`、
`DELETE /audit/logs/{id}` 这类，单独写脚本不划算，
报告里标「未覆盖」比硬凑一个假覆盖诚实。

⚠ **8–12 这个数是按场景分布估的，不是实测的。** 跑完批 1 拿真实 P 边再定，会准得多。

### 批 3 · 跑他自己的 17 条 spec（可选，先不做）

他仓里 `playwright.config.ts` + `node_modules` 齐的，只读 checkout 出来跑
**不违反红线**（不往他仓写）。跑起来抓的流量是**他认为该发生的**，比我们爬的准。
但要 node + npm + 他 `globalSetup` 存的 storageState + 凭据，链子长。
**批 1、2 不依赖它。**

---

## §7 动手前必查

1. **QA 仓一直在动**，§2/§3 的所有数字重跑一遍（`git fetch` 那个 bare 缓存后再统计）。
2. **`qa_page_surveys` 是不是还是 0 行**。不是的话说明有人接线了，先读他的接法。
3. **选的环境是哪个** —— `qa_route_table.py` 的注释写着「别打到网关 :8000 上」，
   R 边要的是 BFF 的 `/api/docs/routes`。
4. **批 2 开跑前确认写权限口径已按 §1.4 改文档** —— 否则代码在写、注释在说「写不出去」，
   下一个人会以为是 bug 然后把它改回去。

---

## §8 要定的两件事

1. ~~**确认口径改动**（§1）~~ ✅ **已定**（2026-09-02，用户原话「测试环境可以操作」
   ／「代码仓库不许操作」）：被测环境 = **枚举只读、脚本可写**；QA git 仓库 = 永远只读。
   §1.4 那三处 docstring 已按此重写；`AD-7` 引用保留（它存在，见 §1.1 订正）。
2. **批 1 先单独交？** 倾向先出批 1，用它的实测数字校准批 2 要写几条脚本。**← 还没定**

批 3 建议先不做。
