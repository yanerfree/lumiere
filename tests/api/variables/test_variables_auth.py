"""
test_variables_auth — 变量/环境/渠道模块的认证测试
覆盖: 未认证用户不能访问任何变量/环境/渠道端点

环境和全局变量 2026-08-21 改成项目级，路径带上了 {project_id}
（迁移 zzo0envproj / zzp0gvarproj）。这里用一个随便编的项目 UUID ——
**未认证就该 401，不该先告诉对方"这个项目不存在"**：认证在鉴权之前。
"""
import uuid

import pytest

FAKE_PROJECT = uuid.uuid4()


class TestVariablesAuth:

    @pytest.mark.asyncio
    async def test_global_variables_requires_auth(self, client):
        """未认证不能访问全局变量"""
        response = await client.get(f"/api/projects/{FAKE_PROJECT}/global-variables")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_environments_requires_auth(self, client):
        """未认证不能访问环境"""
        response = await client.get(f"/api/projects/{FAKE_PROJECT}/environments")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_channels_requires_auth(self, client):
        """未认证不能访问通知渠道"""
        response = await client.get("/api/channels")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_global_variable_requires_auth(self, client):
        """未认证不能创建全局变量"""
        response = await client.post(
            f"/api/projects/{FAKE_PROJECT}/global-variables",
            json={"key": "TEST_VAR", "value": "test"})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_environment_requires_auth(self, client):
        """未认证不能创建环境"""
        response = await client.post(f"/api/projects/{FAKE_PROJECT}/environments",
                                     json={"name": "test-env"})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_channel_requires_auth(self, client):
        """未认证不能创建渠道"""
        response = await client.post("/api/channels", json={
            "name": "test-ch", "webhookUrl": "https://oapi.dingtalk.com/robot/send?access_token=test",
        })
        assert response.status_code == 401
