"""mock_routes：智能应答（可控假上游的指令契约）

被测系统是 AI 网关，测它的护栏 / 脱敏 / fail-closed / 计费统计都要一个**行为可精确
控制的假上游**。原有的条件应答规则表（match_rules）能覆盖大部分场景，但它的响应体是
**静态串**，凡是「响应内容依赖请求内容」的一律做不到：

  · 护栏检查模型要回显「本次待检正文有多长、开头是什么」—— 必须算
  · MODE:LOOP 要跨轮判断（有没有 role=tool 消息），第一轮回 tool_calls、第二轮回终局
  · MODE:SLOW 非流式也要按分片数累计 sleep
  · MODE:FILTER 要改 finish_reason

这四条归 smart_enabled 这个模式管，其余照旧走规则表。

⚠ 这不是 m7f8a9b0c1d2 里那个被拆掉的 smart_response 黑盒 bool 的回归：
那个的关键词和响应全写死在引擎里、页面上看不见改不了，所以被 n8g9h0i1j2k3 拆成了
可见可编辑的规则表。这一版页面上有指令契约面板（看得见它会干什么），还有「展开成规则」
按钮把能用规则表达的那几条落地成 match_rules —— 随时能拿回控制权。

smart_enabled 默认 **false**：存量路由行为逐字节不变。

Revision ID: zz4smart1
Revises: zz3dim3
Create Date: 2026-08-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "zz4smart1"
down_revision = "zz3dim3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mock_routes",
        sa.Column("smart_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    # auto = 按路径判（含 /checker、/guard 的当护栏检查模型）；也可显式指定
    op.add_column(
        "mock_routes",
        sa.Column("smart_role", sa.String(length=20), nullable=False, server_default="auto"),
    )
    # 护栏提示模板里「待检正文」的定位标记。空 = 用内置默认（Text to check: 及几种常见变体）。
    # 模板换了自己改这一格，不用改代码。
    op.add_column(
        "mock_routes",
        sa.Column("smart_body_marker", sa.String(length=200), nullable=True),
    )
    # 这次请求被判成了什么：指令 / 协议形状 / 角色 / stream 实际值 / loop 阶段 /
    # 待检正文与信封各自多长 / 护栏判决。对照实验的证据就靠它，日志详情页直接显示。
    op.add_column(
        "mock_request_logs",
        sa.Column("smart_meta", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mock_request_logs", "smart_meta")
    op.drop_column("mock_routes", "smart_body_marker")
    op.drop_column("mock_routes", "smart_role")
    op.drop_column("mock_routes", "smart_enabled")
