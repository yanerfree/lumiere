# testBench — 给接手者 / AI 助手的必读约定

## 硬规则

- **后端必须跑 8756 端口**（`uvicorn app.main:app --port 8756`）。跑在别的端口前端会全 502，看起来像整个服务挂了，实际只是端口不对。
- **AI 模型 ID 只能填裸 ID**（`claude-sonnet-5`、`claude-opus-5`）。CLI 的长上下文后缀写法 `claude-opus-5[1m]` 打到接口会 404 —— 见下方文档的「红线」一节。
- **不要删 `app/services/ai/llm_client.py` 里的 429 两层处理**（退避重试 + 降级 CLI 通道）。文本主路此前零重试，一个 429 会打死整条场景生成；原因和验证方法都写在文档里。
- 换 AI 模型**不需要改代码**：走「AI 服务配置 → AI 能力→模型」页面即可（下拉是动态拉网关的）。
- **别把项目 skill 放进 `app/skills/preset/`**。那个目录只放平台侧执行的 `tb-*`（会被当 prompt 喂后端 LLM、要绑模型档位）；客户端侧执行的 skill 走 DB，见下方文档。混了会让「AI 能力→模型」页冒出绑不上模型的空档位。

## 文档入口

| 主题 | 文档 |
|---|---|
| AI 网关真面目、429 限流怎么绕、新模型怎么维护、模型选型实测数据、长驻服务依赖 | [docs/ai-gateway-and-models.md](docs/ai-gateway-and-models.md) |
| AI 测试生成用法 | [docs/ai-test-generation-guide.md](docs/ai-test-generation-guide.md) |
| AI 质量改进计划 | [docs/ai-quality-improvement-plan.md](docs/ai-quality-improvement-plan.md) |
| 项目 Skill 怎么传上来 / 给别的项目取用、跟内置 tb-* 的边界 | [docs/skill-sharing.md](docs/skill-sharing.md) |
| **LLM Mock 智能应答**：指令契约（MODE:/SAY:）、护栏回显协议、开关前后页面为什么长得不一样 | [docs/llm-mock-smart-contract.md](docs/llm-mock-smart-contract.md) |
| 下阶段做什么：生成效率 / 生成质量 / 失败优化（含现状实测盘点） | [docs/next-phase-gen-quality-and-failure.md](docs/next-phase-gen-quality-and-failure.md) |
| **CC ↔ 平台闭环的边界规则、红线、Story 清单**（改这一块之前先读） | [docs/cc-platform-loop-spec.md](docs/cc-platform-loop-spec.md) |

## 长驻服务

用 `deploy/start-ai-services.sh` 启动（幂等）：

- **claude-proxy :38210** — 429 降级通道。挂了则限流只能靠重试。**文本生成仍依赖它**。
- **playwright-mcp :38931** — 平台侧 UI 脚本生成的浏览器通道，host 只认 `localhost`。
  **2026-08-08 起平台侧 UI 生成已封存**（见上表那份文档的红线 1），所以**日常运行不需要起它**；
  顶栏「服务 N/17」里它显示 notConfigured 是正常的，不是坏了。只有要重新启用平台侧生成时才起。
