"""接口测试 — 测试场景 + 测试步骤"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Boolean, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class ApiTestScenario(Base):
    """测试场景：一组针对某接口的测试步骤"""
    __tablename__ = "api_test_scenarios"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    folder_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("api_test_folders.id"), nullable=True)
    priority: Mapped[str] = mapped_column(String(5), default="P1")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | published | deprecated
    source: Mapped[str] = mapped_column(String(10), default="ai")  # ai | manual
    # 源用例：运行时据此解析该用例的场景变量(SV_*)，实现 UI/接口共用一份场景变量。
    # **必填**（2026-08-15 迁移 zz9orph1 收敛）：不绑用例的场景拿不到场景变量
    # （scenario_variables.case_id 同样 NOT NULL），跑起来必挂在「变量未解析」。
    # 外键是 CASCADE 而不是 SET NULL —— SET NULL 会把删用例变成"生产孤儿"，
    # 实测这么攒出过 7 条无主场景。
    source_case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False)
    env_variables: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ApiTestStep(Base):
    """测试步骤：场景中的一个请求"""
    __tablename__ = "api_test_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    scenario_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("api_test_scenarios.id", ondelete="CASCADE"), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    group_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)  # GET/POST/PUT/DELETE
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    headers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    body: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    assertions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    variables_extract: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 发请求前先等多久。被测系统的配置下发常是异步的（实测网关 0.06~0.5s 且抖动），
    # 而步骤之间只隔几毫秒 —— "发布完立刻打网关"必然抢跑，第一版就是这么红的。
    wait_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # 断言没过就整步重发，直到过了或超时。**比固定 wait 更该用这个** ——
    # 固定等待要么白等要么不够，换台机器就崩；重试是"等到它真的好了为止"。
    retry_timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    retry_interval_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=300, server_default="300")
    pre_script: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    post_script: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(10), nullable=True)  # pass | fail | skip
    last_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
