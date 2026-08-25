"""AI 用量记账允许全局调用（project_id 可空）

Revision ID: zzr0aiusage
Revises: zzs0rvhash

「AI 能力 → 模型」那页要回答"哪些 AI 入口真被用过"。原来只有三条链路记账，
补齐剩下四条时撞上一件事：**工具箱的正则生成是全局功能**（不属于任何项目，
`resolve_ai_config(None, ...)`），而 `ai_usage_logs.project_id` 是 NOT NULL。

不放开就只有两个选择：给全局调用编一个项目 ID（假账），或者干脆不记
（页面会说"正则生成从没被调用过"，而那是错的）。**「没被数」显示成「没被用」
是会误删功能的** —— 用户已经照着旧页面得出过"其他 AI 都没用到"的结论。

放开约束不影响任何现有读取：项目维度的用量统计（/api/projects/{id}/ai-usage）
本来就 `where project_id = :id`，NULL 行天然不进任何项目的账。
"""
from alembic import op

revision = "zzr0aiusage"
down_revision = "zzs0rvhash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("ai_usage_logs", "project_id", nullable=True)


def downgrade() -> None:
    # 回滚前得先清掉全局行，否则 NOT NULL 加不回去
    op.execute("DELETE FROM ai_usage_logs WHERE project_id IS NULL")
    op.alter_column("ai_usage_logs", "project_id", nullable=False)
