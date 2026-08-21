"""全局变量从全平台改成项目级

Revision ID: zzp0gvarproj
Revises: zzo0envproj

跟着 zzo0envproj（环境项目化）走完的第二步。上一批我把这张表判成"该留全局"，
理由是它是「所有环境都注入」的兜底层 —— **看数据就知道那个判断错了**。
库里 5 条全是**按项目调**的旋钮：

    API_TIMEOUT=30  BASE_WAIT=1000  LOG_LEVEL=INFO  RETRY_COUNT=3  TEST_LANGUAGE=zh

尤其 `TEST_LANGUAGE`（"测试跑哪种语言"）是被测系统的属性，一个项目跑中文、
另一个跑英文是常态，全平台一个值根本不够用。

**语义不变，只是换了作用域**：原来是「全平台所有环境的兜底层，环境变量可覆盖」，
现在是「本项目所有环境的兜底层，环境变量可覆盖」。那条覆盖关系一个字没动
（见 variable_service.build_run_env 和 TEST_LANGUAGE 自己的说明）。

**回填是"复制给每个项目"，不是"归给某个项目"。** 改动前所有项目看到的都是这 5 条，
所以按项目各存一份才是行为不变的那个选择；随便归给一个项目会让其余项目凭空丢掉
默认值（`TEST_LANGUAGE` 丢了会让 UI 脚本的 t() 取不到译文）。
5 条 × N 个项目，量很小。
"""
from alembic import op
import sqlalchemy as sa

revision = "zzp0gvarproj"
down_revision = "zzo0envproj"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    op.add_column("global_variables",
                  sa.Column("project_id", sa.dialects.postgresql.UUID(as_uuid=True),
                            nullable=True))

    # 先把 key 的全局唯一去掉，否则下面复制多份必然撞
    op.drop_constraint("global_variables_key_key", "global_variables", type_="unique")

    # 每个项目各复制一份（原行保持 project_id=NULL，随后删掉）
    conn.execute(sa.text("""
        insert into global_variables (id, project_id, key, value, description, sort_order)
        select gen_random_uuid(), p.id, g.key, g.value, g.description, g.sort_order
          from global_variables g cross join projects p
         where g.project_id is null
    """))
    conn.execute(sa.text("delete from global_variables where project_id is null"))

    op.alter_column("global_variables", "project_id", nullable=False)
    op.create_foreign_key("global_variables_project_id_fkey", "global_variables", "projects",
                          ["project_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_global_variables_project_id", "global_variables", ["project_id"])
    op.create_unique_constraint("uq_global_var_project_key", "global_variables",
                                ["project_id", "key"])


def downgrade() -> None:
    # 回退要把 N 份合并回一份：同名 key 取 project 里最早那个项目的值，其余丢掉。
    # 各项目改出了不同的值时，**回退必然丢数据** —— 这是项目化不可逆的那一半。
    conn = op.get_bind()
    op.drop_constraint("uq_global_var_project_key", "global_variables", type_="unique")
    conn.execute(sa.text("""
        delete from global_variables g
         where g.id not in (
            select distinct on (key) id from global_variables
             order by key, (select created_at from projects where id = project_id)
         )
    """))
    op.drop_index("ix_global_variables_project_id", table_name="global_variables")
    op.drop_constraint("global_variables_project_id_fkey", "global_variables", type_="foreignkey")
    op.drop_column("global_variables", "project_id")
    op.create_unique_constraint("global_variables_key_key", "global_variables", ["key"])
