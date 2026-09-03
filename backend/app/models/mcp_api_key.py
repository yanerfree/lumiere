"""MCP API Key 模型 — 用户级密钥，SHA-256 哈希存储"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class McpApiKey(Base):
    __tablename__ = "mcp_api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), default="default")
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String(8), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    # Key 归属的项目。工具范围现在存在项目上（`projects.mcp_allowed_tools`），
    # 靠这条边查出来 —— tools/list 发生在连接建立时，那时还没有任何 project_id 入参，
    # 所以项目只能从 Key 本身得到。
    # NULL = 未归属任何项目（存量 Key），那时没有项目天花板 → 只看下面那份 Key 范围。
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    # **Key 级收窄**：NULL = 跟随项目范围（默认，也是今天所有 Key 的状态）；
    # 列表 = 在项目天花板内再挑这几个。生效范围 = 项目范围 ∩ 这一份，
    # 判据只在 `app/mcp/middleware.pick_scope` 一处。
    #
    # ⚠ `[]` 是"一个工具都不给"，**不是"不限制"**。想不限制就写 NULL。
    #   （2026-09-03 之前 `[]` 会被 `if raw else None` 当成不限制 —— 方向反的，
    #     而且不报错。）
    #
    # 2026-09-03 之前这一列是【遗留】：范围整体挪到项目级，这里只对未归属项目的
    # 存量 Key 生效，PATCH 绑项目时还会把它清成 NULL。现在它是一等公民 ——
    # 一个项目里的两台 CC 常常各自只该看一小半工具（一台专做回推、一台专做归因），
    # 而项目范围是共用的，只有 Key 上这一层能把它们分开。
    allowed_tools: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
