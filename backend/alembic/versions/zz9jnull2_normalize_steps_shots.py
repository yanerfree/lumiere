"""steps / screenshots 也归一成 SQL NULL

跟 zz8jnull1 同一个坑，只是当时只修了 captured_requests：JSONB 列默认把
Python None 存成字面量 'null'，`IS NOT NULL` 照样为真、jsonb_array_length 直接报
「cannot get array length of a scalar」。存量 steps 25 行、screenshots 112 行。

这坑当前没让页面出错（Python 读回来 JSON null 也是 None），但任何按
「有没有值」做的 SQL 判断都是错的 —— captured_requests 上就是这么把
「回收了 97 条」抹成 0 的。

Revision ID: zz9jnull2
Revises: zzb0rpt1
"""
from __future__ import annotations

from alembic import op

revision = "zz9jnull2"
down_revision = "zzb0rpt1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE script_runs SET steps = NULL WHERE steps = 'null'::jsonb")
    op.execute("UPDATE script_runs SET screenshots = NULL WHERE screenshots = 'null'::jsonb")


def downgrade() -> None:
    pass  # 归一化不需要回滚
