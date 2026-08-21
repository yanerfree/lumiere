"""一次审核 = 一条记录（review-spec §12 ①）。

**为什么非要落库**：批量审核原来是一次长 POST + 一份内存台账
（`_BATCH_PROGRESS`，只留最近 20 批）。后果有三个，每个都真实发生过：

1. **刷新页面就丢**。30 条实测跑满 5 分钟，人这五分钟里不能碰浏览器 ——
   碰了就再也找不回这一批在跑什么，只能干等或者重发一遍。
2. **跨进程查不到**。重启一次，正在跑的那批既不会继续、也不会被标成失败，
   它就那么消失了。
3. **报告页说不出"这次审的是什么"**。类型（模块全量/抽审/单条）、
   范围、环境、发起人全都没地方存 —— 而一份看不出是审了整个模块还是抽了三条的
   报告，过两周就没人信（§4）。

所以这张表存的是「一次审核这件事」本身，不是进度条。结论仍然在
`case_review_rounds` 和 `cases` 上，这张表是**索引和账**。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base

# 类型 —— **报告里必须区分**（§4）。只有 module_full 能代表这个模块的情况；
# 抽审、增量只能说"这几条过了"，否则挑三条好的一审就能宣布模块没问题。
KINDS = ("module_full", "module_incremental", "sample", "single", "checkup")

# 状态五种（§6）。paused 是熔断专用：环境挂了、剩下的先停着等人确认，
# **不是失败** —— 判成失败的话那些用例会被当成"审过了没过"。
STATUSES = ("queued", "running", "paused", "done", "partial", "cancelled")
ACTIVE_STATUSES = ("queued", "running", "paused")


class ReviewBatch(Base):
    __tablename__ = "review_batches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid())
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    # 「订阅管理 16 条」——报告表上那一列。存字面量而不是现算：
    # 模块名会改（`former_names` 那套），改完之后历史报告该显示当时的名字。
    scope_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    folder_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # **环境是结论的一部分**（§5）：测试环境过了不等于预发环境也过。
    # 体检不碰被测系统，所以它可以为空。
    environment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    environment_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    case_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 人发起的插到 CC 自审前面 —— 人在等结果，CC 不在等（§5）。
    # 报告页默认也只看 human 的：CC 一天几十条，混在一起就找不到自己点的那次。
    actor_kind: Mapped[str] = mapped_column(String(10), nullable=False, default="human")

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 「无法审核」单独计数（§9）。混进 rejected 的话，"打回 7 条"里其实有 4 条
    # 是环境没配 —— 人会去改 7 条没毛病的用例。
    inconclusive: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_case_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    with_checkup: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 模块报告的两块内容（§7）：共性问题 + 覆盖缺口。审完算一次存下来 ——
    # 每次打开报告页重算的话，LLM 每轮措辞不同，同一份报告两次打开长得不一样。
    report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 熔断/取消/异常的原因。**要能说出来**，否则页面上「暂停了」和「卡住了」一样。
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewBatchItem(Base):
    """批次里的一条。**独立成行**，不是把结论塞进 batch 的 JSON 里 ——
    「这一批里哪几条还没审」要能用 SQL 问出来（合并去重、断点续跑都靠它），
    塞 JSON 里就只能整份读出来在内存里翻。
    """
    __tablename__ = "review_batch_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid())
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_batches.id", ondelete="CASCADE"),
        nullable=False, index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False, index=True)
    case_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # pending / running / done / skipped（被合并掉的）/ failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    verdict: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 归因（§9）：script_bug / system_bug / env_down / no_env / nothing_to_run / ok
    run_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
