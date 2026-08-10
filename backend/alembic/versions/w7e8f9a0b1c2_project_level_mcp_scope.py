"""MCP 工具范围从 Key 级挪到项目级

## 为什么改

原来范围存在 Key 上（`mcp_api_keys.allowed_tools`），于是每建一个 Key 都要
重新选一遍范围，改范围也得一个 Key 一个 Key 改。实际用起来的心智是
「**这个项目**允许 CC 干哪些活」，不是「这一把钥匙允许干哪些活」——
同一个项目发五把 Key 给五个人，范围本来就该是同一个。

## 为什么 Key 必须先有 project_id

「本项目的所有 Key 都按这个范围生效」要求存在 Key → 项目 这条边，而原来没有：
`mcp_api_keys` 只有 `user_id`。更关键的是 **`tools/list` 发生在连接建立时，
那时候还没有任何 project_id 参数**（project_id 是各个工具自己的入参）——
所以范围只能从 Key 本身查出项目，没有别的路。

## 存量怎么办：不动

- `project_id` 可空。旧 Key 为 NULL，解析时**走原来的 key.allowed_tools 那条路**，
  行为一字不变。不做"猜一个项目塞进去"的回填 —— 猜错了是静默改权限。
- `allowed_tools` 列**保留**。它现在是遗留通道（只对未归属项目的 Key 生效），
  不是死列。页面上不再暴露编辑入口，但已经设过范围的那把 Key 不会被悄悄放开。
- 项目上的 `mcp_allowed_tools` 同样 NULL = 不限制，和 Key 级的语义完全一致，
  所以中间件那句"NULL → 不限制"不用改写两套。

## 为什么加在 projects 上而不是单开一张表

`automation_resources` / `project_i18n_messages` 那两张项目级表是**一个项目多行**，
需要 (project_id, 自然键) 唯一约束。这里是一个项目一份配置，开表就得靠
unique(project_id) 去模拟单行，还多一次 join —— 而中间件那条查询在连接热路径上。
一个可空 JSONB 列表达得完整，就不绕。

Revision ID: w7e8f9a0b1c2
Revises: n8g9h0i1j2k3
Create Date: 2026-08-10
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "w7e8f9a0b1c2"
down_revision = "n8g9h0i1j2k3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 项目级工具范围：NULL = 不限制（全部工具），列表 = 只暴露这些工具名。
    op.add_column(
        "projects",
        sa.Column("mcp_allowed_tools", postgresql.JSONB(), nullable=True),
    )
    # Key 归属项目。可空 —— 存量 Key 保持"不属于任何项目"，走遗留解析路径。
    # ondelete SET NULL 而不是 CASCADE：项目删了不该把别人手里的 Key 一起吊销，
    # 让它退回"未归属"状态、由人去处理，比凭空失效可解释。
    op.add_column(
        "mcp_api_keys",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_mcp_api_keys_project_id", "mcp_api_keys", "projects",
        ["project_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_mcp_api_keys_project_id", "mcp_api_keys", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_mcp_api_keys_project_id", table_name="mcp_api_keys")
    op.drop_constraint("fk_mcp_api_keys_project_id", "mcp_api_keys", type_="foreignkey")
    op.drop_column("mcp_api_keys", "project_id")
    op.drop_column("projects", "mcp_allowed_tools")
