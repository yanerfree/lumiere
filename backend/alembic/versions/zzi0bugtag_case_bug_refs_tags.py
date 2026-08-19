"""cases.bug_refs / cases.tags —— 用例被产品 bug 卡住时，卡在哪、什么时候能继续

Revision ID: zzi0bugtag
Revises: zzh0fldalias
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "zzi0bugtag"
down_revision = "zzh0fldalias"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("bug_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("cases", sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("cases", "tags")
    op.drop_column("cases", "bug_refs")
