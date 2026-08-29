"""
test_project_role_access — 项目级角色权限验证
Test ID: 1.6-API-001
Priority: P0

2026-08-29 按新角色模型重写（迁移 `zzx0role3`）：
项目角色只剩 `manager` / `member`，`project_admin`/`developer`/`tester`/`viewer`/`guest`
四个旧名全部折进这两档 —— 折叠前后这个文件测的**边界没变**：
「能管成员 vs 不能管成员」「是成员 vs 不是成员」，只是档位名换了。

只读语义上移到了**账号层**（系统角色 `guest`），所以本文件末尾多了一组：
游客即便被加成 `member`，写端点仍然 403。那一组才是「封顶真不真」的证据 ——
项目角色守卫是放它过的（元组就是 `("manager","member")`），拦住它的是另一条腿。
"""
import pytest
from sqlalchemy import func, select

from app.models.project import ProjectMember
from tests.conftest import create_test_user, make_auth_headers


class TestProjectRoleAccess:
    """require_project_role：不同角色的访问控制"""

    async def _setup_project_with_members(self, client, db_session):
        """辅助方法：创建项目并绑定不同角色的用户"""
        admin = await create_test_user(db_session, username="rbac_admin", role="admin")
        admin_headers, _ = make_auth_headers(admin)

        # 创建项目
        r = await client.post("/api/projects", headers=admin_headers, json={
            "name": "rbac-proj", "gitUrl": "git@x.com:r.git", "scriptBasePath": "/rbac",
        })
        project_id = r.json()["data"]["id"]

        # 创建各角色用户并绑定到项目
        manager_user = await create_test_user(db_session, username="rbac_mgr", role="user")
        member_user = await create_test_user(db_session, username="rbac_member", role="user")
        # 系统游客 —— 项目角色照样是 member（2 档模型里没有"只读档"可选）
        guest_user = await create_test_user(db_session, username="rbac_guest", role="guest")
        unbound_user = await create_test_user(db_session, username="rbac_unbound", role="user")

        for user, role in [
            (manager_user, "manager"),
            (member_user, "member"),
            (guest_user, "member"),
        ]:
            db_session.add(ProjectMember(project_id=project_id, user_id=user.id, role=role))
        await db_session.flush()

        return {
            "project_id": project_id,
            "admin": admin,
            "manager": manager_user,
            "member": member_user,
            "guest": guest_user,
            "unbound": unbound_user,
        }

    @pytest.mark.asyncio
    async def test_admin_bypasses_project_role(self, client, db_session):
        # Given: 系统 admin（未绑定到项目也可以）
        ctx = await self._setup_project_with_members(client, db_session)
        headers, _ = make_auth_headers(ctx["admin"])

        # When: 访问成员列表
        response = await client.get(f"/api/projects/{ctx['project_id']}/members", headers=headers)

        # Then: 200 通过
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_manager_can_add_member(self, client, db_session):
        # Given: 项目管理员
        ctx = await self._setup_project_with_members(client, db_session)
        new_user = await create_test_user(db_session, username="rbac_new", role="user")
        headers, _ = make_auth_headers(ctx["manager"])

        # When: 添加新成员
        response = await client.post(
            f"/api/projects/{ctx['project_id']}/members", headers=headers,
            json={"userId": str(new_user.id), "role": "member"},
        )

        # Then: 201 成功
        assert response.status_code == 201, response.text

    @pytest.mark.asyncio
    async def test_member_can_view_members(self, client, db_session):
        # Given: 普通成员
        ctx = await self._setup_project_with_members(client, db_session)
        headers, _ = make_auth_headers(ctx["member"])

        # When: 查看成员列表（所有成员都可查看）
        response = await client.get(f"/api/projects/{ctx['project_id']}/members", headers=headers)

        # Then: 200
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_member_cannot_add_member(self, client, db_session):
        # Given: 普通成员
        ctx = await self._setup_project_with_members(client, db_session)
        new_user = await create_test_user(db_session, username="rbac_blocked", role="user")
        headers, _ = make_auth_headers(ctx["member"])

        # When: 尝试添加成员
        response = await client.post(
            f"/api/projects/{ctx['project_id']}/members", headers=headers,
            json={"userId": str(new_user.id), "role": "member"},
        )

        # Then: 403
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PROJECT_ROLE_DENIED"

    @pytest.mark.asyncio
    async def test_unbound_user_rejected(self, client, db_session):
        # Given: 未绑定到项目的用户
        ctx = await self._setup_project_with_members(client, db_session)
        headers, _ = make_auth_headers(ctx["unbound"])

        # When: 尝试访问成员列表
        response = await client.get(f"/api/projects/{ctx['project_id']}/members", headers=headers)

        # Then: 403
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "NOT_PROJECT_MEMBER"

    @pytest.mark.asyncio
    async def test_legacy_role_name_is_rejected_not_silently_accepted(self, client, db_session):
        """旧角色名（tester/viewer/…）现在写不进去 —— 而且要**当场**拒。

        本轮把 4 个旧名折进 member 之后，若接口仍收下 `role="tester"`，
        它会在库里躺成一个 CHECK 约束挡不住的空档（Pydantic 在约束之前），
        或者被约束拦成 500。要的是 422：调用方立刻知道自己该改。
        """
        ctx = await self._setup_project_with_members(client, db_session)
        new_user = await create_test_user(db_session, username="rbac_legacy", role="user")
        headers, _ = make_auth_headers(ctx["admin"])

        response = await client.post(
            f"/api/projects/{ctx['project_id']}/members", headers=headers,
            json={"userId": str(new_user.id), "role": "tester"},
        )
        assert response.status_code == 422, response.text


class TestGuestCeilingIsRealNotJustDisplayed:
    """系统游客 = 硬封顶只读。**这一组是封顶真不真的证据。**

    要点：这里的游客在项目里的角色是 `member` —— 项目角色守卫
    （元组 `("manager","member")`）是**放他过**的。拦住他的是另一条腿：
    `deps/auth._enforce_guest_readonly` 的非 GET 闸门。
    所以这几条一旦变红，说明封顶退化成了"只在 /api/me/permissions 里自报"，
    也就是 operator 当年那个错误又犯了一遍。
    """

    async def _setup(self, client, db_session):
        admin = await create_test_user(db_session, username="ceil_admin", role="admin")
        admin_headers, _ = make_auth_headers(admin)
        r = await client.post("/api/projects", headers=admin_headers, json={"name": "ceil-proj"})
        pid = r.json()["data"]["id"]
        guest = await create_test_user(db_session, username="ceil_guest", role="guest")
        db_session.add(ProjectMember(project_id=pid, user_id=guest.id, role="member"))
        await db_session.flush()
        guest_headers, _ = make_auth_headers(guest)
        return pid, guest_headers, admin_headers

    @pytest.mark.asyncio
    async def test_guest_member_can_still_read(self, client, db_session):
        pid, guest_headers, _ = await self._setup(client, db_session)
        r = await client.get(f"/api/projects/{pid}/members", headers=guest_headers)
        assert r.status_code == 200, r.text

    @pytest.mark.asyncio
    async def test_guest_member_cannot_write_and_nothing_lands_in_db(self, client, db_session):
        """403 不等于没写 —— 所以查库确认。

        写端点选环境创建：它对 `member` 是**放行**的（同一个请求换成普通 member 会 201），
        所以这条红了就是闸门没生效，而不是"这个端点本来就不让成员写"。
        """
        pid, guest_headers, _ = await self._setup(client, db_session)
        from app.models.environment import Environment

        before = (await db_session.execute(
            select(func.count()).select_from(Environment).where(Environment.project_id == pid)
        )).scalar_one()

        r = await client.post(f"/api/projects/{pid}/environments", headers=guest_headers,
                              json={"name": "guest-should-not-create"})
        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "GUEST_READONLY"

        after = (await db_session.execute(
            select(func.count()).select_from(Environment).where(Environment.project_id == pid)
        )).scalar_one()
        assert after == before, "403 了但环境还是建出来了 —— 闸门抛在了副作用之后"

    @pytest.mark.asyncio
    async def test_a_plain_member_can_do_the_very_same_write(self, client, db_session):
        """对照组。少了它，上一条可能只是因为那个端点谁都写不了 —— 假绿。"""
        pid, _, admin_headers = await self._setup(client, db_session)
        member = await create_test_user(db_session, username="ceil_member", role="user")
        db_session.add(ProjectMember(project_id=pid, user_id=member.id, role="member"))
        await db_session.flush()
        headers, _ = make_auth_headers(member)

        r = await client.post(f"/api/projects/{pid}/environments", headers=headers,
                              json={"name": "member-can-create"})
        assert r.status_code in (200, 201), r.text

    @pytest.mark.asyncio
    async def test_guest_can_change_own_password(self, client, db_session):
        """白名单没误伤：拿不到这条，游客账号就永远改不了自己的密码。"""
        _, guest_headers, _ = await self._setup(client, db_session)
        from tests.conftest import TEST_PASSWORD

        r = await client.post("/api/auth/change-password", headers=guest_headers, json={
            "oldPassword": TEST_PASSWORD, "newPassword": "GuestNewPass123!",
        })
        assert r.status_code == 200, r.text

    @pytest.mark.asyncio
    async def test_guest_permissions_payload_is_exactly_project_read(self, client, db_session):
        """呈现那条腿：前端/助手看到的权限点恰好只有 project.read。"""
        pid, guest_headers, _ = await self._setup(client, db_session)
        r = await client.get(f"/api/me/permissions?project_id={pid}", headers=guest_headers)
        assert r.status_code == 200, r.text
        assert set(r.json()["data"]["permissions"]) == {"project.read"}
