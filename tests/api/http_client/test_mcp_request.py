"""HTTP 请求页的 MCP 请求：三种会话方式 + 推送格式（text/event-stream）整理成 JSON + 保存 kind/mcp 设置。

假 MCP 服务：initialize 发会话号；之后的调用**必须**带会话号，不带回 400（和真服务一样）；
回答用推送格式包一层。

跑法（根目录那套打真接口，必须用独占的 DATABASE_URL）：
    DATABASE_URL='...lumiere_test_<你的名字>' backend/.venv/bin/python -m pytest tests/api/http_client -q
"""
import itertools
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.conftest import create_test_user, make_auth_headers


class _FakeMcp(BaseHTTPRequestHandler):
    counter = itertools.count(1)
    sessions: set = set()
    seen: list = []          # (method, 带的会话号)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        method = body.get("method")
        sid = self.headers.get("Mcp-Session-Id")
        type(self).seen.append((method, sid))
        if method == "initialize":
            new = f"s{next(self.counter)}"
            self.sessions.add(new)
            return self._sse({"jsonrpc": "2.0", "id": body.get("id"),
                              "result": {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                                         "serverInfo": {"name": "fake", "version": "1"}}}, sid=new)
        if method == "notifications/initialized":
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if sid not in self.sessions:
            out = b'{"jsonrpc":"2.0","error":{"code":-32000,"message":"Bad Request: No valid session ID provided"}}'
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        if method == "tools/list":
            return self._sse({"jsonrpc": "2.0", "id": body.get("id"), "result": {"tools": [{"name": "echo"}]}})
        if method == "tools/call":
            p = body.get("params") or {}
            return self._sse({"jsonrpc": "2.0", "id": body.get("id"),
                              "result": {"content": [{"type": "text", "text": f"{p.get('name')}:{json.dumps(p.get('arguments'))}"}]}})
        return self._sse({"jsonrpc": "2.0", "id": body.get("id"), "result": {}})

    def _sse(self, obj, sid=None):
        out = ("event: message\ndata: " + json.dumps(obj) + "\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        if sid:
            self.send_header("Mcp-Session-Id", sid)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def mcp_url():
    _FakeMcp.sessions = set()
    _FakeMcp.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeMcp)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/mcp"
    srv.shutdown()


@pytest.fixture
async def ha(db_session):
    u = await create_test_user(db_session, username="hc_mcp_user", role="user")
    return make_auth_headers(u)[0]


def _call(url, session, count=1, method="tools/call", params=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return {"method": "POST", "url": url, "body": json.dumps(body), "count": count,
            "headers": {"Content-Type": "application/json"}, "mcp": {"session": session}}


async def test_不打招呼_不带会话号_要会话号的服务直接拒(client, ha, mcp_url):
    d = (await client.post("/api/http-client/send", headers=ha, json=_call(mcp_url, "none", params={"name": "echo"}))).json()
    assert d["data"]["statusCode"] == 400
    assert _FakeMcp.seen == [("tools/call", None)]          # 没有偷偷打招呼


async def test_整批同一会话_只打一次招呼(client, ha, mcp_url):
    d = (await client.post("/api/http-client/send", headers=ha, json=_call(
        mcp_url, "batch", count=3, params={"name": "echo", "arguments": {"a": 1}}))).json()["data"]
    b = d["batch"]
    assert b["success"] == 3
    sids = {r["sessionId"] for r in b["rows"]}
    assert len(sids) == 1 and None not in sids
    assert [m for m, _ in _FakeMcp.seen].count("initialize") == 1
    # 推送格式被整理成了 JSON
    body = json.loads(d["body"])
    assert body["result"]["content"][0]["text"] == 'echo:{"a": 1}'


async def test_每条单独会话_每条一个新会话号(client, ha, mcp_url):
    b = (await client.post("/api/http-client/send", headers=ha, json=_call(
        mcp_url, "each", count=3, method="tools/list", params={}))).json()["data"]["batch"]
    assert b["success"] == 3
    sids = [r["sessionId"] for r in b["rows"]]
    assert len(set(sids)) == 3 and None not in sids
    assert [m for m, _ in _FakeMcp.seen].count("initialize") == 3


async def test_发一次也能打招呼_返回里带会话号(client, ha, mcp_url):
    d = (await client.post("/api/http-client/send", headers=ha, json=_call(
        mcp_url, "batch", method="tools/list", params={}))).json()["data"]
    assert d["statusCode"] == 200 and d["mcpSessionId"]


async def test_打招呼失败_整批停下并说明原因(client, ha):
    d = (await client.post("/api/http-client/send", headers=ha, json=_call(
        "http://127.0.0.1:1/mcp", "batch", count=5, params={}))).json()["data"]
    b = d["batch"]
    assert b["sent"] == 1 and b["failed"] == 1 and "打招呼失败" in b["rows"][0]["error"]


async def test_保存MCP请求_kind和设置读得回来(client, ha):
    cfg = {"method": "tools/call", "toolName": "echo", "arguments": '{"a":1}', "session": "batch"}
    r = await client.post("/api/http-client/requests", headers=ha, json={"name": "m", "method": "POST", "kind": "mcp", "mcp_config": cfg})
    rid = r.json()["data"]["id"]
    cfg2 = {**cfg, "toolName": "other"}
    await client.put(f"/api/http-client/requests/{rid}", headers=ha, json={"mcp_config": cfg2})
    got = next(i for i in (await client.get("/api/http-client/requests", headers=ha)).json()["data"] if i["id"] == rid)
    assert got["kind"] == "mcp" and got["mcpConfig"]["toolName"] == "other"
    # 普通请求默认 kind=http
    r = await client.post("/api/http-client/requests", headers=ha, json={"name": "h"})
    assert r.json()["data"]["kind"] == "http"
