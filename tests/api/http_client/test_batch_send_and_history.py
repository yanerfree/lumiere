"""「共发几次 / 一次几条」+ 只看得到自己的发送记录。

跑法（根目录那套打真接口，必须用独占的 DATABASE_URL）：
    DATABASE_URL='...lumiere_test_<你的名字>' backend/.venv/bin/python -m pytest \
        tests/api/http_client -q
"""
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.conftest import create_test_user, make_auth_headers


class _Ok(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def ok_url():
    _Ok.hits = 0
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Ok)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _dead_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


@pytest.fixture
async def two_users(db_session):
    a = await create_test_user(db_session, username="hc_batch_a", role="user")
    b = await create_test_user(db_session, username="hc_batch_b", role="admin")
    return make_auth_headers(a)[0], make_auth_headers(b)[0]


async def test_默认发一次_不带批量汇总(client, two_users, ok_url):
    ha, _ = two_users
    d = (await client.post("/api/http-client/send", headers=ha, json={"method": "GET", "url": ok_url})).json()
    assert d["data"]["statusCode"] == 200 and "batch" not in d["data"]
    assert _Ok.hits == 1


async def test_共发5次_一次2条_分3轮(client, two_users, ok_url):
    ha, _ = two_users
    d = (await client.post("/api/http-client/send", headers=ha, json={
        "method": "GET", "url": ok_url, "count": 5, "perRound": 2, "name": "批量"})).json()
    b = d["data"]["batch"]
    assert (b["total"], b["perRound"], b["rounds"], b["success"], b["failed"]) == (5, 2, 3, 5, 0)
    assert [r["round"] for r in b["rows"]] == [1, 1, 2, 2, 3]
    assert b["statusCodes"] == {"200": 5}
    assert _Ok.hits == 5
    assert d["data"]["statusCode"] == 200   # 响应区展示最后一条


async def test_发送记录只看得到自己的_能清空(client, two_users, ok_url):
    ha, hb = two_users
    await client.post("/api/http-client/send", headers=ha, json={"method": "GET", "url": ok_url, "count": 3, "name": "A的"})
    await client.post("/api/http-client/send", headers=ha, json={"method": "GET", "url": _dead_url()})
    await client.post("/api/http-client/send", headers=hb, json={"method": "GET", "url": ok_url})

    ra = (await client.get("/api/http-client/history", headers=ha)).json()
    assert ra["total"] == 2
    newest, older = ra["data"]
    assert (newest["total"], newest["success"], newest["failed"]) == (1, 0, 1)
    assert newest["statusCodes"] == {"出错": 1}
    assert (older["name"], older["total"], older["perRound"], older["success"]) == ("A的", 3, 1, 3)
    assert older["timestamp"]

    # 管理员也只看到自己的那一条
    rb = (await client.get("/api/http-client/history", headers=hb)).json()
    assert rb["total"] == 1

    assert (await client.delete("/api/http-client/history", headers=ha)).status_code == 200
    assert (await client.get("/api/http-client/history", headers=ha)).json()["total"] == 0
    assert (await client.get("/api/http-client/history", headers=hb)).json()["total"] == 1


async def test_次数条数有上限(client, two_users, ok_url):
    ha, _ = two_users
    r = await client.post("/api/http-client/send", headers=ha, json={"method": "GET", "url": ok_url, "count": 1001})
    assert r.status_code == 422
    r = await client.post("/api/http-client/send", headers=ha, json={"method": "GET", "url": ok_url, "perRound": 51})
    assert r.status_code == 422


async def test_间隔_每轮之间等够_默认不等(client, two_users, ok_url):
    ha, _ = two_users
    d = (await client.post("/api/http-client/send", headers=ha, json={
        "method": "GET", "url": ok_url, "count": 3, "perRound": 1, "interval": 0.4})).json()
    b = d["data"]["batch"]
    assert b["interval"] == 0.4 and b["rounds"] == 3
    assert b["elapsedMs"] >= 800          # 3 轮之间等 2 次
    d = (await client.post("/api/http-client/send", headers=ha, json={
        "method": "GET", "url": ok_url, "count": 3, "perRound": 1})).json()
    b = d["data"]["batch"]
    assert b["interval"] is None and b["elapsedMs"] < 800
    r = await client.post("/api/http-client/send", headers=ha, json={"method": "GET", "url": ok_url, "interval": -1})
    assert r.status_code == 422


async def _poll(client, h, run_id, timeout=20):
    import asyncio
    t = 0.0
    while True:
        d = (await client.get(f"/api/http-client/send-batch/{run_id}", headers=h)).json()["data"]
        if not d["batch"]["running"] or t > timeout:
            return d
        await asyncio.sleep(0.1)
        t += 0.1


async def test_边发边看_每条回来就能看到_发完记一行(client, two_users, ok_url):
    import asyncio
    ha, hb = two_users
    rid = (await client.post("/api/http-client/send-batch", headers=ha, json={
        "method": "GET", "url": ok_url, "count": 4, "perRound": 1, "interval": 0.3, "name": "边发边看"})).json()["data"]["runId"]
    await asyncio.sleep(0.45)
    mid = (await client.get(f"/api/http-client/send-batch/{rid}", headers=ha)).json()["data"]["batch"]
    assert mid["running"] and 1 <= mid["sent"] < 4          # 还没发完就已经能看到前面几条
    # 别人看不到这次发送
    assert (await client.get(f"/api/http-client/send-batch/{rid}", headers=hb)).status_code == 404
    d = await _poll(client, ha, rid)
    b = d["batch"]
    assert not b["running"] and b["sent"] == 4 and b["success"] == 4 and d["statusCode"] == 200
    hist = (await client.get("/api/http-client/history", headers=ha)).json()
    assert hist["total"] == 1 and hist["data"][0]["name"] == "边发边看" and hist["data"][0]["total"] == 4
    await _poll(client, ha, rid)                              # 再问一次不会重复记
    assert (await client.get("/api/http-client/history", headers=ha)).json()["total"] == 1


async def test_中途停止_只记已发的(client, two_users, ok_url):
    import asyncio
    ha, _ = two_users
    rid = (await client.post("/api/http-client/send-batch", headers=ha, json={
        "method": "GET", "url": ok_url, "count": 50, "perRound": 1, "interval": 0.2})).json()["data"]["runId"]
    await asyncio.sleep(0.5)
    assert (await client.post(f"/api/http-client/send-batch/{rid}/stop", headers=ha)).status_code == 200
    b = (await _poll(client, ha, rid))["batch"]
    assert b["cancelled"] and not b["running"] and 1 <= b["sent"] < 50
    hist = (await client.get("/api/http-client/history", headers=ha)).json()["data"]
    assert hist[0]["total"] == b["sent"]
