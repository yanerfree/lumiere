"""script_runs：CC 归因 + 人工确认（B2/B3/B4）

三层分开存，是这套设计的核心（docs/cc-platform-loop-spec.md §2.3 + 红线 3）：

  failure_phenomenon  平台判的「现象」（A4 已有），确定性规则，每次执行自动算
  cc_analysis         CC 判的「原因」，**建议值**，进待确认通道
  confirmed_cause     人确认后的结论，**唯一能改状态的东西**

为什么必须是三个字段而不是一个：
- 机器每次执行都会重算 phenomenon，如果和归因共用一列，会把人的判断覆盖掉
- suggested(phenomenon) vs confirmed 一对比，就是分类器的长期准确率；
  cc_analysis vs confirmed 一对比，就是 CC 归因的准确率（B6 要的指标）。
  合成一列这两个指标都算不出来
- CC 归因是"运动员兼裁判"，结构性偏差确定存在（系统性把测试缺陷说成产品缺陷）。
  隔离的办法不是禁止它归因，而是**让它的结论碰不到状态**

cc_analysis 存整个 JSON 而不是拆列：字段还会演化（confidence 的口径、
proposed_fix_target 的取值），拆成列每次都要迁移。

Revision ID: o9c0d1e2f3a4
Revises: n8b9c0d1e2f3
Create Date: 2026-08-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "o9c0d1e2f3a4"
down_revision = "n8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("script_runs", sa.Column("cc_analysis", postgresql.JSONB(), nullable=True))
    op.add_column("script_runs", sa.Column("confirmed_cause", sa.String(32), nullable=True))
    op.add_column("script_runs", sa.Column("confirmed_note", sa.Text(), nullable=True))
    op.add_column(
        "script_runs",
        sa.Column("confirmed_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "script_runs",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "script_runs_confirmed_by_fkey", "script_runs", "users",
        ["confirmed_by"], ["id"], ondelete="SET NULL",
    )
    # 待确认队列：有归因还没人确认的失败。这是人每天要处理的清单，
    # 没索引的话随着 script_runs 增长会越来越慢。
    op.execute("""
        CREATE INDEX ix_script_runs_pending_confirm
        ON script_runs (created_at DESC)
        WHERE confirmed_cause IS NULL AND cc_analysis IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_script_runs_pending_confirm")
    op.drop_constraint("script_runs_confirmed_by_fkey", "script_runs", type_="foreignkey")
    for col in ("confirmed_at", "confirmed_by", "confirmed_note", "confirmed_cause", "cc_analysis"):
        op.drop_column("script_runs", col)
