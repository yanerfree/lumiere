# 改名：testBench → Lumiere（窗口作业）

> **状态：第 1~4 步 + 第 8 步 2026-08-26 已做完。剩第 5（库名）、6（部署命名）、
> 7（仓库改名）—— 这三步都要停服务或动 GitHub，见 §8。**
> 盘点数据是 2026-08-26 实测的，隔久了先重跑 §2 的命令再动手。
>
> 第 1 步能提前做是因为它**对活体 MCP 客户端零影响**：后端起的时候没带 `--reload`，
> 改了文件不重启就不生效，所以当时有个 session 正连着 MCP 也不用等它。
> 顺带做掉的三件不在原清单里的：`public/favicon.svg` 里的字母 **T → L**（顶栏/标签页的图标
> 就是它，i18n 那条 `header.platformName` 反而没渲染在 logo 上）、中文顶栏原来只显示
> 「测试管理平台」品牌名一次都不露 → 改成「Lumiere 测试管理平台」、
> `ui-test-script-gen/testbench-rules.md` 改名 `lumiere-rules.md`（全仓零引用，确认过）。

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
| `projects.description`（`tb-self-shared-project` 那条） | 1 行 | 界面上直接看得见 —— 项目列表卡片写着「testBench 自测链共用的长期项目」。第 1 步做完页面上唯一残留的旧名就是它 |

### A'. 第 1 步刻意留下的（各有原因）

| 位置 | 为什么留 |
|---|---|
| `/home/dreamer/testBench` 路径（`CLAUDE.md:44`、`tests/README.md:7,111`、`scan_overflow.py:17`） | 目录真名。要么不改，要么跟仓库改名一起改（第 7 步） |
| `User-Agent: testBench/1.0` 两处 | 等 UAG 回话，见 §7 |
| `deploy/*`、`DEPLOY.md`（约 25 处）、`pyproject.toml` 的 `name = "testbench-backend"` | 第 6 步一起改。pyproject 改名要重装 venv（editable 安装），别单独动 |
| 带日期的史述：`alembic/versions/zz9orph1_*.py:13`、`docs/cc-platform-loop-spec.md:1850` | 讲的是"2026-07 那时候"，改了是篡改。第 8 步封样要按文件白名单放过这两个 |
| `.mcp.json:3` 的 server key `testbench`、`MCPTools.jsx:493` 里给用户复制的同一个 key | 跟第 2 步的工具改名一起走（同一次重连生效） |
| `run_evidence_service.py:24` 的 `/tmp/testbench_evidence` | 跟第 2 步一起。改了以后 tmp 里的老截图就找不着了（本来也会被清），不是问题但要知道 |
| `_bmad-output/`、`tests/report.txt`、`tea-cases.json` 之外的生成物 | 归档件/生成物，不是源 |

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
| ~~1~~ | ~~显示层 + UI 文案 + 文档~~ **已做（41 文件，08-26）** | 已验：登录页/顶栏/标签页都是新名，1390 单测过，前端 build 过 |
| ~~2~~ | ~~MCP 工具名 `tb_*` → `lum_*`~~ **已做（08-26）** | 已验：`tools/list` 真连回 55 个 `lum_`、0 个 `tb_`；页面工具明细展开 59 个名字全 `lum_` |
| ~~3~~ | ~~预置 skill 目录 `tb-*` → `lum-*`（9 个）~~ **已做（08-26）** | 已验：3 个档位都绑着模型，`cap-lum-quality-review` → `['lum-quality-review']`（档位是 3 个不是 9 个 —— 9 是预置 skill 数，只有质量评审有专用档位） |
| ~~4~~ | ~~alembic 迁移 `zzv0lumren`~~ **已做（08-26）** | 已验：`check_name_drift.py --strict` 退出 0；upgrade→downgrade→比对备份逐字节一致→再 upgrade |
| 5 | 库名 + `.env` + compose + conftest + config 默认值 | 后端起得来、页面有数据 |
| 6 | 部署命名（systemd / nginx / `/opt` / 离线包名） | `systemctl start lumiere` + 页面能开 |
| 7 | 仓库改名 + 两个远端 `git remote set-url` | `git fetch` 两个远端都通 |
| ~~8~~ | ~~加封样测试~~ **已做（08-26）** | `backend/tests/test_name_seal.py`（两堵墙 + 白名单自检 10 项）；故意往 `config.py` 塞一行旧名，两堵墙都红，删掉就绿 |

## 4. 做完必须全量回归

```bash
cd backend && .venv/bin/python -m pytest tests/ -q                       # 基线 1390
DATABASE_URL=…/testbench_test_2 backend/.venv/bin/python -m pytest tests/ -q   # 基线 482，独立库别跟人抢
cd backend && .venv/bin/python scripts/check_name_drift.py --strict      # 库里没有漂移
```

**08-26 改完实跑：** backend `1390 passed`；根目录 `493 passed`（比基线 482 多的 11 条
是别的窗口补的模块闸门用例，不是改名带来的）；`check_name_drift.py --strict` 退出 0。
新增 12 条封样 + 5 条 MCP 端点用例，全绿。

跑完还要**手工验三样**（自动化覆盖不到）：

1. **真连一次 MCP**：`tools/list` 应该是 55 个 `lum_` 名字、0 个 `tb_`，
   随便调一个只读工具通。这是"重连就行"这个前提的唯一验证点。
   **08-26 已验**：`serverInfo.name = "Lumiere"`，55 个工具全 `lum_`，
   `lum_list_projects` 正常返回。**注意地址是 `:18800/mcp/` 不是 `:8756/mcp/`** ——
   主端口那份 mount 早就删了（`app/main.py` 里有说明），打错端口拿到的是
   `{"detail":"Not Found"}` 和 0 个工具，很容易误判成"改名把 MCP 改坏了"。
2. **页面走查**：顶栏、登录页、浏览器标签、「AI 能力→模型」、MCP 工具页的配置片段。
3. **长驻服务**：`deploy/start-ai-services.sh`，顶栏「服务 N/17」里 claude-proxy 是活的
   （它挂了 429 就只能靠重试）。

## 5. 测试缺口（2026-08-26 查的）

1. **根目录那套 0 条打 MCP 端点** —— 223+76+101+73 条里没有一条走 `/mcp`。
   工具改名后"能不能连上、`tools/list` 对不对"没有任何自动化能答。
   **已补**：`tests/integration/mcp/test_mcp_endpoint.py` 5 条 —— 匿名 401、
   假 Key 401、握手报 `Lumiere`、`tools/list` 全 `lum_` 且都在注册表里、
   `tools/call` 只看得见自己项目。写的时候踩到两个坑，都写在文件头了：
   lifespan 必须同一个 task 进出（不然 anyio 报 cancel scope 跨 task），
   以及每条用例开头要 `engine.dispose()` —— `db_session` 的 `drop_all` 会让
   app 引擎池里的旧连接失效，中间件是 fail closed 的，**报出来是 401，
   看着像认证写错了**。
2. **库里那份名字没人管** —— 测试跑的是每次重建的空库，生产数据不在里面，
   所以工具名/skill 名漂了单测永远绿。**已补**：`backend/scripts/check_name_drift.py`
   （今天加的；现在跑：59 个工具、9 个预置 skill，库里引用的都存在）。
3. **改完之后加一条封样**：**已补** `backend/tests/test_name_seal.py`。两堵独立的墙
   （品牌名大小写不敏感 / `tb_`·`tb-` 只管小写，大写 `TB_USERNAME` 是 UAG 的环境变量）。
   白名单分三种、每条都写了理由，另有 10 条参数化用例盯着「白名单指向的文件还在不在」——
   没这一条，白名单会越滚越长，最后墙上全是洞而没人知道哪个洞还有用。
   只扫 `git ls-files`：工作区里 `.mock_state/`、egg-info、playwright 快照都带着旧名字，
   它们自己会重新生成，拦它们只会天天假红（而且 rglob 会读到 `.env`）。

## 6. 回滚

代码是一个 PR，revert 就回去了；迁移写了 downgrade 反向映射；库名改回来；
仓库名改回来重定向照样在。**只要不动 `key_prefix`，整套作业没有不可逆的一步。**

## 7. 一件要先问的

`User-Agent: testBench/1.0`（`ApiManagement.jsx:32`、`ApiStepList.jsx:161`）改之前
先问 UAG 那边 —— 被测系统的日志或白名单可能认这个串，这个我验证不了。

## 8. 剩下没做的（都要停服务或动 GitHub）

| 步 | 为什么留着 |
|---|---|
| 5 库名 `testbench` → `lumiere` | `ALTER DATABASE` 要求无活连接：停后端 → 改 → 改 `.env`（**这个文件只有你能改**）→ 起。还要同步 `docker-compose.yml`、`config.py` 默认值、两套 `conftest` 的 `testbench_test*`。封样里这一类命中是按「连接串/`psql -d` 里的库名」放过的 |
| 6 部署命名 + `pyproject.toml` 包名 | `deploy/*`、`DEPLOY.md` 约 25 处；`name = "testbench-backend"` 改了要重装 venv（editable 安装的包名变了） |
| 7 仓库改名 | GitHub 网页上改（这一步只有你能做），然后 `git remote set-url` 两个远端。改完 `/home/dreamer/testBench` 这个路径和 `tests/README.md` 里的 `cd testBench` 才能跟着改 |
| `User-Agent: testBench/1.0` | 等 UAG 回话，见 §7 |
| 项目名 `tb-self-shared-project` | 项目列表卡片上看得见，但它是**数据行的标识**不是品牌名（描述已经改成「Lumiere 自测链共用的长期项目」）。要改是改一条业务数据，跟改名作业不是一回事 |
| 存量 Key 前缀 `tb_xxxxx` | 3 把在用的 Key。改 `key_prefix` = 吊销已发出去的 Key。新发的已经是 `lum_` |
