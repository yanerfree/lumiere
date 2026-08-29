"""
test_plan_reopen — POST /api/projects/{id}/plans/{planId}/reopen
Story 4.7: 计划重新打开
"""
import pytest

from tests.conftest import create_test_user, make_auth_headers


class TestPlanReopen:

    async def _setup(self, client, db_session):
        """创建项目 + 用例 + 计划 → 执行 → 完成"""
        admin = await create_test_user(db_session, username="reopen_admin", role="admin")
        h, _ = make_auth_headers(admin)

        r = await client.post("/api/projects", headers=h, json={
            "name": "reopen-proj", "gitUrl": "git@x.com:r.git", "scriptBasePath": "/p",
        })
        pid = r.json()["data"]["id"]
        br = await client.get(f"/api/projects/{pid}/branches", headers=h)
        bid = br.json()["data"][0]["id"]

        case_ids = []
        for i in range(2):
            cr = await client.post(f"/api/projects/{pid}/branches/{bid}/cases", headers=h, json={
                "title": f"reopen-case-{i}", "type": "api", "module": "TEST",
                "priority": "P0", "steps": [{"action": "test"}],
            })
            case_ids.append(cr.json()["data"]["id"])

        r = await client.post(f"/api/projects/{pid}/plans", headers=h, json={
            "name": "reopen-plan", "planType": "manual", "testType": "api", "caseIds": case_ids,
        })
        plan_id = r.json()["data"]["id"]

        # 执行 → 完成
        await client.post(f"/api/projects/{pid}/plans/{plan_id}/execute", headers=h)
        await client.post(f"/api/projects/{pid}/plans/{plan_id}/complete", headers=h)

        return h, pid, plan_id, admin

    @pytest.mark.asyncio
    async def test_reopen_completed_plan(self, client, db_session):
        """AC: 已完成计划可重新打开，状态变为 executing"""
        h, pid, plan_id, _ = await self._setup(client, db_session)

        response = await client.post(f"/api/projects/{pid}/plans/{plan_id}/reopen", headers=h)

        assert response.status_code == 200
        assert response.json()["data"]["status"] == "executing"

    @pytest.mark.asyncio
    async def test_reopen_draft_plan_returns_422(self, client, db_session):
        """AC: 非已完成状态不可重新打开"""
        admin = await create_test_user(db_session, username="reopen_admin2", role="admin")
        h, _ = make_auth_headers(admin)

        r = await client.post("/api/projects", headers=h, json={
            "name": "reopen-proj2", "gitUrl": "git@x.com:r2.git", "scriptBasePath": "/p2",
        })
        pid = r.json()["data"]["id"]
        br = await client.get(f"/api/projects/{pid}/branches", headers=h)
        bid = br.json()["data"][0]["id"]

        cr = await client.post(f"/api/projects/{pid}/branches/{bid}/cases", headers=h, json={
            "title": "c", "type": "api", "module": "T", "priority": "P0", "steps": [{"action": "t"}],
        })
        r = await client.post(f"/api/projects/{pid}/plans", headers=h, json={
            "name": "draft-plan", "planType": "manual", "testType": "api",
            "caseIds": [cr.json()["data"]["id"]],
        })
        plan_id = r.json()["data"]["id"]

        response = await client.post(f"/api/projects/{pid}/plans/{plan_id}/reopen", headers=h)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_reopen_preserves_results(self, client, db_session):
        """AC: 重新打开后已有执行结果保留"""
        h, pid, plan_id, _ = await self._setup(client, db_session)

        # 重新打开
        await client.post(f"/api/projects/{pid}/plans/{plan_id}/reopen", headers=h)

        # 结果仍在
        response = await client.get(f"/api/projects/{pid}/plans/{plan_id}/results", headers=h)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data is not None
        assert data["report"]["totalScenarios"] >= 2

    @pytest.mark.asyncio
    async def test_non_creator_non_admin_cannot_reopen(self, client, db_session):
        """AC: 非项目管理员且非计划创建者返回 403"""
        h, pid, plan_id, admin = await self._setup(client, db_session)

        # 添加一个 tester 成员
        tester = await create_test_user(db_session, username="reopen_tester", role="user")
        await client.post(f"/api/projects/{pid}/members", headers=h, json={
            "userId": str(tester.id), "role": "member",
        })
        tester_h, _ = make_auth_headers(tester)

        response = await client.post(f"/api/projects/{pid}/plans/{plan_id}/reopen", headers=tester_h)
        assert response.status_code == 403
