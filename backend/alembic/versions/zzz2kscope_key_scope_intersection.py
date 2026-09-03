"""Key 级工具范围改成「项目范围 ∩ Key 范围」——**不改表，只做上线前的一次对账**

Revision ID: zzz2kscope
Revises: zzz1area

这次改动一个 DDL 都没有：`mcp_api_keys.allowed_tools` 列早就在（2025 年建的），
变的只是**怎么读它**：

    改之前：Key 归属了项目 → 只看项目范围，Key 上这一列**整个忽略**
    改之后：生效范围 = 项目范围 ∩ Key 范围（NULL = 跟随项目）

所以对**一种** Key 来说，同一份数据在新旧口径下会解析出不同结果：
`project_id IS NOT NULL` 且 `allowed_tools` 是**数组**的那种 —— 它原来被忽略的
那份收窄突然生效了，表现是「那把 Key 连上来莫名少掉一批工具」。少工具不报错，
CC 那边看到的只是"平台没有这个工具"，然后它会去挑一个别的工具凑，
排查起来要绕一大圈才回到这里。

所以这条迁移做的是**对账**：数出这种 Key 有几把，非 0 就停下来让人拍板。
2026-09-02 和 2026-09-03 两次实测都是 0 —— 范围挪到项目级那次
（`w7e8f9a0b1c2`）之后，PATCH 绑项目时会把 Key 上那份清成 NULL，
所以库里根本攒不下这种行。这条对账是为了「万一有」，不是为了迁移数据。

⚠ 判据必须是 `jsonb_typeof(allowed_tools) = 'array'`，不能写 `IS NOT NULL`：
   这一列的历史值大量是 jsonb 的 **`null` 标量**（不是 SQL NULL），
   `IS NOT NULL` 对它们全为真 —— 今天库里 3 把 Key 全是这种，
   写成 `IS NOT NULL` 会把 0 数成 3，然后这条迁移在一个干净的库上直接拦住上线。
   `jsonb_array_length()` 也不能直接用：碰上标量会**抛错**而不是返回 0。

数出来非 0 怎么办（两条都可以，选一条）：
  · 想保持原样（那些 Key 继续跟随项目范围）：
        update mcp_api_keys set allowed_tools = null
         where project_id is not null and jsonb_typeof(allowed_tools) = 'array';
  · 认了这份收窄（确认过那几把 Key 就该只有这些工具）：
        带 LUMIERE_KEY_SCOPE_ACK=1 再跑一次 alembic upgrade
迁移本身**一个字都不改数据** —— 清哪几把、认哪几把，是人的决定。
"""
import os

from alembic import op

revision = "zzz2kscope"
down_revision = "zzz1area"
branch_labels = None
depends_on = None


_COUNT_SQL = """
select count(*) from mcp_api_keys
 where project_id is not null
   and jsonb_typeof(allowed_tools) = 'array'
"""

_LIST_SQL = """
select name, key_prefix, is_active, jsonb_array_length(allowed_tools) as n
  from mcp_api_keys
 where project_id is not null
   and jsonb_typeof(allowed_tools) = 'array'
 order by created_at
"""


def upgrade() -> None:
    conn = op.get_bind()
    n = conn.exec_driver_sql(_COUNT_SQL).scalar() or 0
    if n == 0:
        print("[zzz2kscope] 对账通过：没有 Key 会因为口径变化少掉工具。")
        return
    rows = conn.exec_driver_sql(_LIST_SQL).fetchall()
    detail = "\n".join(
        f"    · {r[0]}（{r[1]}，{'启用' if r[2] else '已停用'}）"
        f" Key 上勾了 {r[3]} 个工具，此前被忽略、现在会和项目范围求交集"
        for r in rows
    )
    if os.getenv("LUMIERE_KEY_SCOPE_ACK") == "1":
        print(f"[zzz2kscope] 已确认（LUMIERE_KEY_SCOPE_ACK=1），{n} 把 Key 转为交集口径：\n{detail}")
        return
    raise RuntimeError(
        f"[zzz2kscope] 有 {n} 把 Key 归属了项目、同时自己还存着一份工具范围。\n"
        f"新口径下这份范围会开始生效（生效 = 项目范围 ∩ Key 范围），"
        f"那几把 Key 连上来会少掉一批工具，而且不报错：\n{detail}\n"
        "  想保持原样：update mcp_api_keys set allowed_tools = null "
        "where project_id is not null and jsonb_typeof(allowed_tools) = 'array';\n"
        "  确认就要这份收窄：带 LUMIERE_KEY_SCOPE_ACK=1 重跑 alembic upgrade。\n"
        "（这条迁移不动数据，只拦一下 —— 详见文件头的说明。）"
    )


def downgrade() -> None:
    """没有 DDL 可回退。口径回退靠代码回滚，不靠这里。"""
