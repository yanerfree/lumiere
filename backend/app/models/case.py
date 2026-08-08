import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class CaseFolder(Base):
    """用例目录（路径模式，最多 4 层）"""
    __tablename__ = "case_folders"
    __table_args__ = (
        UniqueConstraint("branch_id", "path", name="uq_folder_branch_path"),
        CheckConstraint("depth <= 4", name="ck_folder_max_depth"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("branches.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("case_folders.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Case(Base):
    """用例"""
    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("branch_id", "case_code", name="uq_case_branch_code"),
        UniqueConstraint("branch_id", "tea_id", name="uq_case_branch_tea_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("branches.id", ondelete="CASCADE"), nullable=False
    )
    case_code: Mapped[str] = mapped_column(String(20), nullable=False)
    tea_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(10), nullable=False)  # api / e2e
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("case_folders.id"), nullable=True
    )
    priority: Mapped[str] = mapped_column(String(5), nullable=False, default="P2", server_default="P2")
    preconditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    expected_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    variables_used: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    api_scenario: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ui_scenario: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    api_scenario_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )  # draft / debugging / completed
    ui_scenario_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    is_api_template: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_ui_template: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # 核心/标杆用例：供其他用例参考应用它来生成（用例级，区别于 per-scenario 的模板）
    is_core: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    automation_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )  # automated / pending / script_removed / archived （旧字段，保留兼容；新展示用 lifecycle_status）
    # —— 状态体系 v2（2026-07）——
    # 整体生命周期：draft(草稿) / done(完成) / deprecated(废弃)
    lifecycle_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    # 三维执行就绪度统一枚举：not_started / draft / debugging / pending_review / executable / needs_fix
    manual_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="not_started", server_default="not_started"
    )
    ui_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="not_started", server_default="not_started"
    )
    api_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="not_started", server_default="not_started"
    )
    # 这条用例**要**做到什么程度（C1）：spec 只要手工步骤 / spec_api 步骤+接口 / full 三件套。
    # 和上面三个"已经做到哪儿"的状态配合，就是 CC 断点续跑的判据：
    # target_level=full 但 ui_status != executable 的，就是还欠着的那些。
    target_level: Mapped[str] = mapped_column(
        String(16), nullable=False, default="spec", server_default="spec"
    )
    source: Mapped[str] = mapped_column(String(10), nullable=False)  # imported / manual
    script_ref_file: Mapped[str | None] = mapped_column(String(500), nullable=True)
    script_ref_func: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_flaky: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    # —— AI 生成用例扩展（功能场景测试模块，仅 source=ai 使用；旧数据全部为 NULL）——
    # pending_review / approved / rejected
    review_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # {category, text, reviewer, at}
    review_reason: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # {total, static, ai_self, warnings: []}；未评分为 NULL（前端显示 "—"）
    quality_score: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    generation_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generation_tasks.id", ondelete="SET NULL"), nullable=True
    )
    # 需求点 UUID 数组（GIN 索引，覆盖矩阵聚合用）
    requirement_point_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 并发审核乐观锁（FR22）
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# Case.generation_task_id 外键依赖 generation_tasks 表；确保任何导入 Case 的
# 场景（含测试 create_all）metadata 中都有目标表，避免 NoReferencedTableError
from app.models import scenario_gen  # noqa: E402, F401
