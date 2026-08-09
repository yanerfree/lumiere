"""测试报告相关模型"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class TestReport(Base):
    __tablename__ = "test_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), nullable=True)
    report_type: Mapped[str] = mapped_column(String(20), default="plan", server_default="plan")  # plan | api_test
    report_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id", ondelete="SET NULL"), nullable=True, index=True)  # NULL = 历史数据（全分支可见）
    environment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("environments.id"), nullable=True)
    executed_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 汇总
    total_scenarios: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    passed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    skipped: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # flaky = 重试之后才通过的（算"跑过但不可信"，要进通过率分母）
    # xfail = pytest 的预期失败（跑了、按预期失败，和 skipped 一样不进分母）
    flaky: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    xfail: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    pass_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    total_duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    manual_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    variables_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TestReportScenario(Base):
    __tablename__ = "test_report_scenarios"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("test_reports.id", ondelete="CASCADE"), nullable=False)
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id"), nullable=True)
    case_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    scenario_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    execution_type: Mapped[str] = mapped_column(String(20), nullable=False)  # automated / manual
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TestReportStep(Base):
    """步骤级执行详情（L3/L4 下钻数据）"""
    __tablename__ = "test_report_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    scenario_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("test_report_scenarios.id", ondelete="CASCADE"), nullable=False)
    step_name: Mapped[str] = mapped_column(String(200), nullable=False)
    step_label: Mapped[str | None] = mapped_column(String(500), nullable=True)
    step_phase: Mapped[str | None] = mapped_column(String(20), nullable=True)
    http_method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    response_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    assertions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
