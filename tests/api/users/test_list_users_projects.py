"""
test_list_users_projects — GET /api/users 带出「归属项目」
Test ID: 1.3-API-007
Priority: P1

用户管理页的「归属项目」列全靠这个字段。它跟别的列不一样的地方是：
**空值有两种含义**，前端要按它们分别渲染 ——
  · 普通用户 projects=[] → 他登进来什么都看不见（一个需要处理的状态）
  · 系统 admin projects=[] → 他能进全部项目（绕过成员绑定，见 deps/auth.py）
所以这里既验"有绑定的能带出来"，也验"admin 没绑定时确实是空数组"——
后者要是被谁改成"给 admin 塞上全部项目"，前端那句「全部项目」就会重复显示。
"""
import pytest

from tests.conftest import create_test_project, create_test_user, make_auth_headers


class TestListUsersProjects:
    """GET /api/users：每个用户带上他加入的项目"""

    @pytest.mark.asyncio
    async def test_projects_field_reflects_membership(self, client, db_session):
        # Given: 一个 admin、一个加了两个项目的人、一个谁也没加的人
        admin = await create_test_user(db_session, username="proj_admin", role="admin")
        # 建项目的人会被自动登记成该项目的 manager，所以"没有任何成员记录的 admin"
        # 得另找一个没建过项目的号来验，不能用上面这个。
        bare_admin = await create_test_user(db_session, username="proj_bare_admin", role="admin")
        joined = await create_test_user(db_session, username="proj_joined", role="user")
        lonely = await create_test_user(db_session, username="proj_lonely", role="user")
        headers, _ = make_auth_headers(admin)

        p_alpha = await create_test_project(client, headers, "用户列表归属项目-Alpha")
        p_beta = await create_test_project(client, headers, "用户列表归属项目-Beta")
        for pid, role in ((p_alpha, "manager"), (p_beta, "member")):
            r = await client.post(
                f"/api/projects/{pid}/members", headers=headers,
                json={"userId": str(joined.id), "role": role},
            )
            assert r.status_code in (200, 201), r.text

        # When: 拉用户列表
        resp = await client.get("/api/users", headers=headers)
        assert resp.status_code == 200
        by_name = {u["username"]: u for u in resp.json()["data"]}

        # Then: 加了项目的人带出两条，含项目名和**规范名**角色
        got = by_name["proj_joined"]["projects"]
        assert {(p["name"], p["role"]) for p in got} == {
            ("用户列表归属项目-Alpha", "manager"),
            ("用户列表归属项目-Beta", "member"),
        }
        assert all(p.get("id") for p in got), "项目 id 得带上，前端拿它做 key"

        # Then: 没加项目的人是空数组，不是缺字段 —— 前端 `user.projects.length` 直接取
        assert by_name["proj_lonely"]["projects"] == []

        # Then: 建项目的 admin 带出他自动获得的 manager 身份 —— 按成员表如实给
        assert {(p["name"], p["role"]) for p in by_name["proj_admin"]["projects"]} == {
            ("用户列表归属项目-Alpha", "manager"),
            ("用户列表归属项目-Beta", "manager"),
        }
        # Then: 没建过也没被加过的 admin 是空数组。**别在后端给 admin 塞上全部项目** ——
        # 「他能进全部项目」是权限推论，不是成员事实；混进来之后前端那句「全部项目」
        # 会和列表重复，而且再也分不出"他真被加进这个项目了"和"他只是管理员"。
        assert by_name["proj_bare_admin"]["projects"] == []

    @pytest.mark.asyncio
    async def test_membership_is_not_leaked_across_users(self, client, db_session):
        """两个人各加各的项目，别串行 —— 分组写错会把所有人的项目都挂给第一个人。"""
        admin = await create_test_user(db_session, username="xproj_admin", role="admin")
        a = await create_test_user(db_session, username="xproj_a", role="user")
        b = await create_test_user(db_session, username="xproj_b", role="user")
        headers, _ = make_auth_headers(admin)

        pid_a = await create_test_project(client, headers, "归属项目串号检查-A")
        pid_b = await create_test_project(client, headers, "归属项目串号检查-B")
        await client.post(f"/api/projects/{pid_a}/members", headers=headers,
                          json={"userId": str(a.id), "role": "member"})
        await client.post(f"/api/projects/{pid_b}/members", headers=headers,
                          json={"userId": str(b.id), "role": "member"})

        resp = await client.get("/api/users", headers=headers)
        by_name = {u["username"]: u for u in resp.json()["data"]}
        assert [p["name"] for p in by_name["xproj_a"]["projects"]] == ["归属项目串号检查-A"]
        assert [p["name"] for p in by_name["xproj_b"]["projects"]] == ["归属项目串号检查-B"]
