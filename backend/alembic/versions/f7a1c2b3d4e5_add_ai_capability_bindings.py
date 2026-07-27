"""add ai_capability_bindings + ai_global_settings

Revision ID: f7a1c2b3d4e5
Revises: f2a3b4c5d6e7
Create Date: 2026-07-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a1c2b3d4e5"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_capability_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("key", sa.String(50), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("category", sa.String(20), nullable=False, server_default="text"),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("module_keys", postgresql.JSONB(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint("uq_ai_capability_bindings_key", "ai_capability_bindings", ["key"])

    op.create_table(
        "ai_global_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("fallback_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # 播种:2 个内置档位,模型取自当前 .env(升级后行为不变),外加全局设置单行(默认开)。
    from app.config import settings
    text_model = settings.ai_model or "claude-haiku-4-5-20251001"
    ui_model = settings.ai_ui_model or settings.ai_model or "claude-sonnet-4-6"

    op.execute(
        sa.text(
            "INSERT INTO ai_capability_bindings (key, label, category, model, is_builtin, sort_order) "
            "VALUES (:k, :l, :c, :m, true, :o)"
        ).bindparams(k="text", l="文本生成", c="text", m=text_model, o=0)
    )
    op.execute(
        sa.text(
            "INSERT INTO ai_capability_bindings (key, label, category, model, is_builtin, sort_order) "
            "VALUES (:k, :l, :c, :m, true, :o)"
        ).bindparams(k="ui_script", l="UI 脚本生成", c="ui_script", m=ui_model, o=1)
    )
    op.execute(sa.text("INSERT INTO ai_global_settings (fallback_enabled) VALUES (true)"))


def downgrade() -> None:
    op.drop_table("ai_global_settings")
    op.drop_constraint("uq_ai_capability_bindings_key", "ai_capability_bindings", type_="unique")
    op.drop_table("ai_capability_bindings")
