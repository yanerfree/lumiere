import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.permissions import SYSTEM_ROLES

# 合法系统角色取值内联成 SQL，DB 层兜底（应用层 Pydantic 也拦一道，两处同源于 SYSTEM_ROLES）
_SYSTEM_ROLES_SQL = ", ".join(f"'{r}'" for r in SYSTEM_ROLES)


class Base(DeclarativeBase):
    """ORM 基类，所有模型继承此类"""
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(f"role IN ({_SYSTEM_ROLES_SQL})", name="ck_user_role_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False, default="user", server_default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
