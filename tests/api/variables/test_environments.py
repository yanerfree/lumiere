"""
test_environments — 环境与环境变量 CRUD + 合并预览 + 克隆（项目级）
Test ID: 3.2-API-001
Priority: P0

环境 2026-08-21 从全局改成项目级（迁移 zzo0envproj），路径从 /api/environments
挪到 /api/projects/{project_id}/environments。
"""
import pytest

from tests.conftest import create_test_project, create_test_user, make_auth_headers

# 新项目自带 development/testing/staging/production 四个环境 ——
# 测试自己建的环境名要避开它们，否则第一次创建就 409
PFX = "et-"


class TestEnvironments:

    @pytest.mark.asyncio
    async def test_新项目自带默认环境且不带变量(self, client, db_session):
        """★ 默认环境故意不带任何变量：老库的种子环境带着
        BASE_URL=https://api.example.com、ADMIN_PASSWORD=123456 这类演示值，
        照抄给新项目等于预埋假凭证 —— 假凭证让「忘了填」看起来像「填过了」。"""
        admin = await create_test_user(db_session, username="env_seed", role="admin")
        headers, _ = make_auth_headers(admin)
        pid = await create_test_project(client, headers, "env-seed-proj")

        r = await client.get(f"/api/projects/{pid}/environments", headers=headers)
        assert r.status_code == 200
        envs = r.json()["data"]
        assert [e["name"] for e in envs] == ["development", "testing", "staging", "production"]

        for e in envs:
            rv = await client.get(f"/api/projects/{pid}/environments/{e['id']}/variables",
                                  headers=headers)
            assert rv.json()["data"] == [], f"{e['name']} 预埋了变量"

    @pytest.mark.asyncio
    async def test_create_and_list_env(self, client, db_session):
        admin = await create_test_user(db_session, username="env_admin", role="admin")
        headers, _ = make_auth_headers(admin)
        pid = await create_test_project(client, headers, "env-crud-proj")
        base = f"/api/projects/{pid}/environments"

        r = await client.post(base, headers=headers,
                              json={"name": f"{PFX}staging", "description": "预发布环境"})
        assert r.status_code == 201
        assert r.json()["data"]["name"] == f"{PFX}staging"

        r2 = await client.get(base, headers=headers)
        assert any(e["name"] == f"{PFX}staging" for e in r2.json()["data"])

    @pytest.mark.asyncio
    async def test_两个项目可以有同名环境(self, client, db_session):
        """★ 改动前 environments.name 是全局 unique，两个项目都想有个 staging 就撞。"""
        admin = await create_test_user(db_session, username="env_two", role="admin")
        headers, _ = make_auth_headers(admin)
        p1 = await create_test_project(client, headers, "env-proj-a")
        p2 = await create_test_project(client, headers, "env-proj-b")

        for pid in (p1, p2):
            r = await client.post(f"/api/projects/{pid}/environments", headers=headers,
                                  json={"name": f"{PFX}same"})
            assert r.status_code == 201, r.text

    @pytest.mark.asyncio
    async def test_列表只返回本项目的环境(self, client, db_session):
        admin = await create_test_user(db_session, username="env_isolate", role="admin")
        headers, _ = make_auth_headers(admin)
        p1 = await create_test_project(client, headers, "env-iso-a")
        p2 = await create_test_project(client, headers, "env-iso-b")

        await client.post(f"/api/projects/{p1}/environments", headers=headers,
                          json={"name": f"{PFX}only-in-a"})
        r = await client.get(f"/api/projects/{p2}/environments", headers=headers)
        names = [e["name"] for e in r.json()["data"]]
        assert f"{PFX}only-in-a" not in names, names

    @pytest.mark.asyncio
    async def test_读别的项目的环境变量报不存在(self, client, db_session):
        """★ 环境里存着 BASE_URL、账号、密码。路径写自己的项目、env_id 填别人的，
        只验成员身份是拦不住的 —— 故意 404 不 403。"""
        admin = await create_test_user(db_session, username="env_cross", role="admin")
        headers, _ = make_auth_headers(admin)
        p1 = await create_test_project(client, headers, "env-cross-a")
        p2 = await create_test_project(client, headers, "env-cross-b")

        r = await client.get(f"/api/projects/{p2}/environments", headers=headers)
        other_env = r.json()["data"][0]["id"]

        r2 = await client.get(f"/api/projects/{p1}/environments/{other_env}/variables",
                              headers=headers)
        assert r2.status_code == 404, r2.text

    @pytest.mark.asyncio
    async def test_put_and_list_env_variables(self, client, db_session):
        admin = await create_test_user(db_session, username="envvar_admin", role="admin")
        headers, _ = make_auth_headers(admin)
        pid = await create_test_project(client, headers, "envvar-proj")
        base = f"/api/projects/{pid}/environments"

        r = await client.post(base, headers=headers, json={"name": f"{PFX}dev"})
        env_id = r.json()["data"]["id"]

        r2 = await client.put(f"{base}/{env_id}/variables", headers=headers, json=[
            {"key": "DB_HOST", "value": "localhost"},
            {"key": "DB_PORT", "value": "5432"},
        ])
        assert r2.status_code == 200
        assert len(r2.json()["data"]) == 2

        r3 = await client.get(f"{base}/{env_id}/variables", headers=headers)
        keys = [v["key"] for v in r3.json()["data"]]
        assert "DB_HOST" in keys
        assert "DB_PORT" in keys

    @pytest.mark.asyncio
    async def test_merged_variables(self, client, db_session):
        """合并预览 = 本项目的全局变量 + 该环境的环境变量，同名以环境为准。"""
        admin = await create_test_user(db_session, username="merge_admin", role="admin")
        headers, _ = make_auth_headers(admin)
        pid = await create_test_project(client, headers, "merge-proj")
        gbase = f"/api/projects/{pid}/global-variables"
        base = f"/api/projects/{pid}/environments"

        await client.post(gbase, headers=headers, json={"key": "MG_TIMEOUT", "value": "30"})
        await client.post(gbase, headers=headers, json={"key": "MG_SHARED", "value": "global_val"})

        r = await client.post(base, headers=headers, json={"name": f"{PFX}merge"})
        env_id = r.json()["data"]["id"]
        await client.put(f"{base}/{env_id}/variables", headers=headers, json=[
            {"key": "MG_SHARED", "value": "env_val"},
            {"key": "MG_ENV_ONLY", "value": "x"},
        ])

        r2 = await client.get(f"{base}/{env_id}/merged-variables", headers=headers)
        merged = {v["key"]: v for v in r2.json()["data"]}
        assert merged["MG_TIMEOUT"]["source"] == "global"
        assert merged["MG_SHARED"]["source"] == "environment"
        assert merged["MG_SHARED"]["value"] == "env_val"
        assert merged["MG_ENV_ONLY"]["source"] == "environment"

    @pytest.mark.asyncio
    async def test_合并预览不带别的项目的全局变量(self, client, db_session):
        """★ 这个接口是排查「变量未解析」的第一入口，混进别的项目的值会把人带偏。"""
        admin = await create_test_user(db_session, username="merge_iso", role="admin")
        headers, _ = make_auth_headers(admin)
        p1 = await create_test_project(client, headers, "merge-iso-a")
        p2 = await create_test_project(client, headers, "merge-iso-b")

        await client.post(f"/api/projects/{p1}/global-variables", headers=headers,
                          json={"key": "MG_ONLY_IN_A", "value": "1"})

        r = await client.get(f"/api/projects/{p2}/environments", headers=headers)
        env_id = r.json()["data"][0]["id"]
        r2 = await client.get(f"/api/projects/{p2}/environments/{env_id}/merged-variables",
                              headers=headers)
        keys = {v["key"] for v in r2.json()["data"]}
        assert "MG_ONLY_IN_A" not in keys, keys

    @pytest.mark.asyncio
    async def test_clone_environment(self, client, db_session):
        """副本留在源环境所属的项目里 —— 跨项目复制等于搬别人的凭证，不能是一次点击的副作用。"""
        admin = await create_test_user(db_session, username="clone_admin", role="admin")
        headers, _ = make_auth_headers(admin)
        pid = await create_test_project(client, headers, "clone-proj")
        base = f"/api/projects/{pid}/environments"

        r = await client.post(base, headers=headers, json={"name": f"{PFX}source"})
        env_id = r.json()["data"]["id"]
        await client.put(f"{base}/{env_id}/variables", headers=headers,
                         json=[{"key": "KEY_A", "value": "val_a"}])

        r2 = await client.post(f"{base}/{env_id}/clone", headers=headers,
                               json={"name": f"{PFX}cloned"})
        assert r2.status_code == 201
        cloned_id = r2.json()["data"]["id"]

        r3 = await client.get(f"{base}/{cloned_id}/variables", headers=headers)
        assert any(v["key"] == "KEY_A" for v in r3.json()["data"])

    @pytest.mark.asyncio
    async def test_duplicate_env_name_returns_409(self, client, db_session):
        admin = await create_test_user(db_session, username="envdup_admin", role="admin")
        headers, _ = make_auth_headers(admin)
        pid = await create_test_project(client, headers, "envdup-proj")
        base = f"/api/projects/{pid}/environments"

        await client.post(base, headers=headers, json={"name": f"{PFX}dup"})
        r = await client.post(base, headers=headers, json={"name": f"{PFX}dup"})
        assert r.status_code == 409
