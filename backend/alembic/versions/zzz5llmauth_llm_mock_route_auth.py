"""mock_routes：加认证列 auth_type / auth_config

Revision ID: zzz5llmauth
Revises: zzz4mockuq

给 LLM Mock 的路由补上和「协议 Mock」（api_mock_routes）一样的**每条路由认证**：

  · auth_type   VARCHAR(20) NOT NULL DEFAULT 'none'  —— none 时不拦（存量路由照旧放行）
  · auth_config JSONB       NULL                     —— 各认证方式的参数（token/key/username…）

默认 'none' 保证**开这条迁移之前建的所有路由行为不变**：不填认证 = 不拦，
和历史一致。校验逻辑在 llm_mock_manager._check_auth，进入应答之前跑，不匹配回 401。

模型侧 MockRoute 已声明这两列，create_all 建的测试库自带；这份迁移给已存在的库补上。
"""
from alembic import op

revision = "zzz5llmauth"
down_revision = "zzz4mockuq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE mock_routes "
        "ADD COLUMN IF NOT EXISTS auth_type VARCHAR(20) NOT NULL DEFAULT 'none'"
    )
    op.execute(
        "ALTER TABLE mock_routes ADD COLUMN IF NOT EXISTS auth_config JSONB"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE mock_routes DROP COLUMN IF EXISTS auth_config")
    op.execute("ALTER TABLE mock_routes DROP COLUMN IF EXISTS auth_type")
