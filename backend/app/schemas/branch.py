import uuid
from datetime import datetime

from pydantic import Field

from app.schemas.common import BaseSchema


class CreateBranchRequest(BaseSchema):
    """创建分支配置请求"""
    # 点号只能做分隔符：不能开头/结尾、不能连用。
    # 分支名会被拼成工作目录 {script_base_path}/{name}/（见 services/git_service.py），
    # 放开点号后必须排除 "." 和 ".."，否则等于目录穿越。
    name: str = Field(min_length=1, max_length=50, pattern=r"^[a-zA-Z0-9_-]+(\.[a-zA-Z0-9_-]+)*$")
    description: str | None = None
    branch: str = Field(default="main", max_length=100)
    source_branch_id: str | None = None  # 从此分支复制数据
    copy_modules: list[str] | None = None  # ["cases", "api_test", "apis"]


class UpdateBranchRequest(BaseSchema):
    """更新分支配置请求（name 不可改）"""
    description: str | None = None
    branch: str | None = Field(default=None, max_length=100)


class BranchResponse(BaseSchema):
    """分支配置响应"""
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str | None
    branch: str
    status: str
    json_file_path: str
    last_sync_at: datetime | None
    last_commit_sha: str | None
    created_at: datetime
    updated_at: datetime


class SyncBranchResponse(BaseSchema):
    """分支同步响应"""
    commit_sha: str
    first_time: bool
    added: int = 0
    modified: int = 0
    deleted: int = 0
