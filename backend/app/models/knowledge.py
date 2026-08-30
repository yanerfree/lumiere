"""知识库 — 项目级知识条目，AI 生成/评审时自动参考"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base

# 正文硬上限。**属于这张表，不属于某一个写入方** ——
# 原来它只写在 MCP 那条通道里（app/mcp/tools/project_notes.py），HTTP 这条一个字不校验，
# 于是「200 字上限」只有一半是真的：CC 写被拒，同一条内容打接口就进去了。
# 一半真的规矩比没规矩坏 —— 它让「我这条超了怎么进去了」看起来像上限没生效，
# 而实际是两条路各说各的。数字在这儿定，两边都 import。
MAX_CONTENT = 200


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
