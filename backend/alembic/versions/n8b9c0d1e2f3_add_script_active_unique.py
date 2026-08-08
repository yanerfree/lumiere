"""scripts 表：同一用例同一类型只能有一个 active 脚本（B0）

`create_script` 是"把旧的标 archived + 插一条新的 active"。已有的
`uq_script_case_type_version` 只管 (case_id, script_type, version) 唯一，
**不管 active 只能有一个** —— 两个人同时回推同一条用例，可能留下两个 active，
之后 `get_active_script` 拿到哪个就看运气了，而且这种错很难复现、很难发现。

部分唯一索引把它变成数据库层面不可能发生的事。
迁移前已确认全库无重复 active。

Revision ID: n8b9c0d1e2f3
Revises: m7a8b9c0d1e2
Create Date: 2026-08-08
"""
from alembic import op

revision = "n8b9c0d1e2f3"
down_revision = "m7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE UNIQUE INDEX uq_script_one_active
        ON scripts (case_id, script_type)
        WHERE status = 'active'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_script_one_active")
