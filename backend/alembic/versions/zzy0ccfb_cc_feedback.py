"""cc_feedback —— 外部 CC 报回来的平台自身问题（全局表，项目只是来源线索）

Revision ID: zzy0ccfb
Revises: zzx0qasrv
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "zzy0ccfb"
down_revision = "zzx0qasrv"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cc_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        # SET NULL 不是 CASCADE：项目删了，「平台这个工具有毛病」这件事依然成立。
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="cc"),
        sa.Column("reporter", sa.String(100), nullable=True),
        sa.Column("tool_name", sa.String(64), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reported_category", sa.String(16), nullable=True),
        sa.Column("category", sa.String(16), nullable=True),
        sa.Column("severity", sa.String(16), nullable=True),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reopened_from", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cc_feedback.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="new"),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("duplicate_of", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cc_feedback.id", ondelete="SET NULL"), nullable=True),
        sa.Column("handled_by", sa.String(100), nullable=True),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ai_analysis", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ack_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # 归并查的就是 (指纹, 状态)：每次上报都要走一次，是这张表最热的路。
    op.create_index("ix_ccfb_fp", "cc_feedback", ["fingerprint", "status"])
    # 页面默认筛「待处理」+ 按最近一次排序
    op.create_index("ix_ccfb_status_seen", "cc_feedback", ["status", "last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_ccfb_status_seen", table_name="cc_feedback")
    op.drop_index("ix_ccfb_fp", table_name="cc_feedback")
    op.drop_table("cc_feedback")
