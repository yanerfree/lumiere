"""script_runs 补齐执行记账字段（A0）

在此之前平台有两个执行账本：script_runs 只有单条即席跑写入，而计划执行、
adhoc 批量、页面「运行验证」(run-stream 的 Python 分支) 三条路只写
test_report_scenarios。后果是失败证据（HAR/截图/现象分类）全挂在 script_runs
上，却覆盖不到任何回归失败。

本迁移只加列，不动 test_reports —— TestReportScenario 冗余存 case_code/status
那几个字段配的是 case_id nullable + 无 CASCADE，那是故意的快照语义（用例删了
历史报告还得能看），不能退化成汇总。

- report_scenario_id: 反查回报告。SET NULL —— 报告删了执行事实还在
- run_mode:  debug(即席调试，不进通过率) / regression(计划与批量回归)
- attempt:   计划执行会重试 N 次，每次单独记一行。flaky 判定要的正是
             "同一版本多次结果翻转"，只记最后一次就永远攒不到这个数据
- captured_requests / failure_phenomenon: A3/A4 要填，列一次加完，
             避免三个 Story 各写一个 migration 撞在一起

Revision ID: m7a8b9c0d1e2
Revises: l6f7a8b9c0d1
Create Date: 2026-08-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "m7a8b9c0d1e2"
down_revision = "l6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "script_runs",
        sa.Column("report_scenario_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "script_runs_report_scenario_id_fkey",
        "script_runs", "test_report_scenarios",
        ["report_scenario_id"], ["id"], ondelete="SET NULL",
    )
    op.add_column(
        "script_runs",
        sa.Column("run_mode", sa.String(12), nullable=False, server_default="debug"),
    )
    op.add_column(
        "script_runs",
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "script_runs",
        sa.Column("captured_requests", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "script_runs",
        sa.Column("failure_phenomenon", sa.String(32), nullable=True),
    )
    # 存量行全是即席调试跑出来的，default 'debug' 正确，不需要回填。
    op.create_index(
        "ix_script_runs_case_mode_created",
        "script_runs", ["case_id", "run_mode", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_script_runs_case_mode_created", table_name="script_runs")
    op.drop_column("script_runs", "failure_phenomenon")
    op.drop_column("script_runs", "captured_requests")
    op.drop_column("script_runs", "attempt")
    op.drop_column("script_runs", "run_mode")
    op.drop_constraint("script_runs_report_scenario_id_fkey", "script_runs", type_="foreignkey")
    op.drop_column("script_runs", "report_scenario_id")
