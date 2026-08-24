"""用例审核的**每一轮**。审核以前只有"当前值"（review_status），没有过程 ——
而真实过程是：AI 打回 → CC 看完整改 → 再提交 → AI 再审 → 直到通过。
没有这张表，"跟进到哪了"只能靠人记。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class CaseReviewRound(Base):
    """一条用例的一次审核 / 一次整改提交 / 一次人工覆盖。

    kind：
      · `ai_review`      —— AI 审了一轮（带 verdict + 分 + findings）
      · `cc_resubmit`    —— 被打回后 CC 改完重新回推（记改了什么，没有 verdict）
      · `human_override` —— 人直接置通过/打回（记理由）
    """
    __tablename__ = "case_review_rounds"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid())
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False, index=True)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    verdict: Mapped[str | None] = mapped_column(String(20), nullable=True)
    total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dimensions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    findings: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    coverage_gaps: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 整改事件记「改了什么」：CC 自己说的 + 平台看到的（步骤/断言条数变化）
    changed: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 这轮是**真跑过再评**还是静态看的。两者结论强度差一个量级（实测同一条：
    # 静态 84 分通过、真跑 56 分打回），不记的话"过审了"看不出是凭什么过的。
    review_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 这轮对了多少条真实流量。是 0 的话「没发现端点问题」只说明没得比。
    traffic_seen: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 审的是哪个版本的场景/脚本 —— 只在 ai_review 轮次填。场景/脚本被后续
    # sync 覆盖掉之后，靠它才能判断这份 verdict 是不是已经对不上现在的内容
    # （见 rounds.content_signature、迁移 zzs0rvhash 的说明）。
    content_hash: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
