"""三维状态收成 3 态（草稿/调试中/完成），审核拆成独立标签

背景：原来三维 5 态（not_started/draft/debugging/pending_review/executable），
其中 `executable` **只有人**在列表上勾选点「发布到回归」才给（红线：CC 说能跑等于自证），
而回归门禁看的就是 executable —— 代价是回归池永远空的（实测 257 条里只有 1 条），
审核也被夹在这条路上：人不点，跑绿几十次的脚本也进不了回归。

改成：
  · 三维 = draft(草稿) / debugging(调试中) / completed(完成)，**CC 跑绿自己置完成**
  · 「要不要人审」拆到 review_status 独立标签（用例级）：
      NULL=待提审 / pending=待审（三维全完成自动进） / approved=已审 / rejected=不通过
  · **审核不挡回归** —— 回归门禁改成"有产物就能跑"（见 execution_service）

映射：
  not_started    → draft        （和 draft 区分不出来，起点就是草稿）
  draft          → draft
  debugging      → debugging
  pending_review → completed    （跑绿了就是完成）
  executable     → completed    （人已经拍过板，更是完成）

review_status 里旧值（那条已下线的 AI 流水线留下的 pending_review/approved/rejected）
先清空，再按新规则统一回填 —— 旧值的语义是"用例内容审核"，跟新标签不是一回事，
留着会让 47 条莫名其妙地显示「待审」。

Revision ID: zz3dim3
Revises: zz2manual1
"""
from alembic import op
import sqlalchemy as sa

revision = "zz3dim3"
down_revision = "zz2manual1"
branch_labels = None
depends_on = None

_MAP = {
    "not_started": "draft",
    "pending_review": "completed",
    "executable": "completed",
}


def upgrade() -> None:
    conn = op.get_bind()
    for col in ("manual_status", "ui_status", "api_status"):
        for old, new in _MAP.items():
            r = conn.execute(sa.text(
                f"UPDATE cases SET {col} = :new WHERE {col} = :old"
            ), {"new": new, "old": old})
            if r.rowcount:
                print(f"    → {col}: {old} → {new}  {r.rowcount} 条")
        op.alter_column("cases", col, server_default="draft")

    # 旧的内容审核值跟新标签不是一回事，先清空
    conn.execute(sa.text("UPDATE cases SET review_status = NULL"))

    # 按新规则回填：三维（按 target_level）全完成 → pending（待审）
    r = conn.execute(sa.text("""
        UPDATE cases SET review_status = 'pending'
        WHERE deleted_at IS NULL
          AND manual_status = 'completed'
          AND (target_level = 'spec' OR api_status = 'completed')
          AND (target_level <> 'full' OR ui_status = 'completed')
    """))
    print(f"    → 审核标签回填「待审」{r.rowcount} 条")


def downgrade() -> None:
    # 不还原 —— pending_review 和 executable 的区别（"跑绿了" vs "人发布了"）
    # 合并之后已经分不出来了。
    pass
