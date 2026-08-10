"""add mock_routes.stream_mode / smart_response (LLM Mock 流式模式 + 智能应答开关)

stream_mode: auto | force_stream | force_json
  —— force_stream 是给网关 fail-closed 场景用的：请求写 stream:false，上游仍然返事件流。
smart_response: 关掉后 prompt 关键词不再覆盖 response_body（护栏/脱敏验证必须关）。

Revision ID: m7f8a9b0c1d2
Revises: a3b4c5d6e7f8
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa

revision = "m7f8a9b0c1d2"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mock_routes", sa.Column("stream_mode", sa.String(length=20), nullable=False, server_default="auto"))
    # 存量路由保持原行为：跟随请求 + 智能应答开着
    op.add_column("mock_routes", sa.Column("smart_response", sa.Boolean(), nullable=False, server_default="true"))


def downgrade() -> None:
    op.drop_column("mock_routes", "smart_response")
    op.drop_column("mock_routes", "stream_mode")
