"""订单服务（样例被测系统）的平台侧接口 —— 「测试工具 → 订单服务」那一页用的。

⚠ 这里的路由和 29000 端口上那个服务**不是一套东西**：
- 29000 是**被测系统**自己的接口，有自己的账号，用例填的 BASE_URL 指向它；
- 这里是**平台页面**的接口，走平台登录态和 `system.tools.use` 权限，
  只是为了让人在页面上能看、能点，不用开 Postman。

两边的增删改都落到 `demo_shop_service` 同一套函数上 —— 页面上点出来的和接口打出来的
必须是同一份数据、同一套规则，否则会出现「接口用例全绿、页面上看是另一回事」。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.db import get_db
from app.services import demo_shop_catalog as catalog
from app.services import demo_shop_openapi as openapi_builder
from app.services import demo_shop_service as svc
from app.services.demo_shop_manager import (
    ACCOUNTS, UNLOGGED_PATHS, demo_shop_manager as mgr,
)
from app.services.demo_shop_service import DemoShopError

router = APIRouter(prefix="/api/demo-shop", tags=["demo-shop"])


def _err(e: DemoShopError) -> JSONResponse:
    return JSONResponse({"error": e.message, "code": e.code}, status_code=e.status)


# ── Schemas ──

class OrderItemIn(BaseModel):
    sku: str
    quantity: int = Field(1, ge=1, le=svc.MAX_QUANTITY)


class OrderCreate(BaseModel):
    customerName: str = Field(..., min_length=1, max_length=60)
    customerPhone: str = ""
    remark: str | None = None
    items: list[OrderItemIn]


class OrderUpdate(BaseModel):
    customerName: str | None = None
    customerPhone: str | None = None
    remark: str | None = None


class ProductUpdate(BaseModel):
    name: str | None = None
    price: str | None = None
    stock: int | None = None
    active: bool | None = None


class ServiceConfig(BaseModel):
    port: int | None = Field(None, ge=1024, le=65535)


# ── 接口清单 ──

@router.get("/endpoints")
async def list_endpoints():
    """页面左边那一列：这个被测系统真实开放的每一条接口。

    出处是 `demo_shop_catalog.ENDPOINTS`，有封样测试拿它和真实注册的路由做双向差集 ——
    清单和实际对不上是**不报错**的那类错（页面上多一条调不通的、或少一条没人知道的）。
    """
    # 状态机是**算出来的**，不是在前端另抄一份：抄了就会悄悄长歪
    # （页面少一条 → 那个动作看着像没做；多一条 → 点下去 409，看着像后端坏了）。
    transitions = [
        {"action": a, "actionLabel": svc.ACTION_LABELS.get(a, a),
         "from": s, "fromLabel": svc.STATUS_LABELS.get(s, s),
         "to": t, "toLabel": svc.STATUS_LABELS.get(t, t)}
        for a, table in svc.ALLOWED_TRANSITIONS.items()
        for s, t in table.items()
    ]
    return {
        "data": catalog.ENDPOINTS,
        "groupOrder": catalog.GROUP_ORDER,
        "accounts": [
            {"username": u, "password": a["password"], "role": a["role"],
             "displayName": a["displayName"]}
            for u, a in ACCOUNTS.items()
        ],
        "rules": {
            "statusLabels": svc.STATUS_LABELS,
            "transitions": transitions,
            "maxQuantity": svc.MAX_QUANTITY,
            # 这几条故意不记日志。不告诉页面的话，「请求日志」空着会被当成
            # 「还没调过」，然后有人去查一个根本不存在的 bug。
            "unloggedPaths": list(UNLOGGED_PATHS),
        },
    }


@router.get("/openapi.json")
async def export_openapi(keys: str = Query("", description="逗号分隔，只导这几条；留空导全部")):
    """「导出」按钮下载的那份标准 OpenAPI，给别的系统导入用。

    ⚠ 不要改成转发被测系统自己的 `/openapi.json`。那份是空壳 ——
    路由上请求体写的是 `payload: dict`，自动生成出来没有必填字段、没有错误码、
    没有鉴权声明，导进 Apifox/Postman 之后照着它发的请求几乎必然 422，
    而那边只会显示"接口返回 422"，看着像接口坏了。
    """
    picked = [k.strip() for k in keys.split(",") if k.strip()] or None
    doc = openapi_builder.build_openapi(f"http://localhost:{mgr.port}", picked)
    return JSONResponse(doc, headers={
        # 让浏览器直接当文件存下来，而不是在标签页里渲染一坨 JSON
        "Content-Disposition": 'attachment; filename="demo-shop-openapi.json"',
    })


# ── 服务控制 ──

@router.get("/status")
async def get_status(session: AsyncSession = Depends(get_db)):
    data = {
        "running": mgr.running,
        "port": mgr.port,
        "startedAt": mgr.started_at,
        "baseUrl": f"http://localhost:{mgr.port}",
        "docsUrl": f"http://localhost:{mgr.port}/docs",
        "totalLogs": len(mgr.logs),
        "accounts": [
            {"username": u, "password": a["password"], "role": a["role"],
             "displayName": a["displayName"]}
            for u, a in ACCOUNTS.items()
        ],
    }
    try:
        data["stats"] = await svc.stats(session)
    except Exception:
        # 表还没建（没跑迁移）时不要把整页打挂：页面要能显示"服务没起来"
        data["stats"] = None
    return {"data": data}


@router.post("/start")
async def start_service():
    try:
        await mgr.start()
        return {"ok": True, "port": mgr.port}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/stop")
async def stop_service():
    await mgr.stop()
    return {"ok": True}


@router.put("/config")
async def update_config(body: ServiceConfig):
    was_running = mgr.running
    if was_running:
        await mgr.stop()
    if body.port is not None:
        mgr.port = body.port
    if was_running:
        await mgr.start()
    return {"data": {"port": mgr.port, "running": mgr.running}}


@router.get("/logs")
async def get_logs(limit: int = Query(100, ge=1, le=500)):
    return {"data": list(mgr.logs)[:limit], "total": len(mgr.logs)}


@router.delete("/logs")
async def clear_logs():
    mgr.logs.clear()
    return {"ok": True}


# ── 订单 ──

@router.get("/orders")
async def list_orders(status: str = "", keyword: str = "", page: int = 1,
                      pageSize: int = Query(20, ge=1, le=100),
                      session: AsyncSession = Depends(get_db)):
    try:
        return await svc.list_orders(session, status, keyword, page, pageSize)
    except DemoShopError as e:
        return _err(e)


@router.get("/orders/{order_no}")
async def get_order(order_no: str, session: AsyncSession = Depends(get_db)):
    try:
        return {"data": svc.order_json(await svc.get_order(session, order_no))}
    except DemoShopError as e:
        return _err(e)


@router.post("/orders", status_code=201)
async def create_order(body: OrderCreate, session: AsyncSession = Depends(get_db)):
    try:
        return {"data": await svc.create_order(session, body.model_dump(), "平台页面")}
    except DemoShopError as e:
        return _err(e)


@router.put("/orders/{order_no}")
async def update_order(order_no: str, body: OrderUpdate,
                       session: AsyncSession = Depends(get_db)):
    try:
        return {"data": await svc.update_order(session, order_no,
                                               body.model_dump(exclude_unset=True))}
    except DemoShopError as e:
        return _err(e)


@router.post("/orders/{order_no}/{action}")
async def order_action(order_no: str, action: str, session: AsyncSession = Depends(get_db)):
    try:
        return {"data": await svc.transition(session, order_no, action)}
    except DemoShopError as e:
        return _err(e)


@router.delete("/orders/{order_no}")
async def delete_order(order_no: str, session: AsyncSession = Depends(get_db)):
    try:
        return await svc.delete_order(session, order_no)
    except DemoShopError as e:
        return _err(e)


# ── 商品 ──

@router.get("/products")
async def list_products(keyword: str = "", onlyActive: bool = False, page: int = 1,
                        pageSize: int = Query(50, ge=1, le=100),
                        session: AsyncSession = Depends(get_db)):
    try:
        return await svc.list_products(session, keyword, onlyActive, page, pageSize)
    except DemoShopError as e:
        return _err(e)


@router.patch("/products/{sku}")
async def update_product(sku: str, body: ProductUpdate,
                         session: AsyncSession = Depends(get_db)):
    try:
        return {"data": await svc.update_product(session, sku,
                                                 body.model_dump(exclude_unset=True))}
    except DemoShopError as e:
        return _err(e)


# ── 样例数据 ──

@router.post("/reset")
async def reset(session: AsyncSession = Depends(get_db)):
    """清空订单 + 商品回出厂值。页面上那个按钮，带二次确认。"""
    return {"data": await svc.reset_demo_data(session)}


@router.post("/seed")
async def seed(session: AsyncSession = Depends(get_db)):
    """只补缺的商品，已有的不动（起服务时也会自动跑一次）。"""
    added = await svc.seed_products(session)
    return {"data": {"added": added}}
