---
name: lum-api-case-generate
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
  "url": "${BASE_URL}${LOGIN_URL}",
  "headers": {"Content-Type": "application/json"},
  "body": {"username": "${ADMIN_USERNAME}", "password": "${ADMIN_PASSWORD}"},
  "assertions": [{"type": "status", "operator": "==", "value": 200}],
  "variables_extract": {"AUTH_TOKEN": "data.access_token"}
}
```

> **🚫 绝对禁止把凭据 / 服务地址 / 登录路径写成明文字面量。** 账号、密码、`http://ip:port` 这类 base 地址、登录 path，**一律用变量引用**——即使 api_info 里给出了真实账号（如 `tenant@stoa.local`）、真实密码（如 `Admin@123`）、真实 URL（如 `http://192.168.51.108:5176`），也**必须换成变量引用**，禁止把这些明文抄进任何步骤的 url / body / headers。

**变量名以 prompt 里注入的「当前项目环境变量」清单为准**（生成时会把该项目/环境真实存在的变量连同取值一起给你）。常见约定：

| 用途 | 变量引用 |
|------|---------|
| 服务地址（base，含协议+ip+端口） | `${BASE_URL}` |
| 登录接口路径 | `${LOGIN_URL}`（拼成 `${BASE_URL}${LOGIN_URL}`） |
| 管理员账号 / 密码 | `${ADMIN_USERNAME}` / `${ADMIN_PASSWORD}` |
| 租户（普通用户）账号 / 密码 | `${TENANT_USERNAME}` / `${TENANT_PASSWORD}` |

**重要**：
- 按用例的**角色**选凭据变量：需要管理员的接口用 `${ADMIN_USERNAME}`/`${ADMIN_PASSWORD}`；租户/普通用户视角用 `${TENANT_USERNAME}`/`${TENANT_PASSWORD}`。用例标题/描述里出现 `[管理员]`/`[租户]` 时按标记选。
- 注入清单里**没有对应变量**时，仍用 `${SNAKE_NAME}` 形式的变量引用（会沉淀成待补的场景变量），**绝不内联明文**。
- 登录 URL 的路径、token 在响应中的路径**必须以 api_info 里的真实登录接口为准**：登录路径优先用 `${LOGIN_URL}`；若 api_info 明确另有路径，用 `${BASE_URL}` 拼真实 path，但域名部分始终是 `${BASE_URL}`。
  - 常见 token 路径差异：`data.access_token`（OAuth2 / FastAPI / Go 网关，如 stoa）、`data.token`、`token`。取错路径会导致 `AUTH_TOKEN` 为空、后续步骤 401。**登录断言里的 token 提取路径要照抄 api_info 的真实响应结构**（stoa 是 `data.access_token`）。
- token 变量名统一为 `AUTH_TOKEN`；后续所有需要认证的步骤都写 `Authorization: Bearer ${AUTH_TOKEN}`。
- **禁止**出现没有登录步骤却直接引用 `${TOKEN}` / `${AUTH_TOKEN}` 的场景——引用了却没有任何步骤提取它，运行时变量为空，受保护接口必然 401。

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
| required | - | `[接口名]-[字段]缺失` → 校验失败(4xx) |
| type: string | - | `[接口名]-[字段]类型错误(数字)` → 校验失败(4xx) |
| minLength: N | `[字段]长度N(最小边界)` → 2xx | `[字段]长度N-1(低于最小值)` → 校验失败(4xx) |
| maxLength: M | `[字段]长度M(最大边界)` → 2xx | `[字段]长度M+1(超过最大值)` → 校验失败(4xx) |
| enum: [A,B] | `[字段]枚举值A(有效)` → 2xx | `[字段]枚举值invalid(无效)` → 校验失败(4xx) |
| pattern: regex | `[字段]格式正确(下划线)` → 2xx | `[字段]格式错误(特殊字符)` → 校验失败(4xx) |

> **⚠️ 反向校验用例的状态码断言：一律用 `in [400, 422]`，禁止写死 `400`。**
> 请求体字段校验失败时，不同后端框架返回码不同：**FastAPI / Pydantic 返回 `422`**，Go validator 网关（stoa 就是）也返回 `422`，Spring / 部分自研网关返回 `400`。写死 400 会让实际返回 422 的用例全部误判为失败。正确写法：
> ```json
> {"type": "status", "operator": "in", "value": [400, 422]}
> ```
>
> **⚠️ 反向用例不要凭空断言错误响应体的文本。** 校验失败用例的**主断言就是状态码 `in [400,422]`**，通常到此为止即可。
> - 错误响应体的字段名、大小写、消息文案是后端强相关的（例如 stoa 把字段回显成 `CreateServiceRequest.Name`，大写 `Name`；而 `body_contains` 是**大小写敏感**的子串匹配，断言小写 `name` 会漏匹配而误判失败）。
> - **只有当 api_info 明确给出了错误响应体格式时**，才追加响应体断言，且优先匹配**稳定的错误码**（如 `{"type":"body_field","field":"error.code","operator":"==","expected":"VALIDATION_ERROR"}`），**禁止**去 `body_contains` 猜字段名/消息文案。
>
> **⚠️ 分页 / 查询参数越界（page、page_size 等）不要假定被拒绝。** 很多后端对非法分页参数是**宽松处理**（截断到边界值后照常返回 `200`），只有明确声明严格校验的接口才会返回 4xx。除非 api_info 明确写了「非法分页返回 4xx」，否则查询参数越界用例应断言 `status in [200, 400, 422]` 并额外校验响应结构，而不是断言一定拒绝。

### 正向测试步骤

1. 登录获取 token
2. 所有字段传合法值 → 期望 2xx（创建类用 `{"type":"status","operator":"in","value":[200,201]}`，不要写死单一状态码）
3. 验证响应字段（id 非空等），但**只断言 api_info 成功响应里明确出现的字段**——用 `data.id not_empty` 这类稳的；不要去断言没在成功响应样例里出现过的字段名/取值（猜的字段路径会 not_empty 失败或取空）
4. DELETE 清理创建的资源（用变量 `${USER_ID}` / `${SERVICE_ID}` 等）

> **⚠️ 请求体真实值必须原样沿用（正向用例最易踩坑）**
> 如果接口信息里给了真实请求体样例（`reqBody:` / `Body:` 抓包样例），正向用例的 body **必须照抄样例里的真实字段值**，尤其是：
> - **非空数组 / ID / UUID / 引用**（如 `isolation_rule_ids: ["019f..."]`、`upstream_id`、`cluster_id`）——这些是被测环境里真实存在、请求成功所必需的资源，**禁止**改成 `[]`、`null`、占位符或自己猜的值；
> - 嵌套对象（`config`/`upstream` 等）的结构和取值。
> 只有**需要唯一性的字段**（如 `name`）才加时间戳变量（`test-xxx-${TIMESTAMP}`）。把真实 ID 清空是导致「正向用例 422/400 校验失败」的头号原因。
>
> **⚠️ 带运行时变量的字段不要用 `==` 精确断言。** 若字段值里含 `${TIMESTAMP}` / `${RANDOM_8}`（如 `name: "test-xxx-${TIMESTAMP}"`），响应校验**必须用 `contains` 匹配稳定前缀**（`{"type":"body_field","field":"data.name","operator":"contains","expected":"test-xxx-"}`），**禁止** `== "test-xxx-${TIMESTAMP}"`——断言里的变量与请求体里的变量可能解析到不同的时刻/取值，精确匹配会偶发失败。

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
  "assertions": [{"type": "status", "operator": "in", "value": [200, 204]}]
}
```

> **⚠️ DELETE 的成功状态码用 `in [200, 204]`，不要写死 200。** 很多后端删除成功返回 `204 No Content`（stoa 就是），部分返回 `200`。写死 200 会让实际返回 204 的清理步骤误判失败。同理，创建成功若不确定是 `200` 还是 `201`，用 `in [200, 201]`。

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

### 环境变量（运行时注入，禁止把它们的取值写成明文）

生成时会把该项目/环境**真实存在的变量清单 + 取值**注入到 prompt 的「当前项目环境变量」里。**引用变量名要与注入清单一致**，常见约定：

| 变量 | 用途 |
|------|------|
| `${BASE_URL}` | 服务地址（协议+ip+端口，绝不写死 IP） |
| `${LOGIN_URL}` | 登录接口路径 |
| `${ADMIN_USERNAME}` / `${ADMIN_PASSWORD}` | 管理员账号 / 密码 |
| `${TENANT_USERNAME}` / `${TENANT_PASSWORD}` | 租户（普通用户）账号 / 密码 |

> 注入清单是权威来源；上表是常见命名。清单里给了什么就用什么，别自己造 `${ADMIN_USER}` 这种对不上的名字。

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
- **反向状态码**：校验失败类反向用例的状态码断言一律用 `{"operator":"in","value":[400,422]}`，禁止写死 400；主断言到状态码为止，不凭空 `body_contains` 猜错误响应体文本（除非 api_info 给了错误格式，且优先匹配错误码）；分页/查询参数越界不假定被拒绝
- **数据**：请求参数必须是具体值，禁止写"无效值"、"合法数据"等笼统描述
- **Headers**：认证步骤必须显式写 Authorization header，禁止留空
- **变量**：**脚本里禁止出现写死的明文**——服务地址/base、账号、密码、登录路径一律用环境变量引用（`${BASE_URL}`/`${ADMIN_USERNAME}`/`${ADMIN_PASSWORD}`/`${TENANT_USERNAME}`/`${TENANT_PASSWORD}`/`${LOGIN_URL}` 等，名字对齐注入清单），即使 api_info 给了真实明文也要换成变量；步骤间传递用 `variables_extract` 走步骤链
- **覆盖**：每个 required 字段至少 1 条缺失用例；有约束的字段至少正向+反向各 1 条
- **清理**：创建了资源必须有 DELETE 清理步骤
- **格式**：`body_field` 断言的路径放 `field`，期望值放 `expected`
