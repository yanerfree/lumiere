"""角色收敛：系统 3 档（admin/user/guest）+ 项目 2 档（manager/member）

Revision ID: zzx0role3
Revises: zzw0roleck

2026-08-29 重定（见 docs/permission-audit-2026-08.md「2026-08-29 决策」）：

- 系统角色 operator **删除**。它自报 project.read 却没有任何强制路径认它
  （登录后看到 0 个项目），是个空壳 → 一律迁成 user。
- 项目角色从 7 个名（新 3 + 旧 4）收成 2 个：project_admin→manager，
  developer|tester|viewer|guest→member。
- **只读语义从项目层上移到账号层**：原来靠项目 viewer/guest 表达的「只读」，
  现在由系统角色 guest 的硬封顶承担（core/permissions.SYSTEM_ROLE_CEILING
  + core/readonly_gate 的非 GET 闸门）。

因此 viewer/guest → member 这一步**单看是提权**，必须配套把「哪儿都只读」的人
降成系统 guest，两步一起做才是等价迁移。本迁移的顺序就是这么排的（先降人，再改角色名）。

**两处刻意不"自动处理"的地方：**

1. **混合读写的人直接中止，不猜。** 一个用户在 A 项目只读、在 B 项目可写，
   2 档模型表达不了。降成 guest 会夺走他在 B 的写权限，留成 user 又让他在 A 变成可写 ——
   两种都是错的，而且都**悄悄**发生。所以列出来交给人决定（真要跑，先手工把这些人
   的成员角色调齐再来）。本库 0 命中，但脚本要为一般情况写。
2. **系统 admin 不降级。** admin 绕过一切项目检查，他的 viewer 成员行没有实际意义。

迁移不能 import app 代码（app 的模块树会随版本变），角色值照抄一份 ——
与 app/core/permissions.py 的 SYSTEM_ROLES / PROJECT_ROLES_ALL 同源，改那里要一起改这里。
downgrade 只还原约束、不回滚数据（与 zzw0roleck 一致）：角色名折叠是有损的，
member 已经分不出当初是 developer 还是 tester。
"""
from alembic import op
import sqlalchemy as sa

revision = "zzx0role3"
down_revision = "zzw0roleck"
branch_labels = None
depends_on = None

# 新模型的合法取值
_SYSTEM_ROLES = ("admin", "user", "guest")
_PROJECT_ROLES = ("manager", "member")

# 旧模型里表示「只读」的项目角色 —— 迁移的分界线就是这一个集合
_READONLY_PROJECT_ROLES = ("viewer", "guest")

_OLD_SYSTEM_ROLES = ("admin", "operator", "user")
_OLD_PROJECT_ROLES = (
    "manager", "member", "viewer",
    "project_admin", "developer", "tester", "guest",
)


def _in_sql(values):
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    conn = op.get_bind()
    ro = _in_sql(_READONLY_PROJECT_ROLES)

    # ── 0. 先体检：同时存在只读成员关系和可写成员关系的用户，本迁移拒绝猜 ──
    mixed = conn.execute(sa.text(f"""
        select u.username,
               string_agg(p.name || '=' || m.role, ', ' order by p.name) as memberships
          from project_members m
          join users u on u.id = m.user_id
          join projects p on p.id = m.project_id
         where u.role <> 'admin'
         group by u.id, u.username
        having bool_or(m.role in ({ro})) and bool_or(m.role not in ({ro}))
    """)).all()
    if mixed:
        lines = "\n".join(f"  - {r.username}: {r.memberships}" for r in mixed)
        raise RuntimeError(
            "迁移中止：下列用户在一部分项目里只读、在另一部分项目里可写，"
            "2 档角色模型表达不了这种情况。\n"
            "请先决定每个人该整体只读还是整体可写（手工调平他们的成员角色），再重跑本迁移：\n"
            f"{lines}"
        )

    # ── 0.5 先把旧约束摘掉，再动数据 ──
    # 顺序不能反：下一步要写入的 'guest' 不在旧的 ck_user_role_valid 白名单里
    # （旧白名单是 admin/operator/user），带着旧约束跑第 1 步会当场 CheckViolation。
    # 「先清洗数据、最后换约束」是上一版 zzw0roleck 的顺序，那次没有引入新取值，
    # 所以成立；这次引入了 guest，就不成立了。
    op.execute("alter table users drop constraint if exists ck_user_role_valid")
    op.execute("alter table project_members drop constraint if exists ck_member_role_valid")

    # ── 1. 「处处只读」的非 admin 用户降成系统 guest ──
    # 必须在改角色名**之前**做：viewer/guest 一旦折进 member 就再也认不出来了。
    demoted = conn.execute(sa.text(f"""
        update users u set role = 'guest'
         where u.role <> 'admin'
           and exists (select 1 from project_members m where m.user_id = u.id)
           and not exists (
               select 1 from project_members m
                where m.user_id = u.id and m.role not in ({ro})
           )
        returning u.username
    """)).all()
    for r in demoted:
        print(f"[zzx0role3] 降为系统游客（原先处处只读）：{r.username}")

    # ── 2. 系统角色：operator 退役 ──
    conn.execute(sa.text("update users set role = 'user' where role = 'operator'"))

    # ── 3. 项目角色折叠成 2 档 ──
    conn.execute(sa.text("update project_members set role = 'manager' where role = 'project_admin'"))
    conn.execute(sa.text(
        "update project_members set role = 'member' "
        "where role in ('developer', 'tester', 'viewer', 'guest')"
    ))

    # ── 4. 脏值兜底（防御性；正常库无命中，但 ADD CONSTRAINT 会因一行脏值整个失败）──
    conn.execute(sa.text(
        f"update users set role = 'user' where role not in ({_in_sql(_SYSTEM_ROLES)})"
    ))
    conn.execute(sa.text(
        f"update project_members set role = 'member' where role not in ({_in_sql(_PROJECT_ROLES)})"
    ))

    # ── 5. 装上新约束（旧的已在 0.5 摘掉）──
    op.execute(
        f"alter table users add constraint ck_user_role_valid "
        f"check (role in ({_in_sql(_SYSTEM_ROLES)}))"
    )
    op.execute(
        f"alter table project_members add constraint ck_member_role_valid "
        f"check (role in ({_in_sql(_PROJECT_ROLES)}))"
    )


def downgrade() -> None:
    # 只还原约束到旧白名单（旧白名单是新的超集，所以现有数据全部合法）。
    # 不回滚数据：折叠有损，且降级过的 guest 分不出原先是 user 还是 operator。
    op.execute("alter table users drop constraint if exists ck_user_role_valid")
    op.execute("alter table project_members drop constraint if exists ck_member_role_valid")
    op.execute(
        f"alter table users add constraint ck_user_role_valid "
        f"check (role in ({_in_sql(_OLD_SYSTEM_ROLES)}))"
    )
    op.execute(
        f"alter table project_members add constraint ck_member_role_valid "
        f"check (role in ({_in_sql(_OLD_PROJECT_ROLES)}))"
    )
