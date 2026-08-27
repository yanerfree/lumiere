"""M2 封样：权限点模型的不变量 + 依赖工厂的判定。

守的是「唯一事实源」这件事本身 —— core/permissions 是后端校验、前端菜单、AI 助手
能力面共读的那一份。它一旦漂了（某角色悄悄多/少一个权限点、新旧名两套集合对不上、
admin 不再是全集），三处一起错，且错得一致、极难看出。所以把它的形状钉死在这里。

纯断言 + 假 session，不起服务、不连库。
"""
import uuid

import pytest

from app.core import permissions as perms
from app.deps.permissions import require_permission, require_system_permission
from app.core.exceptions import ForbiddenError
from app.models.project import ProjectMember
from app.models.user import User


# ── 权限集合的不变量 ──────────────────────────────────────────────
def test_project_roles_are_monotonic():
    """viewer ⊂ tester ⊂ member ⊂ manager —— 高一档必然含低一档的全部权限。"""
    viewer = perms.resolve_permissions("user", "viewer")
    tester = perms.resolve_permissions("user", "tester")
    member = perms.resolve_permissions("user", "member")
    manager = perms.resolve_permissions("user", "manager")
    assert viewer < tester < member < manager


def test_old_and_new_role_names_are_equivalent():
    """兼容期核心保证：旧名与新名解析出**完全一样**的权限集合。"""
    assert perms.project_permissions("guest") == perms.project_permissions("viewer")
    assert perms.project_permissions("developer") == perms.project_permissions("member")
    assert perms.project_permissions("project_admin") == perms.project_permissions("manager")


def test_admin_is_always_full_set():
    """系统 admin 恒为全权，与项目角色无关（对齐 require_project_role 的 admin 直通）。"""
    assert perms.resolve_permissions("admin", None) == perms.ALL_PERMISSIONS
    assert perms.resolve_permissions("admin", "viewer") == perms.ALL_PERMISSIONS
    assert perms.resolve_permissions("admin", "manager") == perms.ALL_PERMISSIONS


def test_all_permissions_is_exactly_the_union():
    """ALL_PERMISSIONS 必须正好等于所有角色权限的并集 —— 不多（无定义了却没人用的孤儿点）、
    不少（无哪个角色能拿到不在全集里的点）。"""
    union = set()
    for s in perms.SYSTEM_ROLE_PERMISSIONS.values():
        union |= set(s)
    for s in perms.PROJECT_ROLE_PERMISSIONS.values():
        union |= set(s)
    assert union == set(perms.ALL_PERMISSIONS)


def test_every_declared_role_has_a_permission_set():
    """SYSTEM_ROLES / PROJECT_ROLES_ALL 里的每个角色都必须有映射，反之亦然 —— 防「加了角色忘了给权限」。"""
    assert set(perms.SYSTEM_ROLES) == set(perms.SYSTEM_ROLE_PERMISSIONS)
    assert set(perms.PROJECT_ROLES_ALL) == set(perms.PROJECT_ROLE_PERMISSIONS)


def test_unknown_role_resolves_empty_not_crash():
    """拼错/未知角色解析成空集合（默认拒绝），不抛异常 —— 坏数据不该把请求打 500。"""
    assert perms.project_permissions("nope") == frozenset()
    assert perms.system_permissions("nope") == frozenset()


# ── 角色归一（新旧名互认）──────────────────────────────────────────
def test_canonical_folds_old_and_new_names():
    c = perms.canonical_project_role
    assert c("manager") == c("project_admin") == "manager"
    assert c("member") == c("developer") == "member"
    assert c("viewer") == c("guest") == "viewer"
    assert c("tester") == "tester"  # tester 不折叠进 member
    assert c(None) is None
    assert c("bogus") == "bogus"  # 未知原样返回（默认拒绝时不会误放行）


# ── DB CheckConstraint 与角色清单同源 ────────────────────────────
def test_model_check_constraints_match_role_lists():
    """模型上的 CHECK 约束 SQL 必须列全 SYSTEM_ROLES / PROJECT_ROLES_ALL，防两处漂移。"""
    user_ck = next(
        c for c in User.__table__.constraints if getattr(c, "name", "") == "ck_user_role_valid"
    )
    member_ck = next(
        c for c in ProjectMember.__table__.constraints if getattr(c, "name", "") == "ck_member_role_valid"
    )
    user_sql = str(user_ck.sqltext)
    member_sql = str(member_ck.sqltext)
    for r in perms.SYSTEM_ROLES:
        assert f"'{r}'" in user_sql
    for r in perms.PROJECT_ROLES_ALL:
        assert f"'{r}'" in member_sql


# ── Pydantic 校验认新名、认 operator、拒非法 ───────────────────────
def test_pydantic_accepts_operator_and_new_project_roles():
    from pydantic import ValidationError

    from app.schemas.project import AddMemberRequest
    from app.schemas.user import CreateUserRequest

    CreateUserRequest(username="ok", password="secret", role="operator")
    for role in ("manager", "member", "viewer", "project_admin", "developer", "tester", "guest"):
        AddMemberRequest(user_id=uuid.uuid4(), role=role)
    with pytest.raises(ValidationError):
        CreateUserRequest(username="ok", password="secret", role="superuser")
    with pytest.raises(ValidationError):
        AddMemberRequest(user_id=uuid.uuid4(), role="root")


# ── require_permission 依赖工厂的判定（假 session）──────────────────
class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, member):
        self._member = member

    async def execute(self, *a, **k):
        return _FakeResult(self._member)


class _User:
    def __init__(self, role):
        self.id = uuid.uuid4()
        self.role = role


class _Member:
    def __init__(self, role):
        self.role = role


PID = uuid.uuid4()


@pytest.mark.asyncio
async def test_require_permission_admin_bypasses():
    check = require_permission(perms.P_PROJECT_SETTINGS)
    out = await check(project_id=PID, current_user=_User("admin"), session=_FakeSession(None))
    assert out.role == "admin"


@pytest.mark.asyncio
async def test_require_permission_non_member_rejected():
    check = require_permission(perms.P_PROJECT_READ)
    with pytest.raises(ForbiddenError):
        await check(project_id=PID, current_user=_User("user"), session=_FakeSession(None))


@pytest.mark.asyncio
async def test_require_permission_insufficient_role_rejected():
    # viewer 拿不到 case.write
    check = require_permission(perms.P_CASE_WRITE)
    with pytest.raises(ForbiddenError):
        await check(project_id=PID, current_user=_User("user"), session=_FakeSession(_Member("viewer")))


@pytest.mark.asyncio
async def test_require_permission_sufficient_role_passes():
    # tester 有 case.write；旧名 developer 有 doc.manage
    ok1 = await require_permission(perms.P_CASE_WRITE)(
        project_id=PID, current_user=_User("user"), session=_FakeSession(_Member("tester"))
    )
    assert ok1.role == "user"
    ok2 = await require_permission(perms.P_DOC_MANAGE)(
        project_id=PID, current_user=_User("user"), session=_FakeSession(_Member("developer"))
    )
    assert ok2.role == "user"


@pytest.mark.asyncio
async def test_require_permission_manager_only_point():
    # member 拿不到 member.manage；manager 能
    with pytest.raises(ForbiddenError):
        await require_permission(perms.P_MEMBER_MANAGE)(
            project_id=PID, current_user=_User("user"), session=_FakeSession(_Member("member"))
        )
    ok = await require_permission(perms.P_MEMBER_MANAGE)(
        project_id=PID, current_user=_User("user"), session=_FakeSession(_Member("manager"))
    )
    assert ok.role == "user"


@pytest.mark.asyncio
async def test_require_system_permission():
    # operator 能读平台设施，不能改
    ok = await require_system_permission(perms.P_SYS_CHANNEL_READ)(current_user=_User("operator"))
    assert ok.role == "operator"
    with pytest.raises(ForbiddenError):
        await require_system_permission(perms.P_SYS_CHANNEL_MANAGE)(current_user=_User("operator"))
    # admin 直通任意系统权限点
    ok2 = await require_system_permission(perms.P_SYS_USER_MANAGE)(current_user=_User("admin"))
    assert ok2.role == "admin"
