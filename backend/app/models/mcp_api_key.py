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
    # NULL = 未归属任何项目（存量 Key），解析时退回下面的 allowed_tools。
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    # 【遗留】Key 级工具白名单：NULL = 不限制；列表 = 只暴露这些工具名。
    # 范围已挪到项目级，页面不再暴露编辑入口。这一列**只对 project_id 为 NULL 的
    # 存量 Key 仍然生效** —— 留着是为了不把已经设过范围的那把 Key 悄悄放开。
    allowed_tools: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
