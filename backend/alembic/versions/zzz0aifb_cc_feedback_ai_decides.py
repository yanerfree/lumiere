"""cc_feedback：AI 自己落裁定所需的三列（谁判的 / 什么还得人来 / 抽检样本）

Revision ID: zzz0aifb
Revises: zzy0ccfb

原来的设计是「AI 只出建议、状态只有人能落」。改掉的理由不是"AI 变强了"，是那个
设计和平台的整体分工反着来 —— 人应该是来看结果或点一下执行的，只有少数 AI 判不了
的才接进来。当初留那道人工闸是因为 wont_fix 会永久短路后续同指纹上报，AI 一次误判
就把一类反馈关死。**正确的做法是把那个不可逆性拆掉，不是拿一道人工闸把它围住**：
AI 判的 wont_fix 现在能被带新证据的重报撬开（decided_by 就是给这件事用的），
人判的才是终局。

三列都可空 / 有默认值，存量 31 条不用回填：decided_by 为 NULL 读作「还没人判过」，
正好就是它们的真实状态。
"""
from alembic import op
import sqlalchemy as sa

revision = "zzz0aifb"
down_revision = "zzy0ccfb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cc_feedback", sa.Column("decided_by", sa.String(16), nullable=True))
    op.add_column("cc_feedback", sa.Column("needs_human", sa.Text(), nullable=True))
    op.add_column("cc_feedback", sa.Column(
        "sampled", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    # 「等人拍板」是页面默认要筛的东西之一，而它的判据是 needs_human 非空。
    # 部分索引而不是全列索引：非空的本来就是少数，这才是它值得建索引的原因。
    op.create_index("ix_cc_feedback_needs_human", "cc_feedback", ["needs_human"],
                    postgresql_where=sa.text("needs_human IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_cc_feedback_needs_human", table_name="cc_feedback")
    op.drop_column("cc_feedback", "sampled")
    op.drop_column("cc_feedback", "needs_human")
    op.drop_column("cc_feedback", "decided_by")
