# Lumiere — 给接手者 / AI 助手的必读约定

## 硬规则

- **后端必须跑 8756 端口**（`uvicorn app.main:app --port 8756`）。跑在别的端口前端会全 502，看起来像整个服务挂了，实际只是端口不对。
- **后端故意不带 `--reload`，所以改完后端必须重启**：`bash deploy/restart-backend.sh`（幂等）。
  lifespan 会绑一整串端口（MCP `:18800`、LLM/API mock 与代理观测的 `28xxx` 段，一共 9 个），
  `--reload` 每次存盘都重绑一遍 —— 撞 `address already in use`，还会把连在 `:18800` 上的
  MCP 客户端（Claude Code 自己）踢下线。**别加 `--reload` 来"省事"。**
  代价是不重启就在跑旧代码，而前端 vite 有 HMR，于是变成「新前端 + 旧后端」，
  症状长得像功能本身有 bug。**新增的响应字段在旧后端上会渲染成假的 0，比报错难发现得多。**
  2026-08-28 起还多一种更响的：前端启动就问 `/api/me/permissions`，旧后端上这条是 404，
  而权限拉不到是**故意 fail-closed**（空集合），于是**全站写按钮一个不剩地消失** ——
  看着像「我这账号权限被人收了」，实际只是后端没重启。
  怀疑时对一眼：`ps -o lstart= -p <pid>` 的进程启动时间 vs `git log -1 --format='%ci'`。
  （2026-08-27 实测：QA 对账页「拉取最新」提示成功、数字一动不动，查了半天 —— 那个 bug
  `4aff452` 前一天 16:58 就修好了，后端还停在 15:38 起的那个进程上。）
- **AI 模型 ID 只能填裸 ID**（`claude-sonnet-5`、`claude-opus-5`）。CLI 的长上下文后缀写法 `claude-opus-5[1m]` 打到接口会 404 —— 见下方文档的「红线」一节。
- **不要删 `app/services/ai/llm_client.py` 里的 429 两层处理**（退避重试 + 降级 CLI 通道）。文本主路此前零重试，一个 429 会打死整条场景生成；原因和验证方法都写在文档里。
- 换 AI 模型**不需要改代码**：走「AI 服务配置 → AI 能力→模型」页面即可（下拉是动态拉网关的）。
- **接口场景只有一种：绑用例的编排链。** 「接口测试」模块（单接口·凭文档 AI 造）
  2026-08-15 已下线，`lum_generate_api_test` 一并摘除。**别加回来** —— 场景变量只能挂在
  用例上（`scenario_variables.case_id` NOT NULL），不绑用例的场景结构上就跑不了。
  理由和实测数据见下方文档的「§11 接口测试模块下线」。
- **「接口库」和「接口场景」是两个东西，别混。** 接口库（`api_nodes`，页面菜单叫
  **接口库**）只记「系统有哪些接口、怎么调」，是**文档，不能执行**；可执行的是上一条
  说的绑用例编排链（`api_test_scenarios`）。名字只差一个字，2026-08-27 真被搞混过一次，
  所以接口库那一页顶上有条**常驻**说明条（`ApiManagement.jsx` 的 `<Alert>`）——
  **别把它做成"读过就不再显示"**：会踩这个坑的恰恰是第一次打开这页的人。
- **别在注释/文档里断言「谁在用 / 还活不活着」。** 要么让它**可查**、并把「怎么查」
  一起写上，要么别写。2026-08-27 撞过：有人问「接口库还在被 MCP 写吗」，而那张表当时
  **一个字都不记** —— 只能靠"库里节点全叫『新建接口』，那是页面新建的默认名"这种间接
  推断来回答。**一个查不出来的事实，等于一个可以随便断言的事实。**
  现在的答案在：**操作日志 → 对象类型选「接口库」→ 看「操作人」列的来源标签**
  （`actor_type` 由 `app/mcp/middleware.py` 的 `on_call_tool` 统一注入，所有 `lum_*` 都带）。
  推论两条：① 新加写操作**必须记账**（`api_collection_service.py` 有封样测试盯着：
  函数里调了 `flush` 就必须调 `_audit_node`）；② **别拿「没人用了」当下线理由** ——
  那是结果不是原因，理由要说清它**产出的东西为什么没价值**（例见文档 §15.1）。
- **游客（系统角色 `guest`）的只读是「硬封顶」，强制点不在权限模型里。**
  真正把门的是 `app/deps/auth.py` 里的**非 GET 闸门**（判据在 `app/core/readonly_gate.py`）：
  游客打 `/api` 下任何非安全方法一律 403。白名单**只有 6 条**，每条都写了「为什么它不是真的写」
  —— `/api/auth/*` 四条 + `POST /api/assistant/chat`（只出提案不落库，`/execute` **故意不在**）
  + `.../scenario-variables/preview`（纯函数展开）。
  **别改成靠 `require_permission` 来管**：`/api` 下 264 条写路由里有 129 条不含 `{project_id}`
  （mock/load-test/toolbox/http-client…），那个工厂的签名靠它取项目语境，**结构上挂不上去**。
  `core/permissions.SYSTEM_ROLE_CEILING` 那条 `∩` 只管**呈现**（前端按钮、助手能力面），
  它一个人挡不住写 —— 角色折叠后守卫元组一律 `("manager","member")`，游客作为成员会直接通过。
  两处封样盯着：`tests/test_authz_seal.py`（对全部写路由取反向差集 + 验闸门**真的被调用**）、
  `tests/test_mcp_guest_key.py`（:18800 从不读 `users.role`，降级前建的 Key 否则照样能写）。
- **建 MCP Key 必须绑项目。** Key 的 `project_id` 现在管两件事：工具范围**和数据范围**
  （能读写哪个项目的用例/环境）。`project_id` 为 NULL 的 Key **不受数据范围限制** ——
  这是为存量 Key 留的口子，不是给新 Key 用的。发 Key 前用
  `select name,key_prefix,project_id from mcp_api_keys where is_active` 确认一遍。
- **环境和全局变量都是项目级的**（2026-08-21，迁移 `zzo0envproj` / `zzp0gvarproj`）。
  页面在 `/projects/:projectId/settings/env`，不在全局设置里；唯一约束是
  `(project_id, name)` / `(project_id, key)`，所以两个项目各有一个 `staging`、
  各有一份 `TEST_LANGUAGE` 都是正常的。**「全局变量」里的"全局"指本项目跨环境，不是跨项目。**
  只有**通知渠道**还是全局的（平台设施），别顺手"补全"它。
- **新建项目会自动铺 4 个环境 + 5 个全局变量**（`app/services/project_defaults.py`）。
  **默认环境故意不带任何变量** —— 老库那几条种子环境带着 `ADMIN_PASSWORD=123456`
  这类演示值，照抄给新项目等于预埋假凭证，而假凭证比没凭证更坏：
  它让「忘了填」看起来像「填过了」。加默认值改那一个文件即可。
- **别给 mock 那几张表加 `project_id`**（`mock_routes`/`api_mock_routes`/`*_mock_*`）。
  看着像该做的项目隔离，实际是**假隔离**：mock 按 `path` 查找（`.where(path == p).first()`），
  行上加归属不改变运行时冲突，两个项目配同一个 path 照样互相覆盖、还偶发。
  真正要做的是给 `path` 加 unique 约束 + 把「path 带前缀」变成服务端强制。
  实测数据和推理见下方文档「数据归属与隔离」的 §5。
- **QA 仓永远只读**（项目上那份 `qa_repo` 配置）。那是别人维护的黑盒验收仓，
  清单文件是他自己门禁（`check-coverage.sh`）的判据来源，平台往里写一笔，他那边就会
  红在一个查不到原因的地方。`services/qa_catalog.py` 只允许 `show`/`ls-tree`/`rev-parse`/`log`/`grep`，
  有封样测试盯着；也**不要求对方仓库为我们加任何字段/文件/钩子**。要做回写先跟仓库主人谈，
  别从这个模块长出来。**配置在「QA 对账」页里配，不在编辑项目弹窗**；只有仓库地址必填，
  分支/清单路径/脚本范围留空 = 自动识别，**别给它们塞 uag-qa 的默认值**（见文档 §3）。
- **别把项目 skill 放进 `app/skills/preset/`**。那个目录只放平台侧执行的 `lum-*`（会被当 prompt 喂后端 LLM、要绑模型档位）；客户端侧执行的 skill 走 DB，见下方文档。混了会让「AI 能力→模型」页冒出绑不上模型的空档位。

## 测试：**两套，都要跑**

```bash
cd backend && .venv/bin/python -m pytest tests/ -q     # 单测/结构封样，~12s，1350+ 条
# API/E2E，几分钟；DATABASE_URL 换成你自己那个库，别用默认的 lumiere_test
cd /home/dreamer/lumiere && DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/lumiere_test_<你的名字>' \
  backend/.venv/bin/python -m pytest tests/ -q
```

**根目录那套必须用独占的 `DATABASE_URL`。** 它的 `db_session` 收尾会 `drop_all`，
两个人（或两个 Claude 会话）同时打 `lumiere_test` 就会互相把表删掉 —— 报出来是几十上百条
"relation does not exist" 的假 red，而代码一行没错。库不存在先 `createdb`：
`psql -h localhost -U postgres -c 'CREATE DATABASE lumiere_test_<你的名字>'`。
（2026-08-26 就这么撞出过 148 条假 red。）

两个目录都叫 `tests/`，很容易只跑前一套就以为绿了 —— **根目录那套打的是真接口**，
改路由/改路径必须跑它，否则「后端全绿、页面全 404」。
（2026-08-21 环境项目化时就这么漏过一次：backend/tests 1130 全过，
根目录 14 条红在旧路径上。）

## 文档入口

| 主题 | 文档 |
|---|---|
| AI 网关真面目、429 限流怎么绕、新模型怎么维护、模型选型实测数据、长驻服务依赖 | [docs/ai-gateway-and-models.md](docs/ai-gateway-and-models.md) |
| AI 测试生成用法 | [docs/ai-test-generation-guide.md](docs/ai-test-generation-guide.md) |
| AI 质量改进计划 | [docs/ai-quality-improvement-plan.md](docs/ai-quality-improvement-plan.md) |
| **AI 评审（六维·逐条）怎么判、为什么能替人工待审** | [backend/app/skills/preset/lum-quality-review/SKILL.md](backend/app/skills/preset/lum-quality-review/SKILL.md) + [docs/cc-platform-loop-spec.md](docs/cc-platform-loop-spec.md) 附节 |
| **审核怎么发起/排队/结果看哪里、模块命名规则**（改这块之前先读；含三个已撤销的设计） | [docs/review-spec.md](docs/review-spec.md) |
| **审核机制对外可借鉴版**（判据/元规则/流程/踩坑，不依赖本平台表结构，可直接发人） | [docs/review-mechanism.md](docs/review-mechanism.md) + 同名 `.html` 单页 |
| 项目 Skill 怎么传上来 / 给别的项目取用、跟内置 lum-* 的边界 | [docs/skill-sharing.md](docs/skill-sharing.md) |
| **LLM Mock 智能应答**：指令契约（MODE:/SAY:）、护栏回显协议、开关前后页面为什么长得不一样 | [docs/llm-mock-smart-contract.md](docs/llm-mock-smart-contract.md) |
| 下阶段做什么：生成效率 / 生成质量 / 失败优化（含现状实测盘点） | [docs/next-phase-gen-quality-and-failure.md](docs/next-phase-gen-quality-and-failure.md) |
| **CC ↔ 平台闭环的边界规则、红线、Story 清单**（改这一块之前先读） | [docs/cc-platform-loop-spec.md](docs/cc-platform-loop-spec.md) |
| **版本升级怎么复用上一版用例**：分支对账（端点反查）、三堆分法、状态流转、废弃审核 | [docs/version-upgrade-branch-diff.md](docs/version-upgrade-branch-diff.md) |
| **数据归属与隔离**：MCP Key 为什么管不住数据、环境改项目级、哪些表该留全局（含一条「假隔离」陷阱） | [docs/data-scoping-and-isolation.md](docs/data-scoping-and-isolation.md) |
| **权限模型**：440 个端点各挂什么守卫、角色档位（系统 admin/user/guest + 项目 manager/member）、前端按权限藏入口的口径、**2026-08-29 为什么砍到这几档** | [docs/permission-audit-2026-08.md](docs/permission-audit-2026-08.md) + `backend/app/core/permissions.py`（权限点与角色映射的唯一出处） |
| **QA 仓场景清单（只读）**：读什么、为什么不能写、清单/脚本头怎么解析、页面为什么这么排（P/R 口径照抄对方定义）、脚本正文白名单、**域级 AI 评审**（环境缺口那一列的四个坑） | [docs/qa-repo-readonly-catalog.md](docs/qa-repo-readonly-catalog.md) |

## 长驻服务

用 `deploy/start-ai-services.sh` 启动（幂等）：

- **claude-proxy :38210** — 429 降级通道。挂了则限流只能靠重试。**文本生成仍依赖它**。
- **playwright-mcp :38931** — 平台侧 UI 脚本生成的浏览器通道，host 只认 `localhost`。
  **2026-08-08 起平台侧 UI 生成已封存**（见上表那份文档的红线 1），所以**日常运行不需要起它**；
  顶栏「服务 N/17」里它显示 notConfigured 是正常的，不是坏了。只有要重新启用平台侧生成时才起。
