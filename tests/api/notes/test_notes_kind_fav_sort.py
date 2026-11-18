"""
test_notes_kind_fav_sort — 「我的记录」类型（待办/笔记）、收藏置顶、拖拽排序（2026-11-18）
"""
import pytest

from tests.conftest import create_test_user, make_auth_headers


async def _new_note(client, headers, title="记一笔", **extra):
    resp = await client.post("/api/notes", json={"title": title, **extra}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


class TestNotesKindFavoriteSort:

    @pytest.mark.asyncio
    async def test_kind_default_note_and_todo_status(self, client, db_session):
        u = await create_test_user(db_session, username="note_kind")
        h, _ = make_auth_headers(u)
        n = await _new_note(client, h)
        assert n["kind"] == "note" and n["isFavorite"] is False
        t = await _new_note(client, h, title="买菜", kind="todo")
        assert t["kind"] == "todo" and t["status"] == "pending"
        r = await client.patch(f"/api/notes/{t['id']}", json={"status": "done"}, headers=h)
        assert r.json()["data"]["status"] == "done"
        # 笔记没有完成状态
        assert (await client.patch(f"/api/notes/{n['id']}", json={"status": "done"}, headers=h)).status_code == 403
        # 按类型筛
        todos = (await client.get("/api/notes?kind=todo", headers=h)).json()["data"]
        assert [x["id"] for x in todos] == [t["id"]]
        # 改成笔记后状态复位
        r = await client.put(f"/api/notes/{t['id']}", json={"title": "买菜", "kind": "note", "status": "done"}, headers=h)
        assert r.json()["data"]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_default_order_created_desc_favorite_first(self, client, db_session):
        u = await create_test_user(db_session, username="note_order")
        h, _ = make_auth_headers(u)
        a = await _new_note(client, h, title="A")
        await _new_note(client, h, title="B")
        await _new_note(client, h, title="C")

        async def titles():
            return [x["title"] for x in (await client.get("/api/notes", headers=h)).json()["data"]]

        assert await titles() == ["C", "B", "A"]
        await client.patch(f"/api/notes/{a['id']}", json={"isFavorite": True}, headers=h)
        assert await titles() == ["A", "C", "B"]
        await client.patch(f"/api/notes/{a['id']}", json={"isFavorite": False}, headers=h)
        assert await titles() == ["C", "B", "A"]

    @pytest.mark.asyncio
    async def test_reorder_only_touches_own(self, client, db_session):
        u = await create_test_user(db_session, username="note_re1")
        o = await create_test_user(db_session, username="note_re2")
        h, _ = make_auth_headers(u)
        ho, _ = make_auth_headers(o)
        a = await _new_note(client, h, title="A")
        b = await _new_note(client, h, title="B")
        c = await _new_note(client, h, title="C")
        other = await _new_note(client, ho, title="别人的")
        r = await client.post("/api/notes/reorder", json={"ids": [a["id"], c["id"], b["id"], other["id"]]}, headers=h)
        assert r.status_code == 200
        got = [x["title"] for x in (await client.get("/api/notes", headers=h)).json()["data"]]
        assert got == ["A", "C", "B"]
        assert (await client.get(f"/api/notes/{other['id']}", headers=ho)).json()["data"]["title"] == "别人的"
        # 新建的排在拖过的前面（顺位 0 < 1）
        await _new_note(client, h, title="新")
        got = [x["title"] for x in (await client.get("/api/notes", headers=h)).json()["data"]]
        assert got[0] == "新"

    @pytest.mark.asyncio
    async def test_cannot_favorite_others_and_guest_blocked(self, client, db_session):
        u = await create_test_user(db_session, username="note_fav1")
        admin = await create_test_user(db_session, username="note_fav_boss", role="admin")
        g = await create_test_user(db_session, username="note_fav_guest", role="guest")
        h, _ = make_auth_headers(u)
        hadm, _ = make_auth_headers(admin)
        hg, _ = make_auth_headers(g)
        n = await _new_note(client, h)
        assert (await client.patch(f"/api/notes/{n['id']}", json={"isFavorite": True}, headers=hadm)).status_code == 403
        assert (await client.patch(f"/api/notes/{n['id']}", json={"isFavorite": True}, headers=hg)).status_code == 403
        assert (await client.post("/api/notes/reorder", json={"ids": []}, headers=hg)).status_code == 403


class TestNotesCompletedAt:

    @pytest.mark.asyncio
    async def test_completed_at_set_kept_and_cleared(self, client, db_session):
        u = await create_test_user(db_session, username="note_done_at")
        h, _ = make_auth_headers(u)
        t = await _new_note(client, h, title="写周报", kind="todo")
        assert t["completedAt"] is None
        r = (await client.patch(f"/api/notes/{t['id']}", json={"status": "done"}, headers=h)).json()["data"]
        first = r["completedAt"]
        assert first
        # 编辑内容但仍是已完成：完成时间不刷新
        r = (await client.put(f"/api/notes/{t['id']}", json={"title": "写周报2", "kind": "todo", "status": "done"}, headers=h)).json()["data"]
        assert r["completedAt"] == first
        # 改回进行中 → 清空
        r = (await client.patch(f"/api/notes/{t['id']}", json={"status": "pending"}, headers=h)).json()["data"]
        assert r["completedAt"] is None
        # 新建时直接选已完成 → 有时间
        d = await _new_note(client, h, title="已做完", kind="todo", status="done")
        assert d["completedAt"]
        # 改成笔记 → 清空
        r = (await client.put(f"/api/notes/{d['id']}", json={"title": "已做完", "kind": "note"}, headers=h)).json()["data"]
        assert r["completedAt"] is None and r["status"] == "pending"
