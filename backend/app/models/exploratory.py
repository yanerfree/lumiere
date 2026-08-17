"""探索测试 — 会话 + 发现记录"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class ExploratorySession(Base):
    """探索测试会话"""
    __tablename__ = "exploratory_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    target_module: Mapped[str | None] = mapped_column(String(100), nullable=True)
    time_limit_minutes: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | completed
    charter: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # AI 生成的章程
    checkpoints: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # 检查点列表
    completed_checkpoints: Mapped[int] = mapped_column(Integer, default=0)
    total_checkpoints: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # AI 生成的报告摘要
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExploratoryFinding(Base):
    """探索测试发现"""
    __tablename__ = "exploratory_findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("exploratory_sessions.id", ondelete="CASCADE"), nullable=False)
    finding_type: Mapped[str] = mapped_column(String(20), nullable=False)  # bug | risk | suggestion
    severity: Mapped[str] = mapped_column(String(10), default="medium")  # critical | high | medium | low
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    checkpoint: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 关联的检查点
    screenshot_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
