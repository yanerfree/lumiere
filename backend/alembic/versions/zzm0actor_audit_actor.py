"""audit_logs 加「操作来源」：是谁的哪把 Key 干的

Revision ID: zzm0actor
Revises: zzl0fticket

为什么不靠 user_id 就够：建 Key 的接口写死 `user_id=current_user.id`，
所有 CC 的 Key 都是同一个人（admin）建的，于是所有 CC 操作在日志里长得一模一样。
Key 上本来就有个人写的名字（"uag-cc使用"、"小李的开发机"），把它落到日志里，
「哪台 CC 改的」才有答案 —— trace_id 只说得出调了哪个工具，说不出是谁的连接。

actor_type 单独存而不从 trace_id 的 "mcp:" 前缀反推：来源是要拿来筛的维度，
反推等于把显示逻辑写进字符串前缀，以后加别的机器来源（定时任务/CI）就得改解析。
"""
from alembic import op
import sqlalchemy as sa

revision = "zzm0actor"
down_revision = "zzl0fticket"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 都可空：存量日志没有来源信息，不编造 —— 页面按 NULL 显示成「人工」之外的空白
    op.add_column("audit_logs", sa.Column("actor_type", sa.String(20), nullable=True))
    op.add_column("audit_logs", sa.Column("actor_label", sa.String(100), nullable=True))
    op.create_index("ix_audit_logs_actor_type", "audit_logs", ["actor_type"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_actor_type", table_name="audit_logs")
    op.drop_column("audit_logs", "actor_label")
    op.drop_column("audit_logs", "actor_type")
