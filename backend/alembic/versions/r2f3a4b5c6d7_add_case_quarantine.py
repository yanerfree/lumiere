"""cases：flaky 自动隔离（quarantined_until + 判定依据）

现状的问题不是"没有 flaky 机制"，而是现有机制**有害**：
`is_flaky` 是个纯手动布尔，一旦标上，执行器就永远跳过这条用例（execution.py:214），
**没有任何回来的路** —— 脚本修好了、环境稳了，它也还在被跳过，而且没人记得当初为什么标它。

这条迁移加两个字段：
- quarantined_until：自动隔离到期时间。非空且未过期 = 隔离中；过期自动回到执行队列，
  不需要定时任务（查询时比时间即可）。
- flaky_evidence：判定依据（哪几次执行、哪个脚本版本、什么结果）。
  判定必须能复核 —— 否则"平台说它 flaky"和"平台说它坏了"一样不可信。

Revision ID: r2f3a4b5c6d7
Revises: q1e2f3a4b5c6
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "r2f3a4b5c6d7"
down_revision = "q1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("quarantined_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("cases", sa.Column("flaky_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    # 按到期时间查"还在隔离中的"，用例列表和执行前置都要走
    op.create_index("ix_cases_quarantined_until", "cases", ["quarantined_until"])


def downgrade() -> None:
    op.drop_index("ix_cases_quarantined_until", table_name="cases")
    op.drop_column("cases", "flaky_evidence")
    op.drop_column("cases", "quarantined_until")
