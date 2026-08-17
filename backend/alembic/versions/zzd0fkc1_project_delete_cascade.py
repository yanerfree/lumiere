"""删项目时把 7 张漏配级联的子表一起带走

`delete_project` 的注释写着「CASCADE 自动清理」，但实际只有一半外键配了
ON DELETE CASCADE。另外 7 张表是 NO ACTION，于是只要项目用过一次 AI、
跑过一次报告、存过一条知识，删除就撞外键约束，接口直接冒 500
（前端显示「服务内部错误」）。

实测 6 个项目里 4 个删不掉，只有从没用过的空项目能删 —— 也就是说这功能
基本是坏的，只是空项目掩盖了它。

口径：项目删掉就全部删掉（已与用户确认），所以 7 张全部改 CASCADE，
不做 SET NULL 保留。test_reports 本来就经 plan_id 级联删了大半，
project_id 这条只是把没挂 plan 的孤儿报告一起收掉。

Revision ID: zzd0fkc1
Revises: zzc0life1
"""
from __future__ import annotations

from alembic import op

revision = "zzd0fkc1"
down_revision = "zzc0life1"
branch_labels = None
depends_on = None

# (表名, 约束名)
_FKS = [
    ("ai_usage_logs", "ai_usage_logs_project_id_fkey"),
    ("api_test_scenarios", "api_test_scenarios_project_id_fkey"),
    ("documents", "documents_project_id_fkey"),
    ("exploratory_sessions", "exploratory_sessions_project_id_fkey"),
    ("knowledge_entries", "knowledge_entries_project_id_fkey"),
    ("project_ai_configs", "project_ai_configs_project_id_fkey"),
    ("test_reports", "test_reports_project_id_fkey"),
]


def upgrade() -> None:
    for table, name in _FKS:
        op.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS "{name}"')
        op.execute(
            f'ALTER TABLE {table} ADD CONSTRAINT "{name}" '
            f"FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE"
        )


def downgrade() -> None:
    for table, name in _FKS:
        op.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS "{name}"')
        op.execute(
            f'ALTER TABLE {table} ADD CONSTRAINT "{name}" '
            f"FOREIGN KEY (project_id) REFERENCES projects(id)"
        )
