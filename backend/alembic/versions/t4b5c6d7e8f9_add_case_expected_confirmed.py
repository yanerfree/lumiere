"""cases 加「预期结果已确认」—— 把 P0 两阶段的第二阶段补上

`intake_gate.check_p0_two_phase` 的设计是：P0 先只回推步骤用例，人确认
「预期结果」那一列之后，再补接口和 UI。但 `has_confirmed_expected` 在唯一的
调用点写死成 `False`（`mcp/tools/test_cases.py`），也没有任何地方能让人去确认 ——
所以第二阶段从来不存在，门禁只是"P0 不许声明 target_level=full"。

实测（真 MCP 连接）：改成 target_level=spec 建 P0 → 放行；紧接着
tb_sync_orchestrated_scenario + tb_sync_ui_script 全部成功，中间没有任何人确认过。
**三件套照样同源直出，门禁拦的只是一次调用里的声明。**

补上这两列，让"人确认过没有"成为可查的事实：
- expected_confirmed_at 有值 = 有人逐条看过「预期结果」这一列并认可
- 改动步骤或预期结果会把它清掉 —— 确认的是当时那一版，不是这条用例的终身通行证

Revision ID: t4b5c6d7e8f9
Revises: s3a4b5c6d7e8
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "t4b5c6d7e8f9"
down_revision = "s3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("expected_confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("cases", sa.Column("expected_confirmed_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_cases_expected_confirmed_by", "cases", "users",
        ["expected_confirmed_by"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_cases_expected_confirmed_by", "cases", type_="foreignkey")
    op.drop_column("cases", "expected_confirmed_by")
    op.drop_column("cases", "expected_confirmed_at")
