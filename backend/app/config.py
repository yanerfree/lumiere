from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 数据库
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/testbench"
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # 安全
    secret_key: str = "change-me-in-production"
    jwt_expire_hours: int = 8  # 已废弃：access token 改用 access_token_expire_minutes
    # 短期 access token（分钟）。可配 15–30，默认 30。
    access_token_expire_minutes: int = 30
    # 长期 refresh token（天），默认 7。
    refresh_token_expire_days: int = 7
    # 轮换重叠窗口（秒）：refresh token 被轮换后，旧的在这段时间内再次使用不判定重放，
    # 而是正常签发。对齐 Okta 的 grace period —— 默认 30、可配 0~60。
    # 为什么需要它：弱网/并发刷新/服务重启时，新 token 可能没送达客户端，客户端会拿旧的重试；
    # 没有这个窗口，正常重试会被当成盗用，进而吊销该用户全部令牌（全端被踢）。
    # 窗口外仍按 OAuth 2.0 Security BCP 处理：判定重放 → 吊销整个 token family。
    refresh_token_grace_seconds: int = 30
    bcrypt_cost: int = 12  # >= 10

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # CORS
    cors_origins: list[str] = ["http://localhost:5173"]

    # Admin seed
    admin_default_password: str = "admin123"

    # AI / LLM
    ai_provider: str = "openai_compatible"  # openai_compatible | anthropic
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_auth_token: str = ""
    ai_model: str = "claude-sonnet-5"
    # UI 脚本生成 agent 专用模型（长链路浏览器探索+代码生成需要强模型）。
    # 空则回退 ai_model。与 ai_model 分开，避免全局强切 sonnet 拖慢高频廉价任务（用例生成等）。
    ai_ui_model: str = ""
    # UI 生成 agent 专用 base_url（指向 claude-proxy，走真 CLI 避开网关对 SDK 的限流）。空=回退 ai_base_url
    ai_ui_base_url: str = ""
    # 限流兜底通道：claude-proxy（把真 claude CLI 包成 OpenAI 接口，配额走 CLI 客户端不被网关限 SDK）。
    # 文本主路 429 退避重试耗尽后自动降级到这条通道。空=回退 ai_ui_base_url。
    ai_proxy_base_url: str = ""
    ai_max_tokens: int = 4096
    # UI 生成 agent 专用 max_tokens（0=回退 ai_max_tokens），避免全局抬高影响其它任务
    ai_ui_max_tokens: int = 0
    ai_temperature: float = 0.3
    ai_timeout_seconds: int = 120
    # 长驻 Playwright MCP 的 SSE 地址（供 UI 生成 agent 用，根治 stdio 的 anyio 崩溃）。空=回退 stdio
    playwright_mcp_url: str = ""
    # UI 生成引擎：cli=真 claude CLI 直驱 MCP(原生 tool_use,快)；langgraph=旧 LangGraph+proxy(每步冷启,慢)
    ui_agent_engine: str = "cli"
    ai_enabled: bool = False

    # 只读 QA 仓的本地 bare 缓存目录。空 = backend/.qa-repos。
    # 独立于 script_base_path：那是"项目自己的脚本库"，这是"别人的验收仓的只读镜像"，
    # 混在一起会让"平台能往哪儿写"这件事变得说不清。
    qa_repo_cache_dir: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
