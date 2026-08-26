"""用例加 target_level_reason —— 「不要 UI/接口」得说清为什么

只有 target_level 一个值时，人分不出「CC 判断这条纯接口验证不需要 UI」和
「CC 没想，用了默认值」—— 而这两件事的后果完全不同。实测被直接问过：
「他自己会规划吗，用户怎么知道呢，是他规划了没写还是没规划」。

照 expected_confirmed_note 的样子留一句话。回推时 target_level != full 且没带理由，
lum_create_case 会在 _qualityWarnings 里提醒（**不硬拦** —— 真有确实不需要的，
写一句话的成本就够了）。

Revision ID: zz6tlreason
Revises: zz5dropss
"""
from alembic import op
import sqlalchemy as sa

revision = "zz6tlreason"
down_revision = "zz5dropss"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("target_level_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("cases", "target_level_reason")
