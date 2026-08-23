"""
test_review_flow — 用例审核状态机（ft S5.1 / FR21-FR28）
Test ID: FT.5.1-API-001
Priority: P0
"""
import pytest
from tests.conftest import create_test_user, make_auth_headers


async def _setup_with_ai_case(client, db_session, username):
    admin = await create_test_user(db_session, username=username, role="admin")
    headers, _ = make_auth_headers(admin)
    r = await client.post("/api/projects", headers=headers, json={
        "name": f"review-{username}", "gitUrl": "git@x.com:r.git", "scriptBasePath": "/r",
    })
    pid = r.json()["data"]["id"]
    br = await client.get(f"/api/projects/{pid}/branches", headers=headers)
    bid = br.json()["data"][0]["id"]
    # 创建一个 source=manual 用例（模拟 AI 生成的——source 字段在创建时自动设为 manual，
    # 但我们可以通过 update 设 review_status 测试审核流程）
    case_resp = await client.post(f"/api/projects/{pid}/branches/{bid}/cases", headers=headers, json={
        "title": "AI生成登录测试", "type": "api", "module": "auth",
        "steps": [{"action": "输入账号密码", "expected": "登录成功返回 token"}],
    })
    case_id = case_resp.json()["data"]["id"]
    # 设为 pending_review 模拟 AI 产出
    await client.put(f"/api/projects/{pid}/branches/{bid}/cases/{case_id}", headers=headers, json={
        "reviewStatus": "pending_review",
    })
    return headers, pid, bid, case_id


class TestReviewFlow:

    @pytest.mark.asyncio
    async def test_approve_case(self, client, db_session):
        headers, pid, bid, cid = await _setup_with_ai_case(client, db_session, "rv_approve")
        r = await client.put(f"/api/projects/{pid}/branches/{bid}/cases/{cid}", headers=headers, json={
            "reviewStatus": "approved",
        })
        assert r.status_code == 200
        assert r.json()["data"]["reviewStatus"] == "approved"

    @pytest.mark.asyncio
    async def test_reject_requires_reason(self, client, db_session):
        headers, pid, bid, cid = await _setup_with_ai_case(client, db_session, "rv_reject_nr")
        r = await client.put(f"/api/projects/{pid}/branches/{bid}/cases/{cid}", headers=headers, json={
            "reviewStatus": "rejected",
        })
        # 应返回 400 拒绝必须带理由
        assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"

    @pytest.mark.asyncio
    async def test_reject_with_reason(self, client, db_session):
        headers, pid, bid, cid = await _setup_with_ai_case(client, db_session, "rv_reject_ok")
        r = await client.put(f"/api/projects/{pid}/branches/{bid}/cases/{cid}", headers=headers, json={
            "reviewStatus": "rejected",
            "reviewReason": {"category": "vague_expectation", "text": "预期结果含糊"},
        })
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["reviewStatus"] == "rejected"
        assert data["reviewReason"]["category"] == "vague_expectation"

    @pytest.mark.asyncio
    async def test_case_response_includes_review_fields(self, client, db_session):
        headers, pid, bid, cid = await _setup_with_ai_case(client, db_session, "rv_fields")
        r = await client.get(f"/api/projects/{pid}/branches/{bid}/cases/{cid}", headers=headers)
        data = r.json()["data"]
        assert "reviewStatus" in data
        assert "qualityScore" in data
        assert "version" in data

    @pytest.mark.asyncio
    async def test_manual_cases_unaffected(self, client, db_session):
        """手动用例不受审核门禁影响（FR27）"""
        admin = await create_test_user(db_session, username="rv_manual", role="admin")
        headers, _ = make_auth_headers(admin)
        r = await client.post("/api/projects", headers=headers, json={
            "name": "review-manual", "gitUrl": "git@x.com:r.git", "scriptBasePath": "/r",
        })
        pid = r.json()["data"]["id"]
        br = await client.get(f"/api/projects/{pid}/branches", headers=headers)
        bid = br.json()["data"][0]["id"]
        case_resp = await client.post(f"/api/projects/{pid}/branches/{bid}/cases", headers=headers, json={
            "title": "手动用例", "type": "api", "module": "auth",
            "steps": [{"action": "手动操作", "expected": "成功"}],
        })
        data = case_resp.json()["data"]
        case_id = data["id"]

        # 写了步骤 → manual 维完成 → 审核标签自动进「待审」（6e5cea5a：
        # 「提交审核」那一下不产生任何信息，所以不让人点）。
        # 这里断言的**不是**这个标签的字面值，而是 FR27 真正保证的东西：
        # 审核不挡回归 —— 待审的手动用例照样能建计划、照样能跑。
        assert data["reviewStatus"] != "rejected"

        plan_resp = await client.post(f"/api/projects/{pid}/plans", headers=headers, json={
            "name": "手动用例回归", "planType": "manual", "testType": "api", "caseIds": [case_id],
        })
        assert plan_resp.status_code in (200, 201), plan_resp.text
        plan_id = plan_resp.json()["data"]["id"]

        exec_resp = await client.post(f"/api/projects/{pid}/plans/{plan_id}/execute", headers=headers)
        assert exec_resp.status_code in (200, 201, 202), exec_resp.text

        results = await client.get(f"/api/projects/{pid}/plans/{plan_id}/results", headers=headers)
        assert results.status_code == 200
        scenarios = results.json()["data"]["scenarios"]
        assert len(scenarios) == 1, f"待审用例被挡在计划外了: {scenarios}"
