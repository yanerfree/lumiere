"""
test_import_cases — tea-cases.json 用例导入
Test ID: 2.3-API-001
Priority: P0
"""
import io
import json

import pytest
from sqlalchemy import select

from app.models.case import Case, CaseFolder
from tests.conftest import create_test_user, make_auth_headers


def _make_tea_json(cases):
    """辅助：构造 tea-cases.json 内容"""
    return json.dumps({"version": "1.0", "cases": cases}).encode()


class TestImportCases:
    """POST /api/projects/{pid}/branches/{bid}/cases/import"""

    async def _setup(self, client, db_session):
        """辅助：创建 admin + 项目 + 获取默认 branch id"""
        admin = await create_test_user(db_session, username="import_admin", role="admin")
        headers, _ = make_auth_headers(admin)
        r = await client.post("/api/projects", headers=headers, json={
            "name": "import-proj", "gitUrl": "git@x.com:r.git", "scriptBasePath": "/imp",
        })
        project_id = r.json()["data"]["id"]
        br = await client.get(f"/api/projects/{project_id}/branches", headers=headers)
        branch_id = br.json()["data"][0]["id"]
        return headers, project_id, branch_id

    @pytest.mark.asyncio
    async def test_import_new_cases(self, client, db_session):
        # Given: 空分支
        headers, pid, bid = await self._setup(client, db_session)
        content = _make_tea_json([
            {"tea_id": "auth_login", "title": "登录成功", "type": "api", "module": "auth", "submodule": "login", "priority": "P0",
             "script_ref": {"file": "tests/api/auth/test_login.py", "func": "test_login"}},
            {"tea_id": "auth_logout", "title": "登出", "type": "api", "module": "auth", "priority": "P1",
             "script_ref": {"file": "tests/api/auth/test_logout.py"}},
        ])

        # When: 上传文件
        response = await client.post(
            f"/api/projects/{pid}/branches/{bid}/cases/import",
            headers=headers,
            files={"file": ("tea-cases.json", io.BytesIO(content), "application/json")},
        )

        # Then: 200 + 摘要显示 2 个新增
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["new"] == 2
        assert data["updated"] == 0
        # 原来断言的是 removed == 0。导入改成「只增不删」之后摘要不再有 removed，
        # 换成如实上报「文件里没有的那几条」的 notInFile（见 845f203）
        assert data["notInFile"] == 0
        assert data["newModules"] >= 1  # AUTH 模块

        # Then: 数据库中有 2 条用例
        result = await db_session.execute(select(Case).where(Case.branch_id == bid))
        cases = result.scalars().all()
        assert len(cases) == 2
        codes = [c.case_code for c in cases]
        assert all(c.startswith("TC-AUTH-") for c in codes)

    @pytest.mark.asyncio
    async def test_reimport_updates_existing(self, client, db_session):
        # Given: 已导入过用例
        headers, pid, bid = await self._setup(client, db_session)
        first = _make_tea_json([
            {"tea_id": "users_create", "title": "创建用户", "type": "api", "module": "users", "priority": "P0",
             "script_ref": {"file": "tests/api/users/test_create.py"}},
        ])
        await client.post(
            f"/api/projects/{pid}/branches/{bid}/cases/import",
            headers=headers,
            files={"file": ("tea-cases.json", io.BytesIO(first), "application/json")},
        )

        # When: 重新导入，标题和优先级变了
        second = _make_tea_json([
            {"tea_id": "users_create", "title": "创建用户V2", "type": "api", "module": "users", "priority": "P1",
             "script_ref": {"file": "tests/api/users/test_create.py"}},
        ])
        response = await client.post(
            f"/api/projects/{pid}/branches/{bid}/cases/import",
            headers=headers,
            files={"file": ("tea-cases.json", io.BytesIO(second), "application/json")},
        )

        # Then: 1 个更新，0 个新增
        data = response.json()["data"]
        assert data["new"] == 0
        assert data["updated"] == 1

    @pytest.mark.asyncio
    async def test_missing_tea_id_is_reported_but_not_removed(self, client, db_session):
        """文件里少了的那条要如实上报，但绝不能动它。

        这条测试原来叫 test_missing_tea_id_marks_removed，断言 removed == 1 ——
        那是旧行为，已经在 845f203「点一次导入删掉 3 条无关用例」里作为数据丢失
        bug 修掉了。导入现在只增不删，改成上报 notInFile。
        断言 B 仍在且字段没变，是为了把那个修复钉住，不是顺手多写一句。
        """
        # Given: 已导入 2 条用例
        headers, pid, bid = await self._setup(client, db_session)
        first = _make_tea_json([
            {"tea_id": "rm_case_a", "title": "A", "type": "api", "module": "test", "priority": "P0", "script_ref": {"file": "a.py"}},
            {"tea_id": "rm_case_b", "title": "B", "type": "api", "module": "test", "priority": "P0", "script_ref": {"file": "b.py"}},
        ])
        await client.post(
            f"/api/projects/{pid}/branches/{bid}/cases/import",
            headers=headers,
            files={"file": ("tea-cases.json", io.BytesIO(first), "application/json")},
        )

        # When: 重新导入，只剩 A
        second = _make_tea_json([
            {"tea_id": "rm_case_a", "title": "A", "type": "api", "module": "test", "priority": "P0", "script_ref": {"file": "a.py"}},
        ])
        response = await client.post(
            f"/api/projects/{pid}/branches/{bid}/cases/import",
            headers=headers,
            files={"file": ("tea-cases.json", io.BytesIO(second), "application/json")},
        )

        # Then: B 被如实上报为「不在这个文件里」，并给出样例供人核对
        data = response.json()["data"]
        assert data["notInFile"] == 1
        assert "B" in data["notInFileSample"]

        # Then: B 还在，而且没被改动 —— 导入不负责删，也不负责改状态
        result = await db_session.execute(
            select(Case).where(Case.branch_id == bid, Case.tea_id == "rm_case_b")
        )
        case_b = result.scalar_one()
        assert case_b.title == "B"

    @pytest.mark.asyncio
    async def test_skip_cases_missing_fields(self, client, db_session):
        # Given: JSON 中有一条缺 title
        headers, pid, bid = await self._setup(client, db_session)
        content = _make_tea_json([
            {"tea_id": "good_case", "title": "OK", "type": "api", "module": "m", "priority": "P0", "script_ref": {}},
            {"tea_id": "bad_case", "type": "api", "module": "m"},  # 缺 title
        ])

        # When: 导入
        response = await client.post(
            f"/api/projects/{pid}/branches/{bid}/cases/import",
            headers=headers,
            files={"file": ("tea-cases.json", io.BytesIO(content), "application/json")},
        )

        # Then: 1 新增 + 1 跳过
        data = response.json()["data"]
        assert data["new"] == 1
        assert data["skipped"] == 1
        assert len(data["skippedReasons"]) == 1

    @pytest.mark.asyncio
    async def test_reject_non_json_file(self, client, db_session):
        # Given: 上传 .txt 文件
        headers, pid, bid = await self._setup(client, db_session)

        # When: 上传
        response = await client.post(
            f"/api/projects/{pid}/branches/{bid}/cases/import",
            headers=headers,
            files={"file": ("data.txt", io.BytesIO(b"not json"), "text/plain")},
        )

        # Then: 400
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_FILE"
