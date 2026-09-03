"""cc_feedback：加「范围」列（area = 坏掉的是哪一块子系统）

Revision ID: zzz1area
Revises: zzz0aifb

页面上原来只有 tool_name 一列能说「这条是关于什么的」，而它答的是**手按在哪个工具
上**，不是**坏的是哪一块**：6 条 AI 评审的反馈里 tool_name 写的是 lum_review_case，
可它在「货架分类」上属于「用例·手工步骤」—— 按货架分类去看，AI 评审这一整块是
隐形的。所以 area 是**故障域**，不是 TOOL_CATALOG.category 的复制。

⚠ **area 不进指纹**（指纹只有 tool_name + 归一化标题两项）。改域不会让同一个坑
分裂成两行，回填也不会影响任何已有的归并关系 —— 这是这次加列敢直接回填的前提。

回填是**规则式**的，不叫模型：
  ① 注册工具名 → 照 `cc_feedback_service._TOOL_AREA` 那张表（本文件里冻结了一份
     副本 —— 迁移是历史快照，不能跟着后面的代码漂）；
  ② 库里 18 个自由文本 tool_name（`AI 评审规则 xxx`、`执行报告`、`接口场景执行器`
     这类，占 56 条里的 18 条）→ 逐条写死；
  ③ **两张表都匹配不上的留 NULL，绝不塞 `other`**。NULL 读作「还没判过域」，
     `other` 读作「判过了，就是归不进任何一档」—— 混成一个值，AI 分诊那一层就
     再也分不出「该我填」和「人已经判了别动」，回填反而把后面的自动填域堵死。

一处**故意和文档里的人工归类不一样**：`lum_list_api_tests :: folder_id 传用例目录
id 会静默返回空列表` 这条，文档手工聚类放进了 apidoc（觉得是"接口库的目录语义"），
这里按 _TOOL_AREA 落 api_run。注册工具名只留**一个**判据来源，宁可让这一行事后被
人/AI 挪一次，也不要在两处维护两份互相打架的表。
"""
from alembic import op
import sqlalchemy as sa

revision = "zzz1area"
down_revision = "zzz0aifb"
branch_labels = None
depends_on = None


# 冻结副本，见上面的说明。改代码里那张表**不要**回来改这里。
_TOOL_AREA = {
    "lum_review_case": "ai_review",
    "lum_review_batch": "ai_review",
    "lum_review_batch_status": "ai_review",
    "lum_review_check": "ai_review",
    "lum_sync_orchestrated_scenario": "sync",
    "lum_sync_ui_script": "sync",
    "lum_upsert_scenario_variables": "sync",
    "lum_list_scenario_variables": "sync",
    "lum_upsert_selectors": "sync",
    "lum_list_selectors": "sync",
    "lum_upsert_i18n_terms": "sync",
    "lum_create_case": "case",
    "lum_update_case": "case",
    "lum_get_case": "case",
    "lum_list_cases": "case",
    "lum_get_folder_tree": "case",
    "lum_request_deprecate": "case",
    "lum_check_deliverable": "gate",
    "lum_check_assertion_bite": "gate",
    "lum_check_env_hygiene": "gate",
    "lum_check_branch": "gate",
    "lum_module_checkup": "gate",
    "lum_next_duty": "gate",
    "lum_run_api_test": "api_run",
    "lum_get_api_test": "api_run",
    "lum_list_api_tests": "api_run",
    "lum_create_plan": "report",
    "lum_run_plan": "report",
    "lum_list_plans": "report",
    "lum_list_reports": "report",
    "lum_get_report_summary": "report",
    "lum_get_failed_scenarios": "report",
    "lum_add_project_note": "note",
    "lum_list_project_notes": "note",
    "lum_get_sync_spec": "spec",
    "lum_create_api_node": "apidoc",
    "lum_get_api_node": "apidoc",
    "lum_list_api_tree": "apidoc",
    "lum_apply_endpoint_diff": "diff",
    "lum_list_branch_endpoints": "diff",
    "lum_get_qa_review": "qa_review",
    "lum_render_ui_script": "ui_script",
    "lum_run_ui_script": "ui_script",
    "lum_run_ui_scripts_batch": "ui_script",
    "lum_get_ui_script_result": "ui_script",
    "lum_list_environments": "env",
    "lum_get_merged_variables": "env",
    "lum_list_global_data": "env",
    "lum_upsert_automation_resource": "env",
}

# 库里那 18 个自由文本 tool_name。**逐条写死，不做关键词匹配** —— 「AI 评审规则
# 文案」靠 `评审` 命中还行，`执行报告` 和 `接口场景执行器` 只差两个字却分属
# report / api_run（一个是报告读出来不对，一个是执行器本身跑错），关键词分不开。
_LEGACY_AREA = {
    "AI 评审（mustFix 输出）": "ai_review",
    "AI 评审（建议内容）": "ai_review",
    "AI 评审（用例正文读取）": "ai_review",
    "AI 评审规则 control_group_in_one": "ai_review",
    "AI 评审规则 control_plane_only": "ai_review",
    "AI 评审规则（retry_timeout_ms）": "ai_review",
    "AI 评审规则文案": "ai_review",
    "AI 评审规则（请求指纹）": "ai_review",
    "hardcodeWarnings（回推校验）": "sync",
    "lum_add_project_note / ai_review": "ai_review",
    "lum_apply_endpoint_diff / 覆盖对齐": "diff",
    "lum_sync_orchestrated_scenario / lum_get_api_test": "sync",
    "执行报告": "report",
    "执行结果状态": "report",
    "接口场景执行报告": "report",
    "覆盖统计": "report",
    "接口场景执行器": "api_run",
    "断言执行（type=status / operator=in）": "api_run",
}


def upgrade() -> None:
    op.add_column("cc_feedback", sa.Column("area", sa.String(24), nullable=True))
    # 全列索引（不像 needs_human 那条部分索引）：页面要按域筛、还要出每个域的条数，
    # 值全都要用上，NULL 那一档也是页面上的一个筛选块。
    op.create_index("ix_cc_feedback_area", "cc_feedback", ["area"])

    conn = op.get_bind()
    for table in (_TOOL_AREA, _LEGACY_AREA):
        for tool, area in table.items():
            conn.execute(
                sa.text("update cc_feedback set area = :a "
                        "where area is null and tool_name = :t"),
                {"a": area, "t": tool},
            )


def downgrade() -> None:
    op.drop_index("ix_cc_feedback_area", table_name="cc_feedback")
    op.drop_column("cc_feedback", "area")
