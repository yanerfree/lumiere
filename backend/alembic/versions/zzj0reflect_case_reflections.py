"""cases.reflections —— 回推时场景级反问的答案

Revision ID: zzj0reflect
Revises: zzi0bugtag
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "zzj0reflect"
down_revision = "zzi0bugtag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("reflections", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("cases", "reflections")
