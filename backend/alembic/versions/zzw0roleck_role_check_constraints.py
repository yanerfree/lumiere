"""给角色列加 CHECK 约束：固化合法取值，DB 层兜底

Revision ID: zzw0roleck
Revises: zzw0selreg

权限大改「先固化 + 留接口」的第一步：把「角色只能是这几个值」从口头约定变成
数据库约束。应用层 Pydantic 也拦一道，但那只挡走 API 的写；直接 SQL / 脚本 / 将来
别的服务写进来的脏角色，只有 DB CHECK 能兜住 —— 而一个拼错的角色（'admni'、'dev'）
会让 require_project_role 静默拒绝该用户，报出来像「莫名其妙没权限」，极难查。

- users.role         ∈ (admin, operator, user)
- project_members.role ∈ 新(manager/member/viewer) + 旧(project_admin/developer/tester/guest)

**项目角色兼容期同时认新旧两套名**：本轮不做破坏性改名（那是 outward-facing 的大改，
放到 demo 通过后再单独迁）。存量数据全是旧名，都在白名单里，加约束不会失败。
取值清单与 app/core/permissions.py 的 SYSTEM_ROLES / PROJECT_ROLES_ALL 同源，改那里要一起改。
"""
from alembic import op

revision = "zzw0roleck"
down_revision = "zzw0selreg"
branch_labels = None
depends_on = None

_SYSTEM_ROLES = ("admin", "operator", "user")
_PROJECT_ROLES = (
    "manager", "member", "viewer",
    "project_admin", "developer", "tester", "guest",
)


def _in_sql(values):
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    # 加约束前先兜底：把任何不在白名单里的历史脏值归一到安全默认，避免 ADD CONSTRAINT 直接失败。
    # 正常库里应无命中（存量角色都是合法旧名）；这两句是防御性的，不改任何合法数据。
    op.execute(
        f"update users set role = 'user' where role not in ({_in_sql(_SYSTEM_ROLES)})"
    )
    op.execute(
        f"update project_members set role = 'viewer' where role not in ({_in_sql(_PROJECT_ROLES)})"
    )
    op.execute(
        f"alter table users add constraint ck_user_role_valid "
        f"check (role in ({_in_sql(_SYSTEM_ROLES)}))"
    )
    op.execute(
        f"alter table project_members add constraint ck_member_role_valid "
        f"check (role in ({_in_sql(_PROJECT_ROLES)}))"
    )


def downgrade() -> None:
    op.execute("alter table project_members drop constraint if exists ck_member_role_valid")
    op.execute("alter table users drop constraint if exists ck_user_role_valid")
