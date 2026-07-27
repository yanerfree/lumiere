"""add locked column to protocol mocks (ws/tcp/udp/grpc)

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-07-27
"""
from alembic import op
import sqlalchemy as sa

revision = "a3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None

_TABLES = ("ws_mock_endpoints", "tcp_mock_handlers", "udp_mock_handlers", "grpc_mock_services")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("locked", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "locked")
