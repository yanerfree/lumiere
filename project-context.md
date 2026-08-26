# Lumiere 项目上下文（TEA 强制约束）

本文件是 TEA 所有工作流（Framework / ATDD / Automate / Review）的强制输入。
TEA 在生成测试用例和脚本时**必须遵循**本文件中的所有规范。

---

## 1. 项目信息

| 项目 | 值 |
|------|---|
| 名称 | Lumiere — 测试管理平台 |
| 技术栈 | fullstack（后端 Python FastAPI + 前端 React） |
| 后端框架 | FastAPI + SQLAlchemy async + asyncpg + pytest |
| 前端框架 | React 19 + Ant Design + Vite |
| 数据库 | PostgreSQL 13+ |
| 语言 | Python 3.11+ |
| 包管理 | pyproject.toml |

---

## 2. 测试目录结构（固定规范）

```
{project_root}/
├── tea-cases.json                        # 用例清单（项目根目录，固定位置）
├── tests/
│   ├── conftest.py                       # 全局公共 fixtures
│   ├── factories.py                      # 数据工厂（faker + overrides）
│   │
│   ├── unit/                             # 单元测试
│   │   ├── {module}/                     #   按业务模块分子目录
│   │   │   ├── conftest.py              #   模块级 fixtures（可选）
│   │   │   ├── test_{slug}.py           #   一场景一文件
│   │   │   └── ...
│   │   └── ...
│   │
│   ├── integration/                      # 集成测试
│   │   ├── {module}/
│   │   │   ├── conftest.py
│   │   │   ├── test_{slug}.py
│   │   │   └── ...
│   │   └── ...
│   │
│   ├── api/                              # API 接口测试
│   │   ├── {module}/
│   │   │   ├── conftest.py
│   │   │   ├── test_{slug}.py
│   │   │   └── ...
│   │   └── ...
│   │
│   └── e2e/                              # E2E 端到端测试
│       ├── {module}/
│       │   ├── conftest.py
│       │   ├── test_{slug}.py
│       │   └── ...
│       └── ...
```

### 2.1 目录规则

| 规则 | 说明 |
|------|------|
| 四个级别目录 | `unit/`、`integration/`、`api/`、`e2e/` 固定不可改 |
| 按模块分子目录 | 每个级别下按 `{module}/` 组织，如 `api/auth/`、`unit/security/` |
| 一场景一文件 | 每个测试场景独立一个 `.py` 文件，文件名 `test_{slug}.py` |
| conftest 层级 | 全局 `tests/conftest.py` + 各级别/模块可选 `conftest.py` |
| factories 全局 | `tests/factories.py` 放在 tests 根目录，所有级别共用 |

### 2.2 不允许的做法

| 禁止 | 原因 |
|------|------|
| 一个文件多个测试场景 | 破坏 tea-cases.json 1:1 映射，影响并发执行 |
| 自定义目录名（如 `spec/`、`test/`） | 平台导入逻辑无法识别 |
| 把测试放在 `backend/tests/` 下 | 测试目录在项目根目录 `tests/`，不在 backend 内 |
| 跳过 `{module}/` 子目录直接放文件 | 模块信息丢失，影响报告分组 |

---

## 3. 四个测试级别定义

### 3.1 unit（单元测试）

| 项目 | 说明 |
|------|------|
| **测什么** | 单个函数、类、工具方法的内部逻辑 |
| **怎么测** | 直接调用函数，不依赖数据库、网络、外部服务 |
| **外部依赖** | 全部 mock，零 I/O |
| **执行速度** | 毫秒级 |
| **目录** | `tests/unit/{module}/test_{slug}.py` |
| **适用场景** | 密码哈希、数据校验、权限判断逻辑、公式计算、状态机转换 |

```python
# 示例：tests/unit/auth/test_password_hashing.py
from app.core.security import hash_password, verify_password

class TestPasswordHashing:
    def test_hash_and_verify_match(self):
        hashed = hash_password("Test@123")
        assert verify_password("Test@123", hashed) is True

    def test_wrong_password_fails(self):
        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False
```

### 3.2 integration（集成测试）

| 项目 | 说明 |
|------|------|
| **测什么** | 多个组件组合在一起的交互，特别是与数据库、消息队列的交互 |
| **怎么测** | 需要真实数据库（测试库），验证数据实际写入/读取 |
| **外部依赖** | 真实数据库，可 mock 外部 HTTP 服务 |
| **执行速度** | 百毫秒到秒级 |
| **目录** | `tests/integration/{module}/test_{slug}.py` |
| **适用场景** | Service 层写入数据库、事务回滚、唯一约束、级联删除、Alembic 迁移 |

```python
# 示例：tests/integration/users/test_user_repository_create.py
class TestUserRepositoryCreate:
    async def test_create_user_persists_to_db(self, db_session):
        user = User(username="testuser", password_hash="...", role="user")
        db_session.add(user)
        await db_session.flush()

        result = await db_session.get(User, user.id)
        assert result.username == "testuser"

    async def test_duplicate_username_raises(self, db_session):
        # ... 验证唯一约束
```

### 3.3 api（API 接口测试）

| 项目 | 说明 |
|------|------|
| **测什么** | 单个 API 端点的 HTTP 请求/响应、状态码、权限控制 |
| **怎么测** | 通过 httpx.AsyncClient 发真实 HTTP 请求到 FastAPI |
| **外部依赖** | FastAPI app + 测试数据库 |
| **执行速度** | 百毫秒到秒级 |
| **目录** | `tests/api/{module}/test_{slug}.py` |
| **适用场景** | 登录接口、CRUD 端点、权限 403、输入校验 422、唯一冲突 409 |

```python
# 示例：tests/api/auth/test_login_success.py
class TestLoginSuccess:
    async def test_correct_credentials_return_token(self, client):
        response = await client.post("/api/auth/login", json={
            "username": "admin",
            "password": "admin123",
        })
        assert response.status_code == 200
        assert "token" in response.json()["data"]
```

### 3.4 e2e（端到端测试）

| 项目 | 说明 |
|------|------|
| **测什么** | 完整的用户业务流程，跨多个接口或页面 |
| **怎么测** | 串联多个 API 调用或浏览器操作，模拟真实用户行为 |
| **外部依赖** | 完整运行环境（FastAPI + PostgreSQL + 前端） |
| **执行速度** | 秒到分钟级 |
| **目录** | `tests/e2e/{module}/test_{slug}.py` |
| **适用场景** | 登录→创建项目→导入用例→执行计划→查看报告 |

```python
# 示例：tests/e2e/auth/test_login_then_access_project.py
class TestLoginThenAccessProject:
    async def test_full_login_flow(self, client):
        # Step 1: 登录
        login_resp = await client.post("/api/auth/login", json={...})
        token = login_resp.json()["data"]["token"]

        # Step 2: 用 token 访问项目列表
        headers = {"Authorization": f"Bearer {token}"}
        projects_resp = await client.get("/api/projects", headers=headers)
        assert projects_resp.status_code == 200
```

### 3.5 级别选择规则

```
能用 unit 就不用 integration
能用 integration 就不用 api
能用 api 就不用 e2e
同一个行为不要在多个级别重复测试
```

| 要测的东西 | 用哪个级别 | 不要用 |
|-----------|-----------|--------|
| 纯函数逻辑（无 I/O） | unit | api/e2e（杀鸡用牛刀） |
| 数据库读写 | integration | unit（mock 不了真实约束） |
| HTTP 接口契约 | api | e2e（太重） |
| 跨接口业务流程 | e2e | unit（覆盖不了） |
| 权限控制 | api | unit（需要真实中间件） |

---

## 4. tea-cases.json 规范

### 4.1 这个文件是什么

`tea-cases.json` 是 TEA 生成的**用例清单文件**，是测试脚本的索引。

TEA 每次生成或更新测试脚本时，会同时生成这个文件。它记录了当前项目的所有测试用例：每条用例叫什么、属于哪个模块、什么优先级、对应的脚本文件在哪里。

**它的作用：**

| 角色 | 怎么用 |
|------|--------|
| **TEA** | 每次生成脚本时同步更新此文件，保证脚本和清单一一对应 |
| **Lumiere 平台** | 读取此文件导入用例，根据 `tea_id` 匹配新增/更新/移除，根据 `script_ref.file` 定位脚本执行 |
| **开发者** | 可以查看当前有多少用例、覆盖了哪些模块、什么优先级分布 |

**它不是什么：**
- 不是测试脚本本身（脚本在 `tests/` 目录下的 `.py` 文件里）
- 不是测试执行结果（结果在执行后生成的 `{case_id}.json` 里）
- 不是手动维护的文件（由 TEA 自动生成，人工不应该直接编辑）

**与脚本的关系：**
```
tea-cases.json 中的一条记录  ←1:1→  tests/ 下的一个 test_{slug}.py 文件
```
如果 tea-cases.json 里有一条 `tea_id: "auth_login_success"`，那 `tests/api/auth/test_login_success.py` 就必须存在。反过来，`tests/` 下的每个测试文件也必须在 tea-cases.json 中有对应记录。

### 4.2 收录级别配置

```yaml
# tea-cases.json 收录哪些测试级别
tea_cases_levels: ["api", "e2e"]
```

| 配置值 | 含义 |
|--------|------|
| `["api", "e2e"]` | **默认值** — 只收录 api 和 e2e 级别的用例，平台导入和执行这两类 |
| `["unit", "integration", "api", "e2e"]` | 全部收录 — 四个级别都纳入平台管理 |

**说明：**
- TEA 生成脚本时**四个级别都会生成**到 `tests/` 目录下
- 但 tea-cases.json **只收录配置中指定的级别**
- 未收录的级别（如 unit/integration）脚本照常存在，开发者可以直接 `pytest tests/unit/` 运行，只是不纳入平台导入
- 后续如果需要扩展，改这个配置即可

### 4.3 位置

项目根目录，与 `tests/`、`frontend/`、`backend/` 同级。

### 4.3 完整示例

```json
{
  "version": "1.0",
  "generatedAt": "2026-04-15T10:00:00Z",
  "project": "Lumiere",
  "summary": {
    "total": 39,
    "byLevel": { "unit": 5, "integration": 4, "api": 25, "e2e": 5 },
    "byPriority": { "P0": 22, "P1": 14, "P2": 3, "P3": 0 },
    "byModule": { "auth": 12, "users": 6, "projects": 8, "health": 1, "security": 3, "permissions": 9 }
  },
  "cases": [
    {
      "tea_id": "auth_login_success",
      "title": "登录成功返回 JWT token",
      "module": "auth",
      "submodule": "login",
      "type": "api",
      "level": "api",
      "priority": "P0",
      "script_ref": {
        "file": "tests/api/auth/test_login_success.py",
        "class": "TestLoginSuccess",
        "func": "test_correct_credentials_return_token"
      },
      "tags": ["smoke", "security"]
    },
    {
      "tea_id": "security_password_hashing",
      "title": "bcrypt 密码哈希生成与验证",
      "module": "security",
      "submodule": null,
      "type": "api",
      "level": "unit",
      "priority": "P0",
      "script_ref": {
        "file": "tests/unit/security/test_password_hashing.py",
        "class": "TestPasswordHashing",
        "func": "test_hash_and_verify_match"
      },
      "tags": ["security"]
    },
    {
      "tea_id": "users_create_persist_db",
      "title": "创建用户写入数据库验证",
      "module": "users",
      "submodule": null,
      "type": "api",
      "level": "integration",
      "priority": "P0",
      "script_ref": {
        "file": "tests/integration/users/test_user_repository_create.py",
        "class": "TestUserRepositoryCreate",
        "func": "test_create_user_persists_to_db"
      },
      "tags": []
    },
    {
      "tea_id": "auth_login_then_access_project",
      "title": "登录后访问项目列表完整流程",
      "module": "auth",
      "submodule": "login",
      "type": "e2e",
      "level": "e2e",
      "priority": "P1",
      "script_ref": {
        "file": "tests/e2e/auth/test_login_then_access_project.py",
        "class": "TestLoginThenAccessProject",
        "func": "test_full_login_flow"
      },
      "tags": ["e2e", "regression"]
    }
  ]
}
```

### 4.4 格式

```json
{
  "version": "1.0",
  "generatedAt": "2026-04-15T10:00:00Z",
  "cases": [
    {
      "tea_id": "auth_login_success",
      "title": "登录成功返回 JWT token",
      "module": "auth",
      "submodule": "login",
      "type": "api",
      "level": "api",
      "priority": "P0",
      "script_ref": {
        "file": "tests/api/auth/test_login_success.py",
        "class": "TestLoginSuccess",
        "func": "test_correct_credentials_return_token"
      },
      "tags": ["smoke", "security"]
    }
  ]
}
```

### 4.5 字段定义

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tea_id` | string | 是 | 唯一标识，格式 `{module}_{slug}`，平台导入匹配键 |
| `title` | string | 是 | 用例标题（中文） |
| `module` | string | 是 | 业务模块编码（对应目录名，如 `auth`） |
| `submodule` | string | 否 | 子模块（导入时自动创建） |
| `type` | string | 是 | 测试类型：`api` 或 `e2e` |
| `level` | string | 是 | 测试级别：`unit` / `integration` / `api` / `e2e` |
| `priority` | string | 是 | 优先级：`P0` / `P1` / `P2` / `P3` |
| `script_ref.file` | string | 是 | 相对项目根目录的脚本路径 |
| `script_ref.class` | string | 否 | 测试类名 |
| `script_ref.func` | string | 否 | 测试函数名 |
| `tags` | string[] | 否 | 标签（如 `smoke`、`security`、`regression`） |

### 4.6 固定约定

| 约定 | 违反后果 |
|------|---------|
| `tea_id` 全局唯一 | 平台导入匹配错乱 |
| `tea_id` 格式 `{module}_{slug}` | 模块归属解析失败 |
| `script_ref.file` 为相对路径 | 执行引擎找不到脚本 |
| `script_ref.file` 与实际文件路径一致 | 执行失败 |
| `tea-cases.json` 放项目根目录 | 平台找不到导入文件 |

---

## 5. 文件命名规范

| 项目 | 规范 | 示例 |
|------|------|------|
| 测试文件 | `test_{slug}.py` | `test_login_success.py` |
| slug | 小写下划线，描述场景 | `login_wrong_password`、`create_user_duplicate` |
| conftest | `conftest.py`（固定名称） | `tests/api/auth/conftest.py` |
| factories | `factories.py`（全局共用） | `tests/factories.py` |
| tea_id | `{module}_{slug}` | `auth_login_success` |

---

## 6. 脚本编写规范

### 6.1 文件结构

```python
"""
test_{slug} — {一句话描述}
Test ID: {Epic}.{Story}-{LEVEL}-{SEQ}
Priority: {P0/P1/P2/P3}
"""
import pytest
# ... imports

class Test{PascalCaseScenario}:
    """测试场景描述"""

    async def test_{specific_behavior}(self, client):
        # Given: 前置条件
        # When: 执行操作
        # Then: 验证结果
        ...
```

### 6.2 编写规则

| 规则 | 说明 |
|------|------|
| 一个文件一个 class | class 名 = 场景名（PascalCase） |
| class 内可多个 test 方法 | 同一场景的不同断言可以在一个 class 里 |
| 不硬编码测试数据 | 使用 factories.py 的工厂函数 |
| Given/When/Then 注释 | 关键步骤用注释标明意图 |
| 异步测试用 async def | pytest-asyncio asyncio_mode=auto |
| 每个测试独立 | 不依赖其他测试的执行顺序或结果 |

---

## 7. 执行时步骤输出格式

每个用例执行后输出 `{case_id}.json`：

```json
{
  "case_id": "auth_login_success",
  "status": "passed",
  "duration_ms": 320,
  "steps": [
    {
      "seq": 1,
      "action": "发送登录请求",
      "status": "passed",
      "phase": "action",
      "duration_ms": 280,
      "requests": [
        {
          "method": "POST",
          "url": "http://localhost:8000/api/auth/login",
          "status": 200,
          "duration_ms": 250,
          "request_body": "{\"username\":\"admin\",\"password\":\"admin123\"}",
          "response_body": "{\"data\":{\"token\":\"eyJ...\"}}"
        }
      ]
    }
  ]
}
```

### 状态枚举

| 状态 | 含义 | 谁判定 |
|------|------|--------|
| `passed` | 通过 | pytest |
| `failed` | 断言失败 | pytest |
| `error` | 执行异常（脚本错误） | pytest |
| `skipped` | 跳过 | pytest |
| `xfail` | 预期失败 | pytest |
| `flaky` | 不稳定测试 | **平台判定**（非脚本判定） |

---

## 8. 模块清单（Lumiere 项目）

| module | 说明 | 对应后端 |
|--------|------|---------|
| `auth` | 认证（登录、token、密码） | `app/api/auth.py` |
| `users` | 用户管理 | `app/api/users.py` |
| `projects` | 项目管理 | `app/api/projects.py` |
| `branches` | 分支配置 | `app/api/branches.py` |
| `cases` | 用例管理 | `app/api/cases.py` |
| `plans` | 测试计划 | `app/api/plans.py` |
| `reports` | 报告 | `app/api/reports.py` |
| `environments` | 环境配置 | `app/api/environments.py` |
| `notifications` | 通知渠道 | `app/api/notifications.py` |
| `logs` | 操作日志 | `app/api/logs.py` |
| `health` | 健康检查 | `app/core/health.py` |
| `security` | 安全工具（bcrypt、JWT） | `app/core/security.py` |
| `permissions` | RBAC 权限 | `app/core/permissions.py` |

---

## 9. 本规范的适用范围

- **TEA 所有工作流**（Framework / ATDD / Automate / Review）在生成 Lumiere 项目的测试时，必须遵循本规范
- **其他项目**可以复制本文件到自己的项目根目录，修改第 1 节（项目信息）和第 8 节（模块清单），其余规范通用
- 如果本文件与 TEA 工作流模板默认行为冲突，**以本文件为准**
