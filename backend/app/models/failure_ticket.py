"""失败跟进单 —— 一个失败点从红到关的全过程。

以前只有 script_runs：每次执行各一条，昨天 CC 分析过的结论今天那条上没有，
等于每轮从零判一遍；"这个问题连着红了几轮"也没人数得出来。

**归并口径（用户拍的）**：
  · 单子**开着**的时候，同一条用例、同一个失败现象的失败都并进来，只是 occurrences+1
    —— 没修好之前它本来就会一直失败，那是同一件事。
  · 单子**关掉之后**又出现同样的失败 → **新开一张**，并记住是从哪张复发的。
    「改好了、跑绿了、过一段时间又失败」是**回归**，是新信息，
    并进老账里会把它埋掉（老账显示"红了 8 次"，看不出中间绿过）。
  · 脚本改过**不强制分界**：改了但没修好，仍是同一件事（现象没变就说明没修对）。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base

# 状态机：
#   open        刚红，还没人分析
#   analyzed    CC 给了原因（建议值），等人确认
#   confirmed   人确认了原因，等处置
#   fixing      处置中（改脚本/改用例/等缺陷修）
#   verifying   自称修好了，等复跑
#   closed      关了（跑绿自动关，或人工关且必须写原因）
#   known       已知问题：知道它红、先不修，必须挂 bug 单号
STATUSES = ("open", "analyzed", "confirmed", "fixing", "verifying", "closed", "known")
OPEN_STATUSES = ("open", "analyzed", "confirmed", "fixing", "verifying")


class FailureTicket(Base):
    __tablename__ = "failure_tickets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid())
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False, index=True)
    script_type: Mapped[str] = mapped_column(String(10), nullable=False)   # ui / api
    # 平台判的现象（timeout / element_not_found / assertion_failed …）。
    # 判不出来时是 "unknown" —— 不用 NULL，否则归并时 NULL != NULL 会每次新开一张。
    phenomenon: Mapped[str] = mapped_column(String(32), nullable=False, server_default="unknown")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="open")

    first_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("script_runs.id", ondelete="SET NULL"), nullable=True)
    last_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("script_runs.id", ondelete="SET NULL"), nullable=True)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # 复发：这张单是从哪张关掉的单子复发出来的 + 这个失败点一共复发过几次。
    # **flaky 和真回归靠这两个字段分**：连着红 8 次 vs 关了又开 3 回，处置完全不同。
    reopened_from: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("failure_tickets.id", ondelete="SET NULL"), nullable=True)
    recurrence: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    cc_analysis: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    confirmed_cause: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confirmed_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 处置方式：script_fix / case_fix / product_defect / data_fix
    disposition: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # 关单：跑绿关的话记那一次 run（**凭什么关的**要留证）；人工关的话原因必填
    closed_by_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("script_runs.id", ondelete="SET NULL"), nullable=True)
    closed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
