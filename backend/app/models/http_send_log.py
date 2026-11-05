"""「HTTP 请求」页的发送记录 —— 谁、什么时候、往哪发、共发几次、一次几条、成没成。

按**用户**隔离：每人只看得到自己的（页面右上「历史」）。只记摘要不记报文 ——
用户 2026-11-05 定的口径是「时间、次数、条数，简单的就行」；报文动辄几百 KB，
一次批量发 1000 条全落库只会把表撑大，而没人会回头逐条翻。
每人只留最近 KEEP_PER_USER 条，写入时顺手清掉更早的。
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base

KEEP_PER_USER = 500


class HttpSendLog(Base):
    __tablename__ = "http_send_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    # 用户删了，他的记录一起删 —— 别人本来就看不到
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=1)       # 共发几次
    per_round: Mapped[int] = mapped_column(Integer, nullable=False, default=1)   # 一次几条
    success: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status_codes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)      # {"200": 28, "500": 2, "出错": 1}
    avg_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)       # 整批从开始到结束
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
