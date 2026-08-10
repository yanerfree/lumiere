"""P0 确认从"平台拦人"改成"CC 带记录回推"

原来的做法是平台硬拦：P0 不许一次性出三件套，CC 被拦住 → 人切到平台页面 →
找到那条用例 → 点「确认预期结果」→ CC 再回来挂。每条 P0 都走一趟，是实打实的税。

而且支撑这道门禁的那个数（80% 断言退化）测的是**平台自己的 AI 生成器**，
不是 CC —— 拿它去拦 CC，推断跨得太快。

改成：确认发生在人已经在的地方（CC 对话里），CC 把确认内容一起带上来，
平台**只存不拦**。留痕靠记录，不靠拦截 —— 反正人在页面上点一下按钮，
同样验证不了他真读了，两边都是形式，那就选便宜的那个。

新增两列（`expected_confirmed_at` 已有）：
- expected_confirmed_actor —— 谁确认的（自由文本；CC 侧填人名，页面确认填当前用户）
- expected_confirmed_note  —— 确认了什么（CC 把对话里那句原话带上来）

Revision ID: v6d7e8f9a0b1
Revises: u5c6d7e8f9a0
"""
from alembic import op
import sqlalchemy as sa

revision = "v6d7e8f9a0b1"
down_revision = "u5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("expected_confirmed_actor", sa.String(100), nullable=True))
    op.add_column("cases", sa.Column("expected_confirmed_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("cases", "expected_confirmed_note")
    op.drop_column("cases", "expected_confirmed_actor")
