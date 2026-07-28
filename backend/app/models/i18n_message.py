import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class ProjectI18nMessage(Base):
    """项目级 i18n 词典（UI 文案沉淀）——为脚本国际化打底座。

    被测系统的 UI 文案（按钮名/占位符/标签/Toast）会硬编码进用例步骤与生成的
    Playwright 脚本，且这些中文既是断言也是选择器。此表把散落的文案结构化沉淀成
    项目级词典，以中文原文本身作自然键，多语种存 translations JSONB。

    一期只采集中文（translations 先留空）；二期脚本按 LOCALE 变量用 t(key_text)
    运行时切换语种，没翻译时兜底退回中文。

    key_text:      中文原文即自然键，(project_id, key_text) 唯一
    translations:  {"en": "...", ...} —— 加语种不改表
    category:      button / placeholder / label / text（按定位方式推断）
    source:        harvested（扫脚本采集）/ manual（手工录入）
    """
    __tablename__ = "project_i18n_messages"
    __table_args__ = (
        UniqueConstraint("project_id", "key_text", name="uq_i18n_project_keytext"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key_text: Mapped[str] = mapped_column(String(500), nullable=False)
    translations: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="harvested", server_default="harvested")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
