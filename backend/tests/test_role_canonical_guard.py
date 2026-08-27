"""M2 封样：require_project_role 走规范名匹配 —— 新旧角色名互认。

守两件事，都是兼容期最容易漏权/越权的地方：

1. **新名成员满足旧名守卫**：role="manager" 的成员必须能过 require_project_role("project_admin")。
   不然一旦有人用新名建了成员，他在所有存量端点（全是旧名守卫）上都会被静默拒绝 —— 报出来像「莫名没权限」。

2. **旧名成员满足新名守卫**：role="guest" 的成员必须能过 scenario_gen 那种已经用新名 viewer 的读守卫。
   这条修的是一个**现存 bug**：canonical 之前，guest 成员读不了 scenario-gen 统计（READ_ROLES 写的是 viewer）。

直接调 require_project_role 生成的 _check + 假 session，不起服务、不连库。
"""
import uuid

import pytest

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

# 现存端点的旧名写守卫 与 scenario_gen 的新名读守卫
_OLD_WRITE_GUARD = ("project_admin", "developer", "tester")
_NEW_READ_GUARD = ("project_admin", "developer", "tester", "viewer")  # = scenario_gen READ_ROLES


async def _call(guard_roles, member_role):
    check = require_project_role(*guard_roles)
    return await check(
        project_id=PID, current_user=_User("user"), session=_FakeSession(_Member(member_role))
    )


@pytest.mark.asyncio
async def test_new_name_member_passes_old_name_guard():
    # manager/member 满足旧名写守卫；tester 也在其中
    for role in ("manager", "member", "tester", "project_admin", "developer"):
        out = await _call(_OLD_WRITE_GUARD, role)
        assert out.role == "user"


@pytest.mark.asyncio
async def test_old_name_guest_passes_new_name_read_guard():
    # 现存 bug 的回归钉子：guest 必须能过写着 viewer 的读守卫
    out = await _call(_NEW_READ_GUARD, "guest")
    assert out.role == "user"
    # 新名 viewer 自然也过
    out2 = await _call(_NEW_READ_GUARD, "viewer")
    assert out2.role == "user"


@pytest.mark.asyncio
async def test_viewer_still_blocked_from_write_guard():
    # 只读角色（新旧名）都进不了写守卫 —— 归一不能把只读放大成可写
    for role in ("viewer", "guest"):
        with pytest.raises(ForbiddenError):
            await _call(_OLD_WRITE_GUARD, role)


@pytest.mark.asyncio
async def test_tester_still_below_developer_tier():
    # tester 不折叠进 member：只含 project_admin+developer 的守卫，tester 过不了
    with pytest.raises(ForbiddenError):
        await _call(("project_admin", "developer"), "tester")
    # member（=developer 档）能过
    out = await _call(("project_admin", "developer"), "member")
    assert out.role == "user"
