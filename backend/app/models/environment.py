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
    __tablename__ = "global_variables"
    __table_args__ = (
        CheckConstraint(r"key ~ '^[A-Za-z][A-Za-z0-9_]{0,62}$'", name="ck_gvar_key_format"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Environment(Base):
    __tablename__ = "environments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
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
