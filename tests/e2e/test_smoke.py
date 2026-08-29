"""
E2E Smoke 测试 — 5 个核心业务全链路

每个测试模拟真实用户操作序列，跨越多个 API 端点，验证端到端业务流程。
与 API 测试的区别：API 测试验证单个端点，E2E 测试验证完整用户旅程。
"""
import uuid

import pytest

from tests.conftest import TEST_PASSWORD, create_test_user, make_auth_headers


# ===========================================================================
# E2E-001: 认证全生命周期
# 注册 → 登录 → 获取用户信息 → 登出 → 确认登出
# ===========================================================================
class TestE2EAuthLifecycle:

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_full_auth_lifecycle(self, client, db_session):
        """用户认证全链路：创建用户 → 登录 → /me → 登出"""
        # 1. Admin 创建新用户
        admin = await create_test_user(db_session, username="e2e_admin", role="admin")
        admin_headers, _ = make_auth_headers(admin)

        resp = await client.post("/api/users", headers=admin_headers, json={
            "username": "e2e_newuser",
            "password": "E2ePass@123",
            "role": "user",
        })
        assert resp.status_code == 201
        new_user_id = resp.json()["data"]["id"]

        # 2. 新用户登录
        resp = await client.post("/api/auth/login", json={
            "username": "e2e_newuser",
            "password": "E2ePass@123",
        })
        assert resp.status_code == 200
        token = resp.json()["data"]["token"]
        assert len(token) > 0
        user_headers = {"Authorization": f"Bearer {token}"}

        # 3. 获取当前用户信息
        resp = await client.get("/api/auth/me", headers=user_headers)
        assert resp.status_code == 200
        me = resp.json()["data"]
        assert me["username"] == "e2e_newuser"
        assert me["role"] == "user"

        # 4. 登出
        resp = await client.post("/api/auth/logout", headers=user_headers)
        assert resp.status_code in (200, 204)


# ===========================================================================
# E2E-002: 项目管理全链路
# 创建项目 → 验证默认分支 → 添加成员 → 成员可见 → 移除成员 → 成员不可见
# ===========================================================================
class TestE2EProjectManagement:

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_project_member_lifecycle(self, client, db_session):
        """项目全链路：创建 → 默认分支 → 添加成员 → 可见性 → 移除 → 不可见"""
        admin = await create_test_user(db_session, username="e2e_proj_admin", role="admin")
        member = await create_test_user(db_session, username="e2e_proj_member", role="user")
        admin_h, _ = make_auth_headers(admin)
        member_h, _ = make_auth_headers(member)

        # 1. 创建项目
        resp = await client.post("/api/projects", headers=admin_h, json={
            "name": "e2e-lifecycle-proj",
            "gitUrl": "git@example.com:e2e/lifecycle.git",
            "scriptBasePath": "/tmp/e2e-lifecycle",
        })
        assert resp.status_code == 201
        pid = resp.json()["data"]["id"]

        # 2. 验证默认分支
        resp = await client.get(f"/api/projects/{pid}/branches", headers=admin_h)
        assert resp.status_code == 200
        branches = resp.json()["data"]
        assert len(branches) == 1
        assert branches[0]["name"] == "default"
        assert branches[0]["branch"] == "main"

        # 3. 成员加入前看不到项目
        resp = await client.get("/api/projects", headers=member_h)
        proj_names = [p["name"] for p in resp.json()["data"]]
        assert "e2e-lifecycle-proj" not in proj_names

        # 4. 添加成员
        resp = await client.post(f"/api/projects/{pid}/members", headers=admin_h, json={
            "userId": str(member.id), "role": "member",
        })
        assert resp.status_code in (200, 201)

        # 5. 成员现在能看到项目
        resp = await client.get("/api/projects", headers=member_h)
        proj_names = [p["name"] for p in resp.json()["data"]]
        assert "e2e-lifecycle-proj" in proj_names

        # 6. 移除成员
        resp = await client.delete(f"/api/projects/{pid}/members/{member.id}", headers=admin_h)
        assert resp.status_code in (200, 204)

        # 7. 成员不再能看到项目
        resp = await client.get("/api/projects", headers=member_h)
        proj_names = [p["name"] for p in resp.json()["data"]]
        assert "e2e-lifecycle-proj" not in proj_names


# ===========================================================================
# E2E-003: 用例管理全链路
# 创建目录 → 创建用例 → 导入用例 → 列表筛选 → 更新 → 软删除
# ===========================================================================
class TestE2ECaseManagement:

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_case_full_lifecycle(self, client, db_session):
        """用例全链路：目录 → 创建 → 导入 → 筛选 → 更新 → 删除"""
        admin = await create_test_user(db_session, username="e2e_case_admin", role="admin")
        h, _ = make_auth_headers(admin)

        # 1. 创建项目 + 获取分支
        resp = await client.post("/api/projects", headers=h, json={
            "name": "e2e-case-proj", "gitUrl": "git@x.com:e2e/case.git",
            "scriptBasePath": "/tmp/e2e-case",
        })
        pid = resp.json()["data"]["id"]
        resp = await client.get(f"/api/projects/{pid}/branches", headers=h)
        bid = resp.json()["data"][0]["id"]

        # 2. 创建模块目录
        resp = await client.post(
            f"/api/projects/{pid}/branches/{bid}/folders?name=AUTH",
            headers=h,
        )
        assert resp.status_code == 201

        # 3. 手动创建用例
        resp = await client.post(f"/api/projects/{pid}/branches/{bid}/cases", headers=h, json={
            "title": "登录成功测试", "type": "api", "module": "AUTH",
            "priority": "P0", "steps": [{"action": "POST /api/auth/login", "expected": "200"}],
        })
        assert resp.status_code == 201
        case_id = resp.json()["data"]["id"]

        # 4. 查看用例列表
        resp = await client.get(f"/api/projects/{pid}/branches/{bid}/cases", headers=h)
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total"] >= 1

        # 5. 按关键字筛选
        resp = await client.get(
            f"/api/projects/{pid}/branches/{bid}/cases?keyword=登录",
            headers=h,
        )
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total"] >= 1

        # 6. 更新用例
        resp = await client.put(
            f"/api/projects/{pid}/branches/{bid}/cases/{case_id}",
            headers=h, json={"priority": "P1"},
        )
        assert resp.status_code == 200

        # 7. 验证更新生效
        resp = await client.get(f"/api/projects/{pid}/branches/{bid}/cases/{case_id}", headers=h)
        assert resp.json()["data"]["priority"] == "P1"

        # 8. 软删除
        resp = await client.delete(f"/api/projects/{pid}/branches/{bid}/cases/{case_id}", headers=h)
        assert resp.status_code in (200, 204)

        # 9. 删除后列表不再包含
        resp = await client.get(f"/api/projects/{pid}/branches/{bid}/cases", headers=h)
        case_ids = [c["id"] for c in resp.json()["data"]]
        assert case_id not in case_ids


# ===========================================================================
# E2E-004: 测试计划全生命周期
# 创建用例 → 创建计划 → 查看详情 → 归档 → 删除
# ===========================================================================
class TestE2EPlanLifecycle:

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_plan_full_lifecycle(self, client, db_session):
        """计划全链路：创建用例 → 创建计划 → 详情 → 归档 → 删除"""
        admin = await create_test_user(db_session, username="e2e_plan_admin", role="admin")
        h, _ = make_auth_headers(admin)

        # 1. 创建项目 + 分支 + 用例
        resp = await client.post("/api/projects", headers=h, json={
            "name": "e2e-plan-proj", "gitUrl": "git@x.com:e2e/plan.git",
            "scriptBasePath": "/tmp/e2e-plan",
        })
        pid = resp.json()["data"]["id"]
        resp = await client.get(f"/api/projects/{pid}/branches", headers=h)
        bid = resp.json()["data"][0]["id"]

        case_ids = []
        for i in range(3):
            resp = await client.post(f"/api/projects/{pid}/branches/{bid}/cases", headers=h, json={
                "title": f"计划用例-{i}", "type": "api", "module": "CORE",
                "priority": "P0", "steps": [{"action": f"step-{i}"}],
            })
            case_ids.append(resp.json()["data"]["id"])

        # 2. 创建计划
        resp = await client.post(f"/api/projects/{pid}/plans", headers=h, json={
            "name": "E2E 冒烟计划", "planType": "manual", "testType": "api",
            "caseIds": case_ids,
        })
        assert resp.status_code == 201
        plan_id = resp.json()["data"]["id"]
        assert resp.json()["data"]["status"] == "draft"

        # 3. 查看计划详情
        resp = await client.get(f"/api/projects/{pid}/plans/{plan_id}", headers=h)
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "E2E 冒烟计划"

        # 4. 计划列表
        resp = await client.get(f"/api/projects/{pid}/plans", headers=h)
        assert resp.status_code == 200
        assert len(resp.json()["data"]) >= 1

        # 5. 归档计划
        resp = await client.post(f"/api/projects/{pid}/plans/{plan_id}/archive", headers=h)
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "archived"

        # 6. 删除已归档计划
        resp = await client.delete(f"/api/projects/{pid}/plans/{plan_id}", headers=h)
        assert resp.status_code == 200

        # 7. 验证已删除
        resp = await client.get(f"/api/projects/{pid}/plans/{plan_id}", headers=h)
        assert resp.status_code == 404


# ===========================================================================
# E2E-005: RBAC 全链路
# 创建项目 → 分配不同角色 → 逐一验证权限边界
# ===========================================================================
class TestE2ERBACEnforcement:

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_rbac_full_chain(self, client, db_session):
        """RBAC 全链路：系统 admin / 项目 manager / 项目 member / 系统 guest / 非成员，逐档验边界。

        2026-08-29 换档：项目角色砍到 manager/member 两档，只读那档上移成**账号级**游客
        （硬封顶，见 core/readonly_gate.py）。所以第 4/5 步的主语是系统 guest，
        它在项目里挂的是 member —— 项目角色守卫放它过，拦住它的是非 GET 闸门。
        """
        # 创建用户
        admin = await create_test_user(db_session, username="e2e_rbac_admin", role="admin")
        pa_user = await create_test_user(db_session, username="e2e_rbac_pa", role="user")
        dev = await create_test_user(db_session, username="e2e_rbac_dev", role="user")
        guest = await create_test_user(db_session, username="e2e_rbac_guest", role="guest")
        outsider = await create_test_user(db_session, username="e2e_rbac_out", role="user")

        admin_h, _ = make_auth_headers(admin)
        pa_h, _ = make_auth_headers(pa_user)
        dev_h, _ = make_auth_headers(dev)
        guest_h, _ = make_auth_headers(guest)
        out_h, _ = make_auth_headers(outsider)

        # 1. Admin 创建项目
        resp = await client.post("/api/projects", headers=admin_h, json={
            "name": "e2e-rbac-proj", "gitUrl": "git@x.com:e2e/rbac.git",
            "scriptBasePath": "/tmp/e2e-rbac",
        })
        pid = resp.json()["data"]["id"]

        # 2. 添加各角色成员
        for user, role in [(pa_user, "manager"), (dev, "member"), (guest, "member")]:
            resp = await client.post(f"/api/projects/{pid}/members", headers=admin_h, json={
                "userId": str(user.id), "role": role,
            })
            assert resp.status_code in (200, 201)

        # 3. 外部用户不能访问项目
        resp = await client.get(f"/api/projects/{pid}/members", headers=out_h)
        assert resp.status_code == 403

        # 4. Guest 可以读取
        resp = await client.get(f"/api/projects/{pid}/members", headers=guest_h)
        assert resp.status_code == 200

        # 5. 游客不能写入 —— 注意他在项目里就是 member，拦住他的是账号级封顶
        new_user = await create_test_user(db_session, username="e2e_rbac_new", role="user")
        resp = await client.post(f"/api/projects/{pid}/members", headers=guest_h, json={
            "userId": str(new_user.id), "role": "member",
        })
        assert resp.status_code == 403

        # 6. 普通成员不能管理成员
        resp = await client.post(f"/api/projects/{pid}/members", headers=dev_h, json={
            "userId": str(new_user.id), "role": "member",
        })
        assert resp.status_code == 403

        # 7. 项目管理员可以管理成员
        resp = await client.post(f"/api/projects/{pid}/members", headers=pa_h, json={
            "userId": str(new_user.id), "role": "member",
        })
        assert resp.status_code in (200, 201)

        # 8. 普通成员不能改项目配置
        resp = await client.put(f"/api/projects/{pid}", headers=dev_h, json={
            "description": "dev tried",
        })
        assert resp.status_code == 403

        # 9. 只有 system admin 能改项目配置
        resp = await client.put(f"/api/projects/{pid}", headers=admin_h, json={
            "description": "admin updated",
        })
        assert resp.status_code == 200

        # 10. 只有 system admin 能管理用户
        resp = await client.get("/api/users", headers=dev_h)
        assert resp.status_code == 403

        resp = await client.get("/api/users", headers=admin_h)
        assert resp.status_code == 200
