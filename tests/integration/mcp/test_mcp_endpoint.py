"""MCP 端点的接口级测试：发 Key → 握手 → tools/list → tools/call。

**为什么单独一套：** 59 个工具此前只有单测（`backend/tests/test_mcp_profiles.py`
钉档位和注册表对得上）和结构封样，**没有一条测试真的从 HTTP 打进来过**。
于是这两类问题谁都拦不住：

1. 认证。`MCPAuthMiddleware` 曾有一条「没带 bearer 且 MCP_API_KEY 未设 → 放行」
   的分支，而那个环境变量从来没设过 —— 匿名就能 initialize 然后把全部项目
   读出来（2026-08-21 实测）。单测看不见这条路，因为它在 ASGI 中间件层。
2. 挂载点。MCP 只挂在独立端口（`MCP_PORT`，默认 18800），主端口没有
   `/mcp`。改挂载/改前缀时，后端单测会全绿而客户端连不上。

**跑法上的两个坑：**

- 中间件和工具用的是 app 自己的 `async_session_factory`，不是 conftest 里
  被 override 的那个 session。所以测试数据必须**通过接口写进去并提交**，
  直接往 `db_session` 里 add 是看不见的。
- 传输是 streamable HTTP，响应体是 SSE（`data: {...}`），不是 JSON。
  拿 `resp.json()` 会炸。
"""
import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import create_test_user, make_auth_headers

pytestmark = pytest.mark.skipif(
    __import__("os").environ.get("BASE_URL", "").strip() != "",
    reason="平台模式下 /mcp 在另一个端口上，不走 BASE_URL",
)

MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _parse_sse(body: str) -> dict | None:
    """从 SSE 响应体里取出第一条 JSON-RPC 消息。"""
    for line in body.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return None


class McpSession:
    """一个 MCP 客户端会话：自己记 session id，按 JSON-RPC 发请求。"""

    def __init__(self, http: AsyncClient, token: str):
        self.http = http
        self.token = token
        self.session_id: str | None = None
        self._next_id = 0

    async def send(self, method: str, params: dict | None = None, *, notify: bool = False):
        payload: dict = {"jsonrpc": "2.0", "method": method}
        if not notify:
            self._next_id += 1
            payload["id"] = self._next_id
        if params is not None:
            payload["params"] = params
        headers = {**MCP_HEADERS, "Authorization": f"Bearer {self.token}"}
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        resp = await self.http.post("/mcp/", headers=headers, json=payload)
        if "mcp-session-id" in resp.headers:
            self.session_id = resp.headers["mcp-session-id"]
        return resp

    async def handshake(self):
        resp = await self.send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest-mcp", "version": "1"},
        })
        assert resp.status_code == 200, resp.text
        msg = _parse_sse(resp.text)
        assert msg and "result" in msg, resp.text
        await self.send("notifications/initialized", notify=True)
        return msg["result"]


@pytest.fixture
async def mcp_http(db_session):
    """把 MCP app 单独挂起来 —— 它不在主 app 上，主端口没有 /mcp。

    **为什么要 `engine.dispose()`：** `db_session` 收尾会 `drop_all`，下一条用例
    再 `create_all` —— 表的 OID 换了，而 app 自己那个引擎的连接池里还压着旧连接，
    asyncpg 在上面缓存过语句计划。中间件拿到这种连接查 Key 会抛
    `InvalidCachedStatementError`，而它是 fail closed 的（`except → 拒绝`），
    于是**报出来是 401「Unauthorized」，看着像认证写错了**。
    实测：单跑任何一条都过，两条一起跑第二条必 401。
    每条用例开头把池丢掉，就没有跨用例继承的旧连接。

    lifespan 必须在**同一个 task 里** enter 和 exit：里面是 anyio task group，
    在 fixture 建立时 enter、在 teardown 时 exit 会撞上
    `RuntimeError: Attempted to exit cancel scope in a different task`
    （pytest-asyncio 的 setup 和 teardown 不保证同一个 task）。所以起一个
    专门的 task 托着它，两头用 Event 对齐 —— uvicorn 也是这么跑的。
    """
    import asyncio
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from app.deps.db import engine
    from app.main import _mcp_app

    await engine.dispose()
    ready, stop = asyncio.Event(), asyncio.Event()

    async def _hold_lifespan():
        async with _mcp_app.lifespan(None):
            ready.set()
            await stop.wait()

    holder = asyncio.create_task(_hold_lifespan())
    await ready.wait()
    try:
        asgi = Starlette(routes=[Mount("/mcp", app=_mcp_app)])
        async with AsyncClient(transport=ASGITransport(app=asgi),
                               base_url="http://mcp-test", timeout=30) as ac:
            yield ac
    finally:
        stop.set()
        await holder


@pytest.fixture
async def issued_key(client, db_session):
    """建项目 + 发一把绑这个项目的 Key，返回 (raw_key, project_id, project_name)。

    Key 必须绑项目：`project_id` 现在同时管工具范围和数据范围，不绑的是给存量
    留的口子（见 CLAUDE.md）。

    注意这里**必须走接口**建项目和发 Key，不能直接往 db_session 里 add：
    中间件和工具用的是 app 自己的 session factory，走的是另一条连接，
    只看得见已提交的数据。接口 handler 会 commit，顺带把这个 admin 也提交掉。
    """
    admin = await create_test_user(db_session, username=f"mcpadm_{uuid.uuid4().hex[:6]}",
                                   role="admin")
    headers, _ = make_auth_headers(admin)
    name = f"mcp-e2e-{uuid.uuid4().hex[:8]}"
    r = await client.post("/api/projects", headers=headers, json={"name": name})
    assert r.status_code in (200, 201), r.text
    project_id = r.json()["data"]["id"]

    r = await client.post("/api/mcp-keys", headers=headers,
                          json={"name": "pytest-mcp-key", "project_id": project_id})
    assert r.status_code in (200, 201), r.text
    data = r.json()["data"]
    assert data["key"].startswith("lum_"), f"新发 Key 的前缀应该是 lum_：{data['keyPrefix']}"
    return data["key"], project_id, name


async def test_anonymous_is_denied(mcp_http):
    """不带凭据一律 401 —— 这条路以前是开的，把全部项目暴露在局域网里。"""
    resp = await mcp_http.post("/mcp/", headers=MCP_HEADERS, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "anon", "version": "1"}},
    })
    assert resp.status_code == 401, f"匿名请求居然通了：{resp.status_code} {resp.text[:200]}"


async def test_bogus_key_is_denied(mcp_http):
    """瞎编的 Key 也得 401（查库查不到 → fail closed，不是放行）。"""
    resp = await mcp_http.post("/mcp/", headers={
        **MCP_HEADERS, "Authorization": "Bearer lum_faketoken_not_in_db"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "bogus", "version": "1"}}})
    assert resp.status_code == 401, resp.text


async def test_handshake_reports_lumiere(mcp_http, issued_key):
    raw_key, _, _ = issued_key
    result = await McpSession(mcp_http, raw_key).handshake()
    assert result["serverInfo"]["name"] == "Lumiere", result["serverInfo"]


async def test_tools_list_all_lum_prefixed(mcp_http, issued_key):
    """工具名必须全是 lum_，且都在注册表里。

    2026-08-26 前缀从 tb_ 改成 lum_。客户端是靠这些名字调的，所以这里既钉
    「没有漏改的 tb_」，也钉「没有凭空多出注册表里没有的名字」。
    """
    from app.mcp import TOOL_CATALOG

    raw_key, _, _ = issued_key
    sess = McpSession(mcp_http, raw_key)
    await sess.handshake()
    resp = await sess.send("tools/list")
    msg = _parse_sse(resp.text)
    tools = msg["result"]["tools"]
    names = [t["name"] for t in tools]

    assert names, "tools/list 返回空 —— 客户端连上了但一个工具都调不了"
    stale = [n for n in names if n.startswith("tb_")]
    assert not stale, f"还有旧前缀的工具名：{stale}"
    assert all(n.startswith("lum_") for n in names), \
        f"有不带 lum_ 前缀的工具名：{[n for n in names if not n.startswith('lum_')]}"

    registered = {t["name"] for t in TOOL_CATALOG}
    assert set(names) <= registered, f"tools/list 里有注册表没有的名字：{set(names) - registered}"


async def test_tools_call_reads_own_project(mcp_http, issued_key):
    """真调一个只读工具，且只看得见自己那个项目（数据范围跟着 Key 的 project_id）。"""
    raw_key, _, project_name = issued_key
    sess = McpSession(mcp_http, raw_key)
    await sess.handshake()
    resp = await sess.send("tools/call", {"name": "lum_list_projects", "arguments": {}})
    msg = _parse_sse(resp.text)
    result = msg["result"]
    assert not result.get("isError"), result
    text = "".join(c.get("text", "") for c in result["content"])
    assert project_name in text, f"没看到自己的项目 {project_name}：{text[:300]}"
