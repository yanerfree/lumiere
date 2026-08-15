"""删掉「接口测试」模块留下的三个死列

Revision ID: zza0dead1
Revises: zz9orph1
Create Date: 2026-08-15

模块下线（§11）之后逐列盘过一遍"还有谁读它"，这三个一个读者都没有：

· `source_api_ids` —— 关联接口库节点。唯一写入方是生成服务的 `api_ids` 参数，
  而那个参数只有模块的生成弹窗会传，前端从来没传过 → 落库恒为 None。
  分支复制里那段"接口也被复制时把 id 重映射"因此也是空转，一并删。

· `edited_after_generate` —— AI 生成后有没有被人改过。唯一读者是
  `GET /stats/quality`（生成质量度量），那个端点全仓零消费方、已随模块删除。
  留着就是个只写不读的位。

· `pre_steps` —— 场景级前置操作（如 auth）。**执行器从头到尾不读它**，
  只有 API 的读写字典里进出。也就是说这个字段配了也不会发生任何事，
  比没有更糟 —— 人会以为配了就生效。

留下没动的（有真读者，别顺手删）：
  `folder_id` → 多场景执行时用来给报告命名（_resolve_common_folder_name）
  `env_variables` → 执行器合并变量时读（api_test_runner 第一优先级）
  `status` / `source` → API 与 MCP 列表回显

不可逆（列里的数据一起没）。但这三列现存值分别是：全 None、全 false、全 None。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "zza0dead1"
down_revision = "zz9orph1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("api_test_scenarios", "source_api_ids")
    op.drop_column("api_test_scenarios", "edited_after_generate")
    op.drop_column("api_test_scenarios", "pre_steps")


def downgrade() -> None:
    op.add_column("api_test_scenarios", sa.Column("pre_steps", JSONB, nullable=True))
    op.add_column("api_test_scenarios", sa.Column(
        "edited_after_generate", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("api_test_scenarios", sa.Column("source_api_ids", JSONB, nullable=True))
