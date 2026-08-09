"""add mock_routes.locked (LLM Mock 锁定路由)

Revision ID: k5e6f7a8b9c0
Revises: j4d5e6f7a8b9
Create Date: 2026-08-07
"""
from alembic import op
import sqlalchemy as sa

revision = "k5e6f7a8b9c0"
down_revision = "j4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mock_routes", sa.Column("locked", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("mock_routes", "locked")
