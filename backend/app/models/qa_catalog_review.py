"""一次「域级 AI 评审」= 一条记录。

跟 `review_batches` **不共用一张表**，虽然页面上长得像。三条不能合的理由：

1. `review_batches.branch_id` 是 NOT NULL，而 QA 仓的域根本不属于任何分支 ——
   它是别人仓库里的一个目录码（`AGT`/`AUT`），不是 `case_folders` 里的模块。
2. 那张表的队列 worker 捡到行就会去 `cases` 里按 id 评用例。QA 域没有 `case_id`，
   捡走的后果是 worker 反复报错重试。
3. 结论落点不同：用例审核的结论要写回 `cases.review_status`（管门禁）；
   这里的结论**谁也不改** —— QA 仓只读，平台这边也没有对应的用例行可写。
   它就是一份「读后感」，存下来只为了下次打开还能看见上次说了什么。

**这张表不会让平台往 QA 仓写任何东西。** 评审读的是 `git show` 出来的文本，
产出留在本库。见 `docs/qa-repo-readonly-catalog.md`。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base

# 只有四态。没有 paused/cancelled —— 一次评审就是一次 LLM 调用，
# 几十秒的事，做暂停/续跑的开关比它本身还复杂。
STATUSES = ("queued", "running", "done", "failed")


class QaCatalogReview(Base):
    __tablename__ = "qa_catalog_reviews"
    __table_args__ = (
        # 页面每次打开都要按「这个项目每个域最近一次」查，域码 + 时间倒序
        Index("ix_qa_catalog_reviews_project_domain", "project_id", "domain", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid())
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)

    # 域码（`AGT`）+ 域名（`Agent 生命周期`）。域名一起存下来是因为**清单是别人的**：
    # 他改了域名，这条历史记录该显示当时那个名字，而不是跟着变。
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    domain_name: Mapped[str] = mapped_column(String(120), default="", server_default="")

    # 环境是结论的一部分（review-spec §5）：同一批脚本，测试环境缺 ADMIN_TOKEN、
    # 预发不缺，结论就不一样。环境删了也要留住名字，所以 SET NULL + 冗余名。
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id", ondelete="SET NULL"), nullable=True)
    environment_name: Mapped[str] = mapped_column(String(120), default="", server_default="")

    # 评的是 QA 仓哪个 commit —— 没有它这份结论过两天就没法复核
    commit_sha: Mapped[str] = mapped_column(String(64), default="", server_default="")
    branch: Mapped[str] = mapped_column(String(200), default="", server_default="")

    actor: Mapped[str] = mapped_column(String(100), default="", server_default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")

    scenario_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    script_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
