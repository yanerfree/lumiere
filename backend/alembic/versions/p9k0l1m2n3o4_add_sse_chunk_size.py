"""mock_routes.sse_chunk_size —— 流式按 N 个字符一块发

对接网关时，"分片数"本身就是被验证的指标（例如某条正文必须恰好切成 9 个 data 分片）。
此前引擎写死逐字符切，同一句话会切出几十片，跟被对照的 mock 对不上，
场景判定直接失真。默认 1 保持原行为，填 6 就跟对方 mock 对齐。

Revision ID: p9k0l1m2n3o4
Revises: n8g9h0i1j2k3
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa

revision = "p9k0l1m2n3o4"
down_revision = "n8g9h0i1j2k3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mock_routes",
        sa.Column("sse_chunk_size", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("mock_routes", "sse_chunk_size")
