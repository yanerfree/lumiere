"""E2E：AI 助手能力面 = 页面动作 ∩ 当前用户权限；写操作落审计(actor_type=assistant)。

Test ID: 1.6-API-ASSIST
Priority: P0

打真接口，验证三件事在端到端链路上成立：
- /capabilities 按当前用户在项目语境下的持有权限过滤（viewer 只读、tester 有写、admin 全权）；
- /execute 复检权限（viewer 执行写操作 → 403 PERMISSION_DENIED，即便他把 tool 名字直接 POST 上来）；
- 写操作成功后，audit_logs 里有一条 actor_type='assistant' 的记录（与人/外部 CC 区分）。
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

        viewer = await create_test_user(db_session, username="asst_viewer", role="user")
        tester = await create_test_user(db_session, username="asst_tester", role="user")
        db_session.add(ProjectMember(project_id=pid, user_id=viewer.id, role="viewer"))
        db_session.add(ProjectMember(project_id=pid, user_id=tester.id, role="tester"))
        await db_session.flush()
        return {"pid": pid, "admin": admin, "admin_headers": admin_headers, "viewer": viewer, "tester": tester}

    # ── 能力面按角色过滤 ──────────────────────────────────────────
    @pytest.mark.asyncio
    async def test_viewer_capabilities_are_read_only(self, client, db_session):
        ctx = await self._setup(client, db_session)
        headers, _ = make_auth_headers(ctx["viewer"])
        r = await client.get(f"/api/assistant/capabilities?project_id={ctx['pid']}", headers=headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        caps = data["capabilities"]
        keys = {c["key"] for c in caps}
        assert "list_cases" in keys              # 读得到
        assert "create_case" not in keys         # 写不到
        assert "create_environment" not in keys
        assert "run_plan" not in keys
        # 项目级写操作一律不可见 —— viewer 看得见却改不了才是漏洞
        assert all(not (c["scope"] == "project" and c["mutates"]) for c in caps)
        assert data["isSuperAdmin"] is False   # 响应过驼峰中间件：is_super_admin → isSuperAdmin

    @pytest.mark.asyncio
    async def test_tester_capabilities_include_writes(self, client, db_session):
        ctx = await self._setup(client, db_session)
        headers, _ = make_auth_headers(ctx["tester"])
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
        assert len(data["capabilities"]) == 10  # 目录全集

    @pytest.mark.asyncio
    async def test_capabilities_without_project_are_system_only(self, client, db_session):
        ctx = await self._setup(client, db_session)
        headers, _ = make_auth_headers(ctx["viewer"])
        r = await client.get("/api/assistant/capabilities", headers=headers)
        assert r.status_code == 200, r.text
        keys = {c["key"] for c in r.json()["data"]["capabilities"]}
        # 不在项目里 → 只剩系统级（列项目 + 建项目，后者靠系统角色 user 的 project.create）
        assert keys == {"list_projects", "create_project"}

    # ── 执行复检权限 ──────────────────────────────────────────────
    @pytest.mark.asyncio
    async def test_viewer_execute_write_denied(self, client, db_session):
        ctx = await self._setup(client, db_session)
        headers, _ = make_auth_headers(ctx["viewer"])
        # viewer 直接把写操作 POST 上来（绕过能力面）→ execute 复检仍拒
        r = await client.post("/api/assistant/execute", headers=headers, json={
            "project_id": ctx["pid"], "tool": "create_environment", "args": {"name": "asst-hack-env"},
        })
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "PERMISSION_DENIED"

    @pytest.mark.asyncio
    async def test_tester_execute_write_allowed_and_audited(self, client, db_session):
        ctx = await self._setup(client, db_session)
        headers, _ = make_auth_headers(ctx["tester"])
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
    async def test_viewer_execute_read_allowed(self, client, db_session):
        ctx = await self._setup(client, db_session)
        headers, _ = make_auth_headers(ctx["viewer"])
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
