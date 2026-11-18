"""我的记录：待办加完成时间 completed_at

Revision ID: zzze0notes
Revises: zzzd0notes
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = "zzze0notes"
down_revision = "zzzd0notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_notes", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    # 已经是「已完成」的存量待办：没记过完成时刻，用最后更新时间近似
    op.execute("UPDATE user_notes SET completed_at = updated_at WHERE kind = 'todo' AND status = 'done'")


def downgrade() -> None:
    op.drop_column("user_notes", "completed_at")
