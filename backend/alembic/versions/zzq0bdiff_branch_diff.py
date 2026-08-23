"""版本升级·分支对账：用例来源指纹 + 废弃审核 + 对账清单两张表

Revision ID: zzq0bdiff
Revises: zzp0gvarproj
Create Date: 2026-08-21

四件事：

1. `cases.source_case_id` / `cases.content_fingerprint` —— 复制来源 + 复制那一刻
   的内容指纹。照抄堆自动过审的判据「内容与源分支逐字一致」靠它变成机械判定，
   不靠 CC 自己声明"我没改"。

2. `cases.deprecate_status` / `cases.deprecate_reason` —— 废弃审核。
   `lifecycle_status='deprecated'` 只在 `deprecate_status='approved'` 时才落。

3. `endpoint_diff_batches` / `endpoint_diff_hits` —— 对账清单。落平台不落 CC 上下文
   （会话一关就没了，续不上）。

**存量数据全部为 NULL 是对的**：老库里那些用例不是复制出来的，也没申请过废弃。
`content_fingerprint IS NULL` 的语义是"不知道它跟谁一致" → 自动过审条件 2 不成立
→ 走人审/AI 审。**这个方向是安全的**（多审一次），反过来（NULL 当一致）就是假绿。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "zzq0bdiff"
down_revision = "zzp0gvarproj"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("source_case_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_cases_source_case", "cases", "cases",
        ["source_case_id"], ["id"], ondelete="SET NULL",
    )
    op.add_column("cases", sa.Column("content_fingerprint", sa.String(64), nullable=True))
    op.add_column("cases", sa.Column("bite_result", postgresql.JSONB(), nullable=True))
    op.add_column("cases", sa.Column("deprecate_status", sa.String(20), nullable=True))
    op.add_column("cases", sa.Column("deprecate_reason", postgresql.JSONB(), nullable=True))
    # 「待废审」徽标要按这个字段筛整个分支，加个部分索引 —— 绝大多数行是 NULL
    op.create_index(
        "ix_cases_deprecate_pending", "cases", ["branch_id"],
        postgresql_where=sa.text("deprecate_status = 'requested'"),
    )

    op.create_table(
        "endpoint_diff_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("changes", postgresql.JSONB(), nullable=True),
        sa.Column("stats", postgresql.JSONB(), nullable=True),
        sa.Column("pending_new", postgresql.JSONB(), nullable=True),
        sa.Column("from_ref", sa.String(100), nullable=True),
        sa.Column("to_ref", sa.String(100), nullable=True),
        sa.Column("actor", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_endpoint_diff_batches_branch", "endpoint_diff_batches", ["branch_id"])

    op.create_table(
        "endpoint_diff_hits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("endpoint_diff_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scenario_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("step_name", sa.String(200), nullable=True),
        sa.Column("assertion_index", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("method", sa.String(10), nullable=True),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_endpoint_diff_hits_batch_id", "endpoint_diff_hits", ["batch_id"])
    op.create_index("ix_endpoint_diff_hits_case_id", "endpoint_diff_hits", ["case_id"])


def downgrade() -> None:
    op.drop_table("endpoint_diff_hits")
    op.drop_index("ix_endpoint_diff_batches_branch", table_name="endpoint_diff_batches")
    op.drop_table("endpoint_diff_batches")
    op.drop_index("ix_cases_deprecate_pending", table_name="cases")
    op.drop_column("cases", "deprecate_reason")
    op.drop_column("cases", "deprecate_status")
    op.drop_column("cases", "bite_result")
    op.drop_column("cases", "content_fingerprint")
    op.drop_constraint("fk_cases_source_case", "cases", type_="foreignkey")
    op.drop_column("cases", "source_case_id")
