"""把已经写了步骤的用例的 manual_status 推到「待发布」

为什么：没有任何代码推进过 manual_status（`apply_case_status` 只管 ui/api，
人也不会一条条去改），所以全库 255 条有步骤的用例里，248 条停在 `draft`、
7 条停在 `not_started`。显示层只好用「有没有内容」派生出「已写」盖住 ——
于是徽标写「手动·已写 (17)」而下拉高亮「未开始」，同一个维度两个说法。
实测被指出来过（同一类问题的第三次）。

手工步骤没有执行器，只有三种真实状态：
    没写(not_started) → 写了等人过(pending_review) → 人发布了(executable)
写的时候就落对，显示层不用再猜。这条迁移补的是存量。

不动 executable 的 —— 那是人拍过板的。

Revision ID: zz2manual1
Revises: zz1orch01
"""
from alembic import op
import sqlalchemy as sa

revision = "zz2manual1"
down_revision = "zz1orch01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    r = conn.execute(sa.text("""
        UPDATE cases SET manual_status = 'pending_review'
        WHERE deleted_at IS NULL
          AND manual_status IN ('not_started', 'draft')
          AND steps IS NOT NULL
          AND jsonb_typeof(steps) = 'array'
          AND jsonb_array_length(steps) > 0
    """))
    print(f"    → 推进了 {r.rowcount} 条用例的 manual_status")

    # 反过来：没步骤却标着 draft/pending_review 的，回到 not_started
    r2 = conn.execute(sa.text("""
        UPDATE cases SET manual_status = 'not_started'
        WHERE deleted_at IS NULL
          AND manual_status IN ('draft', 'pending_review')
          AND (steps IS NULL OR jsonb_typeof(steps) <> 'array'
               OR jsonb_array_length(steps) = 0)
    """))
    print(f"    → 回退了 {r2.rowcount} 条没步骤的")


def downgrade() -> None:
    # 不还原 —— 原来那个 draft 是"从没被推进过"的默认值，不是有意义的状态。
    pass
