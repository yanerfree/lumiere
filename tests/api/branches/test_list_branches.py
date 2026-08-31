"""
test_list_branches — 分支配置列表
Test ID: 2.1-API-002
Priority: P0
"""
import pytest

from tests.conftest import create_test_user, make_auth_headers


class TestListBranches:
    """GET /api/projects/{id}/branches"""

    @pytest.mark.asyncio
    async def test_list_includes_default_branch(self, client, db_session):
        # Given: admin 创建了项目（自动生成 default 分支）
        admin = await create_test_user(db_session, username="br_list_admin", role="admin")
        headers, _ = make_auth_headers(admin)
        r = await client.post("/api/projects", headers=headers, json={
            "name": "br-list-proj", "gitUrl": "git@x.com:r.git", "scriptBasePath": "/bl",
        })
        project_id = r.json()["data"]["id"]

        # When: 查询分支列表
        response = await client.get(f"/api/projects/{project_id}/branches", headers=headers)

        # Then: 包含 default 分支
        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) >= 1
        names = [b["name"] for b in data]
        assert "default" in names

    @pytest.mark.asyncio
    async def test_list_carries_case_count(self, client, db_session):
        """每条分支必须带 caseCount。

        为什么要封这个字段：**分支选错了，和「这个项目一条数据都没有」在页面上长得
        一模一样** —— 都是「暂无目录」+「暂无用例」+「共 0 条」。前端靠这个数在下拉里
        标出空分支、并在空列表上指出数据在哪条分支（BranchSelector.pickInitialBranch /
        CaseManagement.CaseEmpty）。字段一没，那两处会静默退回「盲选第一条 + 只说暂无
        用例」的老行为 —— 不报错，只是又变回原来那个查半天的样子。
        2026-08-31 报过来过一次：UAG 的 41 条用例全在 v2.2.0，站在 default 上看是 0。
        """
        admin = await create_test_user(db_session, username="br_count_admin", role="admin")
        headers, _ = make_auth_headers(admin)
        r = await client.post("/api/projects", headers=headers, json={
            "name": "br-count-proj", "gitUrl": "git@x.com:r.git", "scriptBasePath": "/bc",
        })
        pid = r.json()["data"]["id"]

        # 再开一条分支，两条分支只往其中一条塞用例
        r = await client.post(f"/api/projects/{pid}/branches", headers=headers, json={
            "name": "v9.9.9", "branch": "main",
        })
        assert r.status_code == 201
        loaded_bid = r.json()["data"]["id"]

        for i in range(2):
            rc = await client.post(
                f"/api/projects/{pid}/branches/{loaded_bid}/cases", headers=headers,
                json={"title": f"用例 {i}", "type": "api", "module": "auth",
                      "priority": "P1", "steps": [{"action": "做点什么"}]},
            )
            assert rc.status_code == 201

        response = await client.get(f"/api/projects/{pid}/branches", headers=headers)
        assert response.status_code == 200
        data = response.json()["data"]
        counts = {b["name"]: b["caseCount"] for b in data}
        # 每条都要有这个键 —— 缺键和 0 在前端是两种行为，别混
        assert all("caseCount" in b for b in data), data
        assert counts["v9.9.9"] == 2
        assert counts["default"] == 0
