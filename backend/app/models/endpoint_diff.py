"""版本升级·分支对账的清单落库（2026-08-21）。

**为什么清单必须落平台、不能留在 CC 的上下文里**：CC 一关会话它就没了，续不上。
下一轮开工时"哪些用例该改、改到哪儿了"必须还在，所以落两张表：

- `endpoint_diff_batches` —— 一次对账的**原始输入**（CC 报的 changes 原文）。
  存原文是为了能**重算**：CC 后来补交漏掉的 changes 时，重算要拿旧的 changes
  一起算，否则新批次只看得见新增那几条，已经命中过的用例会从要改堆里凭空消失。
- `endpoint_diff_hits` —— 求交集的**结果**，一行一处证据（哪条用例的哪一步、
  哪个断言、撞的是哪条变更）。

清单**不是状态**。三堆（照抄/要改/该废）全部从这两张表推导，`cases` 表上不加
任何"我属于哪一堆"的列 —— 加了就得有人维护它跟这两张表的一致性，而一旦不一致，
以哪个为准就成了下一个 bug。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class EndpointDiffBatch(Base):
    """一次对账。一个版本对一次账 —— 但允许补交，所以同一分支可以有多批。"""

    __tablename__ = "endpoint_diff_batches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("branches.id", ondelete="CASCADE"), nullable=False
    )
    # CC 报的原文，一条不改地存下来。[{url, method, kind, detail}]
    changes: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 这一批算出来的分堆统计：{revise, reuse, deprecateCandidate, pendingNew, total}
    stats: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # kind=added 的那些：v2.0 新端点，**不命中任何老用例**，所以不进 hits，
    # 但必须有地方记 —— 否则新功能零覆盖且零信号（原设计漏的第四堆）。
    # [{url, method, detail}]
    pending_new: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 两个 git 版本号，CC 报的（平台不知道 v1.0/v2.0 对应哪两个 tag）
    from_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    to_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EndpointDiffHit(Base):
    """一处命中证据。一条用例可能有多行（多步、多断言各撞一次）。"""

    __tablename__ = "endpoint_diff_hits"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("endpoint_diff_batches.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # 指到具体位置。断言序号可以为空（整步命中而不是某个断言命中）。
    step_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    assertion_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # removed（端点没了→该废候选）/ field_changed / new_state / renamed
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
