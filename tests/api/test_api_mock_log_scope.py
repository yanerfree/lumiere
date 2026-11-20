"""API Mock 请求日志按路由过滤 / 按路由清空（页面「本条路由 / 全部」切换的后端依据）"""
import uuid

from app.models.api_mock import ApiMockLog
from tests.conftest import create_test_user, make_auth_headers


async def test_日志按路由过滤且只清这一条(client, db_session):
    u = await create_test_user(db_session, username="apimock_log_admin", role="admin")
    headers, _ = make_auth_headers(u)
    a, b = uuid.uuid4(), uuid.uuid4()
    for rid, path in [(a, "/scope/a"), (a, "/scope/a"), (b, "/scope/b")]:
        db_session.add(ApiMockLog(route_id=rid, method="GET", path=path, status_code=200))
    await db_session.commit()

    r = await client.get(f"/api/api-mock/logs?route_id={a}", headers=headers)
    assert r.status_code == 200
    assert r.json()["total"] == 2 and {x["path"] for x in r.json()["data"]} == {"/scope/a"}

    # 只清 a：b 的还在
    rd = await client.delete(f"/api/api-mock/logs?route_id={a}", headers=headers)
    assert rd.status_code == 200 and rd.json()["deleted"] == 2
    left = await client.get(f"/api/api-mock/logs?route_id={b}", headers=headers)
    assert left.json()["total"] == 1
