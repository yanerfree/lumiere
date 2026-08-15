"""script_runs 加 captured_pruned_count：流量被回收时记下原来有多少条

流量回收规则是「通过的只留最新一次、失败的留最近 5 次」。只把 captured_requests
置空的话，界面上「这次本来就没抓到流量」和「抓了 97 条但被回收了」长得一模一样 ——
人看到空白会当成 bug 报。这一列就是为了让回收这件事**说得出口**。

Revision ID: zz7prune1
Revises: zz6dropmr
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "zz7prune1"
down_revision = "zz6dropmr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("script_runs", sa.Column("captured_pruned_count", sa.Integer(), nullable=True))
    # 回收要按 (用例, 脚本类型, 时间) 找老记录，没索引会全表扫。
    op.create_index("ix_script_runs_case_type_created", "script_runs",
                    ["case_id", "script_type", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_script_runs_case_type_created", table_name="script_runs")
    op.drop_column("script_runs", "captured_pruned_count")
