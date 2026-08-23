"""审核批次落库 —— 一次审核 = 一条记录

Revision ID: zzq0rvbatch
Revises: zzq0bdiff

（原来接在 zzp0gvarproj 后面，和版本升级那条 `zzq0bdiff` 撞成两个 head ——
合并时重新串到它后面。两条互不相干，谁先谁后都行。）

批量审核原来是一次长 POST + 一份内存台账（只留最近 20 批）。三个后果都真实发生过：
刷新页面就丢（30 条实测跑满 5 分钟，这五分钟不能碰浏览器）；重启之后正在跑的
那批既不继续也不标失败，直接消失；报告页说不出"这次审的是什么"——
类型/范围/环境/发起人全都没地方存，而一份看不出是审了整个模块还是抽了三条的报告，
过两周就没人信。

结论仍然在 case_review_rounds 和 cases 上，这两张表是**索引和账**。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "zzq0rvbatch"
down_revision = "zzq0bdiff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("scope_label", sa.String(200), nullable=True),
        sa.Column("folder_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("environment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("environment_name", sa.String(120), nullable=True),
        sa.Column("case_ids", postgresql.JSONB(), nullable=True),
        sa.Column("actor", sa.String(100), nullable=True),
        sa.Column("actor_kind", sa.String(10), nullable=False, server_default="human"),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("approved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("inconclusive", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_case_code", sa.String(64), nullable=True),
        sa.Column("with_checkup", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("report", postgresql.JSONB(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_review_batches_branch", "review_batches", ["branch_id"])
    op.create_index("ix_review_batches_project", "review_batches", ["project_id"])
    # 报告页默认按时间倒序、只看自己发起的；队列调度按 (环境, 状态) 挑下一个。
    op.create_index("ix_review_batches_created", "review_batches", ["created_at"])
    op.create_index("ix_review_batches_queue", "review_batches",
                    ["environment_id", "status"])

    op.create_table(
        "review_batch_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("review_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_code", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("verdict", sa.String(20), nullable=True),
        sa.Column("run_state", sa.String(24), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_review_batch_items_batch", "review_batch_items", ["batch_id"])
    op.create_index("ix_review_batch_items_case", "review_batch_items", ["case_id"])


def downgrade() -> None:
    op.drop_table("review_batch_items")
    op.drop_index("ix_review_batches_queue", table_name="review_batches")
    op.drop_index("ix_review_batches_created", table_name="review_batches")
    op.drop_index("ix_review_batches_project", table_name="review_batches")
    op.drop_index("ix_review_batches_branch", table_name="review_batches")
    op.drop_table("review_batches")
