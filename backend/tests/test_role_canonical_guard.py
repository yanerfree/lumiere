"""封样：require_project_role 走规范名匹配 —— 新旧角色名互认，退役的只读名一律拒绝。

守三件事，都是角色收敛期最容易漏权/越权的地方：

1. **旧名成员满足新名守卫**：迁移 zzx0role3 之前库里全是 project_admin/developer/tester，
   而守卫已经换成新名 (manager/member)。不互认的话，代码一上线所有存量成员就集体
   「莫名没权限」—— 而且是在迁移窗口里，最不该乱的时候。

2. **退役的只读名 viewer / guest 什么都过不了**（连读守卫也过不了）。
   2026-08-29 之前它们是合法的只读项目角色；收敛成 2 档后只读上移到账号层。
   把它们折进 member 就是**静默提权**：残留的只读行会在写端点上被放行，页面上完全看不出来。
   宁可让它们失败得可见。

3. **档位之间仍分得开**：member 过不了只给管理员的守卫。2 档模型下
   TIER_ADMIN 与 TIER_WRITE 取值不同，这条是它俩没被合并掉的证据。

直接调 require_project_role 生成的 _check + 假 session，不起服务、不连库。
"""
import uuid

import pytest

from app.core import permissions as perms
from app.core.exceptions import ForbiddenError
from app.deps.auth import require_project_role


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

# 迁移前库里可能存在的旧名（PROJECT_ROLES_RECOGNIZED 减去新名）
_LEGACY = perms.LEGACY_PROJECT_ROLES
# 退役的只读名 —— 不在 RECOGNIZED 里，故意不做归一
_RETIRED_READONLY = ("viewer", "guest")


async def _call(guard_roles, member_role):
    check = require_project_role(*guard_roles)
    return await check(
        project_id=PID, current_user=_User("user"), session=_FakeSession(_Member(member_role))
    )


@pytest.mark.asyncio
async def test_legacy_names_pass_the_new_name_guards():
    """旧名成员必须能过新名守卫（迁移窗口期的存量成员不能集体失权）。"""
    for role in ("manager", "member", *_LEGACY):
        out = await _call(perms.TIER_WRITE, role)
        assert out.role == "user"


@pytest.mark.asyncio
async def test_legacy_admin_name_passes_the_admin_guard():
    for role in ("manager", "project_admin"):
        out = await _call(perms.TIER_ADMIN, role)
        assert out.role == "user"


@pytest.mark.asyncio
async def test_member_cannot_pass_the_admin_guard():
    """成员档进不了管理员守卫 —— TIER_ADMIN 与 TIER_WRITE 确实是两档。"""
    for role in ("member", "developer", "tester"):
        with pytest.raises(ForbiddenError):
            await _call(perms.TIER_ADMIN, role)


@pytest.mark.asyncio
async def test_retired_readonly_names_pass_nothing():
    """viewer / guest 过不了任何守卫，读守卫也不行 —— 不做归一 = 默认拒绝。"""
    for role in _RETIRED_READONLY:
        for guard in (perms.TIER_READ, perms.TIER_WRITE, perms.TIER_DOC_MANAGE, perms.TIER_ADMIN):
            with pytest.raises(ForbiddenError):
                await _call(guard, role)


@pytest.mark.asyncio
async def test_unknown_role_is_denied_not_crashed():
    """拼错的角色安静地拒绝（默认拒绝），不抛 500。"""
    with pytest.raises(ForbiddenError):
        await _call(perms.TIER_READ, "developr")


@pytest.mark.asyncio
async def test_system_admin_bypasses_project_guards():
    check = require_project_role(*perms.TIER_ADMIN)
    out = await check(project_id=PID, current_user=_User("admin"), session=_FakeSession(None))
    assert out.role == "admin"


@pytest.mark.asyncio
async def test_non_member_is_rejected():
    check = require_project_role(*perms.TIER_READ)
    with pytest.raises(ForbiddenError):
        await check(project_id=PID, current_user=_User("user"), session=_FakeSession(None))
