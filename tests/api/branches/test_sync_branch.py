"""
test_sync_branch — POST /api/projects/{id}/branches/{bid}/sync
Test ID: 2.2-API-001
Priority: P0

覆盖验收标准:
- 提交同步任务返回 202 + taskId
- Git URL 未配置时返回 422
- 脚本路径未配置时返回 422
- 归档分支不能同步
- guest 不能执行同步
- 任务状态轮询 GET /api/tasks/{taskId}/status

注意: 依赖 Redis（arq），无 Redis 时自动跳过。
"""
import pytest
from unittest.mock import AsyncMock, patch

from tests.conftest import create_test_user, make_auth_headers

# 检测 Redis 是否可用
try:
    import redis
    _r = redis.Redis(host="localhost", port=6379, socket_connect_timeout=1)
    _r.ping()
    REDIS_AVAILABLE = True
except Exception:
    REDIS_AVAILABLE = False

pytestmark = pytest.mark.skipif(not REDIS_AVAILABLE, reason="Redis not available")


class TestSyncBranchValidation:
    """同步前置校验 — 不需要 Redis，直接返回 4xx"""

    @pytest.mark.asyncio
    async def test_sync_requires_git_url(self, client, db_session):
        """AC: 项目未配置 Git URL 时返回 422"""
        admin = await create_test_user(db_session, username="sync_admin", role="admin")
        headers, _ = make_auth_headers(admin)

        resp = await client.post("/api/projects", headers=headers, json={
            "name": "no-git-project",
            "scriptBasePath": "/tmp/no-git",
        })
        project_id = resp.json()["data"]["id"]

        resp = await client.get(f"/api/projects/{project_id}/branches", headers=headers)
        branch_id = resp.json()["data"][0]["id"]

        resp = await client.post(
            f"/api/projects/{project_id}/branches/{branch_id}/sync",
            headers=headers,
        )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_sync_requires_script_base_path(self, client, db_session):
        """AC: 项目未配置脚本路径时返回 422"""
        admin = await create_test_user(db_session, username="sync_admin2", role="admin")
        headers, _ = make_auth_headers(admin)

        resp = await client.post("/api/projects", headers=headers, json={
            "name": "no-path-project",
            "gitUrl": "git@example.com:test/repo.git",
        })
        project_id = resp.json()["data"]["id"]

        resp = await client.get(f"/api/projects/{project_id}/branches", headers=headers)
        branch_id = resp.json()["data"][0]["id"]

        resp = await client.post(
            f"/api/projects/{project_id}/branches/{branch_id}/sync",
            headers=headers,
        )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_sync_archived_branch_returns_422(self, client, db_session):
        """AC: 已归档分支不能同步"""
        admin = await create_test_user(db_session, username="sync_admin3", role="admin")
        headers, _ = make_auth_headers(admin)

        resp = await client.post("/api/projects", headers=headers, json={
            "name": "archive-sync-project",
            "gitUrl": "git@example.com:test/repo.git",
            "scriptBasePath": "/tmp/archive-sync",
        })
        project_id = resp.json()["data"]["id"]

        await client.post(f"/api/projects/{project_id}/branches", headers=headers, json={
            "name": "extra-branch",
            "branch": "develop",
        })

        resp = await client.get(f"/api/projects/{project_id}/branches", headers=headers)
        default_branch = [b for b in resp.json()["data"] if b["name"] == "default"][0]
        branch_id = default_branch["id"]

        await client.post(
            f"/api/projects/{project_id}/branches/{branch_id}/archive",
            headers=headers,
        )

        resp = await client.post(
            f"/api/projects/{project_id}/branches/{branch_id}/sync",
            headers=headers,
        )

        assert resp.status_code == 422


class TestSyncBranchAsync:
    """异步同步 — 后台任务（不再走 arq/Redis）

    同步早就从 arq 队列改成了 FastAPI BackgroundTasks + run_git_sync_inline，
    这两条却还在 patch 已经不存在的 app.api.branches.get_arq_pool，直接
    AttributeError。测的是早没了的实现，所以改成断言后台任务被挂上去。
    """

    async def _make_branch(self, client, db_session, username, project_name, path):
        admin = await create_test_user(db_session, username=username, role="admin")
        headers, _ = make_auth_headers(admin)
        resp = await client.post("/api/projects", headers=headers, json={
            "name": project_name,
            "gitUrl": "git@example.com:test/repo.git",
            "scriptBasePath": path,
        })
        project_id = resp.json()["data"]["id"]
        resp = await client.get(f"/api/projects/{project_id}/branches", headers=headers)
        return headers, project_id, resp.json()["data"][0]["id"]

    @pytest.mark.asyncio
    async def test_sync_returns_202_with_task_id(self, client, db_session):
        """AC: 提交任务返回 202 + taskId"""
        headers, project_id, branch_id = await self._make_branch(
            client, db_session, "sync_admin4", "async-sync-project", "/tmp/async-sync")

        # 后台任务本体不跑（会真去 clone），只验端点的返回
        with patch("app.api.branches.set_task_status", new_callable=AsyncMock), \
             patch("app.api.branches.run_git_sync_inline", new_callable=AsyncMock):
            resp = await client.post(
                f"/api/projects/{project_id}/branches/{branch_id}/sync", headers=headers)

        assert resp.status_code == 202
        data = resp.json()["data"]
        assert "taskId" in data
        assert len(data["taskId"]) == 32  # uuid hex

    @pytest.mark.asyncio
    async def test_sync_schedules_background_task(self, client, db_session):
        """AC: 端点调用后，同步任务被挂进后台任务并拿到正确参数"""
        headers, project_id, branch_id = await self._make_branch(
            client, db_session, "sync_admin5", "enqueue-sync-project", "/tmp/enqueue-sync")

        with patch("app.api.branches.set_task_status", new_callable=AsyncMock), \
             patch("app.api.branches.run_git_sync_inline", new_callable=AsyncMock) as mock_sync:
            resp = await client.post(
                f"/api/projects/{project_id}/branches/{branch_id}/sync", headers=headers)

        task_id = resp.json()["data"]["taskId"]
        # BackgroundTasks 在响应发出后执行，此时应已跑过一次
        mock_sync.assert_called_once_with(task_id, str(branch_id), str(project_id))


class TestSyncBranchPermissions:
    """同步权限: project_admin / developer / tester 可同步，guest 不行"""

    @pytest.mark.asyncio
    async def test_guest_cannot_sync(self, client, db_session):
        """AC: guest 不能执行同步"""
        admin = await create_test_user(db_session, username="perm_admin", role="admin")
        guest = await create_test_user(db_session, username="perm_guest", role="user")
        admin_headers, _ = make_auth_headers(admin)
        guest_headers, _ = make_auth_headers(guest)

        resp = await client.post("/api/projects", headers=admin_headers, json={
            "name": "perm-sync-project",
            "gitUrl": "git@example.com:test/repo.git",
            "scriptBasePath": "/tmp/perm-sync",
        })
        project_id = resp.json()["data"]["id"]

        await client.post(f"/api/projects/{project_id}/members", headers=admin_headers, json={
            "userId": str(guest.id),
            "role": "guest",
        })

        resp = await client.get(f"/api/projects/{project_id}/branches", headers=admin_headers)
        branch_id = resp.json()["data"][0]["id"]

        resp = await client.post(
            f"/api/projects/{project_id}/branches/{branch_id}/sync",
            headers=guest_headers,
        )

        assert resp.status_code == 403
