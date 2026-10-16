"""个人记录 —— 每个人自己记点东西用的便签（标题 + 正文）。

按**用户**隔离，不挂项目：记的是人自己的事，跟在哪个项目里无关。
可见性：本人看自己的；系统管理员（role=admin）能看所有人的，但**只能看不能改** ——
改/删只认作者本人，超管替人改笔记没有合理场景，反而会让「这是谁写的」说不清。
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base

MAX_TITLE = 200


class UserNote(Base):
    __tablename__ = "user_notes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    # 用户删了，他的记录一起删 —— 别人本来就看不到，留着没有主人
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    title: Mapped[str] = mapped_column(String(MAX_TITLE), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
