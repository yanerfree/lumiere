# 改名：testBench → Lumiere（窗口作业）

> **状态：未执行。** 等一个能停服务的窗口，一次做完。这份是那天照着走的清单。
> 盘点数据是 2026-08-26 实测的，隔久了先重跑 §2 的命令再动手。

## 1. 已经定了的四件事

**名字 `Lumiere`。** 候选里的 Iris 撞 Go 的 Iris 框架 —— 团队大量系统是 Go 写的，
撞了以后搜索、口头、文档都要加限定词区分，把"短"的好处吃回去了。

**写法只用 ASCII `Lumiere`，永远不写 `Lumière`。** 重音符进 URL、分支名、systemd unit、
`git clone` 会静默变成 `Lumi%C3%A8re`；一个名字两种写法，搜代码的人必漏。
中文名「测试管理平台」不动，不用跟着造新的。

**前缀 `lum_`（MCP 工具）/ `lum-`（平台 skill）。** 跟 `tb_` 一样是 3 个字母，映射表一眼能对。

**原地改 + 仓库改名，不开新仓。** 开新仓一行都不省 —— 改名的编辑量两条路完全一样，
而**数据在 Postgres 里、不在仓库里**，换仓既不需要迁数据也迁不了。新仓额外要付：
10 个本地分支 + 5 个 worktree 要搬或弃、GitHub 的历史/issue/链接、两个远端
（`github` 和内网 `origin`，后者还落后 616 个提交）、别人已经 clone 的全部重来。
GitHub 直接 rename 保留全部历史，旧 URL 自动重定向，本地一句 `git remote set-url`。
（唯一该开新仓的理由是想借机清 git 历史里的敏感值 —— 那是 `git filter-repo`，另一回事。）

**MCP 工具硬改，不做别名层。** 一开始按"外部客户端未知有多少"建议过别名过渡，查完不成立：

```bash
# 3 个活跃 Key，allowed_tools 全是 NULL（都走项目级）；DB 里 skill 正文含 tb_ 的 0 条
backend/.venv/bin/python scripts/check_name_drift.py
```

客户端重连会自动重新拉 `tools/list`，自己不用改。真正要处理的只有落库的那两处（§2 B）。

## 2. 范围盘点

```bash
git grep -Ilic 'testbench' -- . | wc -l                                    # 86 个文件
git grep -Ioh -E 'testBench|testbench|TestBench|TESTBENCH' -- . | wc -l    # 200 处
grep -c 'name="tb_' backend/app/mcp/__init__.py                            # 59 个工具，一个出口
```

### A. 改（代码 + 文档，200 处）

| 类别 | 位置 | 备注 |
|---|---|---|
| 品牌显示名 | `frontend/index.html` title、`i18n.jsx` 的 `header.platformName` / `login.subtitle` | 用户唯一看得见的那层 |
| UI 文案 | `ProjectSkillSection.jsx`、`SkillManage.jsx`、`MCPTools.jsx`、`BranchSelector.jsx` | 「传到 testBench」这类 |
| MCP 工具名 | `app/mcp/__init__.py`(117) `profiles.py`(55) `tools/*.py` | 见 §3 步骤 2 |
| 平台 skill 目录 | `app/skills/preset/tb-*` 9 个 | 目录名 + SKILL.md 内文 |
| 文档 | `docs/cc-platform-loop-spec.md`(80) 等 | 逐条看，别 sed（见坑 1） |
| 部署命名 | `deploy/build-package.sh`、`playwright-mcp.service`、`DEPLOY.md` | systemd / nginx / `/opt` / 包名 |
| 库名 | `config.py:6`、`docker-compose.yml`、`.env.example`、`tests/conftest.py:37` | 见坑 2 |

### B. 迁（库里，sed 够不着）

| 位置 | 量 | 漏了会怎样 |
|---|---|---|
| `projects.mcp_allowed_tools` | 2 行 / 107 个名字 | Key 范围里全是不存在的工具，`tools/list` 空，报错长得像"工具没注册" |
| `ai_capability_bindings.module_keys` | 1 行（`tb-quality-review`） | 「AI 能力→模型」页多一个绑不上模型的空档位 |
| `knowledge_entries`（title + content） | 4 行 | 这是写给未来 CC 看的指引、点名了工具名，不改就是把它指向不存在的工具 |

### C. 不动

- **`mcp_api_keys.key_prefix`（31 行 `tb_xxxxx`）** —— 已经发出去的 Key 字面量。它只是个字符串，
  不影响功能；改 = 吊销别人的 Key。为名字一致做这个不划算。
- **历史记录**：`audit_logs.trace_id`(274)、`ai_usage_logs.skill_name`(9)、
  `case_review_rounds.findings`(43)、`cases.reflections`。它们记的是"当时调了 `tb_update_case`"，
  改了就是篡改历史。
- **被测系统和用例数据** —— 见下面这条坑。

### 坑 1：库里 `tb` 的绝大多数不是我们的名字

全库扫下来命中最多的是这些：`tb-fwgl` / `tb-zcgl-`（UAG 的模块域码）、
`tb-shared-echo-upstream` / `tb-shared-mcp-upstream`（mock 上游主机名）、
`TB_USERNAME` / `TB_PASSWORD`（环境变量）、`tb-dyapp`、`tb-lead`、`tb-probe-model`。
`script_runs.stdout` 253 行、`test_report_steps.*` 上百行 —— **全是数据**。

所以库侧**只允许白名单式地改 §2 B 那三处，禁止 `update … replace()` 全表跑**。
全表替换会把用户数据改坏，而且不报错、不留痕，只有等人打开一条老用例才发现。
文档侧同理：`DEPLOY.md` 和 `docs/` 里有些句子讲的是"已部署的机器上叫什么"，
一把 sed 会把这些历史陈述也改掉，之后跟真实机器对不上。

### 坑 2：库名改了必须一次到齐

`ALTER DATABASE testbench RENAME TO lumiere` 本身几秒（要求无活连接：停后端 → 改 → 起），
但要同时改：`config.py:6` 默认值、`backend/.env`（**只有你能改，我读不了**）、
`docker-compose.yml`、`.env.example`、`tests/conftest.py:37` 那个
`.replace("/testbench","/testbench_test")`、`DEPLOY.md` 约 20 处。
漏一个的表现是"连到一个空库"或"服务起不来"，不会有一行报错指向改名。

## 3. 窗口作业顺序

**第 0 步：独占。** 让其他 session 停手。今天已经踩过两次 commit 互扫（对方的改动被卷进
我的提交）。改名是全仓 sed，跟并发编辑必然打架。开工前确认 `git status` 干净、所有分支已推。

| 步 | 做什么 | 怎么确认这步成了 |
|---|---|---|
| 1 | 显示层 + UI 文案 + 文档（逐条过，不无脑 sed） | 页面顶栏/登录页/浏览器标签 |
| 2 | MCP 工具名 `tb_*` → `lum_*`（`_register` 出口 + profiles + tools + docs） | `pytest backend/tests/test_mcp_profiles.py` —— 它钉了「档位里的名字都真注册过」，改漏一边就红 |
| 3 | 预置 skill 目录 `tb-*` → `lum-*`（9 个） | 「AI 能力→模型」页 9 个档位都在、都能绑模型 |
| 4 | alembic 迁移：§2 B 那三处（带 where，写 downgrade 反向映射） | `scripts/check_name_drift.py --strict` 退出 0 |
| 5 | 库名 + `.env` + compose + conftest + config 默认值 | 后端起得来、页面有数据 |
| 6 | 部署命名（systemd / nginx / `/opt` / 离线包名） | `systemctl start lumiere` + 页面能开 |
| 7 | 仓库改名 + 两个远端 `git remote set-url` | `git fetch` 两个远端都通 |
| 8 | 加封样测试（见 §5 第 3 条） | 新测试绿 |

## 4. 做完必须全量回归

```bash
cd backend && .venv/bin/python -m pytest tests/ -q                       # 基线 1352
DATABASE_URL=…/testbench_test_2 backend/.venv/bin/python -m pytest tests/ -q   # 基线 482，独立库别跟人抢
cd backend && .venv/bin/python scripts/check_name_drift.py --strict      # 库里没有漂移
```

跑完还要**手工验三样**（自动化覆盖不到）：

1. **真连一次 MCP**：拿 `uag-cc使用` 那把 Key 连上，`tools/list` 应该是 55 个 `lum_` 名字、
   0 个 `tb_`，随便调一个只读工具通。这是"重连就行"这个前提的唯一验证点。
2. **页面走查**：顶栏、登录页、浏览器标签、「AI 能力→模型」、MCP 工具页的配置片段。
3. **长驻服务**：`deploy/start-ai-services.sh`，顶栏「服务 N/17」里 claude-proxy 是活的
   （它挂了 429 就只能靠重试）。

## 5. 测试缺口（2026-08-26 查的）

1. **根目录那套 0 条打 MCP 端点** —— 223+76+101+73 条里没有一条走 `/mcp`。
   工具改名后"能不能连上、`tools/list` 对不对"没有任何自动化能答。
   **要补**：一条 API 级用例，建 Key → `tools/list` → `tools/call` 一个只读工具。
   这条跟改名无关也该有。
2. **库里那份名字没人管** —— 测试跑的是每次重建的空库，生产数据不在里面，
   所以工具名/skill 名漂了单测永远绿。**已补**：`backend/scripts/check_name_drift.py`
   （今天加的；现在跑：59 个工具、9 个预置 skill，库里引用的都存在）。
3. **改完之后加一条封样**：全仓不该再出现 `testBench|TestBench|testbench`，
   白名单 = 讲历史的文档段落 + alembic 老迁移 + `key_prefix` 的说明。
   现在加会红，所以放在第 8 步。防的是以后又混回来。

## 6. 回滚

代码是一个 PR，revert 就回去了；迁移写了 downgrade 反向映射；库名改回来；
仓库名改回来重定向照样在。**只要不动 `key_prefix`，整套作业没有不可逆的一步。**

## 7. 一件要先问的

`User-Agent: testBench/1.0`（`ApiManagement.jsx:32`、`ApiStepList.jsx:161`）改之前
先问 UAG 那边 —— 被测系统的日志或白名单可能认这个串，这个我验证不了。
