"""游客的 MCP Key 一律拒 —— HTTP 那条只读闸门管不到 :18800。

为什么需要单独一条封样：HTTP 的游客闸门按「方法安不安全」判
（`core/readonly_gate.blocks_guest`），而 MCP 这条路**没有 HTTP 方法可判**，
鉴权也只把 bearer 哈希后比对 `mcp_api_keys`、**从来不读 `users.role`**。
于是一个用户被降成游客后，他**降级前建的 Key 照样能写** —— 封顶有一条明确的绕过路径。
2026-08-29 加游客角色时补上拦截，这里把它钉住。

这几条**真跑 `on_call_tool`**，不读源码：拦截点周围全是 try/except（记账不能拖垮
MCP 调用），写错了不会报错，只会静默放行。
"""
from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError

from app.mcp import middleware as mw


def _prime(monkeypatch, *, owner_role, allowed=None, project=None):
    """塞一把假 Key 进中间件缓存，并让它以为当前请求带着这个 bearer。"""
    token = f"tb_faketoken_{owner_role}"
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    mw._CACHE[key_hash] = (allowed, str(uuid.uuid4()), "测试Key", project,
                           owner_role, time.monotonic())
    monkeypatch.setattr(mw, "get_http_headers",
                        lambda include=None: {"authorization": f"Bearer {token}"})


def _call(tool_name="lum_update_case"):
    """跑一次 on_call_tool，返回 call_next 有没有被执行到。"""
    ran = []

    async def call_next(_ctx):
        ran.append(True)
        return "ok"

    ctx = SimpleNamespace(message=SimpleNamespace(name=tool_name, arguments={}))
    asyncio.run(mw.ToolScopeMiddleware().on_call_tool(ctx, call_next))
    return bool(ran)


def test_游客的Key调写工具被拒(monkeypatch):
    _prime(monkeypatch, owner_role="guest")
    with pytest.raises(ToolError) as e:
        _call("lum_update_case")
    assert "游客" in str(e.value)


def test_游客的Key连只读工具也拒(monkeypatch):
    """**整条拒绝**是刻意的，不是漏了没细分。

    lum_* 谁读谁写没有任何结构化元数据（就是普通函数，没有 readonly 标注），
    靠函数名手工分五十多个工具，分错一个就是「游客能写」—— 而且分错了不会报错。
    多挡掉游客的只读调用是看得见、可恢复的；漏放一个写工具是静默的。
    将来要给游客开只读 MCP，正确做法是先给工具加 readonly 标注（让「是不是只读」
    变成可查的事实），再回来改这里 —— 那时这条测试会红，红得对。
    """
    _prime(monkeypatch, owner_role="guest")
    with pytest.raises(ToolError):
        _call("lum_list_projects")


def test_游客被拒时工具根本没执行(monkeypatch):
    """拦截必须发生在 call_next **之前**。

    放在后面的话工具已经跑完、库已经写了，再抛错只是让调用方看见一个红 ——
    这正是「403 不等于没写」那类假安全。
    """
    _prime(monkeypatch, owner_role="guest")
    ran = []

    async def call_next(_ctx):
        ran.append(True)
        return "ok"

    ctx = SimpleNamespace(message=SimpleNamespace(name="lum_create_case", arguments={}))
    with pytest.raises(ToolError):
        asyncio.run(mw.ToolScopeMiddleware().on_call_tool(ctx, call_next))
    assert ran == [], "工具已经执行完才拦 —— 库都写了，拦了个寂寞"


def test_普通用户的Key照常放行(monkeypatch):
    """反向：拦截不能宽到把所有人都挡了（那样测试也会全绿，但平台不能用）。"""
    _prime(monkeypatch, owner_role="user")
    assert _call("lum_update_case") is True


def test_查不到主人的Key不受影响(monkeypatch):
    """owner_role 为 None = 查不到主人（env key、存量 Key）。

    这里**故意不 fail-closed**：这条拦的是「降级成游客的人」，不是防攻击者；
    把查不到主人的 Key 一并挡掉会让存量 Key 和 env key 集体失联，
    代价远大于它挡住的东西。真要收紧，应当先把 Key 都归好主人。
    """
    _prime(monkeypatch, owner_role=None)
    assert _call("lum_update_case") is True
