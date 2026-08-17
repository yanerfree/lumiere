"""知识库 — 项目级知识条目，AI 生成/评审时自动参考"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    # category: review_feedback | bug_pattern | api_note | custom
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(30), default="manual")  # manual | ai_review | ai_diagnose
    reference_id: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 关联的用例/报告 ID
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
