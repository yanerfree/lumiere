# UI 脚本生成 —— 运行时依赖与托管

## 架构（2026-07 重构后）
UI 脚本生成用 **CLI 引擎**（`ui_agent_engine=cli`，默认）：后端调真实 `claude` CLI，CLI 经 **Playwright MCP(SSE)** 原生驱动浏览器（native tool_use、一个会话、不冷启、走网关 Claude Code 配额不 429），Lumiere 侧 verify（npx playwright test），失败 `--resume` 同会话自愈 ≤3 轮。

## 运行时必需
| 服务 | 端口 | 必需性 | 说明 |
|---|---|---|---|
| **playwright-mcp** | 38931 (SSE) | **必需** | CLI 引擎的浏览器工具来源。host 只认 `localhost` |
| claude-proxy | 38210 | 可选 | 仅当 `ui_agent_engine=langgraph`（旧引擎）时才需要；CLI 引擎不用 |
| 后端 | 8756 | 必需 | uvicorn app.main:app |

`backend/.env` 关键项：
```
PLAYWRIGHT_MCP_URL=http://localhost:38931/sse
AI_UI_MODEL=claude-sonnet-4-6
AI_AUTH_TOKEN=<公司网关 gw-token>
ui_agent_engine=cli   # 默认，可省
```
另需机器上已安装并可用 `claude` CLI（`claude --version`）与 Playwright 浏览器（`npx playwright install chromium`）。

## 托管方式（二选一）
### A. systemd 用户服务（推荐，开机自启+崩溃重拉）
```bash
mkdir -p ~/.config/systemd/user
cp deploy/playwright-mcp.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now playwright-mcp
loginctl enable-linger $USER   # 让用户服务在未登录时也常驻
systemctl --user status playwright-mcp
```

### B. 手动启动脚本（临时/开发）
```bash
bash deploy/start-ai-services.sh   # 幂等：停旧实例再起 playwright-mcp(38931) + claude-proxy(38210)
```

## 已知限制（现状，非阻塞）
- 复杂用例生成 ~3-8 分钟（30+ 步真实浏览器探索）；运行脚本很快（秒级）。
- **不要并发生成**（共享网关/CLI/MCP，2 条并发各慢 ~3 倍）——生成请求应排队。
- 生成"探索"阶段会在被测环境真实建数据（如服务），脚本 cleanup 只在脚本运行时清，探索期造的数据需另清（后续可学参考项目 api_setup 做探索期 teardown）。
