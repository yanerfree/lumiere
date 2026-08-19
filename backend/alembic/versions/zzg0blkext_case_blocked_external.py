"""用例可以标注「卡在外部条件上」

外部 CC 的第十一条反馈：TC-DYGL-00015 因为环境变量还没加，场景被硬拒（拒得对），
用例停在 api_scenario_missing —— 而它和「我压根没写场景」在 tb_check_branch 里
**长得一模一样**。看板上分不出责任在谁，于是每轮都要人挨个去问一遍。

一列文本就够：写清等的是什么。**不新增状态枚举** ——
状态由执行事实推进（红线），"等外部条件"不是一种进度，是一句归责说明。

Revision ID: zzg0blkext
Revises: zzf0brdot
"""
from alembic import op
import sqlalchemy as sa

revision = "zzg0blkext"
down_revision = "zzf0brdot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("blocked_external", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("cases", "blocked_external")
