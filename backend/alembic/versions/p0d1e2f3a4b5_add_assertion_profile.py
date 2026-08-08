"""scripts.assertion_profile —— 断言指纹留档（B5）

存下来才能做新旧对比。硬拦截只有"断言数为 0"一条（100% 可判），
强度变化走软警告 —— 强度做不到可靠硬判，误拦会逼 CC 拆断言凑数，比不拦更糟。

Revision ID: p0d1e2f3a4b5
Revises: o9c0d1e2f3a4
Create Date: 2026-08-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "p0d1e2f3a4b5"
down_revision = "o9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scripts", sa.Column("assertion_profile", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("scripts", "assertion_profile")
