import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
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


class Script(Base):
    """测试脚本 — 存储在数据库中，支持版本管理"""
    __tablename__ = "scripts"
    __table_args__ = (
        UniqueConstraint("case_id", "script_type", "version", name="uq_script_case_type_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    script_type: Mapped[str] = mapped_column(String(10), nullable=False)  # api / ui
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    language: Mapped[str] = mapped_column(String(20), nullable=False, default="python", server_default="python")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    func_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )  # draft / active / archived
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="manual", server_default="manual"
    )  # manual / git_sync / upload
    commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # 断言指纹（B5）：条数 + 按类型分桶 + 强度分。存下来才能和下一版对比，
    # 让"改到绿了但测试死了"这种退化**可见**。
    assertion_profile: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ScriptRun(Base):
    """脚本执行记录 — 记录每次手动运行的结果"""
    __tablename__ = "script_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    script_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scripts.id", ondelete="SET NULL"), nullable=True
    )
    script_type: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    stdout: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenshots: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    executed_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    # ── 执行记账（A0）──
    # 计划执行/adhoc 批量跑出来的行，反查回它在报告里对应的那条。报告删了执行事实还在。
    report_scenario_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("test_report_scenarios.id", ondelete="SET NULL"), nullable=True
    )
    # debug=即席调试（不进通过率口径）/ regression=计划与批量回归
    run_mode: Mapped[str] = mapped_column(String(12), nullable=False, server_default="debug", default="debug")
    # 计划执行会重试 N 次，每次单独一行。只记最后一次的话，flaky 判定要的
    # "同一版本多次结果翻转"就永远攒不到。
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1", default=1)
    captured_requests: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # ── 失败判断的三层，谁也不覆盖谁（见 o9c0d1e2f3a4 迁移的说明）──
    # 平台判的「现象」：确定性规则，每次执行自动算
    failure_phenomenon: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # CC 判的「原因」：**建议值**，进待确认通道，碰不到任何状态
    cc_analysis: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 人确认后的结论：唯一能改状态的东西
    confirmed_cause: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confirmed_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
