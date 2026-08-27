"""HTTP 请求集合的目录层级 —— 一共两层：一级目录 → 子目录 → 请求。

为什么后端要拦（前端已经按两层画树了，看着是重复的）：
越界的行**不会报错，只会从树上消失** —— `renderTreeItem` 只从根往下递归两层，
第三层的请求既不在树上、又照旧算进顶部那个请求计数，最后是一条查不出来源的
「请求不见了」。同理，删目录只删直接子级会留下 parentId 指向已删行的孤儿：
实测用户库里就有 6 条（2026-07-10 留下的），一直看不见也删不掉。

跑法（根目录那套打真接口，必须用独占的 DATABASE_URL）：
    DATABASE_URL='...lumiere_test_<你的名字>' backend/.venv/bin/python -m pytest \
        tests/api/http_client -q
"""
import pytest

from tests.conftest import create_test_user, make_auth_headers

BASE = "/api/http-client/requests"


async def _mk(client, headers, name, type_="folder", parent=None):
    r = await client.post(BASE, headers=headers,
                          json={"name": name, "type": type_, "parent_id": parent})
    assert r.status_code == 201, f"{name}: {r.status_code} {r.text}"
    return r.json()["data"]["id"]


async def _all(client, headers):
    r = await client.get(BASE, headers=headers)
    assert r.status_code == 200
    return r.json()["data"]


@pytest.fixture
async def headers(db_session):
    admin = await create_test_user(db_session, username="hc_nest_admin", role="admin")
    h, _ = make_auth_headers(admin)
    return h


class TestFolderNesting:

    @pytest.mark.asyncio
    async def test_一级目录下能建子目录并落库(self, client, headers):
        root = await _mk(client, headers, "hc-root")
        sub = await _mk(client, headers, "hc-sub", parent=root)

        rows = {d["id"]: d for d in await _all(client, headers)}
        assert rows[root]["parentId"] is None
        assert rows[sub]["parentId"] == root
        assert rows[sub]["type"] == "folder"

    @pytest.mark.asyncio
    async def test_子目录里不能再建目录(self, client, headers):
        root = await _mk(client, headers, "hc-root2")
        sub = await _mk(client, headers, "hc-sub2", parent=root)

        r = await client.post(BASE, headers=headers,
                              json={"name": "hc-第三层", "type": "folder", "parent_id": sub})
        assert r.status_code == 400, f"第三层目录被放进去了：{r.status_code}"
        assert "两层" in r.json()["error"]
        # 真的没落库 —— 只看状态码的话，一条 400 之后仍然可能有脏行
        assert not [d for d in await _all(client, headers) if d["name"] == "hc-第三层"]

    @pytest.mark.asyncio
    async def test_子目录里可以放请求(self, client, headers):
        root = await _mk(client, headers, "hc-root3")
        sub = await _mk(client, headers, "hc-sub3", parent=root)
        req = await _mk(client, headers, "hc-req3", type_="request", parent=sub)

        rows = {d["id"]: d for d in await _all(client, headers)}
        assert rows[req]["parentId"] == sub and rows[req]["type"] == "request"

    @pytest.mark.asyncio
    async def test_移动目录进子目录被拒(self, client, headers):
        root = await _mk(client, headers, "hc-root4")
        sub = await _mk(client, headers, "hc-sub4", parent=root)
        other = await _mk(client, headers, "hc-other4")

        r = await client.put(f"{BASE}/{other}", headers=headers, json={"parent_id": sub})
        assert r.status_code == 400
        assert "两层" in r.json()["error"]
        rows = {d["id"]: d for d in await _all(client, headers)}
        assert rows[other]["parentId"] is None, "被拒了，父级却已经改了"

    @pytest.mark.asyncio
    async def test_带子目录的目录不能被挪进别的目录(self, client, headers):
        """它自己合法、目标也合法，合起来就是三层 —— 单看一边判不出来。"""
        a = await _mk(client, headers, "hc-a5")
        await _mk(client, headers, "hc-a5-sub", parent=a)
        b = await _mk(client, headers, "hc-b5")

        r = await client.put(f"{BASE}/{a}", headers=headers, json={"parent_id": b})
        assert r.status_code == 400
        assert "子目录" in r.json()["error"]

    @pytest.mark.asyncio
    async def test_不能移动到自己里面(self, client, headers):
        f = await _mk(client, headers, "hc-self6")
        r = await client.put(f"{BASE}/{f}", headers=headers, json={"parent_id": f})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_请求不能当父级(self, client, headers):
        req = await _mk(client, headers, "hc-req7", type_="request")
        r = await client.post(BASE, headers=headers,
                              json={"name": "hc-挂在请求下", "parent_id": req})
        assert r.status_code == 400
        assert "目录" in r.json()["error"]

    @pytest.mark.asyncio
    async def test_拖拽接口也拦得住(self, client, headers):
        """拖拽走 batch-sort，不走 PUT —— 只在 PUT 上加判据等于没加。"""
        root = await _mk(client, headers, "hc-root8")
        sub = await _mk(client, headers, "hc-sub8", parent=root)
        other = await _mk(client, headers, "hc-other8")

        r = await client.post(f"{BASE}/batch-sort", headers=headers,
                              json={"items": [{"id": other, "sort_order": 0, "parent_id": sub}]})
        assert r.status_code == 400
        rows = {d["id"]: d for d in await _all(client, headers)}
        assert rows[other]["parentId"] is None

    @pytest.mark.asyncio
    async def test_删一级目录把两层都删干净不留孤儿(self, client, headers):
        root = await _mk(client, headers, "hc-root9")
        sub = await _mk(client, headers, "hc-sub9", parent=root)
        direct = await _mk(client, headers, "hc-直接请求", type_="request", parent=root)
        deep = await _mk(client, headers, "hc-孙请求", type_="request", parent=sub)

        r = await client.delete(f"{BASE}/{root}", headers=headers)
        assert r.status_code == 200

        rows = await _all(client, headers)
        ids = {d["id"] for d in rows}
        assert not ids & {root, sub, direct, deep}, "两层没删干净"
        assert not [d for d in rows if d["parentId"] and d["parentId"] not in ids], \
            "留下了 parentId 指向已删行的孤儿 —— 它不会出现在树上，但仍然算进请求计数"

    @pytest.mark.asyncio
    async def test_删子目录不动父目录(self, client, headers):
        root = await _mk(client, headers, "hc-root10")
        sub = await _mk(client, headers, "hc-sub10", parent=root)
        keep = await _mk(client, headers, "hc-留着", type_="request", parent=root)
        await _mk(client, headers, "hc-跟着走", type_="request", parent=sub)

        await client.delete(f"{BASE}/{sub}", headers=headers)
        ids = {d["id"] for d in await _all(client, headers)}
        assert root in ids and keep in ids
        assert sub not in ids
