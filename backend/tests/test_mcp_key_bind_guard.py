"""M1 封样：MCP Key 归属项目时的写权限校验。

CLAUDE.md 硬规则：Key 的 project_id 同时管工具范围**和数据范围**（能读写哪个项目的
用例/环境）。project_id 走 body 不走 path，所以按 path 取参的 require_project_role
依赖用不上 —— 校验落在 _assert_can_bind_project 里手写。这条守它：非成员/只读成员
不能把 Key 归到项目（否则等于凭空给自己开一把能读写他人项目的 Key）。

直接调函数 + 假 session，不起服务、不连库。
"""
import uuid

import pytest

from app.api.mcp_keys import _BIND_ROLES, _assert_can_bind_project
from app.core.exceptions import ForbiddenError


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """execute() 恒返回预置的 member（或 None），够覆盖归属校验那一条查询。"""

    def __init__(self, member):
        self._member = member

    async def execute(self, *args, **kwargs):
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
async def test_none_project_id_is_noop():
    # 不归属任何项目（走遗留 allowed_tools 路径）→ 不校验，直接过
    await _assert_can_bind_project(_FakeSession(None), _User("user"), None)


@pytest.mark.asyncio
async def test_system_admin_bypasses():
    # 系统 admin 全权，绕过项目成员校验，口径同 require_project_role
    await _assert_can_bind_project(_FakeSession(None), _User("admin"), PID)


@pytest.mark.asyncio
async def test_non_member_rejected():
    with pytest.raises(ForbiddenError):
        await _assert_can_bind_project(_FakeSession(None), _User("user"), PID)


@pytest.mark.asyncio
async def test_guest_member_rejected():
    # guest 是只读成员 → 不能发一把能写的 Key
    with pytest.raises(ForbiddenError):
        await _assert_can_bind_project(_FakeSession(_Member("guest")), _User("user"), PID)


@pytest.mark.asyncio
async def test_write_role_members_allowed():
    for role in _BIND_ROLES:  # project_admin / developer / tester
        await _assert_can_bind_project(_FakeSession(_Member(role)), _User("user"), PID)


@pytest.mark.asyncio
async def test_new_name_write_members_allowed():
    # 归一后：新名 manager / member 也能发 Key（等价于 project_admin / developer）
    for role in ("manager", "member"):
        await _assert_can_bind_project(_FakeSession(_Member(role)), _User("user"), PID)


@pytest.mark.asyncio
async def test_new_name_viewer_rejected():
    # 新名只读 viewer 与旧名 guest 同样被拒
    with pytest.raises(ForbiddenError):
        await _assert_can_bind_project(_FakeSession(_Member("viewer")), _User("user"), PID)
