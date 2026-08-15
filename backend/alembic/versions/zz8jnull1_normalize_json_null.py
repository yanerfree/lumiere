"""把 captured_requests 里的 JSON null 归一成 SQL NULL

JSONB 列默认把 Python None 存成字面量 'null'，不是 SQL NULL。回收逻辑上线后
第一批被清的行就是这个状态：`IS NOT NULL` 仍为真 → 每次执行都被重新选出来清一遍
→ `len(None or [])` = 0 → 原来记的条数被抹成 0。

列上已经加了 none_as_null=True，这里把存量修回来。已经被抹成 0 的 count 恢复不了，
置回 NULL —— 界面上显示「流量已回收」而不是「0 条流量已回收」，后者更莫名其妙。

Revision ID: zz8jnull1
Revises: zz7prune1
"""
from __future__ import annotations

from alembic import op

revision = "zz8jnull1"
down_revision = "zz7prune1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE script_runs
        SET captured_requests = NULL,
            captured_pruned_count = NULLIF(captured_pruned_count, 0)
        WHERE captured_requests = 'null'::jsonb
    """)


def downgrade() -> None:
    pass  # 归一化不需要回滚
