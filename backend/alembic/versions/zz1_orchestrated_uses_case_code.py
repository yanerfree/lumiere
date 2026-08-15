"""编排场景改用用例编号，不再占 AT-#### 序列

为什么：一个用例 = 一条编排场景（按 source_case_id 幂等），它**没有独立身份**。
再发一个 AT-0011 就是给同一件东西起第二个名字，而那个号还是从「接口测试模块」的
序列里拿的 —— 人在用例详情里看到 AT-0011，去接口测试页面搜却搜不到（那个页面默认
只列单接口场景）。实测被直接问过：「为什么要单独一个 ID，不是和用例同一个 id 吗」。

改完之后：
- 编排场景 code = 用例编号（TC-FWGL-00001），跟着用例走
- AT-#### 序列只归单接口场景，两个模块的编号空间彻底分开

(branch_id, code) 的唯一约束仍然成立：用例编号在分支内本来就唯一，
而一个用例只有一条编排场景。

有冲突就跳过不改（比如同分支下已经有个单接口场景叫这个名字 —— 不该发生，
但撞了就宁可留着原来的 AT 号，也不要让迁移炸掉）。

Revision ID: zz1orch01
Revises: z0b1c2d3e4f5
"""
from alembic import op
import sqlalchemy as sa

revision = "zz1orch01"
down_revision = "z0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("""
        SELECT s.id, s.code AS old_code, s.branch_id, c.case_code
        FROM api_test_scenarios s
        JOIN cases c ON c.id = s.source_case_id
        WHERE s.source_case_id IS NOT NULL
          AND s.code <> c.case_code
        ORDER BY s.code
    """)).fetchall()

    for r in rows:
        clash = conn.execute(sa.text("""
            SELECT 1 FROM api_test_scenarios
            WHERE branch_id = :b AND code = :c AND id <> :i LIMIT 1
        """), {"b": str(r.branch_id), "c": r.case_code, "i": str(r.id)}).scalar()
        if clash:
            continue          # 撞了就别动，留原样比迁移失败好
        conn.execute(sa.text("UPDATE api_test_scenarios SET code = :c WHERE id = :i"),
                     {"c": r.case_code, "i": str(r.id)})


def downgrade() -> None:
    # 不还原 —— 还原要重新分配 AT 号，而分配依据（当初是第几个）已经没了。
    pass
