"""
test_user_notes — 「我的记录」按用户隔离

需求（2026-10-16 用户提）：每人只能看见自己的记录，超管能看见所有人的。
补充口径：超管看得见但**改不了别人的**（只读）；游客账号级只读，建不了。
"""
import pytest

from tests.conftest import create_test_user, make_auth_headers


async def _new_note(client, headers, title="记一笔", content="正文"):
    resp = await client.post("/api/notes", json={"title": title, "content": content}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


class TestUserNotes:

    @pytest.mark.asyncio
    async def test_create_then_list_and_read_back(self, client, db_session):
        u = await create_test_user(db_session, username="note_alice")
        h, _ = make_auth_headers(u)

        created = await _new_note(client, h, title="  周会要点  ", content="第一行\n第二行")
        assert created["title"] == "周会要点"  # 首尾空格去掉

        listed = (await client.get("/api/notes", headers=h)).json()["data"]
        assert [n["id"] for n in listed] == [created["id"]]

        got = (await client.get(f"/api/notes/{created['id']}", headers=h)).json()["data"]
        assert got["content"] == "第一行\n第二行"
        assert got["mine"] is True and got["username"] == "note_alice"

    @pytest.mark.asyncio
    async def test_users_cannot_see_each_other(self, client, db_session):
        a = await create_test_user(db_session, username="note_a")
        b = await create_test_user(db_session, username="note_b")
        ha, _ = make_auth_headers(a)
        hb, _ = make_auth_headers(b)
        note_a = await _new_note(client, ha, title="A 的")
        await _new_note(client, hb, title="B 的")

        titles_b = [n["title"] for n in (await client.get("/api/notes", headers=hb)).json()["data"]]
        assert titles_b == ["B 的"]

        # 别人的 id 直接打：读/改/删都当不存在
        assert (await client.get(f"/api/notes/{note_a['id']}", headers=hb)).status_code == 404
        r = await client.put(f"/api/notes/{note_a['id']}", json={"title": "改掉"}, headers=hb)
        assert r.status_code == 404
        assert (await client.delete(f"/api/notes/{note_a['id']}", headers=hb)).status_code == 404
        # A 的记录原样还在
        got = (await client.get(f"/api/notes/{note_a['id']}", headers=ha)).json()["data"]
        assert got["title"] == "A 的"

    @pytest.mark.asyncio
    async def test_admin_sees_all_but_cannot_edit_others(self, client, db_session):
        admin = await create_test_user(db_session, username="note_boss", role="admin")
        u = await create_test_user(db_session, username="note_staff")
        hadm, _ = make_auth_headers(admin)
        hu, _ = make_auth_headers(u)
        staff_note = await _new_note(client, hu, title="员工的")
        await _new_note(client, hadm, title="老板的")

        all_titles = {n["title"]: n for n in (await client.get("/api/notes", headers=hadm)).json()["data"]}
        assert set(all_titles) >= {"员工的", "老板的"}
        assert all_titles["员工的"]["username"] == "note_staff"
        assert all_titles["员工的"]["mine"] is False

        mine = (await client.get("/api/notes?mine_only=true", headers=hadm)).json()["data"]
        assert [n["title"] for n in mine] == ["老板的"]

        assert (await client.get(f"/api/notes/{staff_note['id']}", headers=hadm)).status_code == 200
        r = await client.put(f"/api/notes/{staff_note['id']}", json={"title": "改掉"}, headers=hadm)
        assert r.status_code == 403
        assert (await client.delete(f"/api/notes/{staff_note['id']}", headers=hadm)).status_code == 403

    @pytest.mark.asyncio
    async def test_owner_can_update_and_delete(self, client, db_session):
        u = await create_test_user(db_session, username="note_owner")
        h, _ = make_auth_headers(u)
        n = await _new_note(client, h)

        r = await client.put(f"/api/notes/{n['id']}", json={"title": "新标题", "content": "新正文"}, headers=h)
        assert r.status_code == 200, r.text
        got = (await client.get(f"/api/notes/{n['id']}", headers=h)).json()["data"]
        assert (got["title"], got["content"]) == ("新标题", "新正文")

        assert (await client.delete(f"/api/notes/{n['id']}", headers=h)).status_code == 200
        assert (await client.get("/api/notes", headers=h)).json()["data"] == []

    @pytest.mark.asyncio
    async def test_blank_title_rejected(self, client, db_session):
        u = await create_test_user(db_session, username="note_blank")
        h, _ = make_auth_headers(u)
        r = await client.post("/api/notes", json={"title": "   ", "content": "x"}, headers=h)
        assert r.status_code == 422
        assert (await client.get("/api/notes", headers=h)).json()["data"] == []

    @pytest.mark.asyncio
    async def test_guest_cannot_create(self, client, db_session):
        g = await create_test_user(db_session, username="note_guest", role="guest")
        h, _ = make_auth_headers(g)
        r = await client.post("/api/notes", json={"title": "游客想写"}, headers=h)
        assert r.status_code == 403
        assert (await client.get("/api/notes", headers=h)).status_code == 200

    @pytest.mark.asyncio
    async def test_requires_login(self, client):
        assert (await client.get("/api/notes")).status_code == 401
