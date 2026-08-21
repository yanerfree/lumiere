"""审计日志模型 — 仅追加，不可更新和删除。"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_project_id", "project_id"),
        Index("ix_audit_logs_user_id", "user_id"),
        Index("ix_audit_logs_target_type_action", "target_type", "action"),
        Index("ix_audit_logs_actor_type", "actor_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True,
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    target_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    changes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 操作来源：None/"human" = 人在页面上点的；"mcp" = 外部 Claude Code 通过 MCP 调的。
    # user_id 分不出这个 —— CC 用的 Key 全是同一个人建的（建 Key 接口只能给自己建）。
    actor_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 那把 Key 的名字（"uag-cc使用"、"小李的开发机"）。**存快照而不存 key_id**：
    # Key 删了以后日志还得说得出是哪台机器干的，外键 SET NULL 会把这个答案一起抹掉。
    actor_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    # 无 updated_at — 审计日志仅追加，不可修改
