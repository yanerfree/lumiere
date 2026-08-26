# Lumiere 项目专属脚本生成规则（最高优先级）

被测系统前端基本都是 **Ant Design (React)** 或类似组件库。以下是必须遵守的硬规则，违反会导致 verify 失败。

## 1. 中文双字按钮的空格陷阱（最常见失败原因）

Ant Design 会把**双字中文按钮**自动排版成中间带空格的形式：
- 页面写「登录」→ DOM 实际是 `登 录`
- 「保存」→ `保 存`，「创建」→ `创 建`，「删除」→ `删 除`，「确定」→ `确 定`，「取消」→ `取 消`，「提交」→ `提 交`，「新建」→ `新 建`

**因此选择器必须容忍空格**：
```typescript
// 正确 — 用 \s* 容忍空格
await page.getByRole('button', { name: /登\s*录/ }).click();
await page.getByRole('button', { name: /保\s*存/ }).click();

// 也可以用 getByText 模糊匹配（exact 默认 false）
await page.getByText('登录').click();  // getByText 会规范化空白，能匹配"登 录"

// 错误 — 精确匹配匹配不到带空格的实际文本
await page.getByRole('button', { name: '登录', exact: true });  // ✗ 匹配不到"登 录"
await page.getByRole('button', { name: /登录/ });               // ✗ 正则不含空格
```

**规则**：凡是双字中文按钮/链接，用 `getByRole` 时 name 一律用 `/字\s*字/` 正则；三字及以上或含标点的文本一般不加空格（如「创建服务」渲染为「创建服务」不加空格，但「创 建」这种双字会加）。不确定时优先用 `getByText('文字')`（自动规范化空白）。

## 2. 下拉/弹出菜单：点击按钮后必须再点菜单项

很多"创建/新建"按钮点击后不是直接跳转，而是弹出**下拉菜单让你选类型**（如"创建服务"→弹出 API 服务/MCP 服务/TCP 服务）。
- 阶段一探索时如果点击按钮后出现了菜单并选了某项，脚本里必须包含**两次 click**：先 click 按钮，再 click 菜单项
- 菜单项常用 portal 渲染，用 `getByRole('menuitem', { name: '...' })` 或 `getByText('...')` 定位
- **不要**假设点击按钮直接 `waitForURL`，先确认是否有中间菜单

```typescript
await page.getByRole('button', { name: /创建服务/ }).click();
await page.getByText('API 服务').click();          // 选菜单项
await page.waitForURL(/\/services\/create/);        // 再等跳转
```

## 3. 登录流程（每个脚本开头必写）

```typescript
await page.goto('/');
// 用户名框：Ant Design 登录页常用 placeholder，用 getByPlaceholder 或 getByRole textbox
await page.getByRole('textbox').first().fill(process.env.TEST_USER!);
await page.locator('input[type="password"]').fill(process.env.TEST_PASSWORD!);
await page.getByRole('button', { name: /登\s*录/ }).click();
await page.waitForURL(url => !/\/login|\/auth|\/signin/i.test(url.href), { timeout: 20000 });
```

## 4. 验证类断言不要硬编码动态数字

「服务总数 18」这种数字会变，断言只匹配标签文本（`toContainText('服务总数')`），不要带具体数字。

## 5. 列表中验证新建数据项：必须先搜索过滤

列表通常有分页（如 18+ 条），新建的数据**不一定在第一页可见**。返回列表页验证刚创建的数据项时，**必须先用搜索框输入名称过滤**，再断言该行可见：

```typescript
// 返回列表后先搜索，再验证 —— 否则分页导致 element(s) not found
await page.getByRole('textbox', { name: /搜索/ }).fill(serviceName);
await page.waitForTimeout(800);  // 等列表过滤刷新
const row = page.getByRole('row').filter({ hasText: serviceName });
await expect(row).toBeVisible();
await expect(row).toContainText('草稿');
```

**禁止**返回列表后直接 `getByRole('row').filter({ hasText: name })` 断言而不搜索 —— 数据在后面分页时必然找不到。

