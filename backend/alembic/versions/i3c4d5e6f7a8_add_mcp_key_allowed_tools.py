"""add mcp_api_keys.allowed_tools (per-key MCP tool whitelist)

Revision ID: i3c4d5e6f7a8
Revises: h2b3c4d5e6f7
Create Date: 2026-07-29

NULL = 不限制（全部工具），列表 = 只暴露这些工具名。
可空且无默认，存量 Key 无需回填、行为不变。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'i3c4d5e6f7a8'
down_revision: Union[str, None] = 'h2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'mcp_api_keys',
        sa.Column('allowed_tools', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('mcp_api_keys', 'allowed_tools')
