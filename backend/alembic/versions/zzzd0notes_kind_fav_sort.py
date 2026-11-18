"""我的记录：加类型(kind)、状态(status)、收藏(is_favorite)、手动排序(sort_order)

Revision ID: zzzd0notes
Revises: zzzc0httpmcp
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = "zzzd0notes"
down_revision = "zzzc0httpmcp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 存量记录一律算「笔记」，不会凭空变成待办
    op.add_column("user_notes", sa.Column("kind", sa.String(length=10), nullable=False, server_default="note"))
    op.add_column("user_notes", sa.Column("status", sa.String(length=10), nullable=False, server_default="pending"))
    op.add_column("user_notes", sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("user_notes", sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    for c in ("sort_order", "is_favorite", "status", "kind"):
        op.drop_column("user_notes", c)
