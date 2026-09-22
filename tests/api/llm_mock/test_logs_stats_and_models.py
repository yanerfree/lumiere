"""LLM Mock：请求日志「正确/错误」两个统计数 + 内置模型清单 —— 打真接口。

盯两件在页面上肉眼看得见、但很容易静默坏掉的事：
1. 日志页顶上的「正确 / 错误」两个数（`GET /logs/stats`）。它必须声明在
   `/logs/{log_id}` **前面**，否则 "stats" 会被当成 UUID 去解析 → 422，
   页面上两个数永远是 0，且不报错。这里专门反着验一遍：stats 拿到的是统计字典，
   不是「log_id 格式不对」。
2. 页面「内置模型」抽屉（`GET /models`）。它复用 mock 服务 `/v1/models` 的同一份
   构造函数，所以**服务停着也应当返回**——否则「加了 /v1/models」这件事在页面上隐形。
"""
from tests.conftest import create_test_user, make_auth_headers

from app.services import llm_mock_service as svc


async def _admin(db_session, username):
    u = await create_test_user(db_session, username=username, role="admin")
    headers, _ = make_auth_headers(u)
    return headers


def _payload(r):
    d = r.json()
    return d["data"] if isinstance(d, dict) and "data" in d else d


# ───── /logs/stats ─────

async def test_统计数就是正确加错误而且不随筛选变(client, db_session):
    headers = await _admin(db_session, "stats_admin")
    # 3 条正确（<400）、2 条错误（>=400）
    for code in (200, 201, 204):
        await svc.create_log(db_session, {"method": "POST", "path": "/s/v1/chat/completions", "status_code": code})
    for code in (429, 500):
        await svc.create_log(db_session, {"method": "POST", "path": "/s/v1/chat/completions", "status_code": code})
    await db_session.flush()

    r = await client.get("/api/llm-mock/logs/stats", headers=headers)
    assert r.status_code == 200, r.text
    d = _payload(r)
    assert d == {"total": 5, "ok": 3, "error": 2}


async def test_stats不会被当成log_id去解析(client, db_session):
    """这条是「路由顺序」封样：/logs/stats 必须先于 /logs/{log_id} 声明。
    若顺序反了，"stats" 会被 UUID 解析拒掉（422），而不是走到统计。"""
    headers = await _admin(db_session, "stats_order_admin")
    r = await client.get("/api/llm-mock/logs/stats", headers=headers)
    assert r.status_code == 200, r.text  # 不是 422
    assert set(_payload(r).keys()) == {"total", "ok", "error"}


async def test_统计可按路由过滤(client, db_session):
    headers = await _admin(db_session, "stats_filter_admin")
    # 建一条路由，拿它的 id 挂日志
    rc = await client.post(
        "/api/llm-mock/routes",
        headers=headers,
        json={"name": "统计过滤", "method": "POST", "path": "/sf/v1/chat/completions"},
    )
    assert rc.status_code == 201, rc.text
    rid = _payload(rc)["id"]

    # 这条路由：1 正确 1 错误；另一条无归属日志：1 正确
    await svc.create_log(db_session, {"route_id": rid, "method": "POST", "path": "/sf", "status_code": 200})
    await svc.create_log(db_session, {"route_id": rid, "method": "POST", "path": "/sf", "status_code": 500})
    await svc.create_log(db_session, {"method": "POST", "path": "/other", "status_code": 200})
    await db_session.flush()

    r = await client.get(f"/api/llm-mock/logs/stats?route_id={rid}", headers=headers)
    assert r.status_code == 200, r.text
    assert _payload(r) == {"total": 2, "ok": 1, "error": 1}


async def test_没有日志时三个数都是零(client, db_session):
    headers = await _admin(db_session, "stats_empty_admin")
    r = await client.get("/api/llm-mock/logs/stats", headers=headers)
    assert r.status_code == 200, r.text
    assert _payload(r) == {"total": 0, "ok": 0, "error": 0}


# ───── /models ─────

async def test_内置模型清单是openai格式且非空(client, db_session):
    headers = await _admin(db_session, "models_admin")
    r = await client.get("/api/llm-mock/models", headers=headers)
    assert r.status_code == 200, r.text
    # /models 直接透传 OpenAI 的 /v1/models 信封：顶层就是 {object, data}，
    # 不套平台的 {data: ...} 外壳，这样任何 OpenAI 客户端都能原样吃下。
    d = r.json()
    assert d.get("object") == "list"
    data = d.get("data") or []
    assert len(data) > 0
    ids = {m["id"] for m in data}
    # 至少带上我们平台默认在用的那几个 anthropic 模型之一
    assert "claude-sonnet-5" in ids
    # 每条都是 openai 的 model 形状。注意：这条走的是**平台**透传口，响应会过平台的
    # camelCase 中间件，所以 owned_by 到手是 ownedBy；mock 服务自己的 /v1/models
    # 才是原样 snake_case。前端两种拼写都认（LlmMock.jsx modelsByOwner），这里也两种都收。
    for m in data:
        assert m["object"] == "model"
        assert ("owned_by" in m) or ("ownedBy" in m)
