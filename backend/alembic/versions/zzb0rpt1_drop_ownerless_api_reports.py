"""删掉无主场景留下的历史接口报告

Revision ID: zzb0rpt1
Revises: zza0dead1
Create Date: 2026-08-15

`report_type='api_test'` 的报告里，有一批条目的 `case_id` 是空 —— 它们是
「接口测试」模块里点「运行」跑那些无主场景留下的。模块下线、场景清掉之后，
这些记录**指向的东西全都不存在了**：点不进用例、找不到场景，只剩一行标题。

它们不是无害的历史：`api_test` 报告 88 条里有 73 条**整条都是无主场景**，
通过率 47.9%（绑用例的那批是 66.7%），把接口测试的整体通过率从 66.7% 压到 55%。
通过率是质量门禁的输入，输入里混着一批"结构上就跑不了、现在连场景都没了"的失败，
这个数字就不说明任何事。

删的范围（保守，只删确定无主的）：
  · `test_report_scenarios` 里 case_id 为空的条目，及其步骤
  · 因此变空的 `test_reports`（整条报告的条目全是无主的那 73 条）
**混合报告不删** —— 只要有一条条目绑着用例，那条报告就还在说明某个用例的历史，
只把它里面无主的条目摘掉。

不可逆。
"""
from alembic import op
import sqlalchemy as sa

revision = "zzb0rpt1"
down_revision = "zza0dead1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ① 步骤先走（test_report_steps.scenario_id 指向 test_report_scenarios）
    n_step = conn.execute(sa.text("""
        DELETE FROM test_report_steps
        WHERE scenario_id IN (
            SELECT rs.id FROM test_report_scenarios rs
            JOIN test_reports r ON r.id = rs.report_id
            WHERE r.report_type = 'api_test' AND rs.case_id IS NULL
        )
    """)).rowcount

    n_sc = conn.execute(sa.text("""
        DELETE FROM test_report_scenarios rs
        USING test_reports r
        WHERE r.id = rs.report_id AND r.report_type = 'api_test' AND rs.case_id IS NULL
    """)).rowcount

    # ② 再收因此变空的报告。**只收 api_test 的空报告** ——
    #    别的类型出现空报告是另一回事，不该被这个迁移顺手带走。
    n_rpt = conn.execute(sa.text("""
        DELETE FROM test_reports r
        WHERE r.report_type = 'api_test'
          AND NOT EXISTS (SELECT 1 FROM test_report_scenarios rs WHERE rs.report_id = r.id)
    """)).rowcount

    print(f"[zzb0rpt1] 删除无主报告条目 {n_sc} 条（步骤 {n_step} 行），"
          f"回收因此变空的报告 {n_rpt} 条")


def downgrade() -> None:
    # 历史记录删了就回不来。这里不做任何事，免得给人"能回滚"的错觉。
    pass
