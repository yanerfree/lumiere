"""
test_report_dashboard — 报告仪表盘 API
Test ID: 5.1-API-001
Priority: P0
"""
import pytest

from tests.conftest import create_test_user, make_auth_headers


class TestReportDashboard:
    """GET /api/projects/{pid}/plans/{planId}/report"""

    async def _setup_completed_plan(self, client, db_session):
        """辅助：创建项目 + 用例 + 手动计划 + 执行完成"""
        admin = await create_test_user(db_session, username="rpt_admin", role="admin")
        headers, _ = make_auth_headers(admin)

        r = await client.post("/api/projects", headers=headers, json={"name": "rpt-proj"})
        pid = r.json()["data"]["id"]
        br = await client.get(f"/api/projects/{pid}/branches", headers=headers)
        bid = br.json()["data"][0]["id"]

        # 创建 3 条用例（2 个 auth 模块，1 个 users 模块）
        case_ids = []
        for name, module in [("登录测试", "auth"), ("登出测试", "auth"), ("创建用户", "users")]:
            cr = await client.post(f"/api/projects/{pid}/branches/{bid}/cases", headers=headers, json={
                "title": name, "type": "api", "module": module, "priority": "P0", "steps": [{"action": "test"}],
            })
            case_ids.append(cr.json()["data"]["id"])

        # 创建计划
        pr = await client.post(f"/api/projects/{pid}/plans", headers=headers, json={
            "name": "报告测试计划", "planType": "manual", "testType": "api", "caseIds": case_ids,
        })
        plan_id = pr.json()["data"]["id"]

        # 启动执行
        await client.post(f"/api/projects/{pid}/plans/{plan_id}/execute", headers=headers)

        # 获取 scenarios
        results = await client.get(f"/api/projects/{pid}/plans/{plan_id}/results", headers=headers)
        scenarios = results.json()["data"]["scenarios"]

        # 录入结果：2 passed + 1 failed
        await client.post(f"/api/projects/{pid}/plans/{plan_id}/manual-record", headers=headers, json={
            "scenarioId": scenarios[0]["id"], "status": "passed",
        })
        await client.post(f"/api/projects/{pid}/plans/{plan_id}/manual-record", headers=headers, json={
            "scenarioId": scenarios[1]["id"], "status": "passed",
        })
        await client.post(f"/api/projects/{pid}/plans/{plan_id}/manual-record", headers=headers, json={
            "scenarioId": scenarios[2]["id"], "status": "failed", "remark": "接口返回 500",
        })

        # 确认完成
        await client.post(f"/api/projects/{pid}/plans/{plan_id}/complete", headers=headers)

        return headers, pid, plan_id

    @pytest.mark.asyncio
    async def test_report_summary(self, client, db_session):
        headers, pid, plan_id = await self._setup_completed_plan(client, db_session)

        response = await client.get(f"/api/projects/{pid}/plans/{plan_id}/report", headers=headers)

        assert response.status_code == 200
        data = response.json()["data"]
        summary = data["summary"]
        assert summary["totalScenarios"] == 3
        assert summary["passed"] == 2
        assert summary["failed"] == 1
        assert summary["passRate"] is not None

    @pytest.mark.asyncio
    async def test_report_modules(self, client, db_session):
        headers, pid, plan_id = await self._setup_completed_plan(client, db_session)

        response = await client.get(f"/api/projects/{pid}/plans/{plan_id}/report", headers=headers)

        data = response.json()["data"]
        modules = data["modules"]
        assert len(modules) >= 2

        # auth 模块应该有 2 条（2 passed）。报告按 CaseFolder.name 分组，
        # 而 name 存的是建目录时人写的原样（这里用例传的 module 就是小写 auth）
        auth_mod = next((m for m in modules if m["module"] == "auth"), None)
        assert auth_mod is not None
        assert auth_mod["total"] == 2
        assert auth_mod["passed"] == 2

    @pytest.mark.asyncio
    async def test_report_before_execution_returns_null(self, client, db_session):
        admin = await create_test_user(db_session, username="rpt_null_admin", role="admin")
        headers, _ = make_auth_headers(admin)
        r = await client.post("/api/projects", headers=headers, json={"name": "rpt-null-proj"})
        pid = r.json()["data"]["id"]
        br = await client.get(f"/api/projects/{pid}/branches", headers=headers)
        bid = br.json()["data"][0]["id"]
        cr = await client.post(f"/api/projects/{pid}/branches/{bid}/cases", headers=headers, json={
            "title": "x", "type": "api", "module": "x", "priority": "P0", "steps": [{"action": "x"}],
        })
        pr = await client.post(f"/api/projects/{pid}/plans", headers=headers, json={
            "name": "未执行计划", "planType": "manual", "testType": "api", "caseIds": [cr.json()["data"]["id"]],
        })

        response = await client.get(f"/api/projects/{pid}/plans/{pr.json()['data']['id']}/report", headers=headers)
        assert response.status_code == 200
        assert response.json()["data"] is None
