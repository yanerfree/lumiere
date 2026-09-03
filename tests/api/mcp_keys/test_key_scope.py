"""MCP Key 的工具范围 —— 打真接口，验「项目范围 ∩ Key 范围」这条链。

和 backend/tests/test_project_mcp_scope.py 分工：那边测 `pick_scope` 这条**纯判据**
和几处源码封样，这边测**跨请求**的部分 —— 建 Key 时勾的东西存进去了没有、
换项目会不会把它清掉、改完项目范围之后同一把 Key 的生效范围跟不跟着变。
后者只有在真库 + 真路由上才成立。

⚠ 这里**不测 MCP 那条 :18800 通道本身**（tools/list 到底露了几个）。
那条要真起 fastmcp 会话，见 tests/integration/mcp/。这里管的是"范围算得对不对、
存得住不住"，那是页面和 CC 都直接依赖的一层。
"""
import pytest

from tests.conftest import create_test_project, create_test_user, make_auth_headers

# 都是注册表里真实存在的工具名 —— `_validate_tools` 会把不存在的名字直接丢掉，
# 拿假名字写测试会得到一个"范围莫名变空"的假红。
T_PROJ = "lum_list_projects"
T_CASE = "lum_get_case"
T_LIST = "lum_list_cases"


async def _admin(db_session, username="scope_admin"):
    u = await create_test_user(db_session, username=username, role="admin")
    headers, _ = make_auth_headers(u)
    return headers


async def _new_key(client, h, *, project_id, name="k", tools=None):
    body = {"name": name, "projectId": project_id}
    if tools is not None:
        body["allowedTools"] = tools
    r = await client.post("/api/mcp-keys", headers=h, json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]


async def _set_project_scope(client, h, pid, tools):
    body = {"allowedTools": tools} if tools is not None else {"resetTools": True}
    r = await client.put(f"/api/projects/{pid}/mcp-scope", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def _one(client, h, key_id):
    r = await client.get("/api/mcp-keys", headers=h)
    assert r.status_code == 200, r.text
    got = [k for k in r.json()["data"] if k["id"] == key_id]
    assert got, f"{key_id} 不在列表里"
    return got[0]


class TestKeyScope:

    async def test_默认跟随项目范围(self, client, db_session):
        """不勾任何东西 = 跟随项目。这是今天所有存量 Key 的状态，
        也是新建时的默认档 —— 它必须落成 NULL 而不是空数组。"""
        h = await _admin(db_session)
        pid = await create_test_project(client, h, "范围-默认")
        await _set_project_scope(client, h, pid, [T_PROJ, T_CASE])
        d = await _new_key(client, h, project_id=pid)
        assert d["allowedTools"] is None
        assert d["scope"]["followsProject"] is True
        assert sorted(d["scope"]["effectiveTools"]) == sorted([T_PROJ, T_CASE])
        assert d["scope"]["effectiveCount"] == 2

    async def test_Key自己收窄生效(self, client, db_session):
        h = await _admin(db_session)
        pid = await create_test_project(client, h, "范围-收窄")
        await _set_project_scope(client, h, pid, [T_PROJ, T_CASE, T_LIST])
        d = await _new_key(client, h, project_id=pid, tools=[T_CASE])
        assert d["scope"]["followsProject"] is False
        assert d["scope"]["effectiveTools"] == [T_CASE]
        assert d["scope"]["blockedByProject"] == []

    async def test_勾了项目范围外的会被挡掉而且说出来(self, client, db_session):
        """交集只能收窄。勾了天花板外的工具**不会**让这把 Key 反向扩出去 ——
        但也不能悄悄丢掉，页面要显示「有 N 个被项目范围挡住了」。"""
        h = await _admin(db_session)
        pid = await create_test_project(client, h, "范围-越界")
        await _set_project_scope(client, h, pid, [T_PROJ])
        d = await _new_key(client, h, project_id=pid, tools=[T_PROJ, T_CASE])
        assert d["scope"]["effectiveTools"] == [T_PROJ]
        assert d["scope"]["blockedByProject"] == [T_CASE]

    async def test_项目不限时Key那份就是全部(self, client, db_session):
        """项目范围 NULL = 天花板不限，此时生效范围就是 Key 那份，
        **不是**"两个都没设所以不限制"。"""
        h = await _admin(db_session)
        pid = await create_test_project(client, h, "范围-天花板不限")
        d = await _new_key(client, h, project_id=pid, tools=[T_CASE])
        assert d["scope"]["effectiveTools"] == [T_CASE]
        assert d["scope"]["blockedByProject"] == []

    async def test_两层都不设才是不限制(self, client, db_session):
        h = await _admin(db_session)
        pid = await create_test_project(client, h, "范围-全不限")
        d = await _new_key(client, h, project_id=pid)
        sc = d["scope"]
        assert sc["effectiveTools"] is None
        # 不限制时生效数是**全量**，不是 0 —— 回 0 的话页面上「生效 0 / 63」
        # 和"这把 Key 什么都干不了"长得一模一样
        assert sc["effectiveCount"] == sc["totalTools"] > 0

    async def test_空列表是一个都不给_不是不限制(self, client, db_session):
        """`[]` 曾经被 `if raw else None` 当成"不限制"，方向完全反了。
        现在它就是空：连上来一个工具都没有。"""
        h = await _admin(db_session)
        pid = await create_test_project(client, h, "范围-空列表")
        d = await _new_key(client, h, project_id=pid, tools=[])
        assert d["allowedTools"] == []
        assert d["scope"]["followsProject"] is False
        assert d["scope"]["effectiveTools"] == []
        assert d["scope"]["effectiveCount"] == 0

    async def test_改项目范围_Key的生效范围跟着变(self, client, db_session):
        """Key 那份不动，天花板降下来 → 生效范围跟着收。
        这是"项目范围是天花板"的实际含义，也是最容易被缓存住的一处。"""
        h = await _admin(db_session)
        pid = await create_test_project(client, h, "范围-天花板变化")
        await _set_project_scope(client, h, pid, [T_PROJ, T_CASE])
        d = await _new_key(client, h, project_id=pid, tools=[T_PROJ, T_CASE])
        assert sorted(d["scope"]["effectiveTools"]) == sorted([T_PROJ, T_CASE])
        await _set_project_scope(client, h, pid, [T_PROJ])
        row = await _one(client, h, d["id"])
        assert row["scope"]["effectiveTools"] == [T_PROJ]
        assert row["scope"]["blockedByProject"] == [T_CASE]
        # Key 上存的那份一个字没动 —— 天花板再放开时它得原样回来
        assert sorted(row["allowedTools"]) == sorted([T_PROJ, T_CASE])
        await _set_project_scope(client, h, pid, None)
        row = await _one(client, h, d["id"])
        assert sorted(row["scope"]["effectiveTools"]) == sorted([T_PROJ, T_CASE])

    async def test_换项目不清掉Key那份收窄(self, client, db_session):
        """2026-09-03 之前 PATCH 绑项目会把 Key 上那份清成 NULL。
        代价是**换个项目就把人挑好的工具悄悄清空**，页面上完全看不出发生了什么。"""
        h = await _admin(db_session)
        p1 = await create_test_project(client, h, "范围-原项目")
        p2 = await create_test_project(client, h, "范围-新项目")
        await _set_project_scope(client, h, p1, [T_PROJ, T_CASE])
        await _set_project_scope(client, h, p2, [T_CASE, T_LIST])
        d = await _new_key(client, h, project_id=p1, tools=[T_CASE])
        r = await client.patch(f"/api/mcp-keys/{d['id']}", headers=h,
                               json={"projectId": p2})
        assert r.status_code == 200, r.text
        got = r.json()["data"]
        assert got["allowedTools"] == [T_CASE], "换项目不该清掉 Key 自己那份"
        assert got["scope"]["effectiveTools"] == [T_CASE]

    async def test_显式改成跟随项目(self, client, db_session):
        """`resetTools` 是唯一一条"清成跟随项目"的路 —— JSON 里 null 表达不了
        "不改这个字段"和"改成跟随"的区别，所以必须有这个显式开关。"""
        h = await _admin(db_session)
        pid = await create_test_project(client, h, "范围-改回跟随")
        await _set_project_scope(client, h, pid, [T_PROJ, T_CASE])
        d = await _new_key(client, h, project_id=pid, tools=[T_CASE])
        r = await client.patch(f"/api/mcp-keys/{d['id']}", headers=h,
                               json={"resetTools": True})
        assert r.status_code == 200, r.text
        got = r.json()["data"]
        assert got["allowedTools"] is None
        assert got["scope"]["followsProject"] is True
        assert sorted(got["scope"]["effectiveTools"]) == sorted([T_PROJ, T_CASE])

    async def test_只改名字不动范围(self, client, db_session):
        """PATCH 不传 allowedTools / resetTools 时，范围必须一个字不变。
        改名把范围顺手清了是最典型的一种"不报错的坏"。"""
        h = await _admin(db_session)
        pid = await create_test_project(client, h, "范围-只改名")
        d = await _new_key(client, h, project_id=pid, tools=[T_CASE])
        r = await client.patch(f"/api/mcp-keys/{d['id']}", headers=h,
                               json={"name": "改了个名"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["allowedTools"] == [T_CASE]

    async def test_拼错的工具名存不进去(self, client, db_session):
        """存进去会让范围静默变窄：那条工具永远不出现，而没有任何报错。"""
        h = await _admin(db_session)
        pid = await create_test_project(client, h, "范围-拼错")
        d = await _new_key(client, h, project_id=pid,
                           tools=[T_CASE, "lum_根本没有这个工具"])
        assert d["allowedTools"] == [T_CASE]
        assert d["scope"]["staleTools"] == []

    @pytest.mark.parametrize("tools,expect_follow", [(None, True), ([T_CASE], False)])
    async def test_列表里每把Key都带生效范围(self, client, db_session, tools, expect_follow):
        """页面那一列（「N / 63 · 跟随项目 / 本 Key 收窄」）全靠它。
        列表不带 scope 的话，页面只能显示"我勾了什么"，而生效的是交集。"""
        h = await _admin(db_session)
        pid = await create_test_project(client, h, f"范围-列表{expect_follow}")
        await _set_project_scope(client, h, pid, [T_PROJ, T_CASE])
        d = await _new_key(client, h, project_id=pid, tools=tools)
        row = await _one(client, h, d["id"])
        assert row["scope"]["followsProject"] is expect_follow
        assert row["scope"]["totalTools"] > 0
