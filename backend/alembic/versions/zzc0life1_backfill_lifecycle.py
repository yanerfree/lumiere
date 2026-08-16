"""按 target_level 把存量用例的整体状态补齐

整体状态（lifecycle_status）此前完全靠人手点，没有任何自动推进。结果是列表页
同一行三个信号自相矛盾：「状态」列写着草稿，右边三件套全绿、审核写着「待审」。
人看列表第一眼看的就是状态列，它说草稿就等于说这条没做完 —— 实测被问到。

现在 sync_review_status 会跟着推，这里把存量补上：该做的维度（按 target_level）
都 completed 的，整体状态置 done。

**不碰 deprecated** —— 那是人的决定。也不碰反向（done 但维度没齐的），
避免把人手工标的结论一次性抹掉；那些会在下次执行时自然收敛。

Revision ID: zzc0life1
Revises: zz9jnull2
"""
from __future__ import annotations

from alembic import op

revision = "zzc0life1"
down_revision = "zz9jnull2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE cases SET lifecycle_status = 'done'
        WHERE lifecycle_status = 'draft'
          AND manual_status = 'completed'
          AND (COALESCE(target_level,'spec') = 'spec' OR api_status = 'completed')
          AND (COALESCE(target_level,'spec') <> 'full' OR ui_status = 'completed')
    """)


def downgrade() -> None:
    pass  # 补齐不需要回滚
