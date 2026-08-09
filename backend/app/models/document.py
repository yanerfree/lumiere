"""文档管理 — 操作手册/验收文档/培训教材"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(20), default="manual")  # manual | acceptance | training
    language: Mapped[str] = mapped_column(String(10), default="zh")  # zh | en
    content: Mapped[str | None] = mapped_column(Text, nullable=True)  # Markdown
    # 「优化文字」覆盖前的正文，用来撤销。只留最近一版，不是版本管理。
    previous_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    gen_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # 生成时的配置（地址/账号/范围等）
    source_case_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | published
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
