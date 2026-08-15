"""script_runs 加 steps 列 —— 执行历史展开要能看到每一步

为什么需要：UI 脚本跑完，步骤是解析出来了的（平台会自动埋点，断言和
goto/click/fill 各算一步），但**没有地方存**。执行历史展开读的是 script_runs 这一行，
里面只有 stdout —— 而 stdout 是 pytest 的输出，通过时只有一行 `1 passed in 12.56s`。

于是现象是「脚本跑完没有报错」+ 一坨 pytest 启动横幅，十几个 expect() 验了什么
一个都看不到，跑挂了也看不出挂在哪一步。实测被指出来过两轮。

接口场景那边不需要这个 —— 它的每一步存在 api_test_steps.last_status/last_response 上，
本来就看得到。这一列只给 UI 脚本用。

Revision ID: z0b1c2d3e4f5
Revises: y9a0b1c2d3e4
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "z0b1c2d3e4f5"
down_revision = "y9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("script_runs", sa.Column("steps", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("script_runs", "steps")
