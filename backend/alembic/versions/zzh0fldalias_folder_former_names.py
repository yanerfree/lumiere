"""case_folders.former_names —— 模块改名后，CC 手上的旧模块名还要能命中

Revision ID: zzh0fldalias
Revises: zzg0blkext
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "zzh0fldalias"
down_revision = "zzg0blkext"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("case_folders",
                  sa.Column("former_names", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("case_folders", "former_names")
