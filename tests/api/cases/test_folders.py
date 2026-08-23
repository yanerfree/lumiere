"""
test_folders — 用例目录树 CRUD
Test ID: 2.5-API-001
Priority: P0
"""
import io
import json

import pytest

from tests.conftest import create_test_user, make_auth_headers


class TestFolders:
    """用例目录 GET/POST/DELETE"""

    async def _setup(self, client, db_session):
        admin = await create_test_user(db_session, username="folder_admin", role="admin")
        headers, _ = make_auth_headers(admin)
        r = await client.post("/api/projects", headers=headers, json={
            "name": "folder-proj", "gitUrl": "git@x.com:r.git", "scriptBasePath": "/f",
        })
        pid = r.json()["data"]["id"]
        br = await client.get(f"/api/projects/{pid}/branches", headers=headers)
        bid = br.json()["data"][0]["id"]
        return headers, pid, bid

    @pytest.mark.asyncio
    async def test_empty_tree(self, client, db_session):
        # Given: 空分支
        headers, pid, bid = await self._setup(client, db_session)

        # When: 查询目录树
        response = await client.get(f"/api/projects/{pid}/branches/{bid}/folders", headers=headers)

        # Then: 空数组
        assert response.status_code == 200
        assert response.json()["data"] == []

    @pytest.mark.asyncio
    async def test_create_module(self, client, db_session):
        # Given: 空分支
        headers, pid, bid = await self._setup(client, db_session)

        # When: 创建模块
        response = await client.post(
            f"/api/projects/{pid}/branches/{bid}/folders?name=auth", headers=headers
        )

        # Then: 201。name 存人写的原样，path 存大写匹配键（e09d7a9 改的：
        # 原来 name 也强制大写，页面上一律 SHOUTING 且没有任何地方能改回来）
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["name"] == "auth"
        assert data["path"] == "AUTH"
        assert data["depth"] == 1

        # 大小写混排的显示名要原样留住 —— 这条才挡得住 .upper() 被加回 name
        r2 = await client.post(
            f"/api/projects/{pid}/branches/{bid}/folders?name=LLM%20Providers", headers=headers
        )
        assert r2.status_code == 201
        assert r2.json()["data"]["name"] == "LLM Providers"
        assert r2.json()["data"]["path"] == "LLM PROVIDERS"

    @pytest.mark.asyncio
    async def test_create_submodule(self, client, db_session):
        # Given: 已有 AUTH 模块
        headers, pid, bid = await self._setup(client, db_session)
        r = await client.post(f"/api/projects/{pid}/branches/{bid}/folders?name=auth", headers=headers)
        parent_id = r.json()["data"]["id"]

        # When: 创建子模块
        response = await client.post(
            f"/api/projects/{pid}/branches/{bid}/folders?name=login&parentId={parent_id}", headers=headers
        )

        # Then: 201，depth=2。name 原样、path 大写
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["name"] == "login"
        assert data["path"] == "AUTH/LOGIN"
        assert data["depth"] == 2

    @pytest.mark.asyncio
    async def test_tree_with_case_count(self, client, db_session):
        # Given: 有目录和用例
        headers, pid, bid = await self._setup(client, db_session)

        # 导入用例（自动创建 AUTH/LOGIN 目录）
        content = json.dumps({"version": "1.0", "cases": [
            {"tea_id": "tree_case_1", "title": "Case 1", "type": "api", "module": "auth", "submodule": "login",
             "priority": "P0", "script_ref": {"file": "t.py"}},
            {"tea_id": "tree_case_2", "title": "Case 2", "type": "api", "module": "auth", "submodule": "login",
             "priority": "P1", "script_ref": {"file": "t2.py"}},
        ]}).encode()
        await client.post(
            f"/api/projects/{pid}/branches/{bid}/cases/import",
            headers=headers,
            files={"file": ("tea-cases.json", io.BytesIO(content), "application/json")},
        )

        # When: 查询目录树
        response = await client.get(f"/api/projects/{pid}/branches/{bid}/folders", headers=headers)

        # Then: AUTH 模块下 LOGIN 子模块有 2 个用例
        assert response.status_code == 200
        tree = response.json()["data"]
        assert len(tree) >= 1
        # 按 path 找 —— path 才是大写匹配键，name 是人写的原样
        auth = next(n for n in tree if n["path"] == "AUTH")
        assert auth["name"] == "auth"
        login = next(n for n in auth["children"] if n["path"] == "AUTH/LOGIN")
        assert login["caseCount"] == 2

    @pytest.mark.asyncio
    async def test_delete_empty_folder(self, client, db_session):
        # Given: 一个空目录
        headers, pid, bid = await self._setup(client, db_session)
        r = await client.post(f"/api/projects/{pid}/branches/{bid}/folders?name=empty_mod", headers=headers)
        folder_id = r.json()["data"]["id"]

        # When: 删除
        response = await client.delete(f"/api/projects/{pid}/branches/{bid}/folders/{folder_id}", headers=headers)

        # Then: 200
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_cannot_delete_non_empty_folder(self, client, db_session):
        # Given: 目录下有用例
        headers, pid, bid = await self._setup(client, db_session)

        # 创建用例（自动创建 ORDERS 目录）
        await client.post(f"/api/projects/{pid}/branches/{bid}/cases", headers=headers, json={
            "title": "订单测试", "type": "api", "module": "orders",
            "priority": "P0", "steps": [{"action": "test"}],
        })

        # 找到 ORDERS 目录 ID
        tree = (await client.get(f"/api/projects/{pid}/branches/{bid}/folders", headers=headers)).json()["data"]
        orders_folder = next(n for n in tree if n["path"] == "ORDERS")

        # When: 尝试删除
        response = await client.delete(
            f"/api/projects/{pid}/branches/{bid}/folders/{orders_folder['id']}", headers=headers
        )

        # Then: 422
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "FOLDER_NOT_EMPTY"

    @pytest.mark.asyncio
    async def test_duplicate_folder_returns_409(self, client, db_session):
        # Given: 已有 AUTH 目录
        headers, pid, bid = await self._setup(client, db_session)
        await client.post(f"/api/projects/{pid}/branches/{bid}/folders?name=auth", headers=headers)

        # When: 重复创建
        response = await client.post(f"/api/projects/{pid}/branches/{bid}/folders?name=auth", headers=headers)

        # Then: 409
        assert response.status_code == 409
