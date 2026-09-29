"""MCP Mock 多服务：mcp_mock_servers / mcp_mock_tools / mcp_mock_logs

Revision ID: zzz8mcpmulti
Revises: zzz7demoshop

原来 MCP Mock 是「一个服务 + 一份 JSON 文件」，只能测一种接入方式。改成多服务之后，
一个端口（28300）上按路径挂 N 个：`http://<host>:28300/<slug>/mcp`。

⚠ 不带 down 的数据迁移：老配置在 `backend/.mock_state/mcp_mock_tools.json` 里，
由 `app/services/mcp_mock_presets.py` 的 `_migrate_legacy_tools()` 在**运行时**搬进
一个普通（可删可改）服务里，搬完把文件改名成 `.json.migrated`。放在这里做不到 ——
alembic 迁移只有数据库连接，拿不到那份文件所在的应用目录，而且回滚一次就会把
用户配过的工具删干净。

⚠ `slug` 唯一是硬要求：两个服务同一个 slug 的话，后挂的那个会把前一个整个盖掉，
而且**一声不响** —— 表现是「页面上明明有两个服务，连上去只剩一个的工具」。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "zzz8mcpmulti"
down_revision = "zzz7demoshop"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_mock_servers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(50), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("instructions", sa.Text, nullable=False, server_default=""),
        # streamable-http | sse（stdio 是本地进程管道，平台这边起不了，页面上标「暂不支持」）
        sa.Column("transport", sa.String(20), nullable=False, server_default="streamable-http"),
        # none | bearer | apikey
        sa.Column("auth_type", sa.String(20), nullable=False, server_default="none"),
        sa.Column("auth_config", postgresql.JSONB, nullable=True),
        # strict | loose | off
        sa.Column("validate_mode", sa.String(20), nullable=False, server_default="strict"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        # builtin=平台预置，永远不许删（删了重启又回来）；locked=用户自己上的锁，解得开
        sa.Column("builtin", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("locked", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("call_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_call_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        "mcp_mock_tools",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("server_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        # 参数定义是**列表**不是字典：顺序在页面和 tools/list 里都要稳定，
        # 而 {name: type} 字典靠插入序撑着，一次 json.loads 之后就不保证了
        sa.Column("params", postgresql.JSONB, nullable=True),
        sa.Column("mode", sa.String(20), nullable=False, server_default="success"),
        sa.Column("success_data", postgresql.JSONB, nullable=True),
        sa.Column("custom_data", postgresql.JSONB, nullable=True),
        sa.Column("custom_is_error", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("error_message", sa.Text, nullable=False,
                  server_default="Mock error: tool call failed"),
        sa.Column("delay_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("locked", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("server_id", "name", name="uq_mcp_mock_tool_server_name"),
    )
    op.create_index("ix_mcp_mock_tools_server_id", "mcp_mock_tools", ["server_id"])

    op.create_table(
        "mcp_mock_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("server_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("arguments", postgresql.JSONB, nullable=True),
        sa.Column("response", sa.Text, nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="mock-server"),
        sa.Column("mode", sa.String(20), nullable=False, server_default="success"),
        sa.Column("is_error", sa.Boolean, nullable=False, server_default=sa.false()),
        # 认证失败和参数校验失败必须分得开：页面上都是一条红日志，
        # 但一个要去改 token、一个要去改参数，混在一起只能靠猜
        sa.Column("reject_kind", sa.String(20), nullable=True),
        sa.Column("reject_detail", sa.Text, nullable=True),
        sa.Column("elapsed_ms", sa.Float, nullable=False, server_default="0"),
    )
    op.create_index("ix_mcp_mock_logs_server_id", "mcp_mock_logs", ["server_id"])


def downgrade() -> None:
    op.drop_index("ix_mcp_mock_logs_server_id", table_name="mcp_mock_logs")
    op.drop_table("mcp_mock_logs")
    op.drop_index("ix_mcp_mock_tools_server_id", table_name="mcp_mock_tools")
    op.drop_table("mcp_mock_tools")
    op.drop_table("mcp_mock_servers")
