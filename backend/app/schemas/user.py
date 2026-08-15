import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import BaseSchema


class UserResponse(BaseSchema):
    """用户响应（不含密码）"""
    id: uuid.UUID
    username: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CreateUserRequest(BaseSchema):
    """创建用户请求"""
    username: str = Field(min_length=2, max_length=50, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(min_length=6, max_length=128)
    role: Literal["admin", "user"] = "user"


class UpdateUserRequest(BaseSchema):
    """更新用户请求（所有字段可选）"""
    role: Literal["admin", "user"] | None = None
    is_active: bool | None = None
    # 管理员重置他人密码。此前只有 /auth/change-password 一条路，它要求提供原密码、
    # 且只能改自己 —— 用户忘了密码就彻底没救，只能删号重建。
    password: str | None = Field(default=None, min_length=6, max_length=128)
