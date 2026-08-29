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
    """member ⊂ manager —— 高一档必然含低一档的全部权限。

    2026-08-29 前这条链是 viewer ⊂ tester ⊂ member ⊂ manager。砍成 2 档之后
    只读不再由项目角色表达（改由系统角色 guest 的封顶承担），链自然短了。
    """
    member = perms.resolve_permissions("user", "member")
    manager = perms.resolve_permissions("user", "manager")
    assert member < manager


def test_old_and_new_role_names_are_equivalent():
    """兼容期核心保证：仍认得的旧名与新名解析出**完全一样**的权限集合。"""
    assert perms.project_permissions("developer") == perms.project_permissions("member")
    assert perms.project_permissions("tester") == perms.project_permissions("member")
    assert perms.project_permissions("project_admin") == perms.project_permissions("manager")


def test_retired_readonly_role_names_grant_nothing():
    """viewer / guest 这两个退役的只读项目角色**必须解析成空集**，而不是折进 member。

    折进 member 会让「代码已上、迁移还没跑」那个窗口里的残留只读行悄悄拿到写权限 ——
    静默提权是本次改动最该避免的失败形状。让它们什么都干不了，失败是可见的。
    """
    for name in ("viewer", "guest"):
        assert perms.project_permissions(name) == frozenset()
        assert perms.canonical_project_role(name) == name  # 不折叠 → 守卫一律拒绝


def test_admin_is_always_full_set():
    """系统 admin 恒为全权，与项目角色无关（对齐 require_project_role 的 admin 直通）。"""
    assert perms.resolve_permissions("admin", None) == perms.ALL_PERMISSIONS
    assert perms.resolve_permissions("admin", "viewer") == perms.ALL_PERMISSIONS
    assert perms.resolve_permissions("admin", "manager") == perms.ALL_PERMISSIONS


def test_all_permissions_is_exactly_the_union():
    """ALL_PERMISSIONS 必须正好等于所有角色权限的并集 —— 不多（无定义了却没人用的孤儿点）、
    不少（无哪个角色能拿到不在全集里的点）。

    **admin 必须排除在并集之外**：SYSTEM_ROLE_PERMISSIONS["admin"] 本身就是
    ALL_PERMISSIONS，把它算进来这条断言就恒真了 —— 那样加一个谁也拿不到的孤儿权限点，
    这里照样绿。admin 独占的那几个点由 ADMIN_ONLY_PERMISSIONS 单独声明并对账。
    """
    union = set()
    for role, s in perms.SYSTEM_ROLE_PERMISSIONS.items():
        if role == "admin":
            continue
        union |= set(s)
    for s in perms.PROJECT_ROLE_PERMISSIONS.values():
        union |= set(s)
    assert union | set(perms.ADMIN_ONLY_PERMISSIONS) == set(perms.ALL_PERMISSIONS)
    # admin 专属点确实没有任何非 admin 角色拿得到（否则「专属」是假的）
    assert union & set(perms.ADMIN_ONLY_PERMISSIONS) == set()


def test_every_declared_role_has_a_permission_set():
    """每个声明过的角色都必须有映射，反之亦然 —— 防「加了角色忘了给权限」。

    项目角色这一头分两个集合：可写入的（PROJECT_ROLES_ALL，= DB CHECK 的白名单）
    是「仍认得的」（PROJECT_ROLES_RECOGNIZED，含兼容期旧名）的子集，
    而权限映射表必须恰好覆盖后者 —— 多一个 = 认了个没人能写进来的名，
    少一个 = 存量行读出来是零权限。
    """
    assert set(perms.SYSTEM_ROLES) == set(perms.SYSTEM_ROLE_PERMISSIONS)
    assert set(perms.PROJECT_ROLES_ALL) <= set(perms.PROJECT_ROLES_RECOGNIZED)
    assert set(perms.PROJECT_ROLES_RECOGNIZED) == set(perms.PROJECT_ROLE_PERMISSIONS)


# ── 系统角色封顶（游客硬只读）──────────────────────────────────────
def test_guest_ceiling_caps_everything_to_read():
    """游客的解析结果恒 ⊆ {project.read}，**哪怕他在项目里被挂成 manager**。

    这是「只读上移到账号层」的全部含金量所在：如果这条不成立，把游客加进项目
    就等于给了他写权限，而页面上完全看不出来。
    """
    for project_role in (None, "member", "manager", "project_admin", "bogus"):
        assert perms.resolve_permissions("guest", project_role) <= {perms.P_PROJECT_READ}
    # 不在任何项目里的游客 = 零权限，不是「凭空能读」
    assert perms.resolve_permissions("guest", None) == frozenset()
    assert perms.system_permissions("guest") == frozenset()
    # 在项目里挂成员，才拿得到读
    assert perms.resolve_permissions("guest", "member") == {perms.P_PROJECT_READ}


def test_ceiling_default_is_no_cap_not_empty():
    """未配置封顶的系统角色 = 不封顶（ALL_PERMISSIONS），不是空集。

    写成空集的话，「新加一个系统角色忘了配封顶」会变成静默的全站瘫痪；
    挡脏值的是 DB CHECK 约束，不是这里。
    """
    assert perms.ceiling("user") == perms.ALL_PERMISSIONS
    assert perms.ceiling("bogus") == perms.ALL_PERMISSIONS
    assert perms.ceiling("guest") == frozenset({perms.P_PROJECT_READ})
    # 未知系统角色的行为与封顶引入前一致：系统权限为空 → 结果仍取决于项目角色
    assert perms.resolve_permissions("bogus", "member") == perms.project_permissions("member")


def test_unknown_role_resolves_empty_not_crash():
    """拼错/未知角色解析成空集合（默认拒绝），不抛异常 —— 坏数据不该把请求打 500。"""
    assert perms.project_permissions("nope") == frozenset()
    assert perms.system_permissions("nope") == frozenset()


# ── 角色归一（新旧名互认）──────────────────────────────────────────
def test_canonical_folds_old_and_new_names():
    c = perms.canonical_project_role
    assert c("manager") == c("project_admin") == "manager"
    assert c("member") == c("developer") == c("tester") == "member"
    assert c(None) is None
    assert c("bogus") == "bogus"  # 未知原样返回（默认拒绝时不会误放行）
    # 退役的只读名不折叠 —— 见 test_retired_readonly_role_names_grant_nothing
    assert c("viewer") == "viewer" and c("guest") == "guest"


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
def test_pydantic_role_validation_matches_the_source_of_truth():
    """Pydantic 收的角色取值必须**派生自**事实源，而不是各写一份字面量。

    2026-08-29 之前 schemas/user.py 是手写的 Literal["admin","operator","user"]，
    没有任何东西盯着它 —— 「事实源里删了 operator、这里还收着」可以静默发生，
    结果是能建出一个后端解析成零权限的账号。
    """
    from pydantic import ValidationError

    from app.schemas.project import AddMemberRequest
    from app.schemas.user import CreateUserRequest, UpdateUserRequest

    for role in perms.SYSTEM_ROLES:
        assert CreateUserRequest(username="ok", password="secret", role=role).role == role
        assert UpdateUserRequest(role=role).role == role
    for role in perms.PROJECT_ROLES_ALL:
        assert AddMemberRequest(user_id=uuid.uuid4(), role=role).role == role

    # 退役的名字必须被拒，否则会写进库、然后卡在 DB CHECK 上（或更糟：绕过约束存活）
    for bad in ("operator", "superuser"):
        with pytest.raises(ValidationError):
            CreateUserRequest(username="ok", password="secret", role=bad)
    for bad in ("project_admin", "developer", "tester", "viewer", "guest", "root"):
        with pytest.raises(ValidationError):
            AddMemberRequest(user_id=uuid.uuid4(), role=bad)


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
    # 退役的 viewer 解析成零权限 → 拿不到 case.write
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
    # 普通用户能建项目、能进工具组，碰不了平台设施
    ok = await require_system_permission(perms.P_PROJECT_CREATE)(current_user=_User("user"))
    assert ok.role == "user"
    ok = await require_system_permission(perms.P_SYS_TOOLS_USE)(current_user=_User("user"))
    assert ok.role == "user"
    for point in (perms.P_SYS_CHANNEL_READ, perms.P_SYS_CHANNEL_MANAGE, perms.P_SYS_USER_MANAGE):
        with pytest.raises(ForbiddenError):
            await require_system_permission(point)(current_user=_User("user"))
    # admin 直通任意系统权限点
    ok2 = await require_system_permission(perms.P_SYS_USER_MANAGE)(current_user=_User("admin"))
    assert ok2.role == "admin"


@pytest.mark.asyncio
async def test_require_system_permission_applies_the_ceiling():
    """游客过不了任何系统权限点 —— 它必须走 resolve_permissions（过封顶），
    不能走 system_permissions（不过封顶）。

    走错那一个的后果不是"多给一点"，是**自报一套、强制另一套**：
    /api/me/permissions 说游客只有 project.read，守卫却按未削减的集合放行。
    这正是被删掉的 operator 犯的错，不能在游客身上重演。
    """
    for point in (perms.P_PROJECT_CREATE, perms.P_SYS_TOOLS_USE, perms.P_SYS_USER_MANAGE):
        with pytest.raises(ForbiddenError):
            await require_system_permission(point)(current_user=_User("guest"))
