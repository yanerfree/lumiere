"""
test_delete_project_guard — 删项目的硬门禁：有人工资产就不许删
Test ID: 1.5-API-005
Priority: P0

背景：外键补成 ON DELETE CASCADE（zzd0fkc1）之后，删项目会把子表数据物理删干净。
实测有项目挂着 330 条用例，而前端只有一个一键 Popconfirm —— 所以在 service 层
硬拦：还有用例 / 知识条目 / 需求文档就 409，不留 force 绕过口子。

这里同时钉住「什么不算门槛」：计划和报告是执行痕迹，重跑能再生，
跟着项目一起删是预期行为。哪天有人把它们也加进门槛，这条测试会红。
"""
import uuid

import pytest

from app.models.case import Case
from app.models.knowledge import KnowledgeEntry
from app.models.plan import Plan
from app.models.project import Branch
from app.models.scenario_gen import RequirementDoc
from tests.conftest import create_test_user, make_auth_headers


async def _make_project(client, headers, name):
    """建项目，返回 (project_id, branch_id)。创建接口会自动建默认 branch。"""
    r = await client.post("/api/projects", headers=headers, json={
        "name": name, "gitUrl": "git@x.com:r.git", "scriptBasePath": f"/{name}",
    })
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


async def _branch_of(db_session, project_id):
    from sqlalchemy import select
    return (await db_session.scalar(
        select(Branch.id).where(Branch.project_id == uuid.UUID(project_id)).limit(1)
    ))


class TestDeleteProjectGuard:
    """DELETE /api/projects/{id} —— 门禁"""

    @pytest.mark.asyncio
    async def test_empty_project_can_be_deleted(self, client, db_session):
        # Given: 一个没有任何人工资产的项目
        admin = await create_test_user(db_session, username="delg_empty", role="admin")
        headers, _ = make_auth_headers(admin)
        pid = await _make_project(client, headers, "delg-empty")

        # When: 删除
        r = await client.delete(f"/api/projects/{pid}", headers=headers)

        # Then: 放行
        assert r.status_code == 200, r.text
        assert r.json()["message"] == "删除成功"

    @pytest.mark.asyncio
    async def test_cases_block_deletion(self, client, db_session):
        # Given: 项目下有 2 条用例
        admin = await create_test_user(db_session, username="delg_case", role="admin")
        headers, _ = make_auth_headers(admin)
        pid = await _make_project(client, headers, "delg-case")
        bid = await _branch_of(db_session, pid)
        for i in (1, 2):
            db_session.add(Case(branch_id=bid, case_code=f"DG-{i}", title=f"用例{i}",
                                type="api", source="manual"))
        await db_session.flush()

        # When: 删除
        r = await client.delete(f"/api/projects/{pid}", headers=headers)

        # Then: 409，且报清欠多少条
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "PROJECT_NOT_EMPTY"
        assert "用例 2 条" in r.json()["error"]["message"]

        # Then: 项目没被删掉 —— 断言到这一步才算证明「拦住了」而不只是「报错了」
        still = await client.get(f"/api/projects/{pid}", headers=headers)
        assert still.status_code == 200

    @pytest.mark.asyncio
    async def test_knowledge_entries_block_deletion(self, client, db_session):
        # Given: 项目下没有用例，只有 1 条知识条目
        admin = await create_test_user(db_session, username="delg_kb", role="admin")
        headers, _ = make_auth_headers(admin)
        pid = await _make_project(client, headers, "delg-kb")
        db_session.add(KnowledgeEntry(project_id=uuid.UUID(pid), category="manual",
                                      title="踩过的坑", content="正文"))
        await db_session.flush()

        # When / Then: 同样拦住
        r = await client.delete(f"/api/projects/{pid}", headers=headers)
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "PROJECT_NOT_EMPTY"
        assert "知识条目 1 条" in r.json()["error"]["message"]

    @pytest.mark.asyncio
    async def test_requirement_docs_block_deletion(self, client, db_session):
        # Given: 项目下只有 1 份需求文档
        admin = await create_test_user(db_session, username="delg_req", role="admin")
        headers, _ = make_auth_headers(admin)
        pid = await _make_project(client, headers, "delg-req")
        bid = await _branch_of(db_session, pid)
        db_session.add(RequirementDoc(project_id=uuid.UUID(pid), branch_id=bid,
                                      content_markdown="# 需求"))
        await db_session.flush()

        # When / Then: 同样拦住
        r = await client.delete(f"/api/projects/{pid}", headers=headers)
        assert r.status_code == 409, r.text
        assert "需求文档 1 条" in r.json()["error"]["message"]

    @pytest.mark.asyncio
    async def test_deletable_after_cases_cleared(self, client, db_session):
        # Given: 项目本来有用例被拦，随后用例被清空
        admin = await create_test_user(db_session, username="delg_clear", role="admin")
        headers, _ = make_auth_headers(admin)
        pid = await _make_project(client, headers, "delg-clear")
        bid = await _branch_of(db_session, pid)
        case = Case(branch_id=bid, case_code="DG-C1", title="待清理",
                    type="api", source="manual")
        db_session.add(case)
        await db_session.flush()
        assert (await client.delete(f"/api/projects/{pid}", headers=headers)).status_code == 409

        # When: 清空用例后再删
        await db_session.delete(case)
        await db_session.flush()
        r = await client.delete(f"/api/projects/{pid}", headers=headers)

        # Then: 放行 —— 门禁是可解的，不是死路
        assert r.status_code == 200, r.text

    @pytest.mark.asyncio
    async def test_plans_do_not_block_deletion(self, client, db_session):
        """计划不算门槛：执行痕迹重跑能再生，跟着项目一起删是预期行为。"""
        # Given: 项目下没有用例，但有 1 个测试计划
        admin = await create_test_user(db_session, username="delg_plan", role="admin")
        headers, _ = make_auth_headers(admin)
        pid = await _make_project(client, headers, "delg-plan")
        db_session.add(Plan(project_id=uuid.UUID(pid), name="回归计划",
                            plan_type="automated", test_type="api", created_by=admin.id))
        await db_session.flush()

        # When: 删除
        r = await client.delete(f"/api/projects/{pid}", headers=headers)

        # Then: 放行，计划随项目级联删掉
        assert r.status_code == 200, r.text
        from sqlalchemy import func, select
        left = await db_session.scalar(
            select(func.count()).select_from(Plan).where(Plan.project_id == uuid.UUID(pid))
        )
        assert left == 0
