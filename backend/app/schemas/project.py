import uuid
from datetime import datetime

from pydantic import Field

from app.schemas.common import BaseSchema


# QA 仓默认约定。按 uag-qa 的布局给的默认值，别的项目布局不同就在页面上改——
# 所以这几项是配置而不是写死的常量。
QA_DEFAULT_CATALOG_PATH = "docs/test-scenario-catalog.md"
QA_DEFAULT_CASE_GLOBS = ["api/**/*.sh", "scenarios/**/*.sh", "ui/tests/**/*.spec.ts"]


class QaRepoConfig(BaseSchema):
    """只读 QA 仓配置。

    url 传空串 = 清空配置（更新请求里 None 表示"这次不动它"，所以清空需要另一个信号）。
    """
    url: str = Field(default="", max_length=500)
    branch: str = Field(default="main", max_length=100)
    catalog_path: str = Field(default=QA_DEFAULT_CATALOG_PATH, max_length=300)
    case_globs: list[str] = Field(default_factory=lambda: list(QA_DEFAULT_CASE_GLOBS))


class CreateProjectRequest(BaseSchema):
    """创建项目请求"""
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    # git_url / script_base_path 是旧式 git 脚本库那套（用例的 script_ref_file 指向
    # 仓里的 pytest 文件）。页面已不再提供入口，字段保留是因为存量项目还在用它执行；
    # 详见 engine/tasks/execution.py 里 needs_sandbox 那段。
    git_url: str | None = Field(default=None, max_length=500)
    script_base_path: str | None = Field(default=None, max_length=500)
    qa_repo: QaRepoConfig | None = None


class UpdateProjectRequest(BaseSchema):
    """更新项目请求（所有字段可选）"""
    description: str | None = None
    git_url: str | None = Field(default=None, max_length=500)
    script_base_path: str | None = Field(default=None, max_length=500)
    qa_repo: QaRepoConfig | None = None


class ProjectResponse(BaseSchema):
    """项目响应"""
    id: uuid.UUID
    name: str
    description: str | None
    git_url: str | None
    script_base_path: str | None
    qa_repo: dict | None = None
    created_at: datetime
    updated_at: datetime


# ---- 项目成员 ----

PROJECT_ROLES = ("project_admin", "developer", "tester", "guest")


class AddMemberRequest(BaseSchema):
    """添加项目成员请求"""
    user_id: uuid.UUID
    role: str = Field(pattern=r"^(project_admin|developer|tester|guest)$")


class UpdateMemberRequest(BaseSchema):
    """更新成员角色请求"""
    role: str = Field(pattern=r"^(project_admin|developer|tester|guest)$")


class MemberResponse(BaseSchema):
    """项目成员响应（含用户信息）"""
    id: uuid.UUID
    user_id: uuid.UUID
    username: str
    role: str
    joined_at: datetime
