"""删掉条件应答（match_enabled / match_rules）—— 跟智能应答重复

一条路由上同时挂两套「按请求内容决定回什么」，页面上是两个开关、两块配置，
而「这次到底是谁决定了响应」只能靠猜。智能应答的指令契约已经覆盖了实际在用的
那几个场景（HIT/PII/EMPTY/FILTER/DEFY/SLOW/LOOP + SAY 原样回显），保留哪个都行，
但不能两个都留。

一并删掉的还有内置那条「测试用例生成」规则（关键词命中就回一段用例 JSON）。
它没有任何代码依赖 —— 全仓库只有 llm_mock_engine.py 自己引用 _MOCK_CASES_JSON。
唯一的行为变化是：把平台自己的 AI 配置指向这个 Mock 时，问「测试用例」不再回
用例 JSON，而是回路由上配的那段响应内容。

⚠ 这一步不可逆：路由上自定义的关键词规则会随列一起没掉。降级只恢复空表，
恢复不了内容。

Revision ID: zz6dropmr
Revises: zz6tlreason
Create Date: 2026-08-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "zz6dropmr"
# 接在 zz6tlreason 后面：它和本条都是从 zz5dropss 分叉出来的（两个人并行改），
# 谁先应用谁在前，别留成两个 head —— 多头之后 `alembic upgrade head` 会直接报错罢工
down_revision = "zz6tlreason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("mock_routes", "match_rules")
    op.drop_column("mock_routes", "match_enabled")


def downgrade() -> None:
    # 列能加回来，规则内容加不回来 —— 降级后所有路由都是空规则表
    op.add_column(
        "mock_routes",
        sa.Column("match_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "mock_routes",
        sa.Column("match_rules", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
