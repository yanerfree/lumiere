# 权限审计报告 — 2026-08（对外多用户开放前）

> 目的：对外开放多用户前，摸清 Lumiere 当前权限模型够不够用、管控点有没有 bug。
> 方法：对全部 **440 个 API 端点**按依赖树做运行时反射（不是正则匹配函数名——那样会把
> `require_project_role` 返回的闭包误判成裸奔），逐条算出最强守卫与允许角色。
> **本报告只读审计，未改任何代码。**

## 0. 结论速览

- **核心链路（用例/计划/环境/变量/分支）治理扎实**：`verify_path_scope` 防跨项目改 id、
  `tests/test_endpoint_auth.py` 封样防「整族漏鉴权」、skill 路径穿越双保险。上一轮已收口
  74 个未认证端点。
- **问题集中在后加的、边缘的模块**：截图服务、MCP Key、平台 skill、knowledge、ai-config、
  通知渠道、debug 代理。
- **现有角色档位区分度过低**，不足以支撑「按人分配可见页面」的多用户开放。
- **前端几乎不按角色藏入口**（全项目仅 1 处 admin 菜单判断），真正的边界全靠后端。

## 1. 守卫分布（440 端点）

| 守卫级别 | 数量 | 说明 |
|---|---|---|
| `PROJECT_ROLE` | 200 | 项目级角色校验（最强） |
| `AUTHED_ONLY` | 217 | **仅校验登录，不看角色** |
| `SYS_ROLE` | 15 | 系统级 admin 校验 |
| `CASE_ACCESS` | 3 | 仅 case_id 的归属校验 |
| `NAKED` | 5 | 无鉴权——**均为设计公开**（healthz/readyz/login/refresh/截图 capability URL） |

217 个 `AUTHED_ONLY` 中，169 条是 Mock/工具族（设计上「登录即用」，可接受），
其余藏着下面的越权与平台级风险。

## 2. 漏洞清单（按严重度）

### P0 — 未认证任意文件读取（已实测复现）

**`GET /api/screenshots/files/{path:path}`**（`screenshots.py:55`）
```python
file_path = UPLOAD_DIR / path          # 无 resolve/包含性校验
return FileResponse(file_path)
```
- 该端点在封样白名单 `PUBLIC_BY_DESIGN` 里（免鉴权，因为 `<img>` 带不了 Authorization 头）。
- `path` 未规范化，`../../` 直接穿越出 `data/screenshots`。
- **实测**：`curl '/api/screenshots/files/../../pyproject.toml'` 无 token 读出仓库文件（200）。
- 上传侧 `project_id`/`session_id` 也是用户可控、直接拼进落盘路径。
- 修法：`resolved = (UPLOAD_DIR / path).resolve()`，校验 `UPLOAD_DIR` 是其父链；上传侧同样规范化。
  **这条无论走哪种权限模型都应最先修。**

### P1 — 登录即可越权（跨项目 / 平台级）

| 端点 | 问题 | 影响 |
|---|---|---|
| `POST /api/mcp-keys`、`PATCH /api/mcp-keys/{id}` | 建/改 Key 不校验请求体 `project_id` 是否为调用者所属项目（`mcp_keys.py:133`） | 任意用户发一把绑**别人项目**的 Key，拿到该项目**数据范围**。MCP 中间件下游只比对「Key.project_id == 资源.project_id」，**无成员兜底** |
| `PUT /api/skills/{name}`、`POST .../rollback/{ts}` | 函数签名无守卫，仅路由级 `_AUTHED` | 任意登录用户覆写平台预置 `lum-*` skill 正文（喂后端 LLM 的 prompt）→ **平台级 prompt 注入**。路径穿越已被 `_skill_dir` 挡住，缺的是授权 |
| `GET/POST/DELETE /api/projects/{project_id}/knowledge` | 带 `project_id` 仅校验登录 | 越权读/写/删**任意项目知识库**；会喂 AI → 污染他人项目 AI 上下文 |
| `/api/projects/{project_id}/ai-config`（select/custom/test/delete，5 条） | 同上 | 把**别人项目**的 AI 通道改到攻击者端点 → 该项目用例/需求文档随生成请求外泄 |
| `GET /api/projects/{project_id}` | 无成员校验（docstring 自陈「后补的」） | 登录即可读任意项目详情。同路径 PUT/DELETE 却要系统 admin |
| `GET /api/projects/{project_id}/exploratory/sessions[/{id}]` | 带 project_id 仅校验登录 | 越权读他人项目探索测试记录 |

### P2 — 登录即可（平台设施 / SSRF）

| 端点 | 问题 |
|---|---|
| `POST /api/debug/send` | 无限制 HTTP 代理（任意 method/URL/header/body），仅登录 → SSRF，可打内网。与 toolbox/http-client 重复且更裸 |
| `channels` CRUD（`variables.py`） | 通知渠道是**全局平台设施**，登录即可增删改，无 admin 门槛。紧挨着的环境/全局变量都用了 `require_project_role`，唯独 channels 漏了 |
| `GET /api/system/services` | 暴露内部服务与端口清单，仅登录 |

## 3. 角色能力矩阵（回答「档位够不够」）

**系统角色**：`admin` / `user`（2 档）。**项目角色**：`project_admin` / `developer` / `tester` / `guest`（4 档）。
均为裸 `String`，**无 DB CheckConstraint / Enum 约束**。

| 项目角色 | 可达项目端点 | 其中写操作 | 观察 |
|---|---|---|---|
| project_admin | 200 | 123 | 只比 developer 多 **13** 个端点，管理区分度低 |
| developer | 187 | 110 | 与 tester 能力**几乎重合** |
| tester | 177 | 100 | 与 developer 差异极小 |
| guest | 74 | **7** | 名义只读，却混进 7 个写端点（documents：生成/删除/优化） |

**判断**：现在实际只有「能改 / 只读」两档在起作用，`developer`/`tester` 名不副实、`guest` 不安全。
对外开放「给某人只开某几个页面」的诉求，现有模型给不了。

## 4. 结构性缺口

1. **封样只保证「要登录」，不保证「要对的角色 / 对的项目」。** 缺一条封样：
   「路径含 `{project_id}` 的写端点必须有 `require_project_role`」。
2. **需补项目级守卫的端点（9 条，去重后）**：knowledge×3、ai-config×5、project 详情×1，
   （exploratory sessions×2 视需要）。
3. **平台级设施应归系统角色**：`channels`、`skills`、`debug`、`system/services`、`ai-providers`；
   `mcp-keys` 建 Key 应对齐数据范围。
4. **前端不按角色收口**：菜单/按钮对所有登录用户可见（仅 `App.jsx:182` 一处 admin 判断）。
   → 对 AI 助手方案的硬约束：助手能力边界**必须以后端角色/权限校验为准**，不能以「页面上有没有按钮」为准。

## 5. 附录：审计方法

- 遍历 `app.routes` 的 `APIRoute`，递归其 `dependant.dependencies`；按依赖 `call` 的
  `__module__`（`app.deps.auth` / `app.deps.scope`）和 `__qualname__` 判定守卫类型；
  从 `require_role` / `require_project_role` 的闭包 `__closure__` 取出允许角色元组。
- 挂载级依赖（`include_router(dependencies=...)`）也在依赖树上，能正确算进去。
- P0 用 `curl` 对运行中的 `:8756` 实测复现，只读取无害的 `pyproject.toml`，未触碰 `.env` 或系统文件。
