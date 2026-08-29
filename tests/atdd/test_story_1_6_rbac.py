"""
ATDD 验收测试 — Story 1.6: RBAC 权限体系全局强制

2026-08-29 按新角色模型重写（迁移 `zzx0role3`）：
系统角色 admin / user / guest(硬封顶只读)；项目角色 manager / member。
`project_admin`/`developer`/`tester`/`viewer` 及项目角色 `guest` 全部退役。

原文件用 4 档项目角色逐档打，但那 4 档在权限点上只差一个 doc.manage —— 分档测出来的
边界其实只有两条，剩下的是重复。折成两档后覆盖的边界**没少**：

- admin 绕过一切
- 非成员 → 403 NOT_PROJECT_MEMBER
- manager 能管成员、member 不能 → 403 PROJECT_ROLE_DENIED
- 改项目配置只有系统 admin（项目 manager 也不行）

新增的是原来测不到的一条：**账号级游客**。它在项目里挂 member，项目角色守卫是放它过的，
拦住它的是另一条腿（`deps/auth` 的非 GET 闸门）。所以断言里盯的是 `error.code` 而不只是 403 ——
两条腿都返回 403，只看状态码分不出是哪条在起作用，而"哪条在起作用"正是这轮要保住的东西。
"""
import pytest

from tests.conftest import create_test_user, make_auth_headers


async def _setup_project_with_roles(client, db_session):
    """辅助: 创建项目 + 各档用户，返回所有上下文"""
    admin = await create_test_user(db_session, username="rbac_admin", role="admin")
    manager_user = await create_test_user(db_session, username="rbac_manager", role="user")
    member_user = await create_test_user(db_session, username="rbac_member", role="user")
    # 系统游客 —— 项目角色照样是 member（2 档模型里没有"只读档"可挑）
    guest = await create_test_user(db_session, username="rbac_guest", role="guest")
    unbound_user = await create_test_user(db_session, username="rbac_unbound", role="user")

    admin_headers, _ = make_auth_headers(admin)

    resp = await client.post("/api/projects", headers=admin_headers, json={
        "name": "rbac-test-project",
        "gitUrl": "git@x.com:rbac/test.git",
        "scriptBasePath": "/tmp/rbac",
    })
    project_id = resp.json()["data"]["id"]

    for user, role in [
        (manager_user, "manager"),
        (member_user, "member"),
        (guest, "member"),
    ]:
        r = await client.post(f"/api/projects/{project_id}/members", headers=admin_headers, json={
            "userId": str(user.id),
            "role": role,
        })
        # 断言而不是忽略：加成员一旦静默失败（比如角色名被 schema 拒成 422），
        # 后面那些"某某不能写"会因为**根本不是成员**而 403，全体假绿。
        assert r.status_code in (200, 201), r.text

    return {
        "project_id": project_id,
        "admin": (admin, make_auth_headers(admin)[0]),
        "manager": (manager_user, make_auth_headers(manager_user)[0]),
        "member": (member_user, make_auth_headers(member_user)[0]),
        "guest": (guest, make_auth_headers(guest)[0]),
        "unbound": (unbound_user, make_auth_headers(unbound_user)[0]),
    }


# ---------------------------------------------------------------------------
# 1.6-API-001: admin 可访问所有项目
# Priority: P0
# ---------------------------------------------------------------------------
class TestAdminAccessAll:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_admin_can_access_any_project(self, client, db_session):
        """AC: 系统管理员可访问所有项目所有数据"""
        ctx = await _setup_project_with_roles(client, db_session)
        _, admin_headers = ctx["admin"]

        response = await client.get(
            f"/api/projects/{ctx['project_id']}/members",
            headers=admin_headers,
        )

        assert response.status_code == 200

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_admin_sees_all_projects_in_list(self, client, db_session):
        """AC: admin 项目列表包含所有项目"""
        ctx = await _setup_project_with_roles(client, db_session)
        _, admin_headers = ctx["admin"]

        response = await client.get("/api/projects", headers=admin_headers)
        assert response.status_code == 200
        names = [p["name"] for p in response.json()["data"]]
        assert "rbac-test-project" in names


# ---------------------------------------------------------------------------
# 1.6-API-002: 非 admin 未绑定项目 → 403
# Priority: P0
# ---------------------------------------------------------------------------
class TestUnboundUserDenied:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_unbound_user_cannot_access_project(self, client, db_session):
        """AC: 未绑定项目的用户访问该项目 API 返回 403"""
        ctx = await _setup_project_with_roles(client, db_session)
        _, unbound_headers = ctx["unbound"]

        response = await client.get(
            f"/api/projects/{ctx['project_id']}/members",
            headers=unbound_headers,
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "NOT_PROJECT_MEMBER"


# ---------------------------------------------------------------------------
# 1.6-API-003: manager 可管理配置和成员
# Priority: P0
# ---------------------------------------------------------------------------
class TestManagerPermissions:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_manager_can_manage_members(self, client, db_session):
        """AC: 项目管理员可管理所属项目成员"""
        ctx = await _setup_project_with_roles(client, db_session)
        _, mgr_headers = ctx["manager"]
        new_user = await create_test_user(db_session, username="rbac_new_mem", role="user")

        response = await client.post(
            f"/api/projects/{ctx['project_id']}/members",
            headers=mgr_headers,
            json={"userId": str(new_user.id), "role": "member"},
        )

        assert response.status_code in (200, 201), response.text

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_manager_cannot_update_project(self, client, db_session):
        """update_project 需要系统 admin 权限，项目管理员返回 403。

        这条容易被误当成"多余"：项目管理员既然能管成员，为什么不能改项目？
        因为项目的 git 地址/脚本根路径是**平台侧接线**，改错会让整个项目的执行全挂 ——
        它属于系统 admin，不属于项目自治的范围。
        """
        ctx = await _setup_project_with_roles(client, db_session)
        _, mgr_headers = ctx["manager"]

        response = await client.put(
            f"/api/projects/{ctx['project_id']}",
            headers=mgr_headers,
            json={"description": "updated by project manager"},
        )

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 1.6-API-004: member 权限边界
# Priority: P1
# ---------------------------------------------------------------------------
class TestMemberPermissions:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_member_cannot_modify_project_config(self, client, db_session):
        """AC: 普通成员不可改项目配置"""
        ctx = await _setup_project_with_roles(client, db_session)
        _, member_headers = ctx["member"]

        response = await client.put(
            f"/api/projects/{ctx['project_id']}",
            headers=member_headers,
            json={"description": "member tried to update"},
        )

        assert response.status_code == 403

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_member_cannot_manage_members(self, client, db_session):
        """AC: 普通成员不可管理成员"""
        ctx = await _setup_project_with_roles(client, db_session)
        _, member_headers = ctx["member"]
        new_user = await create_test_user(db_session, username="rbac_mem_add", role="user")

        response = await client.post(
            f"/api/projects/{ctx['project_id']}/members",
            headers=member_headers,
            json={"userId": str(new_user.id), "role": "member"},
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PROJECT_ROLE_DENIED"

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_member_can_read_project_data(self, client, db_session):
        """AC: 普通成员可查看项目数据"""
        ctx = await _setup_project_with_roles(client, db_session)
        _, member_headers = ctx["member"]

        response = await client.get(
            f"/api/projects/{ctx['project_id']}/members",
            headers=member_headers,
        )

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 1.6-API-006: 账号级游客 —— 写操作 → 403，且必须是**闸门**拦的
# Priority: P0
# ---------------------------------------------------------------------------
class TestGuestPermissions:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_guest_cannot_modify_project(self, client, db_session):
        """AC: 游客写操作返回 403"""
        ctx = await _setup_project_with_roles(client, db_session)
        _, guest_headers = ctx["guest"]

        response = await client.put(
            f"/api/projects/{ctx['project_id']}",
            headers=guest_headers,
            json={"description": "guest tried"},
        )

        assert response.status_code == 403

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_guest_cannot_write_even_where_a_member_can(self, client, db_session):
        """AC: 游客拿不到**成员拿得到**的写权限 —— 这条才证明封顶在起作用。

        选建用例这个端点，因为它对 member 是放行的（下一条就是对照组）。
        若挑一个 member 也写不了的端点，游客 403 说明不了任何事。
        """
        ctx = await _setup_project_with_roles(client, db_session)
        _, guest_headers = ctx["guest"]
        _, admin_headers = ctx["admin"]

        br = await client.get(f"/api/projects/{ctx['project_id']}/branches", headers=admin_headers)
        bid = br.json()["data"][0]["id"]
        payload = {"title": "游客不该建出来的用例", "type": "api", "module": "rbac",
                   "priority": "P2", "steps": [{"action": "x"}]}

        response = await client.post(
            f"/api/projects/{ctx['project_id']}/branches/{bid}/cases",
            headers=guest_headers, json=payload,
        )
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "GUEST_READONLY", response.text

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_a_member_can_do_that_very_same_write(self, client, db_session):
        """对照组。少了它，上一条可能只是因为那个端点谁都写不了 —— 假绿。"""
        ctx = await _setup_project_with_roles(client, db_session)
        _, member_headers = ctx["member"]
        _, admin_headers = ctx["admin"]

        br = await client.get(f"/api/projects/{ctx['project_id']}/branches", headers=admin_headers)
        bid = br.json()["data"][0]["id"]

        response = await client.post(
            f"/api/projects/{ctx['project_id']}/branches/{bid}/cases",
            headers=member_headers,
            json={"title": "成员建得出来的用例", "type": "api", "module": "rbac",
                  "priority": "P2", "steps": [{"action": "x"}]},
        )
        assert response.status_code in (200, 201), response.text

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_guest_can_read_project_data(self, client, db_session):
        """AC: 游客可以查看数据 —— 封顶砍的是写，不是读。"""
        ctx = await _setup_project_with_roles(client, db_session)
        _, guest_headers = ctx["guest"]

        response = await client.get(
            f"/api/projects/{ctx['project_id']}/members",
            headers=guest_headers,
        )

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 1.6-API-007/008: 装饰器验证
# Priority: P0
# ---------------------------------------------------------------------------
class TestRoleDecorators:

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_require_role_blocks_non_admin(self, client, db_session):
        """AC: @require_role 装饰器正确拦截系统级权限"""
        user = await create_test_user(db_session, username="deco_user", role="user")
        headers, _ = make_auth_headers(user)

        response = await client.get("/api/users", headers=headers)
        assert response.status_code == 403

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_require_role_allows_admin(self, client, db_session):
        """AC: @require_role 允许 admin 通过"""
        admin = await create_test_user(db_session, username="deco_admin", role="admin")
        headers, _ = make_auth_headers(admin)

        response = await client.get("/api/users", headers=headers)
        assert response.status_code == 200

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_require_project_role_blocks_unbound(self, client, db_session):
        """AC: @require_project_role 装饰器正确拦截项目级权限"""
        ctx = await _setup_project_with_roles(client, db_session)
        _, unbound_headers = ctx["unbound"]

        response = await client.get(
            f"/api/projects/{ctx['project_id']}/members",
            headers=unbound_headers,
        )
        assert response.status_code == 403

    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_require_project_role_allows_member(self, client, db_session):
        """AC: @require_project_role 允许项目成员通过"""
        ctx = await _setup_project_with_roles(client, db_session)
        _, member_headers = ctx["member"]

        response = await client.get(
            f"/api/projects/{ctx['project_id']}/members",
            headers=member_headers,
        )
        assert response.status_code == 200
