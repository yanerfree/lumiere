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
    is_api_template: bool | None = None
    is_ui_template: bool | None = None
    is_core: bool | None = None
    script_ref_file: str | None = None
    script_ref_func: str | None = None
    is_flaky: bool | None = None
    remark: str | None = None
    # 关联 bug：整份覆盖（传 [] 就是清空 = 不再卡着）。每条 {ref, url?, status, note?}
    bug_refs: list[dict] | None = None
    tags: list[str] | None = None
    # AI 审核扩展（FR21-FR28）
    # 审核标签：NULL=待提审（不存值）/ pending=待审 / approved=已审 / rejected=不通过
    review_status: Literal["pending", "approved", "rejected"] | None = None
    review_reason: dict | None = None
    # 状态体系 v2（可编辑）
    lifecycle_status: Literal["draft", "done", "deprecated"] | None = None
    manual_status: Literal["draft", "debugging", "completed"] | None = None
    ui_status: Literal["draft", "debugging", "completed"] | None = None
    api_status: Literal["draft", "debugging", "completed"] | None = None


class BatchCaseRequest(BaseSchema):
    """批量操作请求"""
    action: Literal["move", "archive", "unarchive", "set_priority", "set_flaky", "unset_flaky",
                    "delete", "hard_delete", "publish", "unpublish", "restore"]
    case_ids: list[uuid.UUID] = Field(min_length=1)
    # publish / unpublish 作用在哪一维（manual / ui / api）。不传 = 三维一起。
    dimension: Literal["manual", "ui", "api"] | None = None
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
    is_api_template: bool
    is_ui_template: bool
    is_core: bool = False
    automation_status: str
    lifecycle_status: str = "draft"
    # target_level 必须给前端 —— 没有它，列表页分不出「UI 草稿」是**还没做**
    # 还是**本来就不做**，两种情况长得一模一样。实测被当成"没做完"问过。
    target_level: str = "spec"
    manual_status: str = "draft"
    ui_status: str = "draft"
    api_status: str = "draft"
    source: str
    script_ref_file: str | None
    script_ref_func: str | None
    is_flaky: bool
    # 自动隔离：非空且未过期 = 还在隔离中；evidence 是判定依据，人要能复核
    quarantined_until: datetime | None = None
    flaky_evidence: dict | None = None
    # 「卡在外部条件上」：自述等什么。不是状态，只为了让看板分清
    # 「没人写」和「写不了」—— 不给前端的话，人看到的还是一片"未开始"。
    blocked_external: str | None = None
    # 关联 bug + 标签。派生的两个布尔别让前端自己算 ——
    # 「还卡着」和「可以继续了」是两处（列表、CC 的 check_branch）都要用的判断，
    # 各算一遍必然分叉。
    bug_refs: list | None = None
    tags: list | None = None
    # 场景级反问的答案 + 还没答的标记（交付门禁看后者）
    reflections: dict | None = None
    reflection_pending: bool = False
    blocked_by_bug: bool = False
    # 痕迹：这条曾经发现过 bug 且已验回来。列表灰着显示，不催任何人
    has_fixed_bug: bool = False
    bug_found_count: int = 0
    # P0 两阶段：有人确认过「预期结果」这一列没有。改了步骤/预期结果会清掉
    expected_confirmed_at: datetime | None = None
    expected_confirmed_by: uuid.UUID | None = None
    expected_confirmed_actor: str | None = None
    expected_confirmed_note: str | None = None
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
