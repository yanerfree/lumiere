"""订单服务「导出」按钮：打真接口，验导出来的那份文件真能给别的系统用。

为什么要在这儿再验一遍（backend/tests 已经有静态封样）：
那边验的是**生成函数**，这边验的是**那条路由** —— 权限挂没挂、
勾选参数传没传进去、浏览器会不会当成文件存下来。
路由挂错了静态封样一个字都不会红，而页面上按「导出」会 404 或 401。
"""
import json

from tests.conftest import create_test_user, make_auth_headers

from app.services import demo_shop_catalog as catalog


async def _headers(db_session, username):
    u = await create_test_user(db_session, username=username, role="admin")
    h, _ = make_auth_headers(u)
    return h


async def test_导出全部接口(client, db_session):
    h = await _headers(db_session, "exp_all")
    r = await client.get("/api/demo-shop/openapi.json", headers=h)
    assert r.status_code == 200, r.text
    doc = r.json()
    ops = [op for item in doc["paths"].values() for op in item.values()]
    assert len(ops) == len(catalog.ENDPOINTS)
    assert doc["openapi"].startswith("3.0")


async def test_浏览器会把它当文件存下来(client, db_session):
    """少了这个头，点「导出」会在标签页里渲染一坨 JSON，而人以为按钮坏了。"""
    h = await _headers(db_session, "exp_dl")
    r = await client.get("/api/demo-shop/openapi.json", headers=h)
    assert "attachment" in r.headers.get("content-disposition", "")
    assert ".json" in r.headers["content-disposition"]


async def test_只导勾选的那几条(client, db_session):
    h = await _headers(db_session, "exp_some")
    r = await client.get("/api/demo-shop/openapi.json?keys=login,order_create", headers=h)
    ids = {op["operationId"] for item in r.json()["paths"].values() for op in item.values()}
    assert ids == {"login", "order_create"}


async def test_没登录不给导(client):
    """这页挂在「测试工具」权限下，导出也得跟着那把锁 —— 否则菜单看不见、文件照样能拉走。"""
    r = await client.get("/api/demo-shop/openapi.json")
    assert r.status_code in (401, 403)


async def test_导出的内容带必填和鉴权(client, db_session):
    """对方系统真正会读的就是这两样：哪些字段必填、要不要带 token。

    缺了它们，那边生成的调用是 422 或 401，而那边只会显示「接口报错」。
    """
    h = await _headers(db_session, "exp_req")
    doc = (await client.get("/api/demo-shop/openapi.json", headers=h)).json()

    # 下单：客户名和商品是必填，且要带 token
    post_order = doc["paths"]["/api/orders"]["post"]
    schema = post_order["requestBody"]["content"]["application/json"]["schema"]
    assert set(schema["required"]) == {"customerName", "items"}
    assert post_order["security"] == [{"bearerAuth": []}]
    # 成功是 201 不是 200
    assert "201" in post_order["responses"]

    # 登录：不用带 token
    assert "security" not in doc["paths"]["/api/login"]["post"]

    # 路径参数一律必填
    detail = doc["paths"]["/api/orders/{order_no}"]["get"]
    assert all(p["required"] for p in detail["parameters"] if p["in"] == "path")


async def test_导出的文件是合法openapi(client, db_session):
    """拿 FastAPI 自己那套 OpenAPI 模型解析一遍 —— 它就是 3.x 规范的 schema。"""
    from fastapi.openapi.models import OpenAPI
    h = await _headers(db_session, "exp_valid")
    doc = (await client.get("/api/demo-shop/openapi.json", headers=h)).json()
    OpenAPI.model_validate(doc)
    json.dumps(doc)  # 能序列化回去，别塞进不可序列化的东西
