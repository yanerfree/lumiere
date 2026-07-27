---
name: tb-api-case-generate
description: 根据 API 接口定义自动生成接口测试场景（含请求步骤和断言）
version: 2
inputs:
  - api_info: 接口定义文本（method/url/参数约束/响应格式）
  - branch_id: 分支 ID（必填）
  - folder_id: 目标文件夹 ID（可选，不填则自动创建）
---

# 接口测试用例生成 Skill

根据 API 接口的参数定义（字段类型、必填、长度、枚举、正则等），自动生成结构化的测试场景。

## 场景拆分规则

| 条件 | 拆分方式 |
|------|---------|
| 接口字段 ≤3 个 | 所有参数校验合成一个场景：`[接口名]-参数校验` |
| 接口字段 >3 个 | 按字段拆分，每个字段一个场景：`[接口名]-[字段名]校验` |
| 任何接口 | 额外生成正向测试场景：`[接口名]-正向测试` |
| 需要认证的接口 | 额外生成安全测试场景：`[接口名]-安全测试` |

## 步骤生成规则

### 认证前置步骤

每个场景第一步：POST 登录获取 token。

```json
{
  "name": "登录获取token",
  "method": "POST",
  "url": "${BASE_URL}/api/auth/login",
  "headers": {"Content-Type": "application/json"},
  "body": {"username": "${ADMIN_USER}", "password": "${ADMIN_PASS}"},
  "assertions": [{"type": "status", "operator": "==", "value": 200}],
  "variables_extract": {"AUTH_TOKEN": "data.token"}
}
```

**重要**：
- 登录 URL 固定为 `${BASE_URL}/api/auth/login`
- token 变量名统一为 `AUTH_TOKEN`
- body 使用 `${ADMIN_USER}` 和 `${ADMIN_PASS}` 环境变量

### 需要认证的步骤

所有需要认证的步骤（不只是登录后的第一步）都**必须**显式写 Authorization header：

```json
"headers": {
  "Authorization": "Bearer ${AUTH_TOKEN}",
  "Content-Type": "application/json"
}
```

**禁止**写空 headers `{}` 依赖继承——执行引擎不支持 header 继承。

### 参数校验步骤

对每个字段，根据 schema 约束生成：

| 约束 | 正向步骤 | 反向步骤 |
|------|---------|---------|
| required | - | `[接口名]-[字段]缺失` → 400 |
| type: string | - | `[接口名]-[字段]类型错误(数字)` → 400 |
| minLength: N | `[字段]长度N(最小边界)` → 2xx | `[字段]长度N-1(低于最小值)` → 400 |
| maxLength: M | `[字段]长度M(最大边界)` → 2xx | `[字段]长度M+1(超过最大值)` → 400 |
| enum: [A,B] | `[字段]枚举值A(有效)` → 2xx | `[字段]枚举值invalid(无效)` → 400 |
| pattern: regex | `[字段]格式正确(下划线)` → 2xx | `[字段]格式错误(特殊字符)` → 400 |

### 正向测试步骤

1. 登录获取 token
2. 所有字段传合法值 → 期望 201
3. 验证响应字段（id 非空、返回值匹配）
4. DELETE 清理创建的资源（用变量 `${USER_ID}` 等）

> **⚠️ 请求体真实值必须原样沿用（正向用例最易踩坑）**
> 如果接口信息里给了真实请求体样例（`reqBody:` / `Body:` 抓包样例），正向用例的 body **必须照抄样例里的真实字段值**，尤其是：
> - **非空数组 / ID / UUID / 引用**（如 `isolation_rule_ids: ["019f..."]`、`upstream_id`、`cluster_id`）——这些是被测环境里真实存在、请求成功所必需的资源，**禁止**改成 `[]`、`null`、占位符或自己猜的值；
> - 嵌套对象（`config`/`upstream` 等）的结构和取值。
> 只有**需要唯一性的字段**（如 `name`）才加时间戳变量（`test-xxx-${TIMESTAMP}`）。把真实 ID 清空是导致「正向用例 422/400 校验失败」的头号原因。

### 安全测试步骤

1. 无 token 访问 → 401
2. 无效 token 访问 → 401
3. 低权限用户访问（需 admin 的接口） → 403
4. 重复数据（如用户名已存在） → 409

### 清理步骤

正向测试创建的资源**必须**在场景末尾 DELETE 清理：

```json
{
  "name": "清理-删除测试用户",
  "method": "DELETE",
  "url": "${BASE_URL}/api/users/${USER_ID}",
  "headers": {"Authorization": "Bearer ${AUTH_TOKEN}"},
  "assertions": [{"type": "status", "operator": "==", "value": 200}]
}
```

## 断言规范

### 断言类型

| type | 说明 | field 含义 | value 含义 |
|------|------|-----------|-----------|
| status | HTTP 状态码 | 不需要 | 期望状态码(数字) |
| body_field | 响应 JSON 字段 | JSONPath（如 `data.id`） | 期望值 |
| body_contains | 响应包含文本 | 不需要 | 要包含的文本 |

### 断言格式（严格遵守）

```json
// 状态码断言
{"type": "status", "operator": "==", "value": 200}

// JSON 字段断言 — field 是路径，expected 是期望值
{"type": "body_field", "field": "data.id", "operator": "not_empty"}
{"type": "body_field", "field": "data.username", "operator": "==", "expected": "testuser"}
{"type": "body_field", "field": "data.role", "operator": "==", "expected": "user"}

// 文本包含断言
{"type": "body_contains", "value": "username"}
```

**关键**：`body_field` 的路径放在 `field` 字段，期望值放在 `expected` 字段。`value` 只用于 `status` 和 `body_contains`。

### 操作符

`==` | `!=` | `>` | `<` | `contains` | `not_empty` | `in`

## 变量体系

### 环境变量（预配置）

| 变量 | 用途 |
|------|------|
| `${BASE_URL}` | 服务地址 |
| `${ADMIN_USER}` | 管理员用户名 |
| `${ADMIN_PASS}` | 管理员密码 |

### 步骤提取变量

通过 `variables_extract` 从响应中提取，后续步骤用 `${变量名}` 引用：

```json
"variables_extract": {"AUTH_TOKEN": "data.token", "USER_ID": "data.id"}
```

### 运行时变量

`${RANDOM_8}` — 8位随机字符串，`${TIMESTAMP}` — 当前时间戳

## 输出格式

直接输出 JSON（不要用 ```json 包裹）：

```
{"scenarios": [{"title": "场景名", "priority": "P0", "description": "...", "steps": [...]}]}
```

## 质量红线

- **命名**：场景名 = `[接口名]-[测试维度]`；步骤名 = `[操作]-[具体条件]`
- **断言**：每个步骤必须有断言；断言必须包含具体状态码
- **数据**：请求参数必须是具体值，禁止写"无效值"、"合法数据"等笼统描述
- **Headers**：认证步骤必须显式写 Authorization header，禁止留空
- **变量**：公共参数用环境变量引用，步骤间传递用 `variables_extract`
- **覆盖**：每个 required 字段至少 1 条缺失用例；有约束的字段至少正向+反向各 1 条
- **清理**：创建了资源必须有 DELETE 清理步骤
- **格式**：`body_field` 断言的路径放 `field`，期望值放 `expected`
