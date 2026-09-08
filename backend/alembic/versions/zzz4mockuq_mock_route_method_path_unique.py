"""mock_routes：给「方法+路径」建唯一索引（先归一 method、再去重）

Revision ID: zzz4mockuq
Revises: zzz3pedge

LLM Mock 的路由在运行时是按 (method, path) 二元组选中的（`_match_route` 里
method 用 `upper()` 做大小写不敏感比较，path 先精确后通配）。两条路由用同一个
「方法+路径」时，只有排在前面的那条会被命中，另一条永远被顶掉 —— 页面上却是两行，
看着像两条不同的 mock，改了后一条完全不生效，还偶发（谁在前取决于 sort_order）。

所以唯一键就该建在这个二元组上。分三步，缺一步都会让建索引失败或口径对不上：

  ① **归一 method 为大写。** 老数据可能混着 `post`/`POST`；运行时把它们当同一条，
     但一个 `unique(method, path)` 的普通索引会把它们当两条放过去。这里统一成大写，
     和运行时、和写入侧（service `_norm_method`）三处对齐。
  ② **去重。** 同一个 (upper(method), path) 只保留一条：按 sort_order → created_at
     → id 取最靠前的那条（也就是运行时真正会命中的那条），其余删掉。
     **删的是「反正也命中不到」的影子行**，安全的前提是：mock 路由没有任何外键指向它，
     测试场景/变量引用的是 **path 字符串**（它们照样打那个 path，留下的行会应答），
     不是路由 id —— 所以删影子行不会打断任何引用。
  ③ **建唯一函数索引** `upper(method), path`。用函数索引而不是普通列索引，是为了
     即便将来有人绕过写入归一直接塞一条小写 method，也照样被大小写不敏感地拦住 ——
     和运行时的比较口径完全一致。

模型侧 `MockRoute.__table_args__` 里声明了同名索引，`create_all` 建的测试库也有它；
这份迁移是给**已存在的库**补上（迁移是历史快照，不跟着后面的代码漂）。
"""
from alembic import op

revision = "zzz4mockuq"
down_revision = "zzz3pedge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ① method 统一大写 + 去掉 path 首尾空白（历史脏数据归一）
    op.execute("UPDATE mock_routes SET method = upper(trim(method)) WHERE method IS NOT NULL")
    op.execute("UPDATE mock_routes SET path = trim(path) WHERE path <> trim(path)")

    # ② 同一 (upper(method), path) 只留运行时会命中的那条，其余影子行删掉
    op.execute(
        """
        DELETE FROM mock_routes
        WHERE id IN (
            SELECT id FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY upper(method), path
                    ORDER BY sort_order ASC, created_at ASC, id ASC
                ) AS rn
                FROM mock_routes
            ) t WHERE t.rn > 1
        )
        """
    )

    # ③ 唯一函数索引：和运行时 upper(method) 的比较口径对齐
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_mock_routes_method_path "
        "ON mock_routes (upper(method), path)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_mock_routes_method_path")
