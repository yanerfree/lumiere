"""LLM Mock 路由「方法+路径」唯一性 —— 打真接口。

运行时按 (method, path) 选路由，两条撞上后一条永久命中不到、还偶发，
页面上却是两行看不出问题。所以建/改路由时同一个「方法+路径」只能有一条。
这里钉住三件事：撞了要回 409、方法不同不算撞、大小写要当同一条。
"""
from tests.conftest import create_test_user, make_auth_headers


async def _admin(db_session, username="mock_admin"):
    u = await create_test_user(db_session, username=username, role="admin")
    headers, _ = make_auth_headers(u)
    return headers


async def _create(client, headers, name, path, method="POST"):
    return await client.post(
        "/api/llm-mock/routes",
        headers=headers,
        json={"name": name, "method": method, "path": path},
    )


async def test_同方法同路径重复建会被拦(client, db_session):
    headers = await _admin(db_session)
    p = "/uniq-test/v1/chat/completions"
    r1 = await _create(client, headers, "第一条", p)
    assert r1.status_code == 201, r1.text
    r2 = await _create(client, headers, "重复的", p)
    assert r2.status_code == 409, r2.text
    assert "已存在" in r2.json()["error"]


async def test_方法不同同路径可以共存(client, db_session):
    headers = await _admin(db_session, "mock_admin2")
    p = "/uniq-test2/v1/chat/completions"
    r1 = await _create(client, headers, "POST 版", p, method="POST")
    assert r1.status_code == 201, r1.text
    r2 = await _create(client, headers, "GET 版", p, method="GET")
    assert r2.status_code == 201, r2.text


async def test_大小写方法当作同一条(client, db_session):
    headers = await _admin(db_session, "mock_admin3")
    p = "/uniq-test3/v1/chat/completions"
    r1 = await _create(client, headers, "大写 POST", p, method="POST")
    assert r1.status_code == 201, r1.text
    r2 = await _create(client, headers, "小写 post", p, method="post")
    assert r2.status_code == 409, r2.text


async def test_改路径撞上别的路由会被拦(client, db_session):
    headers = await _admin(db_session, "mock_admin4")
    ra = await _create(client, headers, "A", "/uniq-test4a/v1/chat/completions")
    rb = await _create(client, headers, "B", "/uniq-test4b/v1/chat/completions")
    assert ra.status_code == 201 and rb.status_code == 201
    b_id = (rb.json().get("data") or rb.json())["id"]
    # 把 B 的路径改成和 A 一样 → 409
    r = await client.put(
        f"/api/llm-mock/routes/{b_id}",
        headers=headers,
        json={"path": "/uniq-test4a/v1/chat/completions"},
    )
    assert r.status_code == 409, r.text
