"""
test_global_variables — 全局变量 CRUD（项目级）
Test ID: 3.1-API-001
Priority: P0

「全局变量」的"全局"= **本项目所有环境共用**，不是跨项目（迁移 zzp0gvarproj）。
路径 2026-08-21 从 /api/global-variables 挪到
/api/projects/{project_id}/global-variables。
"""
import pytest

from tests.conftest import create_test_project, create_test_user, make_auth_headers

# 新项目自带 5 个默认全局变量，测试用的 key 一律加前缀避免撞上去
PFX = "GVT_"


class TestGlobalVariables:
    """全局变量 CRUD API"""

    @pytest.mark.asyncio
    async def test_新项目自带默认全局变量(self, client, db_session):
        """环境/全局变量项目化之后新项目是空的，所以 create_project 会铺一份默认。
        少了它，新项目的 UI 脚本 t() 就没有 TEST_LANGUAGE 兜底。"""
        admin = await create_test_user(db_session, username="gvar_seed", role="admin")
        headers, _ = make_auth_headers(admin)
        pid = await create_test_project(client, headers, "gvar-seed-proj")

        r = await client.get(f"/api/projects/{pid}/global-variables", headers=headers)
        assert r.status_code == 200
        got = {v["key"]: v["value"] for v in r.json()["data"]}
        assert set(got) == {"API_TIMEOUT", "BASE_WAIT", "LOG_LEVEL",
                            "RETRY_COUNT", "TEST_LANGUAGE"}, got
        assert got["TEST_LANGUAGE"] == "zh"

    @pytest.mark.asyncio
    async def test_create_and_list(self, client, db_session):
        admin = await create_test_user(db_session, username="gvar_admin", role="admin")
        headers, _ = make_auth_headers(admin)
        pid = await create_test_project(client, headers, "gvar-crud-proj")
        base = f"/api/projects/{pid}/global-variables"

        r = await client.post(base, headers=headers, json={
            "key": f"{PFX}TIMEOUT", "value": "30", "description": "本项目超时"
        })
        assert r.status_code == 201
        assert r.json()["data"]["key"] == f"{PFX}TIMEOUT"

        r2 = await client.get(base, headers=headers)
        assert r2.status_code == 200
        keys = [v["key"] for v in r2.json()["data"]]
        assert f"{PFX}TIMEOUT" in keys

    @pytest.mark.asyncio
    async def test_两个项目可以有同名key(self, client, db_session):
        """★ 改动前 key 是全平台 unique，两个项目没法各有一份 TEST_LANGUAGE。"""
        admin = await create_test_user(db_session, username="gvar_two", role="admin")
        headers, _ = make_auth_headers(admin)
        p1 = await create_test_project(client, headers, "gvar-proj-a")
        p2 = await create_test_project(client, headers, "gvar-proj-b")

        for pid, val in ((p1, "a"), (p2, "b")):
            r = await client.post(f"/api/projects/{pid}/global-variables", headers=headers,
                                  json={"key": f"{PFX}SAME", "value": val})
            assert r.status_code == 201, r.text

        for pid, val in ((p1, "a"), (p2, "b")):
            r = await client.get(f"/api/projects/{pid}/global-variables", headers=headers)
            got = {v["key"]: v["value"] for v in r.json()["data"]}
            assert got[f"{PFX}SAME"] == val, f"{pid} 拿到的是 {got.get(PFX + 'SAME')}"

    @pytest.mark.asyncio
    async def test_全量替换不影响别的项目(self, client, db_session):
        """★ 项目化之前是无条件 delete(GlobalVariable)：任何项目点一次保存
        就会清空全平台所有项目的全局变量。"""
        admin = await create_test_user(db_session, username="gvar_put", role="admin")
        headers, _ = make_auth_headers(admin)
        p1 = await create_test_project(client, headers, "gvar-put-a")
        p2 = await create_test_project(client, headers, "gvar-put-b")

        r = await client.put(f"/api/projects/{p1}/global-variables", headers=headers,
                             json=[{"key": f"{PFX}ONLY", "value": "1"}])
        assert r.status_code == 200
        assert [v["key"] for v in r.json()["data"]] == [f"{PFX}ONLY"]

        r2 = await client.get(f"/api/projects/{p2}/global-variables", headers=headers)
        keys = {v["key"] for v in r2.json()["data"]}
        assert "TEST_LANGUAGE" in keys, "别的项目的默认变量被清空了"

    @pytest.mark.asyncio
    async def test_update_variable(self, client, db_session):
        admin = await create_test_user(db_session, username="gvar_upd", role="admin")
        headers, _ = make_auth_headers(admin)
        pid = await create_test_project(client, headers, "gvar-upd-proj")
        base = f"/api/projects/{pid}/global-variables"

        r = await client.post(base, headers=headers, json={"key": f"{PFX}UPD", "value": "old"})
        var_id = r.json()["data"]["id"]

        r2 = await client.put(f"{base}/{var_id}", headers=headers, json={"value": "new"})
        assert r2.status_code == 200
        assert r2.json()["data"]["value"] == "new"

    @pytest.mark.asyncio
    async def test_改别的项目的变量报不存在(self, client, db_session):
        """★ 路径写自己的项目、var_id 填别人的 —— 故意 404 不 403。"""
        admin = await create_test_user(db_session, username="gvar_cross", role="admin")
        headers, _ = make_auth_headers(admin)
        p1 = await create_test_project(client, headers, "gvar-cross-a")
        p2 = await create_test_project(client, headers, "gvar-cross-b")

        r = await client.get(f"/api/projects/{p2}/global-variables", headers=headers)
        other_id = r.json()["data"][0]["id"]

        r2 = await client.put(f"/api/projects/{p1}/global-variables/{other_id}",
                              headers=headers, json={"value": "越权改"})
        assert r2.status_code == 404, r2.text

    @pytest.mark.asyncio
    async def test_delete_variable(self, client, db_session):
        admin = await create_test_user(db_session, username="gvar_del", role="admin")
        headers, _ = make_auth_headers(admin)
        pid = await create_test_project(client, headers, "gvar-del-proj")
        base = f"/api/projects/{pid}/global-variables"

        r = await client.post(base, headers=headers, json={"key": f"{PFX}DEL", "value": "x"})
        var_id = r.json()["data"]["id"]

        r2 = await client.delete(f"{base}/{var_id}", headers=headers)
        assert r2.status_code == 200

    @pytest.mark.asyncio
    async def test_duplicate_key_returns_409(self, client, db_session):
        admin = await create_test_user(db_session, username="gvar_dup", role="admin")
        headers, _ = make_auth_headers(admin)
        pid = await create_test_project(client, headers, "gvar-dup-proj")
        base = f"/api/projects/{pid}/global-variables"

        await client.post(base, headers=headers, json={"key": f"{PFX}DUP", "value": "1"})
        r = await client.post(base, headers=headers, json={"key": f"{PFX}DUP", "value": "2"})
        assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_reserved_key_returns_422(self, client, db_session):
        admin = await create_test_user(db_session, username="gvar_rsv", role="admin")
        headers, _ = make_auth_headers(admin)
        pid = await create_test_project(client, headers, "gvar-rsv-proj")

        r = await client.post(f"/api/projects/{pid}/global-variables", headers=headers,
                              json={"key": "PATH", "value": "/usr/bin"})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "RESERVED_KEY"
