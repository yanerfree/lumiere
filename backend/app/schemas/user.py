import uuid
from datetime import datetime

from pydantic import Field

from app.core.permissions import SYSTEM_ROLES
from app.schemas.common import BaseSchema

# 系统角色取值同源于 core/permissions.SYSTEM_ROLES —— 此前这里是手写的三个字面量，
# 与事实源之间没有任何东西盯着，于是「事实源里删掉一个角色、这里还收着它」
# 可以静默发生（封样测试见 tests/test_permissions_seal.py）。
_SYSTEM_ROLE_PATTERN = r"^(" + "|".join(SYSTEM_ROLES) + r")$"


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
    # admin=全权 / user=普通 / guest=游客（硬封顶只读，见 core/readonly_gate.py）
    role: str = Field(default="user", pattern=_SYSTEM_ROLE_PATTERN)


class UpdateUserRequest(BaseSchema):
    """更新用户请求（所有字段可选）"""
    role: str | None = Field(default=None, pattern=_SYSTEM_ROLE_PATTERN)
    is_active: bool | None = None
    # 管理员重置他人密码。此前只有 /auth/change-password 一条路，它要求提供原密码、
    # 且只能改自己 —— 用户忘了密码就彻底没救，只能删号重建。
    password: str | None = Field(default=None, min_length=6, max_length=128)
