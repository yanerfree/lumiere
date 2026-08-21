"""环境配置相关模型 — 全局变量、环境、环境变量、通知渠道"""
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class GlobalVariable(Base):
    """本项目所有环境共用的兜底变量层，环境变量同名时以环境为准。

    **「全局」指的是"项目内跨环境"，不是"跨项目"。** 这张表原本是全平台一份，
    2026-08-21 改成项目级（迁移 zzp0gvarproj）—— 看数据就知道原来那个作用域不对：
    5 条全是按项目调的旋钮（API_TIMEOUT / RETRY_COUNT / BASE_WAIT / LOG_LEVEL /
    TEST_LANGUAGE），其中 TEST_LANGUAGE「测试跑哪种语言」是被测系统的属性，
    一个项目跑中文另一个跑英文是常态。

    覆盖关系一个字没动：全局是默认值，环境是这台机器的实情。
    见 variable_service.build_run_env。
    """

    __tablename__ = "global_variables"
    __table_args__ = (
        CheckConstraint(r"key ~ '^[A-Za-z][A-Za-z0-9_]{0,62}$'", name="ck_gvar_key_format"),
        # 项目内唯一。改动前是 key 全平台唯一，那让两个项目没法各有一份 TEST_LANGUAGE
        UniqueConstraint("project_id", "key", name="uq_global_var_project_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Environment(Base):
    """一个项目的测试环境。

    **项目级，不是全局的。** 原本这张表没有 project_id、`name` 还是全局 unique，
    于是大家在用「名字里塞项目前缀」手动模拟隔离（`uag` / `stoa` / `测试平台self`），
    而且两个项目都想有个 `staging` 就撞。2026-08-21 改成项目级，
    见 docs/data-scoping-and-isolation.md §4 和迁移 zzo0envproj。

    同文件里的 `GlobalVariable` 也跟着项目化了（迁移 zzp0gvarproj）；
    只有 `NotificationChannel` **仍然是全局的** —— 通知渠道是平台设施，
    不是项目资产。别顺手"补全"它。
    """

    __tablename__ = "environments"
    __table_args__ = (
        # 项目内唯一，不是全局唯一 —— 两个项目各有一个 staging 是正常需求
        UniqueConstraint("project_id", "name", name="uq_environment_project_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class EnvironmentVariable(Base):
    __tablename__ = "environment_variables"
    __table_args__ = (
        UniqueConstraint("environment_id", "key", name="uq_envvar_env_key"),
        CheckConstraint(r"key ~ '^[A-Za-z][A-Za-z0-9_]{0,62}$'", name="ck_envvar_key_format"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    environment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("environments.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    webhook_url: Mapped[str] = mapped_column(Text, nullable=False)  # AES-256 加密存储（一期先明文）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
