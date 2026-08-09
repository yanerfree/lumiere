"""cases.target_level —— 这条用例要做到什么程度（C1）

人的意图（"这批要全套，那批只要步骤+接口"）最终就是落在**每条用例要做到什么
程度**上。存在用例上它才是持久事实，而不是一句聊天记录 —— CC 换个会话就忘了。

不新建"批次表"：批次是过程，做到什么程度才是结果。存过程会多一张要维护的表，
而且断点续跑的判据本来就是现成的三个维度状态（manual/api/ui_status）。

Revision ID: q1e2f3a4b5c6
Revises: p0d1e2f3a4b5
Create Date: 2026-08-08
"""
from alembic import op
import sqlalchemy as sa

revision = "q1e2f3a4b5c6"
down_revision = "p0d1e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # spec = 只要手工步骤 / spec_api = 步骤 + 接口场景 / full = 三件套
    op.add_column(
        "cases",
        sa.Column("target_level", sa.String(16), nullable=False, server_default="spec"),
    )
    # 存量用例按"已经做到哪儿"回填，别让它们一夜之间全变成"欠着 UI 没做"
    op.execute("""
        UPDATE cases SET target_level = CASE
            WHEN ui_status <> 'not_started' THEN 'full'
            WHEN api_status <> 'not_started' THEN 'spec_api'
            ELSE 'spec'
        END
    """)


def downgrade() -> None:
    op.drop_column("cases", "target_level")
