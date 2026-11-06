"""HTTP 请求页支持 MCP 请求：http_requests 加 kind + mcp_config

Revision ID: zzzc0httpmcp
Revises: zzzb0httplog
Create Date: 2026-11-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "zzzc0httpmcp"
down_revision = "zzzb0httplog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("http_requests", sa.Column("kind", sa.String(length=10), nullable=False, server_default="http"))
    op.add_column("http_requests", sa.Column("mcp_config", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("http_requests", "mcp_config")
    op.drop_column("http_requests", "kind")
