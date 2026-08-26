---
name: generate-test-suite
description: '根据业务逻辑和接口信息，生成三合一测试用例（手动步骤 + API 脚本 + UI 脚本）。Use when user says "生成测试用例" or "generate test suite for [feature]"'
allowed-tools: Read Write Bash Glob
---

# 三合一测试用例生成

根据用户提供的**业务逻辑描述**和**接口信息**，一次性生成包含三个维度的完整测试用例：
- **手动测试步骤** — 给测试人员的中文操作指南
- **接口测试场景** — API 自动化脚本 + 步骤
- **UI 测试场景** — E2E 自动化脚本 + 步骤

输出格式直接兼容 Lumiere 平台导入。

---

## 输入参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `业务描述` | ✅ | 功能的业务逻辑说明（自然语言） |
| `接口信息` | ✅ | API 端点、请求/响应格式、字段约束（Swagger/代码/文档均可） |
| `模块名` | ✅ | 业务模块（如 `users`、`plans`），用于文件命名和 tea_id |
| `页面URL` | 可选 | UI 测试的目标页面（如 `http://localhost:5173/users`）。不提供则只生成手动步骤和 API 场景 |
| `文档路径` | 可选 | PRD 或设计文档路径，用于补充业务规则 |

---

## 执行流程

```
Step 1 收集信息    → 解析业务描述 + 接口信息 + 可选文档
Step 2 推导场景    → 拆分为独立的测试场景（正向 + 异常）
Step 3 生成手动步骤 → 每个场景写人能读的操作步骤
Step 4 生成API场景  → 每个场景写接口自动化步骤 + pytest 脚本
Step 5 生成UI场景   → 每个场景写 E2E 自动化步骤 + Playwright 脚本
Step 6 输出文件     → tea-cases.json + test_*.py 脚本文件
Step 7 验证        → pytest --collect-only 检查语法
Step 8 报告        → 输出生成摘要
```

---

## Step 1：收集信息

### 1.1 解析接口信息

从用户提供的信息中提取：

| 信息 | 来源 | 示例 |
|------|------|------|
| HTTP 方法 + 路径 | Swagger / 代码 / 用户描述 | `POST /api/users` |
| 请求体字段 | Schema 定义 | `{username: str(3-50), email: str, role: enum}` |
| 字段约束 | Field 定义 | `min_length=3, max_length=50, Literal[...]` |
| 响应格式 | 响应 Schema | `{data: {id, username, role}}` |
| 权限要求 | Depends 声明 | `require_role("admin")` |
| 错误码 | AppError 定义 | `USER_DUPLICATE → 409` |

### 1.2 解析业务规则

从业务描述中提取：
- 正向流程（创建成功、查询返回、更新生效）
- 约束规则（唯一性、必填、枚举范围）
- 异常流程（重复、不存在、权限不足）
- 副作用（发邮件、写日志、级联更新）

### 1.3 读取项目规范

用 `Read` 工具读取：
1. **`project-context.md`** — 目录结构、脚本模板
2. **`tests/conftest.py`** — 可用 fixtures
3. **`.env`** — 环境变量和凭据

---

## Step 2：推导测试场景

对每个接口，按以下规则推导场景：

### 正向场景

| 接口类型 | 场景 | slug | 优先级 |
|---------|------|------|--------|
| POST 创建 | 创建成功 | `create_{resource}_success` | P0 |
| GET 列表 | 列表返回 | `list_{resources}` | P0 |
| GET 详情 | 获取成功 | `get_{resource}_detail` | P1 |
| PUT 更新 | 更新成功 | `update_{resource}_success` | P1 |
| DELETE | 删除成功 | `delete_{resource}_success` | P1 |

### 异常场景

| 条件 | 场景 | slug | 优先级 |
|------|------|------|--------|
| 有权限依赖 | 无 token → 401 | `{action}_unauthorized` | P1 |
| 有权限依赖 | 低权限 → 403 | `{action}_forbidden` | P1 |
| 有路径参数 | ID 不存在 → 404 | `{action}_not_found` | P1 |
| 有请求体 | 缺必填字段 → 422 | `create_{resource}_missing_fields` | P1 |
| 有唯一约束 | 重复 → 409 | `create_{resource}_duplicate` | P1 |
| 有枚举字段 | 非法值 → 422 | `create_{resource}_invalid_enum` | P2 |

---

## Step 3：生成手动测试步骤

为每个场景生成**测试人员可直接执行**的手动步骤。

### 手动步骤格式

```json
[
  {"seq": 1, "action": "以管理员身份登录系统", "expected": "登录成功，进入首页"},
  {"seq": 2, "action": "进入用户管理页面", "expected": "页面正常加载，显示用户列表"},
  {"seq": 3, "action": "点击【新建用户】按钮", "expected": "弹出新建用户对话框"},
  {"seq": 4, "action": "填写用户名 test_user01、邮箱 test@example.com、角色选择 tester，点击确认", "expected": "提示"创建成功"，对话框关闭"},
  {"seq": 5, "action": "在用户列表中搜索 test_user01", "expected": "列表中显示新创建的用户，角色为 tester"}
]
```

### 编写原则

1. **操作步骤用人话写**：不写代码术语，写用户在页面上的操作
2. **预期结果要具体**：不写"正确"，写"提示创建成功，列表中出现新用户"
3. **包含验证步骤**：最后 1-2 步必须验证操作结果
4. **前置条件写清楚**：第一步说明需要什么角色/权限登录

---

## Step 4：生成 API 测试场景

### API 场景格式

```json
{
  "steps": [
    {"seq": 1, "action": "管理员登录", "expected": "获取 token", "phase": "setup", "apiEndpoint": "POST /api/auth/login → 200"},
    {"seq": 2, "action": "创建用户", "expected": "返回 201 + 用户数据", "phase": "action", "apiEndpoint": "POST /api/users → 201"},
    {"seq": 3, "action": "验证用户存在", "expected": "用户列表包含新用户", "phase": "verify", "apiEndpoint": "GET /api/users → 200"}
  ],
  "scriptRefFile": "tests/api/users/test_create_user_success.py",
  "scriptRefFunc": "TestCreateUserSuccess::test_create_user_success",
  "variablesUsed": ["base_url", "admin_username", "admin_password"]
}
```

### API 脚本模板

```python
"""
test_{slug} — {场景中文描述}
Test ID: {module}_{slug}
Priority: {P0/P1/P2}
"""
import pytest
from tea_step import tea_step


class Test{PascalCaseSlug}:
    """{模块}: {场景描述}"""

    async def _setup(self, client, db_session):
        with tea_step("管理员登录", phase="setup"):
            from tests.conftest import create_test_user, make_auth_headers
            user = await create_test_user(db_session, role="admin")
            headers = make_auth_headers(user)
        return headers

    @pytest.mark.asyncio
    async def test_{slug}(self, client, db_session):
        headers = await self._setup(client, db_session)

        with tea_step("{操作描述}", phase="action"):
            resp = await client.{method}(
                "{path}",
                headers=headers,
                json={请求体},
            )

        with tea_step("{验证描述}", phase="verify"):
            assert resp.status_code == {expected_status}
            data = resp.json()["data"]
            assert {业务字段断言}
```

### 脚本输出路径

```
tests/api/{module}/test_{slug}.py
```

---

## Step 5：生成 UI 测试场景

如果用户提供了页面 URL，生成 UI 测试场景。

### UI 场景格式

```json
{
  "steps": [
    {"seq": 1, "action": "打开登录页并登录", "expected": "进入首页", "phase": "setup", "uiTarget": "/login"},
    {"seq": 2, "action": "导航到用户管理页面", "expected": "页面加载完成", "phase": "setup", "uiTarget": "/users"},
    {"seq": 3, "action": "点击新建用户按钮", "expected": "弹出对话框", "phase": "action", "uiTarget": "button[name=新建用户]"},
    {"seq": 4, "action": "填写表单并提交", "expected": "提示创建成功", "phase": "action", "uiTarget": "form.create-user"},
    {"seq": 5, "action": "验证用户列表", "expected": "列表包含新用户", "phase": "verify", "uiTarget": "table.user-list"}
  ],
  "scriptRefFile": "tests/e2e/users/test_create_user_success.py",
  "scriptRefFunc": "TestCreateUserSuccess::test_create_user_success",
  "variablesUsed": ["APP_URL", "USERNAME", "PASSWORD"]
}
```

### UI 脚本模板

```python
"""
test_{slug} — {场景中文描述}（E2E）
Test ID: {module}_e2e_{slug}
Priority: {P0/P1/P2}
"""
import pytest
import re
from playwright.async_api import Page, expect
from tea_step import tea_step


PAGE_URL = "{目标页面路径}"

class Test{PascalCaseSlug}:
    """{页面名}: {场景描述}"""

    async def _login(self, page: Page):
        with tea_step("用户登录", phase="setup"):
            await page.goto("/login")
            await page.get_by_placeholder("用户名").fill("{USERNAME}")
            await page.get_by_placeholder("密码").fill("{PASSWORD}")
            await page.get_by_role("button", name="登录").click()
            await page.wait_for_url("**/dashboard**")

    @pytest.mark.asyncio
    async def test_{slug}(self, page: Page):
        await self._login(page)

        with tea_step("导航到目标页面", phase="setup"):
            await page.goto(PAGE_URL)
            await page.wait_for_load_state("networkidle")

        with tea_step("{操作描述}", phase="action"):
            {Playwright 操作代码}

        with tea_step("{验证描述}", phase="verify"):
            {Playwright 断言代码}
```

### 脚本输出路径

```
tests/e2e/{module}/test_{slug}.py
```

---

## Step 6：输出 tea-cases.json

### 三合一用例格式

每条用例包含 `steps`（手动）、`api_scenario`（接口）、`ui_scenario`（UI）三个部分：

```json
{
  "cases": [
    {
      "tea_id": "users_create_user_success",
      "title": "创建用户 — 正向流程",
      "module": "users",
      "type": "api",
      "priority": "P0",
      "preconditions": "管理员账号可用",
      "steps": [
        {"seq": 1, "action": "以管理员身份登录系统", "expected": "登录成功"},
        {"seq": 2, "action": "进入用户管理页面，点击【新建用户】", "expected": "弹出新建对话框"},
        {"seq": 3, "action": "填写用户名 test_user、邮箱 test@example.com、角色 tester，点击确认", "expected": "提示创建成功"},
        {"seq": 4, "action": "在用户列表搜索 test_user", "expected": "列表中显示该用户"}
      ],
      "expected_result": "用户创建成功并可在列表中查看",
      "api_scenario": {
        "steps": [
          {"seq": 1, "action": "管理员登录", "expected": "获取 token", "phase": "setup", "apiEndpoint": "POST /api/auth/login → 200"},
          {"seq": 2, "action": "创建用户", "expected": "返回 201", "phase": "action", "apiEndpoint": "POST /api/users → 201"},
          {"seq": 3, "action": "查询用户列表", "expected": "列表包含新用户", "phase": "verify", "apiEndpoint": "GET /api/users → 200"}
        ],
        "scriptRefFile": "tests/api/users/test_create_user_success.py",
        "scriptRefFunc": "TestCreateUserSuccess::test_create_user_success",
        "variablesUsed": ["base_url", "admin_username", "admin_password"]
      },
      "ui_scenario": {
        "steps": [
          {"seq": 1, "action": "登录并进入用户管理", "expected": "页面加载", "phase": "setup", "uiTarget": "/users"},
          {"seq": 2, "action": "点击新建并填写表单", "expected": "提交成功", "phase": "action", "uiTarget": "button[name=新建]"},
          {"seq": 3, "action": "验证列表包含新用户", "expected": "用户可见", "phase": "verify", "uiTarget": "table.users"}
        ],
        "scriptRefFile": "tests/e2e/users/test_create_user_success.py",
        "scriptRefFunc": "TestCreateUserSuccess::test_create_user_success",
        "variablesUsed": ["APP_URL", "USERNAME", "PASSWORD"]
      },
      "tags": ["auto-generated", "smoke"]
    }
  ],
  "summary": {
    "total": 1,
    "by_priority": {"P0": 1},
    "by_type": {"api": 1},
    "generated_at": "2026-05-26T10:00:00Z"
  }
}
```

### 关键规则

1. **`steps`** — 手动步骤，永远是 `{seq, action, expected}` 格式，给人读的
2. **`api_scenario`** — 接口测试场景，步骤带 `phase` + `apiEndpoint`，关联 pytest 脚本
3. **`ui_scenario`** — UI 测试场景，步骤带 `phase` + `uiTarget`，关联 Playwright 脚本
4. 三个部分**独立但互补**：手动步骤是业务视角，API/UI 是自动化视角
5. 如果没有提供页面 URL，`ui_scenario` 可以为 `null`

---

## Step 7：验证

```bash
# 验证 API 脚本语法
python -m pytest tests/api/{module}/ --collect-only -q 2>&1 | tail -20

# 验证 E2E 脚本语法（如果生成了的话）
python -m pytest tests/e2e/{module}/ --collect-only -q 2>&1 | tail -20
```

---

## Step 8：输出报告

```
## 三合一测试用例生成报告

### 业务模块: {module}
### 输入信息: {接口数量} 个端点 + {业务规则数量} 条规则

### 场景推导
| # | 场景 | 优先级 | 手动步骤 | API 场景 | UI 场景 |
|---|------|--------|---------|---------|---------|
| 1 | 创建用户成功 | P0 | 4 步 | 3 步 | 3 步 |
| 2 | 用户名重复 | P1 | 3 步 | 3 步 | - |
| 3 | 缺少必填字段 | P1 | 3 步 | 2 步 | - |

### 输出文件
- tea-cases.json: +{n} 条用例
- tests/api/{module}/: {n} 个脚本
- tests/e2e/{module}/: {n} 个脚本

### 导入方式
将生成的 tea-cases.json 通过平台「导入」功能导入，
用例将自动包含手动步骤、接口场景、UI 场景三个维度。
```

---

## 示例调用

用户输入：
```
生成测试用例 users

业务描述：
- 管理员可以创建用户，需填写用户名（3-50字符）、邮箱、角色（admin/developer/tester/guest）
- 用户名不可重复
- 创建成功后用户可立即登录

接口信息：
POST /api/users  → 201
  请求体: {username: str(3-50), email: str, role: Literal[admin,developer,tester,guest], password: str(8+)}
  权限: admin
GET /api/users   → 200
  查询参数: page, pageSize, keyword
  权限: any member

页面URL: http://localhost:5173/settings/users
```

AI 会为每个推导出的场景生成手动步骤 + API 脚本 + UI 脚本，输出 tea-cases.json 和 test_*.py 文件。
