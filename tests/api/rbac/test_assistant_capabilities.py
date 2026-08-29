"""E2E：AI 助手能力面 = 页面动作 ∩ 当前用户权限；写操作落审计(actor_type=assistant)。

Test ID: 1.6-API-ASSIST
Priority: P0

打真接口，验证三件事在端到端链路上成立：
- /capabilities 按当前用户在项目语境下的持有权限过滤（游客只读、成员有写、admin 全权）；
- /execute 复检权限（非成员把 tool 名字直接 POST 上来 → 403 PERMISSION_DENIED）；
- 写操作成功后，audit_logs 里有一条 actor_type='assistant' 的记录（与人/外部 CC 区分）。

2026-08-29 换主语：原来的只读主体是项目角色 `viewer`，该档已退役，
现在是**系统角色 `guest`**（硬封顶只读）。注意两者对 `/execute` 的拒绝**不是同一条路**：
游客先被 `deps/auth` 的非 GET 闸门挡下（GUEST_READONLY），根本走不到复检；
所以复检那条覆盖改用「非项目成员」来打，两条路各测各的，别让一条把另一条盖住。
"""
import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.project import ProjectMember
from tests.conftest import create_test_project, create_test_user, make_auth_headers


class TestAssistantCapabilities:
    async def _setup(self, client, db_session):
        admin = await create_test_user(db_session, username="asst_admin", role="admin")
        admin_headers, _ = make_auth_headers(admin)
        pid = await create_test_project(client, admin_headers, "asst-proj")

        # 只读主体：系统游客，项目角色照样是 member（2 档模型里没有只读档）
        guest = await create_test_user(db_session, username="asst_guest", role="guest")
        member = await create_test_user(db_session, username="asst_member", role="user")
        outsider = await create_test_user(db_session, username="asst_outsider", role="user")
        db_session.add(ProjectMember(project_id=pid, user_id=guest.id, role="member"))
        db_session.add(ProjectMember(project_id=pid, user_id=member.id, role="member"))
        await db_session.flush()
        return {"pid": pid, "admin": admin, "admin_headers": admin_headers,
                "guest": guest, "member": member, "outsider": outsider}

    # ── 能力面按角色过滤 ──────────────────────────────────────────
    @pytest.mark.asyncio
    async def test_guest_capabilities_are_read_only(self, client, db_session):
        ctx = await self._setup(client, db_session)
        headers, _ = make_auth_headers(ctx["guest"])
        r = await client.get(f"/api/assistant/capabilities?project_id={ctx['pid']}", headers=headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        caps = data["capabilities"]
        keys = {c["key"] for c in caps}
        assert "list_cases" in keys              # 读得到
        assert "create_case" not in keys         # 写不到
        assert "create_environment" not in keys
        assert "run_plan" not in keys
        # 项目级写操作一律不可见 —— 看得见却改不了才是漏洞
        assert all(not (c["scope"] == "project" and c["mutates"]) for c in caps)
        assert data["isSuperAdmin"] is False   # 响应过驼峰中间件：is_super_admin → isSuperAdmin

    @pytest.mark.asyncio
    async def test_guest_sees_exactly_the_five_read_tools(self, client, db_session):
        """封顶之后能力面恰好是这 5 个 —— 写成集合相等，不是"包含"。

        「包含」式断言漏不掉少给的，但漏得掉**多给的**，而多给正是封顶失效的症状。
        list_projects 的 permission 是 None（谁都能列自己的项目），其余四个要 project.read。
        """
        ctx = await self._setup(client, db_session)
        headers, _ = make_auth_headers(ctx["guest"])
        r = await client.get(f"/api/assistant/capabilities?project_id={ctx['pid']}", headers=headers)
        keys = {c["key"] for c in r.json()["data"]["capabilities"]}
        assert keys == {"list_projects", "list_cases", "list_environments",
                        "list_global_variables", "list_plans"}, keys

    @pytest.mark.asyncio
    async def test_member_capabilities_include_writes(self, client, db_session):
        ctx = await self._setup(client, db_session)
        headers, _ = make_auth_headers(ctx["member"])
        r = await client.get(f"/api/assistant/capabilities?project_id={ctx['pid']}", headers=headers)
        assert r.status_code == 200, r.text
        keys = {c["key"] for c in r.json()["data"]["capabilities"]}
        assert {"create_case", "create_environment", "set_global_variable", "run_plan"} <= keys

    @pytest.mark.asyncio
    async def test_admin_capabilities_are_full(self, client, db_session):
        ctx = await self._setup(client, db_session)
        r = await client.get(f"/api/assistant/capabilities?project_id={ctx['pid']}", headers=ctx["admin_headers"])
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["isSuperAdmin"] is True
        # 目录全集 —— 跟目录本身对，别写死数字：加一个工具就得改测试，而那不是回归
        from app.services.assistant import catalog
        assert {c["key"] for c in data["capabilities"]} == {t.key for t in catalog.TOOLS}

    @pytest.mark.asyncio
    async def test_capabilities_without_project_are_system_only(self, client, db_session):
        ctx = await self._setup(client, db_session)
        headers, _ = make_auth_headers(ctx["member"])
        r = await client.get("/api/assistant/capabilities", headers=headers)
        assert r.status_code == 200, r.text
        keys = {c["key"] for c in r.json()["data"]["capabilities"]}
        # 不在项目语境里 → 只剩系统级（列项目 + 建项目，后者靠系统角色 user 的 project.create）
        assert keys == {"list_projects", "create_project"}

    @pytest.mark.asyncio
    async def test_guest_without_project_has_only_list_projects(self, client, db_session):
        """游客的系统权限是**空集** —— 连建项目都没有。

        这条钉住一个容易走反的设计：游客的 project.read 是「在某个项目里当成员」挣来的，
        不是系统角色白送的。写成白送的话，「游客且不属于任何项目」会凭空能读。
        """
        ctx = await self._setup(client, db_session)
        headers, _ = make_auth_headers(ctx["guest"])
        r = await client.get("/api/assistant/capabilities", headers=headers)
        assert r.status_code == 200, r.text
        keys = {c["key"] for c in r.json()["data"]["capabilities"]}
        assert keys == {"list_projects"}, keys

    # ── 执行复检权限 ──────────────────────────────────────────────
    @pytest.mark.asyncio
    async def test_outsider_execute_write_denied_by_recheck(self, client, db_session):
        """非成员直接把写操作 POST 上来（绕过能力面）→ execute 复检仍拒。

        主语用非成员而不是游客：游客会先被非 GET 闸门挡掉，测不到这里的复检。
        """
        ctx = await self._setup(client, db_session)
        headers, _ = make_auth_headers(ctx["outsider"])
        r = await client.post("/api/assistant/execute", headers=headers, json={
            "project_id": ctx["pid"], "tool": "create_environment", "args": {"name": "asst-hack-env"},
        })
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "PERMISSION_DENIED"

    @pytest.mark.asyncio
    async def test_guest_execute_is_stopped_by_the_gate_before_the_recheck(self, client, db_session):
        """游客连读操作都进不了 /execute —— 因为它整条不在白名单里。

        这是**故意**的：`/execute` 会落库，按端点粒度放行读工具等于把
        「哪个 tool 是读」的判断从闸门挪到 catalog，多一处会漂的判断。
        游客要读，走 /capabilities 和页面本身（都是 GET）。
        """
        ctx = await self._setup(client, db_session)
        headers, _ = make_auth_headers(ctx["guest"])
        r = await client.post("/api/assistant/execute", headers=headers, json={
            "project_id": ctx["pid"], "tool": "list_cases", "args": {},
        })
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "GUEST_READONLY", r.text

    @pytest.mark.asyncio
    async def test_guest_chat_still_works(self, client, db_session):
        """对照：/chat 在白名单里（只出提案不落库），游客拿得到。

        少了这条，上一条可能只是因为"游客打不了任何 POST"—— 那就说明白名单整个失效了，
        而这是个静默故障：所有测试都还是绿的（拒绝得更多而已），只是游客再也改不了自己的密码。
        """
        ctx = await self._setup(client, db_session)
        headers, _ = make_auth_headers(ctx["guest"])
        r = await client.post("/api/assistant/chat", headers=headers, json={
            "project_id": ctx["pid"], "messages": [{"role": "user", "content": "我能干什么"}],
        })
        assert r.status_code != 403, r.text

    @pytest.mark.asyncio
    async def test_member_execute_write_allowed_and_audited(self, client, db_session):
        ctx = await self._setup(client, db_session)
        headers, _ = make_auth_headers(ctx["member"])
        r = await client.post("/api/assistant/execute", headers=headers, json={
            "project_id": ctx["pid"], "tool": "create_environment",
            "args": {"name": "asst-e2e-env", "description": "由助手创建"},
        })
        assert r.status_code == 200, r.text
        body = r.json()["data"]
        assert body["tool"] == "create_environment"
        assert body["mutates"] is True
        assert body["result"]["name"] == "asst-e2e-env"

        rows = (await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "create_environment",
                AuditLog.actor_type == "assistant",
            )
        )).scalars().all()
        assert len(rows) >= 1
        assert rows[0].actor_label == "AI 助手"
        assert rows[0].target_type == "assistant_action"

    @pytest.mark.asyncio
    async def test_member_execute_read_allowed(self, client, db_session):
        ctx = await self._setup(client, db_session)
        headers, _ = make_auth_headers(ctx["member"])
        r = await client.post("/api/assistant/execute", headers=headers, json={
            "project_id": ctx["pid"], "tool": "list_cases", "args": {},
        })
        assert r.status_code == 200, r.text
        assert "cases" in r.json()["data"]["result"]

    # ── 入参/边界 ────────────────────────────────────────────────
    @pytest.mark.asyncio
    async def test_unknown_tool_rejected(self, client, db_session):
        ctx = await self._setup(client, db_session)
        r = await client.post("/api/assistant/execute", headers=ctx["admin_headers"], json={
            "project_id": ctx["pid"], "tool": "drop_database", "args": {},
        })
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "UNKNOWN_TOOL"

    @pytest.mark.asyncio
    async def test_project_tool_requires_project_id(self, client, db_session):
        ctx = await self._setup(client, db_session)
        r = await client.post("/api/assistant/execute", headers=ctx["admin_headers"], json={
            "tool": "list_cases", "args": {},   # 缺 project_id
        })
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "PROJECT_REQUIRED"

    @pytest.mark.asyncio
    async def test_list_global_variables_hides_values(self, client, db_session):
        """助手列全局变量只回 key/描述/有没有值，绝不回明文 —— 全局变量可能存密码类。"""
        ctx = await self._setup(client, db_session)
        r = await client.post("/api/assistant/execute", headers=ctx["admin_headers"], json={
            "project_id": ctx["pid"], "tool": "list_global_variables", "args": {},
        })
        assert r.status_code == 200, r.text
        variables = r.json()["data"]["result"]["variables"]
        assert len(variables) >= 1   # 新项目自带 5 个默认全局变量
        for v in variables:
            assert "value" not in v          # 绝不回明文（真正的安全属性）
            assert "hasValue" in v           # 只回「有没有值」；has_value → hasValue（驼峰中间件）
