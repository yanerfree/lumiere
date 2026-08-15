"""清掉无主接口场景，并让「无主」从此不可能出现

Revision ID: zz9orph1
Revises: zz8jnull1
Create Date: 2026-08-15

「接口测试」模块（单接口·凭文档 AI 造）下线之后，`api_test_scenarios` 里
`source_case_id IS NULL` 的那些**不再有任何功能会产出，也没有任何页面会展示**，
但它们仍会被执行捞取、仍会落进 report_type='api_test' 报告稀释通过率 ——
关掉窗口反而让这个污染更隐蔽。所以存量要清。

清的是什么（下线时实查 47 条 / 259 步）：
  · 30 条 2026-07-07 与 07-09 对 testBench 自己接口的 dogfood，**同一批建了两遍、
    一字未改**，全停 draft
  · 10 条 probe-* / 创建资源-* 探针垃圾
  ·  7 条曾绑用例、用例被删后残留的孤儿（就是下面要堵的那个洞造出来的）

**为什么顺手改外键**：`fk_api_scenario_source_case` 原来是 ON DELETE SET NULL ——
删一条用例，它的接口场景不会消失，只是把 source_case_id 置空，于是**降级成孤儿**。
那 7 条就是这么来的。`case_service` 的彻底删除路径里补过一句 sa_delete，
但那只堵住了一条路径，别的路径（和直接动库）照样漏。

改成 CASCADE + NOT NULL 之后，「场景必须属于某条用例」才从约定变成不变量：
用例删了场景跟着走，谁也写不出一条无主场景。这两个改动**必须一起做** ——
只加 NOT NULL 不改 SET NULL 的话，删用例会直接撞非空约束报错。

不可逆。执行前的备份口径见 docs/cc-platform-loop-spec.md §11。
"""
from alembic import op
import sqlalchemy as sa

revision = "zz9orph1"
down_revision = "zz8jnull1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ① 记下这次会被波及的目录，**删场景之前**记 —— 删完就查不出它们原来装过东西了
    hit = [r[0] for r in conn.execute(sa.text(
        "SELECT DISTINCT folder_id FROM api_test_scenarios "
        "WHERE source_case_id IS NULL AND folder_id IS NOT NULL"
    ))]

    # ② 清存量。步骤靠 api_test_steps.scenario_id 的 ON DELETE CASCADE 跟着走
    n = conn.execute(sa.text(
        "DELETE FROM api_test_scenarios WHERE source_case_id IS NULL"
    )).rowcount
    print(f"[zz9orph1] 删除无主接口场景 {n} 条")

    # ③ 只回收**因此变空**的目录。从没装过场景的不碰 —— 那可能是人先搭好的结构，
    #    这条纪律和用例目录那边保持一致（见 spec §8.5「空目录 51/93」）
    if hit:
        m = conn.execute(sa.text("""
            DELETE FROM api_test_folders f
            WHERE f.id = ANY(:ids)
              AND NOT EXISTS (SELECT 1 FROM api_test_scenarios s WHERE s.folder_id = f.id)
              AND NOT EXISTS (SELECT 1 FROM api_test_folders c WHERE c.parent_id = f.id)
        """), {"ids": hit}).rowcount
        print(f"[zz9orph1] 回收因此变空的目录 {m} 个")

    # ④ 堵住孤儿工厂：SET NULL → CASCADE
    op.drop_constraint("fk_api_scenario_source_case", "api_test_scenarios", type_="foreignkey")
    op.create_foreign_key(
        "fk_api_scenario_source_case", "api_test_scenarios", "cases",
        ["source_case_id"], ["id"], ondelete="CASCADE",
    )

    # ⑤ 让「无主」不可能再出现
    op.alter_column("api_test_scenarios", "source_case_id", nullable=False)


def downgrade() -> None:
    # 数据回不来（清掉的场景不再重建），只把约束放回去，
    # 让老代码路径不至于因为非空约束直接崩。
    op.alter_column("api_test_scenarios", "source_case_id", nullable=True)
    op.drop_constraint("fk_api_scenario_source_case", "api_test_scenarios", type_="foreignkey")
    op.create_foreign_key(
        "fk_api_scenario_source_case", "api_test_scenarios", "cases",
        ["source_case_id"], ["id"], ondelete="SET NULL",
    )
