# Lumiere 测试

## 环境准备

```bash
# 在项目根目录执行
cd testBench
pip install -e "backend/.[dev]"
```

确保 PostgreSQL 测试数据库可用：
```bash
createdb testbench_test
```

## 运行测试

```bash
# 在项目根目录执行（不是 backend/ 下）

# 全部测试
make test

# 带覆盖率
make test-cov

# 按级别运行
make test-unit           # 单元测试
make test-integration    # 集成测试
make test-api            # API 接口测试
make test-e2e            # E2E 端到端测试
```

## 双模式运行

测试脚本支持两种运行模式，由 `conftest.py` 自动切换：

| 模式 | 触发条件 | HTTP 客户端 | 用途 |
|------|---------|------------|------|
| 本地模式 | `BASE_URL` 环境变量为空 | `httpx ASGITransport`（进程内） | 本地开发、CI |
| 平台模式 | `BASE_URL` 环境变量有值 | `httpx.AsyncClient(base_url=BASE_URL)` | 平台执行、环境验证 |

平台执行时，环境变量由平台根据计划配置的"目标环境"自动注入（全局变量 + 环境变量合并，环境覆盖全局）。

## 环境变量使用规范（重要）

**核心原则：平台下发的环境变量优先，没有则用脚本自己的默认值。**

### 内置变量（conftest.py 自动处理）

| 变量名 | 平台值示例 | 本地默认值 | 说明 |
|--------|-----------|-----------|------|
| `BASE_URL` | `http://10.0.1.100:8000` | 空（走 ASGI） | HTTP 请求目标地址 |
| `DATABASE_URL` | `postgresql+asyncpg://...` | `settings.database_url` + `_test` 后缀 | 测试数据库连接 |

### 自定义变量（脚本中自行读取）

在测试脚本中使用 `os.environ.get("KEY", "默认值")` 读取，**禁止硬编码**：

```python
import os

# ✅ 正确：从环境变量读取，有默认值兜底
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
API_TIMEOUT = int(os.environ.get("API_TIMEOUT", "30"))

# ❌ 错误：硬编码
ADMIN_USERNAME = "admin"
```

### 认证方式

conftest.py 提供以下 helper，**新脚本必须使用 API 方式**：

```python
from tests.conftest import login_as, create_user_via_api

# ✅ 推荐：通过 API 认证（本地 + 平台都能用）
admin_headers = await login_as(client)                         # 用默认管理员
admin_headers = await login_as(client, "admin", "admin123")    # 指定账号
user_data = await create_user_via_api(client, admin_headers, "tester")  # API 创建用户
tester_headers = await login_as(client, "tester", "Test@123456")
```

```python
from tests.conftest import create_test_user, make_auth_headers

# ⚠️ 仅本地调试：直接操作 DB + 本地签 token（平台模式下会失败）
user = await create_test_user(db_session, "testuser")
headers, _ = make_auth_headers(user)
```

### 常用平台变量

| 变量名 | 说明 | 建议默认值 |
|--------|------|-----------|
| `BASE_URL` | 目标服务地址 | 空（本地 ASGI） |
| `DATABASE_URL` | 数据库连接串 | 本地 testbench_test |
| `ADMIN_USERNAME` | 管理员账号 | `admin` |
| `ADMIN_PASSWORD` | 管理员密码 | `admin123` |
| `TEST_PASSWORD` | 测试用户默认密码 | `Test@123456` |
| `API_TIMEOUT` | 请求超时秒数 | `30` |
| `DEBUG` | 调试模式 | `false` |

> 在平台"环境配置"页面设置这些变量，执行时会自动注入到测试进程。

## 目录结构

```
testBench/
├── tests/                          # 项目根目录下（不在 backend/ 内）
│   ├── conftest.py                 # 全局 fixtures（db_session, client）+ 双模式切换
│   ├── factories.py                # 数据工厂（make_user, make_project）
│   ├── unit/{module}/              # 单元测试：纯函数逻辑，无 I/O
│   ├── integration/{module}/       # 集成测试：数据库交互验证
│   ├── api/{module}/               # API 测试：HTTP 接口请求/响应
│   └── e2e/{module}/               # E2E 测试：完整业务流程
├── tea-cases.json                  # TEA 生成的用例清单
├── pyproject.toml                  # pytest + coverage 配置
└── Makefile                        # 测试命令
```

## 四个测试级别

| 级别 | 测什么 | 外部依赖 | 速度 |
|------|--------|---------|------|
| unit | 单个函数/类的内部逻辑 | 无，全部 mock | 毫秒级 |
| integration | 组件间交互（DB 读写等） | 真实数据库 | 百毫秒级 |
| api | 单个 API 端点请求/响应 | FastAPI + 测试库 | 百毫秒级 |
| e2e | 完整用户业务流程 | 完整运行环境 | 秒级 |

## Fixtures

| Fixture | Scope | 说明 |
|---------|-------|------|
| `db_session` | function | 每个测试独立事务，自动回滚 |
| `client` | function | httpx.AsyncClient，本地走 ASGI / 平台走真实 HTTP |

## 数据工厂

```python
from tests.factories import make_user, make_project

user = make_user()
admin = make_user(username="admin", role="admin")
project = make_project(name="my-project")
```

## 命名规范

- 文件名：`test_{slug}.py`（一场景一文件）
- 类名：`Test{PascalCaseScenario}`
- 方法名：`test_{specific_behavior}`
- tea_id：`{module}_{slug}`
