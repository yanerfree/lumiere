"""add environments.sort_order (环境列表拖拽排序)

Revision ID: l6f7a8b9c0d1
Revises: k5e6f7a8b9c0
Create Date: 2026-08-07
"""
from alembic import op
import sqlalchemy as sa

revision = "l6f7a8b9c0d1"
down_revision = "k5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("environments", sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))
    # 按现有的 name 升序回填 —— 之前列表就是按 name 排的，不回填的话
    # 升级后所有 sort_order 都是 0，顺序会退化成不确定的插入序，看着像乱跳。
    op.execute("""
        UPDATE environments e
        SET sort_order = t.rn
        FROM (SELECT id, (ROW_NUMBER() OVER (ORDER BY name) - 1) AS rn FROM environments) t
        WHERE e.id = t.id
    """)


def downgrade() -> None:
    op.drop_column("environments", "sort_order")
