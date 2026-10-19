"""Headers 的备注列 + 中文自动编码。

- 备注（每行 header 上的 `desc`）只是给人看的，要能存住、读回来；发送接口只收 {名: 值}，
  备注由前端剔掉，不会发出去。
- 请求头只能是 ASCII，以前填中文一发就是 `'ascii' codec can't encode`，整条请求出不去。
  现在发送前把非 ASCII 字符按 UTF-8 编成 %XX，ASCII 部分原样不动。

跑法（根目录那套打真接口，必须用独占的 DATABASE_URL）：
    DATABASE_URL='...lumiere_test_<你的名字>' backend/.venv/bin/python -m pytest \
        tests/api/http_client -q
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests.conftest import create_test_user, make_auth_headers


class _Echo(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"headers": dict(self.headers), "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def echo_url():
    srv = HTTPServer(("127.0.0.1", 0), _Echo)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
async def headers(db_session):
    admin = await create_test_user(db_session, username="hc_cn_admin", role="admin")
    h, _ = make_auth_headers(admin)
    return h


async def test_备注能存住读回来(client, headers):
    r = await client.post("/api/http-client/requests", headers=headers, json={"name": "备注"})
    rid = r.json()["data"]["id"]
    hs = [{"key": "X-Token", "value": "abc", "desc": "登录拿到的令牌", "enabled": True}]
    r = await client.put(f"/api/http-client/requests/{rid}", headers=headers, json={"headers": hs})
    assert r.status_code == 200
    items = (await client.get("/api/http-client/requests", headers=headers)).json()["data"]
    got = next(i for i in items if i["id"] == rid)
    assert got["headers"][0]["desc"] == "登录拿到的令牌"


async def test_中文请求头自动编码后发出去(client, headers, echo_url):
    r = await client.post("/api/http-client/send", headers=headers, json={
        "method": "GET", "url": f"{echo_url}/搜索?q=中文",
        "headers": {"X-User": "张三 a%20b", "X-Plain": "ok"},
    })
    d = r.json()
    assert "error" not in d, d
    data = d["data"]
    assert data["statusCode"] == 200
    echoed = json.loads(data["body"])
    seen = {k.lower(): v for k, v in echoed["headers"].items()}
    # 中文被编码；ASCII（含已有的 %20）原样保留，不会二次编码成 %2520
    assert seen["x-user"] == "%E5%BC%A0%E4%B8%89 a%20b"
    assert seen["x-plain"] == "ok"
    # 地址里的中文也照常编码送达
    assert echoed["path"] == "/%E6%90%9C%E7%B4%A2?q=%E4%B8%AD%E6%96%87"
    # 「实际请求」页签展示的就是编码后的值
    actual = {k.lower(): v for k, v in data["actualRequest"]["headers"].items()}
    assert actual["x-user"] == "%E5%BC%A0%E4%B8%89 a%20b"
