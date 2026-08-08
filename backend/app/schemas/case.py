import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import BaseSchema


class CreateCaseRequest(BaseSchema):
    """手动创建用例请求"""
    title: str = Field(min_length=1, max_length=200)
    type: Literal["api", "e2e"]
    module: str = Field(min_length=1, max_length=100)
    submodule: str | None = None
    priority: Literal["P0", "P1", "P2", "P3"] = "P2"
    preconditions: str | None = None
    steps: list[dict] = Field(default_factory=list, min_length=1)
    expected_result: str | None = None
    variables_used: list[str] | None = None
    api_scenario: dict | None = None
    ui_scenario: dict | None = None
    api_scenario_status: Literal["draft", "debugging", "completed"] = "draft"
    ui_scenario_status: Literal["draft", "debugging", "completed"] = "draft"
    is_api_template: bool = False
    is_ui_template: bool = False
    script_ref_file: str | None = None
    script_ref_func: str | None = None
    remark: str | None = None
    # 这条用例**要**做到什么程度。CC 的断点续跑靠它判"还欠什么"——
    # 人在页面上建的用例如果不带这个，CC 永远不知道该不该给它补接口和 UI。
    target_level: Literal["spec", "spec_api", "full"] = "spec"


class UpdateCaseRequest(BaseSchema):
    """更新用例请求（所有字段可选）"""
    title: str | None = Field(default=None, max_length=200)
    type: Literal["api", "e2e"] | None = None
    module: str | None = None
    submodule: str | None = None
    priority: Literal["P0", "P1", "P2", "P3"] | None = None
    preconditions: str | None = None
    steps: list[dict] | None = None
    expected_result: str | None = None
    variables_used: list[str] | None = None
    api_scenario: dict | None = None
    ui_scenario: dict | None = None
    api_scenario_status: Literal["draft", "debugging", "completed"] | None = None
    ui_scenario_status: Literal["draft", "debugging", "completed"] | None = None
    is_api_template: bool | None = None
    is_ui_template: bool | None = None
    is_core: bool | None = None
    script_ref_file: str | None = None
    script_ref_func: str | None = None
    is_flaky: bool | None = None
    remark: str | None = None
    # AI 审核扩展（FR21-FR28）
    review_status: Literal["pending_review", "approved", "rejected"] | None = None
    review_reason: dict | None = None
    # 状态体系 v2（可编辑）
    lifecycle_status: Literal["draft", "done", "deprecated"] | None = None
    manual_status: Literal["not_started", "draft", "debugging", "pending_review", "executable", "needs_fix"] | None = None
    ui_status: Literal["not_started", "draft", "debugging", "pending_review", "executable", "needs_fix"] | None = None
    api_status: Literal["not_started", "draft", "debugging", "pending_review", "executable", "needs_fix"] | None = None


class BatchCaseRequest(BaseSchema):
    """批量操作请求"""
    action: Literal["move", "archive", "unarchive", "set_priority", "set_flaky", "unset_flaky", "delete", "hard_delete"]
    case_ids: list[uuid.UUID] = Field(min_length=1)
    folder_id: uuid.UUID | None = None      # action=move 时必填
    priority: Literal["P0", "P1", "P2", "P3"] | None = None  # action=set_priority 时必填


class CopyFromBranchRequest(BaseSchema):
    """跨分支复制请求"""
    source_branch_id: uuid.UUID
    case_ids: list[uuid.UUID] = Field(min_length=1)


class CaseResponse(BaseSchema):
    """用例响应"""
    id: uuid.UUID
    branch_id: uuid.UUID
    case_code: str
    tea_id: str | None
    title: str
    type: str
    folder_id: uuid.UUID | None
    priority: str
    preconditions: str | None
    steps: list[dict]
    expected_result: str | None
    variables_used: list[str] | None
    api_scenario: dict | None
    ui_scenario: dict | None
    api_scenario_status: str
    ui_scenario_status: str
    is_api_template: bool
    is_ui_template: bool
    is_core: bool = False
    automation_status: str
    lifecycle_status: str = "draft"
    manual_status: str = "not_started"
    ui_status: str = "not_started"
    api_status: str = "not_started"
    source: str
    script_ref_file: str | None
    script_ref_func: str | None
    is_flaky: bool
    remark: str | None
    # AI 审核扩展
    review_status: str | None = None
    review_reason: dict | None = None
    quality_score: dict | None = None
    generation_task_id: uuid.UUID | None = None
    requirement_point_ids: list | None = None
    version: int = 1
    created_at: datetime
    updated_at: datetime
