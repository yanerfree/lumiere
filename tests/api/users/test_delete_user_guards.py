"""
test_delete_user_guards — 放开「删其他管理员」之后补的守卫

背景：2026-08-31 之前整个 admin 角色都删不掉（前端按 role 隐藏按钮、后端不设防），
结果是这一档**只进不出** —— 历史管理员账号只能先降级再删，中间那步没人记得做。

这里盯四条，第一条是**正面目标**，不是边界：
  1. 管理员之间必须删得动 —— 它挂了说明又被改回"整个角色一刀切"，正是这次要修的毛病；
  2. 内置 admin 删不掉；
  3. 删不掉自己（删完当场掉线，是这次放宽才够得着的新坑）；
  4. count_active_admins 的语义 —— 只数**启用中**的管理员。

第 4 条为什么在 service 层验、不在接口层：接口层的「最后一个管理员」那道守卫
**今天走 API 到不了** —— 调用方必过 require_role("admin")，停用账号又过不了认证，
所以调用方一定是启用管理员；第 3 条又保证他删的不是自己，于是删完他自己还在，
启用管理员数永远 ≥ 1。那道守卫留着是为了「第 3 条哪天被放宽」，它的判据正确性
只能在这一层验（详见 app/api/users.py 里 delete_user 的 docstring）。
"""
import pytest

from tests.conftest import create_test_user, make_auth_headers


class TestDeleteUserGuards:
    """DELETE /api/users/{id}：放开管理员互删之后的边界"""

    @pytest.mark.asyncio
    async def test_admin_can_delete_another_admin(self, client, db_session):
        """本次改动的正面目标 —— 挂了说明又被改回按角色一刀切了。"""
        # Given: 两个管理员
        actor = await create_test_user(db_session, username="guard_actor", role="admin")
        target = await create_test_user(db_session, username="guard_other_admin", role="admin")
        headers, _ = make_auth_headers(actor)

        # When: 删掉另一个管理员
        response = await client.delete(f"/api/users/{target.id}", headers=headers)

        # Then: 放行
        assert response.status_code == 200, response.text

    @pytest.mark.asyncio
    async def test_builtin_admin_cannot_be_deleted(self, client, db_session):
        # Given: 内置 admin，外加另一个管理员来执行删除
        builtin = await create_test_user(db_session, username="admin", role="admin")
        actor = await create_test_user(db_session, username="guard_actor2", role="admin")
        headers, _ = make_auth_headers(actor)

        # When: 删内置 admin
        response = await client.delete(f"/api/users/{builtin.id}", headers=headers)

        # Then: 403，且确认是「内置账号」这道拦的
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "BUILTIN_ADMIN_PROTECTED"

    @pytest.mark.asyncio
    async def test_cannot_delete_self(self, client, db_session):
        # Given: 当前登录的管理员
        actor = await create_test_user(db_session, username="guard_self", role="admin")
        headers, _ = make_auth_headers(actor)

        # When: 删自己
        response = await client.delete(f"/api/users/{actor.id}", headers=headers)

        # Then: 403
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "CANNOT_DELETE_SELF"

    @pytest.mark.asyncio
    async def test_deleting_a_deactivated_admin_is_allowed(self, client, db_session):
        """停用的管理员不占"还有人管"的名额，删他不该被兜底那道拦下。"""
        actor = await create_test_user(db_session, username="guard_last", role="admin")
        stale = await create_test_user(
            db_session, username="guard_stale_admin", role="admin", is_active=False
        )
        headers, _ = make_auth_headers(actor)

        response = await client.delete(f"/api/users/{stale.id}", headers=headers)

        assert response.status_code == 200, response.text

    @pytest.mark.asyncio
    async def test_count_active_admins_counts_only_active_admins(self, db_session):
        """兜底那道的判据本身 —— 停用的管理员和普通用户都不算数。

        这条**故意不走接口**：接口层今天到不了那道守卫（见模块 docstring）。
        判据错了的后果是保护静默失效，所以宁可在这一层钉死。
        """
        from app.services import user_service

        await create_test_user(db_session, username="cnt_active_admin", role="admin")
        inactive_admin = await create_test_user(
            db_session, username="cnt_inactive_admin", role="admin", is_active=False
        )
        plain_user = await create_test_user(db_session, username="cnt_plain_user", role="user")

        total = await user_service.count_active_admins(db_session)
        assert total >= 1

        # 停用的管理员本来就没被算进去，把他排除掉数字不该变
        assert await user_service.count_active_admins(
            db_session, exclude_id=inactive_admin.id
        ) == total, "停用管理员被错误地算成了「还有人管」"

        # 普通用户同理
        assert await user_service.count_active_admins(
            db_session, exclude_id=plain_user.id
        ) == total, "普通用户被错误地算成了管理员"
