"""test_reports 加 flaky / xfail 计数

规范（change-sync-log「状态枚举扩展」）要求 6 种状态：
passed / failed / error / skipped / xfail / flaky，通过率公式是
`passed / (passed + failed + error + flaky)`，skipped 和 xfail 不计入分母。

实现里只有 4 种，缺的两种各有后果：

· **flaky**：用例失败后重试通过，最终记成 `passed`，只在备注里写「重试 2 次，
  最终通过」。**通过率照样算 100%** —— 这正是 flaky 这个状态要防的"假通过"。
· **xfail**：pytest 的预期失败在 junit 里是 `<skipped type="pytest.xfail">`，
  被当成"跳过"。分母没算错（两者都排除），但报告上写"跳过"是不对的 ——
  它跑了，而且按预期失败了。

Revision ID: u5c6d7e8f9a0
Revises: t4b5c6d7e8f9
"""
from alembic import op
import sqlalchemy as sa

revision = "u5c6d7e8f9a0"
down_revision = "t4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("test_reports", sa.Column("flaky", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("test_reports", sa.Column("xfail", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("test_reports", "xfail")
    op.drop_column("test_reports", "flaky")
