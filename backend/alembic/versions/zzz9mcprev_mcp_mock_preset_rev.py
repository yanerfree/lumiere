"""MCP Mock 预置服务加版本号列 preset_rev

启动时靠它判断「库里这套预置是不是平台当前发的那一版」：
对不上就整套刷新（说明 + 认证 + 校验 + 工具）。没有这一列的话，
改了 services/mcp_mock_presets.py 里的内容，已经建出来的行**一个字都不会变** ——
代码里明明改了、页面上还是老样子，而且不报错。

Revision ID: zzz9mcprev
Revises: zzz8mcpmulti
Create Date: 2026-09-29
"""
from alembic import op
import sqlalchemy as sa


revision = "zzz9mcprev"
down_revision = "zzz8mcpmulti"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mcp_mock_servers",
        sa.Column("preset_rev", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mcp_mock_servers", "preset_rev")
