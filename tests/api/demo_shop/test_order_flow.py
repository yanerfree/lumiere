"""订单服务 Demo：打真接口，盯的是「它真的会拒绝」而不是「它能返回 200」。

这套被测系统存在的意义就是**有脾气** —— 库存不够不给下、发过货不给取消。
所以这里每条正向流程后面都跟一条反向：光验「下单成功」，哪天规则被绕过去了
（比如库存判断写反），照样全绿，那这个练手系统就没价值了。

⚠ 断言 `code` 不断言中文 message：文案随时会改，code 是契约。
"""
from tests.conftest import create_test_user, make_auth_headers

from app.services.demo_shop_manager import DEFAULT_PORT


async def _admin(db_session, username):
    u = await create_test_user(db_session, username=username, role="admin")
    headers, _ = make_auth_headers(u)
    return headers


def _payload(r):
    d = r.json()
    return d["data"] if isinstance(d, dict) and "data" in d else d


async def _seed(client, headers):
    r = await client.post("/api/demo-shop/seed", headers=headers)
    assert r.status_code == 200, r.text
    return r


async def _stock(client, headers, sku):
    r = await client.get("/api/demo-shop/products?pageSize=100", headers=headers)
    assert r.status_code == 200, r.text
    for p in r.json()["data"]:
        if p["sku"] == sku:
            return p["stock"]
    raise AssertionError(f"样例商品里没有 {sku}")


async def _order(client, headers, sku="SKU-001", qty=2, name="张三"):
    return await client.post("/api/demo-shop/orders", headers=headers, json={
        "customerName": name, "customerPhone": "13800000000",
        "items": [{"sku": sku, "quantity": qty}],
    })


# ───── 下单 ─────

async def test_下单会真的扣库存而且金额按单价乘数量算(client, db_session):
    headers = await _admin(db_session, "shop_a")
    await _seed(client, headers)
    before = await _stock(client, headers, "SKU-001")

    r = await _order(client, headers, "SKU-001", 2)
    assert r.status_code == 201, r.text
    o = _payload(r)

    assert o["status"] == "pending"
    assert o["totalAmount"] == "798.00"          # 399.00 × 2
    assert o["orderNo"].startswith("SO")
    assert len(o["items"]) == 1
    assert o["items"][0]["subtotal"] == "798.00"
    assert await _stock(client, headers, "SKU-001") == before - 2


async def test_库存不够就不给下单而且一件都不扣(client, db_session):
    """SKU-008 是特意留的零库存商品 —— 不用先把别的商品买空才能测到这条。"""
    headers = await _admin(db_session, "shop_b")
    await _seed(client, headers)
    assert await _stock(client, headers, "SKU-008") == 0

    r = await _order(client, headers, "SKU-008", 1)
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "OUT_OF_STOCK"
    assert await _stock(client, headers, "SKU-008") == 0


async def test_一张下不成的订单不许在库存上留半截痕迹(client, db_session):
    """两行：第一行买得到、第二行缺货。整单必须失败，而且第一行的库存要原封不动。

    这条是「先扣了再发现不行」那类 bug 的照妖镜 —— 只买一件缺货商品的用例
    抓不到它，因为那时候压根没扣过任何东西。
    """
    headers = await _admin(db_session, "shop_r")
    await _seed(client, headers)
    before = await _stock(client, headers, "SKU-005")

    r = await client.post("/api/demo-shop/orders", headers=headers, json={
        "customerName": "孙七",
        "items": [{"sku": "SKU-005", "quantity": 1}, {"sku": "SKU-008", "quantity": 1}],
    })
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "OUT_OF_STOCK"
    assert await _stock(client, headers, "SKU-005") == before
    assert (await client.get("/api/demo-shop/orders", headers=headers)).json()["total"] == 0


async def test_买到一半撞上已下架同样一件都不扣(client, db_session):
    headers = await _admin(db_session, "shop_s")
    await _seed(client, headers)
    before = await _stock(client, headers, "SKU-006")

    r = await client.post("/api/demo-shop/orders", headers=headers, json={
        "customerName": "周八",
        "items": [{"sku": "SKU-006", "quantity": 2}, {"sku": "SKU-009", "quantity": 1}],
    })
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "PRODUCT_INACTIVE"
    assert await _stock(client, headers, "SKU-006") == before


async def test_已下架的商品不给下单(client, db_session):
    headers = await _admin(db_session, "shop_c")
    await _seed(client, headers)
    r = await _order(client, headers, "SKU-009", 1)
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "PRODUCT_INACTIVE"


async def test_同一个商品写两行直接拒(client, db_session):
    """不合并、不取最后一行 —— 静默合并会让「买 1 件」和「买 2 件」跑出同一个结果。"""
    headers = await _admin(db_session, "shop_d")
    await _seed(client, headers)
    r = await client.post("/api/demo-shop/orders", headers=headers, json={
        "customerName": "李四",
        "items": [{"sku": "SKU-001", "quantity": 1}, {"sku": "SKU-001", "quantity": 1}],
    })
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "DUPLICATE_ITEM"


async def test_商品不存在报404不是500(client, db_session):
    headers = await _admin(db_session, "shop_e")
    await _seed(client, headers)
    r = await _order(client, headers, "SKU-NOPE", 1)
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "PRODUCT_NOT_FOUND"


# ───── 状态流转 ─────

async def test_付款发货完成一路走得通(client, db_session):
    headers = await _admin(db_session, "shop_f")
    await _seed(client, headers)
    no = _payload(await _order(client, headers))["orderNo"]

    for action, expect in [("pay", "paid"), ("ship", "shipped"), ("complete", "completed")]:
        r = await client.post(f"/api/demo-shop/orders/{no}/{action}", headers=headers)
        assert r.status_code == 200, r.text
        assert _payload(r)["status"] == expect

    o = _payload(await client.get(f"/api/demo-shop/orders/{no}", headers=headers))
    # 时间戳真的写进去了，不是只改了状态字段
    assert o["paidAt"] and o["shippedAt"]


async def test_跳过付款直接发货不给过(client, db_session):
    headers = await _admin(db_session, "shop_g")
    await _seed(client, headers)
    no = _payload(await _order(client, headers))["orderNo"]

    r = await client.post(f"/api/demo-shop/orders/{no}/ship", headers=headers)
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "INVALID_STATUS"


async def test_发过货的订单取消不了(client, db_session):
    headers = await _admin(db_session, "shop_h")
    await _seed(client, headers)
    no = _payload(await _order(client, headers))["orderNo"]
    await client.post(f"/api/demo-shop/orders/{no}/pay", headers=headers)
    await client.post(f"/api/demo-shop/orders/{no}/ship", headers=headers)

    r = await client.post(f"/api/demo-shop/orders/{no}/cancel", headers=headers)
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "INVALID_STATUS"


async def test_没有的动作报404(client, db_session):
    headers = await _admin(db_session, "shop_i")
    await _seed(client, headers)
    no = _payload(await _order(client, headers))["orderNo"]
    r = await client.post(f"/api/demo-shop/orders/{no}/refund", headers=headers)
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "UNKNOWN_ACTION"


# ───── 库存还回去 ─────

async def test_取消订单把库存还回去(client, db_session):
    headers = await _admin(db_session, "shop_j")
    await _seed(client, headers)
    before = await _stock(client, headers, "SKU-002")
    no = _payload(await _order(client, headers, "SKU-002", 3))["orderNo"]
    assert await _stock(client, headers, "SKU-002") == before - 3

    r = await client.post(f"/api/demo-shop/orders/{no}/cancel", headers=headers)
    assert r.status_code == 200, r.text
    assert await _stock(client, headers, "SKU-002") == before


async def test_删掉待支付订单也把库存还回去(client, db_session):
    headers = await _admin(db_session, "shop_k")
    await _seed(client, headers)
    before = await _stock(client, headers, "SKU-003")
    no = _payload(await _order(client, headers, "SKU-003", 1))["orderNo"]

    r = await client.delete(f"/api/demo-shop/orders/{no}", headers=headers)
    assert r.status_code == 200, r.text
    assert await _stock(client, headers, "SKU-003") == before

    r = await client.get(f"/api/demo-shop/orders/{no}", headers=headers)
    assert r.status_code == 404, r.text


async def test_删掉已完成订单不还库存(client, db_session):
    """货已经发出去了，删条记录不该让库存凭空长回来 —— 这条正是上一条的反面。"""
    headers = await _admin(db_session, "shop_l")
    await _seed(client, headers)
    before = await _stock(client, headers, "SKU-004")
    no = _payload(await _order(client, headers, "SKU-004", 2))["orderNo"]
    for a in ("pay", "ship", "complete"):
        await client.post(f"/api/demo-shop/orders/{no}/{a}", headers=headers)

    await client.delete(f"/api/demo-shop/orders/{no}", headers=headers)
    assert await _stock(client, headers, "SKU-004") == before - 2


# ───── 查询 / 统计 / 重置 ─────

async def test_按状态和关键字筛得出来(client, db_session):
    headers = await _admin(db_session, "shop_m")
    await _seed(client, headers)
    a = _payload(await _order(client, headers, "SKU-001", 1, "王五"))["orderNo"]
    await _order(client, headers, "SKU-002", 1, "赵六")
    await client.post(f"/api/demo-shop/orders/{a}/pay", headers=headers)

    r = await client.get("/api/demo-shop/orders?status=paid", headers=headers)
    assert [o["orderNo"] for o in r.json()["data"]] == [a]

    r = await client.get("/api/demo-shop/orders?keyword=赵六", headers=headers)
    assert r.json()["total"] == 1

    r = await client.get("/api/demo-shop/orders?status=乱写", headers=headers)
    assert r.status_code == 422
    assert r.json()["code"] == "INVALID_PARAM"


async def test_统计里的已收金额只算付过钱的(client, db_session):
    headers = await _admin(db_session, "shop_n")
    await _seed(client, headers)
    paid = _payload(await _order(client, headers, "SKU-001", 1))["orderNo"]   # 399.00
    await _order(client, headers, "SKU-002", 1)                               # 129.00，不付
    await client.post(f"/api/demo-shop/orders/{paid}/pay", headers=headers)

    s = _payload(await client.get("/api/demo-shop/status", headers=headers))["stats"]
    assert s["orders"] == 2
    assert s["ordersByStatus"]["pending"] == 1
    assert s["ordersByStatus"]["paid"] == 1
    assert s["revenue"] == "399.00"


async def test_重置清空订单但商品编号不变(client, db_session):
    """用例里会写死 SKU-001。重置要是把商品也重新发一遍 id，写好的用例会集体变红。"""
    headers = await _admin(db_session, "shop_o")
    await _seed(client, headers)
    await _order(client, headers, "SKU-001", 5)
    assert await _stock(client, headers, "SKU-001") == 45

    r = await client.post("/api/demo-shop/reset", headers=headers)
    assert r.status_code == 200, r.text
    assert _payload(r)["deletedOrders"] == 1

    assert (await client.get("/api/demo-shop/orders", headers=headers)).json()["total"] == 0
    assert await _stock(client, headers, "SKU-001") == 50       # 回到出厂库存


async def test_重复播种不会把改过的商品覆盖回去(client, db_session):
    headers = await _admin(db_session, "shop_p")
    await _seed(client, headers)
    await client.patch("/api/demo-shop/products/SKU-001", headers=headers, json={"stock": 7})

    r = await _seed(client, headers)
    assert _payload(r)["added"] == 0
    assert await _stock(client, headers, "SKU-001") == 7


# ───── 页面能不能开 / 权限 ─────

async def test_服务没起来时状态接口照样能开页面(client, db_session):
    """页面靠 /status 渲染。它要是随服务状态一起挂，人就只能看到白屏，
    连「点这里启动」的按钮都看不见。"""
    headers = await _admin(db_session, "shop_q")
    r = await client.get("/api/demo-shop/status", headers=headers)
    assert r.status_code == 200, r.text
    d = _payload(r)
    assert d["port"] == DEFAULT_PORT
    assert {a["username"] for a in d["accounts"]} == {"admin", "clerk"}


async def test_游客打不了写接口(client, db_session):
    """游客是硬封顶只读（app/deps/auth.py 的非 GET 闸门）。下单属于写。"""
    u = await create_test_user(db_session, username="shop_guest", role="guest")
    headers, _ = make_auth_headers(u)
    r = await _order(client, headers)
    assert r.status_code == 403, r.text
