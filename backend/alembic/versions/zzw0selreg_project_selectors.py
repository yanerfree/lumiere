"""项目级选择器登记表 project_selectors

Revision ID: zzw0selreg
Revises: zzv0lumren

脚本里的选择器从"每条脚本各写一份字面量"改成"项目登记一次、脚本写 ${SEL:键}"，
和文案词典（project_i18n_messages + ${键|中文}）完全同形。

起因是实测：本库 18 个 UI 脚本、125 处 page.locator(...)，只有 4 处用 testid，
其余大量是 `.card.card-pad` / `button.btn.sm.primary` / `span.chip.sm` / `.ant-modal`
这种**样式类** —— 前端改一次样式要逐条改脚本，改漏的那几条等下次回归红了才知道。

`status='gap'` 那一档是这张表的关键，不是可选项：
被测前端没有抓手时，正确动作是**去前端仓补 data-testid 提 MR**，
而不是在脚本里换个脆选择器凑合、或者干脆放弃这条 UI 用例。
没有这一档的话，「没抓手」是一个**免费且无痕**的借口 —— 说一次就没人再提起。
所以 gap 行**故意不存 selector**（存了下一个人会直接拿去用），
并由 lum_next_duty 的「待补 testid」/「回来写 UI」两个队列一直盯着，
直到 blocked_cases 里那几条用例真的有了 active 的 UI 脚本才出队。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "zzw0selreg"
down_revision = "zzv0lumren"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_selectors",
        sa.Column("id", sa.UUID(), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        # status='gap' 时为空 —— 见模块头，这是有意的
        sa.Column("selector", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False, server_default="style"),
        sa.Column("module", sa.String(64), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("gap_note", sa.Text(), nullable=True),
        sa.Column("blocked_cases", JSONB(), nullable=False, server_default="[]"),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "key", name="uq_selector_project_key"),
    )
    op.create_index("ix_project_selectors_project_id", "project_selectors", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_project_selectors_project_id", table_name="project_selectors")
    op.drop_table("project_selectors")
