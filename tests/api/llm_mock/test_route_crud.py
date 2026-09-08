"""LLM Mock 路由「增删改查」全链路 —— 打真接口。

这一条盯的是最基本的 CRUD：建得出、读得到、改得动、删得掉，
以及「找不到路由」时给的是能看懂的中文提示，而不是一句英文 Route not found。
"""
import uuid

from tests.conftest import create_test_user, make_auth_headers


async def _admin(db_session, username):
    u = await create_test_user(db_session, username=username, role="admin")
    headers, _ = make_auth_headers(u)
    return headers


async def _create(client, headers, name, path, method="POST"):
    r = await client.post(
        "/api/llm-mock/routes",
        headers=headers,
        json={"name": name, "method": method, "path": path},
    )
    return r


def _body(r):
    d = r.json()
    return d["data"] if isinstance(d, dict) and "data" in d else d


def _rows(r):
    d = r.json()
    return d["data"] if isinstance(d, dict) and "data" in d else d


async def test_增删改查全链路(client, db_session):
    headers = await _admin(db_session, "crud_admin")

    # 建两条：第一条会成为「默认路由」（最早创建，不允许删），拿第二条走删除
    ra = await _create(client, headers, "默认", "/crud-a/v1/chat/completions")
    assert ra.status_code == 201, ra.text
    rb = await _create(client, headers, "待改待删", "/crud-b/v1/chat/completions")
    assert rb.status_code == 201, rb.text
    b_id = _body(rb)["id"]

    # 读：列表里两条都在
    rl = await client.get("/api/llm-mock/routes", headers=headers)
    assert rl.status_code == 200
    paths = {x["path"] for x in _rows(rl)}
    assert {"/crud-a/v1/chat/completions", "/crud-b/v1/chat/completions"} <= paths

    # 改：名字 + 路径都改，读回确认真的改了
    ru = await client.put(
        f"/api/llm-mock/routes/{b_id}",
        headers=headers,
        json={"name": "改过了", "path": "/crud-b2/v1/chat/completions"},
    )
    assert ru.status_code == 200, ru.text
    assert _body(ru)["name"] == "改过了"
    assert _body(ru)["path"] == "/crud-b2/v1/chat/completions"

    # 删：删掉第二条，再读就没了
    rd = await client.delete(f"/api/llm-mock/routes/{b_id}", headers=headers)
    assert rd.status_code == 200, rd.text
    rl2 = await client.get("/api/llm-mock/routes", headers=headers)
    paths2 = {x["path"] for x in _rows(rl2)}
    assert "/crud-b2/v1/chat/completions" not in paths2


async def test_改一条不存在的路由给中文提示不是英文(client, db_session):
    headers = await _admin(db_session, "crud_admin2")
    ghost = uuid.uuid4()
    r = await client.put(
        f"/api/llm-mock/routes/{ghost}",
        headers=headers,
        json={"name": "x"},
    )
    assert r.status_code == 404, r.text
    err = r.json()["error"]
    assert "Route not found" not in err
    assert "不存在" in err


async def test_删一条不存在的路由给中文提示(client, db_session):
    headers = await _admin(db_session, "crud_admin3")
    r = await client.delete(f"/api/llm-mock/routes/{uuid.uuid4()}", headers=headers)
    assert r.status_code == 404, r.text
    assert "不存在" in r.json()["error"]
