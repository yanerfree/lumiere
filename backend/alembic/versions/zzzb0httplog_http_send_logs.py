"""HTTP 请求页的发送记录表 http_send_logs（按用户隔离，只记摘要）

Revision ID: zzzb0httplog
Revises: zzza0notes
Create Date: 2026-11-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "zzzb0httplog"
down_revision = "zzza0notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "http_send_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("per_round", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("success", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status_codes", postgresql.JSONB(), nullable=True),
        sa.Column("avg_ms", sa.Integer(), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_http_send_logs_user_id", "http_send_logs", ["user_id"])
    op.create_index("ix_http_send_logs_created_at", "http_send_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_http_send_logs_created_at", table_name="http_send_logs")
    op.drop_index("ix_http_send_logs_user_id", table_name="http_send_logs")
    op.drop_table("http_send_logs")
