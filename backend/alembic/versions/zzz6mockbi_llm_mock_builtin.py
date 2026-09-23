"""mock_routes：加内置套件列 builtin / category / purpose / usage_hint

Revision ID: zzz6mockbi
Revises: zzz5llmauth

LLM Mock 原来只有人手一条条建出来的路由，新人打开这页面对的是一堆裸路径，
「这条是干嘛的、我该往网关里填哪一个」只能问人。这四列是为**内置套件**加的：

  · builtin     BOOLEAN NOT NULL DEFAULT false —— 是不是平台随版本发的内置路由
  · category    VARCHAR(30)  NULL              —— 内置分组（protocol/shape/error/smart/auth）
  · purpose     VARCHAR(200) NULL              —— 这条拿来测什么，一句话
  · usage_hint  TEXT         NULL              —— 怎么用（填哪儿、发什么、看什么）

purpose 存的是**写好的文案**，不是前端从配置反推的句子 —— 反推那版对
「正常文本响应」这类配置只能给同一句兜底话，三十条长得一模一样，等于没写。

默认 false/NULL 保证存量路由行为和呈现都不变：不是内置的照旧归「自建」。
落地与幂等规则在 app/services/llm_mock_builtin.py。
"""
from alembic import op

revision = "zzz6mockbi"
down_revision = "zzz5llmauth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE mock_routes "
        "ADD COLUMN IF NOT EXISTS builtin BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute("ALTER TABLE mock_routes ADD COLUMN IF NOT EXISTS category VARCHAR(30)")
    op.execute("ALTER TABLE mock_routes ADD COLUMN IF NOT EXISTS purpose VARCHAR(200)")
    op.execute("ALTER TABLE mock_routes ADD COLUMN IF NOT EXISTS usage_hint TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE mock_routes DROP COLUMN IF EXISTS usage_hint")
    op.execute("ALTER TABLE mock_routes DROP COLUMN IF EXISTS purpose")
    op.execute("ALTER TABLE mock_routes DROP COLUMN IF EXISTS category")
    op.execute("ALTER TABLE mock_routes DROP COLUMN IF EXISTS builtin")
