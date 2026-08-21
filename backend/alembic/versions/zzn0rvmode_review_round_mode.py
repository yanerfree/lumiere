"""审核轮次记下「这轮是真跑过还是静态看的」

Revision ID: zzn0rvmode
Revises: zzm0actor

为什么必须落库：执行式审核和静态审核的结论强度差一个量级 —— 同一条用例实测
静态 84 分通过、真跑 56 分打回（接口场景里的端点，页面一次都没调过；这个判据
不靠模型，是 URL 集合比对）。而此前两种审核在轮次列表里长得一模一样，
"这条过审了"看不出是凭什么过的。

traffic_seen 一并存：是 0 的话「没发现端点问题」只说明没得比，不说明端点是对的。
两列都可空 —— 存量轮次没有这个信息，不编造，页面按 NULL 显示成「未知」。
"""
import sqlalchemy as sa
from alembic import op

revision = "zzn0rvmode"
down_revision = "zzm0actor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("case_review_rounds",
                  sa.Column("review_mode", sa.String(20), nullable=True))
    op.add_column("case_review_rounds",
                  sa.Column("traffic_seen", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("case_review_rounds", "traffic_seen")
    op.drop_column("case_review_rounds", "review_mode")
