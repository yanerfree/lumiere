"""接口场景编号：先给已撞号的重编，再加 (branch_id, code) 唯一约束

为什么需要：编号本来该是分支内唯一的定位符，但
- 「用例编排」那边用 `max(code)+1`（sync.py）
- 「接口测试模块」那边用 **`count()+1`**（api_scenario_gen_service.py）

count() 一旦有删除就会撞上已存在的号，而 `code` 上**没有唯一约束**，撞了不报错。
实测已经撞出来了：某分支的 AT-0024 / AT-0027 各有两条。撞号之后按编号定位、
按编号对话（"AT-0024 那条挂了"）全是错的，而且没有任何提示。

分配逻辑已改成两边都 max+1；这里补数据修复和约束，让它撞不了。

重编规则：同一 (branch_id, code) 下按 created_at 保留最早那条的编号，
其余的追加到该分支当前最大号之后 —— **不动最早那条**，因为外部（报告、
对话记录、文档）引用的多半是先出现的那个。

Revision ID: y9a0b1c2d3e4
Revises: x8f9a0b1c2d3
"""
from alembic import op
import sqlalchemy as sa

revision = "y9a0b1c2d3e4"
down_revision = "x8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1) 找出撞号的：同分支同 code 有多条。保留最早的，其余重编。
    dupes = conn.execute(sa.text("""
        SELECT id, branch_id, code
        FROM (
            SELECT id, branch_id, code,
                   ROW_NUMBER() OVER (PARTITION BY branch_id, code ORDER BY created_at, id) AS rn
            FROM api_test_scenarios
        ) t
        WHERE rn > 1
        ORDER BY branch_id, code, id
    """)).fetchall()

    # 每个分支当前的最大号，重编从它往后接
    next_seq: dict[str, int] = {}
    for row in dupes:
        bid = str(row.branch_id)
        if bid not in next_seq:
            cur = conn.execute(sa.text("""
                SELECT MAX(CAST(SUBSTRING(code FROM 'AT-([0-9]+)$') AS INTEGER))
                FROM api_test_scenarios
                WHERE branch_id = :bid AND code ~ '^AT-[0-9]+$'
            """), {"bid": bid}).scalar()
            next_seq[bid] = int(cur or 0) + 1
        new_code = f"AT-{next_seq[bid]:04d}"
        next_seq[bid] += 1
        conn.execute(sa.text(
            "UPDATE api_test_scenarios SET code = :c WHERE id = :i"
        ), {"c": new_code, "i": str(row.id)})

    # 2) 加唯一约束。到这里应该已经没有撞号了；万一还有（比如非 AT- 前缀的
    #    历史数据撞了），让它在迁移时报错，别把问题留到运行期静默发生。
    op.create_unique_constraint(
        "uq_api_scenario_branch_code", "api_test_scenarios", ["branch_id", "code"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_api_scenario_branch_code", "api_test_scenarios", type_="unique")
    # 重编过的号不还原 —— 还原回撞号状态没有意义，而且分不清哪些是这次改的。
