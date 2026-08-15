"""删掉 api_scenario_status / ui_scenario_status —— 和三维完全重复

`apply_case_status` 一直**同时写两套**（维度状态 + 这两个），说的是同一件事。
实测 255 条里 0 处不一致 —— 也就是说这两列从来没提供过任何额外信息，
只是多了一处会漏写的地方：两个字段表达一件事，迟早有一处漏写就开始互相矛盾，
而那时没人知道该信哪个。详情页上还各有一个下拉，改了一处另一处不动。

`automation_status` **不删**：它有 3 条活数据（`automated` + `script_ref_file`
指向仓库里的旧式脚本），删了那 3 条就跑不了。等它们迁到 scripts 表再说。

Revision ID: zz5dropss
Revises: zz4smart1
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "zz5dropss"
down_revision = "zz4smart1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("cases", "api_scenario_status")
    op.drop_column("cases", "ui_scenario_status")


def downgrade() -> None:
    op.add_column("cases", sa.Column("api_scenario_status", sa.String(20),
                                     nullable=False, server_default="draft"))
    op.add_column("cases", sa.Column("ui_scenario_status", sa.String(20),
                                     nullable=False, server_default="draft"))
    # 回填成和三维一致 —— 它们本来就该一致
    conn = op.get_bind()
    for col, dim in (("api_scenario_status", "api_status"), ("ui_scenario_status", "ui_status")):
        conn.execute(sa.text(f"UPDATE cases SET {col} = {dim}"))
