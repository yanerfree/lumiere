"""个人记录表 user_notes（按用户隔离，超管可读全部）

Revision ID: zzza0notes
Revises: zzz9mcprev
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "zzza0notes"
down_revision = "zzz9mcprev"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_user_notes_user_id", "user_notes", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_notes_user_id", table_name="user_notes")
    op.drop_table("user_notes")
