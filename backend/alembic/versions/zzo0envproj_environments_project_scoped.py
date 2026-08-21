"""环境从全局改成项目级

Revision ID: zzo0envproj
Revises: zzn0rvmode

为什么改：`environments` 表原本没有 project_id、`name` 还是**全局 unique**。
结果是大家在用「名字里塞项目前缀」手动模拟隔离 —— 实测库里 8 条环境，
`stoa` / `uag` / `api-test-local` / `测试平台self` 四条明显是按项目取的名，
变量也各自成一摊（16 / 15 / 3 / 7 个）。这就是 scoping 放错层的典型信号。
而且全局 unique 意味着两个项目都想有个 `staging` 就撞。

背景、决定和不改哪些表：docs/data-scoping-and-isolation.md §4。
（同批盘点里 Mock 那一族和压测**故意不改** —— 它们是真·全局工具，见该文档 §5。）

**回填按实际用法来，不按名字猜。** 三趟：
  ① 谁真在用它 —— `plans.environment_id` / `test_reports.environment_id` 指向它的
     那些行属于哪个项目，就归哪个项目（并列时取行数多的那个）。
  ② 还剩下的，按「环境名和项目名对得上」归（大小写不敏感）。
  ③ 再剩下的（从没被引用、名字也对不上）归最早建的那个项目 —— 要的是**确定**，
     不是聪明。归错了人在页面上一眼能看出来并改掉；留着 NULL 则谁都看不见它。

**一种情形会删数据：** 库里压根没有任何项目（全新安装 + 种子环境）。
那种环境在新模型里永远打不开（环境页在项目壳里面），留着只是一批归不了属的孤儿。
只在 `projects` 表为空时触发，正常库走不到这条路。
"""
from alembic import op
import sqlalchemy as sa

revision = "zzo0envproj"
down_revision = "zzn0rvmode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    op.add_column("environments",
                  sa.Column("project_id", sa.dialects.postgresql.UUID(as_uuid=True),
                            nullable=True))

    # ① 按实际引用回填。plans 和 test_reports 各投一票，票多者得；
    #    完全并列时按 project_id 排序取第一个 —— 只为让结果可复现。
    conn.execute(sa.text("""
        with votes as (
            select environment_id as env, project_id as proj, count(*) as n
              from plans where environment_id is not null and project_id is not null
             group by 1, 2
            union all
            select environment_id, project_id, count(*)
              from test_reports where environment_id is not null and project_id is not null
             group by 1, 2
        ), tally as (
            select env, proj, sum(n) as n,
                   row_number() over (partition by env order by sum(n) desc, proj) as rk
              from votes group by env, proj
        )
        update environments e set project_id = t.proj
          from tally t where t.env = e.id and t.rk = 1
    """))

    # ② 名字对得上项目名的
    conn.execute(sa.text("""
        update environments e set project_id = p.id
          from projects p
         where e.project_id is null and lower(p.name) = lower(e.name)
    """))

    # ③ 兜底：最早建的项目
    conn.execute(sa.text("""
        update environments set project_id = (select id from projects order by created_at limit 1)
         where project_id is null
    """))

    # 只有「库里没有任何项目」才可能还剩 NULL，见文档字符串
    left = conn.execute(sa.text(
        "select count(*) from environments where project_id is null")).scalar()
    if left:
        conn.execute(sa.text("delete from environments where project_id is null"))

    op.alter_column("environments", "project_id", nullable=False)
    op.create_foreign_key("environments_project_id_fkey", "environments", "projects",
                          ["project_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_environments_project_id", "environments", ["project_id"])

    # 全局 unique 换成项目内 unique。原约束名是建表时 PG 自动生成的 environments_name_key。
    op.drop_constraint("environments_name_key", "environments", type_="unique")
    op.create_unique_constraint("uq_environment_project_name", "environments",
                                ["project_id", "name"])


def downgrade() -> None:
    # 注意：回退会重新要求 name 全局唯一。项目化之后如果两个项目各建了同名环境
    # （这正是本次改动要支持的事），这一步会失败 —— 那是真冲突，只能先人工改名。
    op.drop_constraint("uq_environment_project_name", "environments", type_="unique")
    op.create_unique_constraint("environments_name_key", "environments", ["name"])
    op.drop_index("ix_environments_project_id", table_name="environments")
    op.drop_constraint("environments_project_id_fkey", "environments", type_="foreignkey")
    op.drop_column("environments", "project_id")
