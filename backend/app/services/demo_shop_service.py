"""样例订单系统的业务规则 —— 唯一出处。

**这里是"被测系统"的代码，不是平台功能的代码。** 它故意写得像一个真的小系统：
有库存、有状态机、有快照、有会报错的边界，而不是一个怎么调都返回 200 的假后端。

为什么规则要集中在这一个文件：订单服务（独立端口 29000）和平台页面
（「测试工具 → 订单服务」那一页的增删改）走的是**同一套函数**。分两套实现的话，
页面上点出来的数据和接口打出来的数据会慢慢长歪 —— 而这种歪最坑：用例在接口那边
全绿，人在页面上看到的却是另一回事，还找不到哪边错了。

状态机（改这里就是改被测系统的行为，会让写好的用例变红，改之前想清楚）：

    pending ──pay──▶ paid ──ship──▶ shipped ──complete──▶ completed
       │               │
       └──────cancel───┴──▶ cancelled        （取消会把库存还回去）

错误一律走 DemoShopError，带 HTTP 状态码 + 机读 code + 人读 message：
断言写 `code == "OUT_OF_STOCK"` 比断言中文文案稳（文案会改，code 不会）。
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.demo_shop import DemoOrder, DemoOrderItem, DemoProduct

# ── 状态 ──

STATUSES = ["pending", "paid", "shipped", "completed", "cancelled"]
STATUS_LABELS = {
    "pending": "待支付",
    "paid": "已支付",
    "shipped": "已发货",
    "completed": "已完成",
    "cancelled": "已取消",
}

# 动作 → {当前状态: 目标状态}。不在表里的组合一律 409，别放行。
ALLOWED_TRANSITIONS: dict[str, dict[str, str]] = {
    "pay": {"pending": "paid"},
    "ship": {"paid": "shipped"},
    "complete": {"shipped": "completed"},
    "cancel": {"pending": "cancelled", "paid": "cancelled"},
}
ACTION_LABELS = {"pay": "支付", "ship": "发货", "complete": "完成", "cancel": "取消"}

# 取消/删除时要把库存还回去的状态（已发货之后货已经出库，不还）
RESTOCK_STATUSES = {"pending", "paid"}

MAX_ITEM_LINES = 20
MAX_QUANTITY = 99
MAX_PAGE_SIZE = 100

# ── 样例商品：重置就回到这一份 ──
SEED_PRODUCTS = [
    {"sku": "SKU-001", "name": "机械键盘 87 键", "category": "外设", "price": "399.00", "stock": 50},
    {"sku": "SKU-002", "name": "无线鼠标", "category": "外设", "price": "129.00", "stock": 80},
    {"sku": "SKU-003", "name": "27 寸显示器", "category": "显示", "price": "1299.00", "stock": 20},
    {"sku": "SKU-004", "name": "USB-C 扩展坞", "category": "配件", "price": "249.00", "stock": 35},
    {"sku": "SKU-005", "name": "人体工学椅", "category": "家具", "price": "1899.00", "stock": 10},
    {"sku": "SKU-006", "name": "笔记本支架", "category": "配件", "price": "89.00", "stock": 100},
    {"sku": "SKU-007", "name": "降噪耳机", "category": "音频", "price": "899.00", "stock": 15},
    # 故意留一件 0 库存、一件已下架 —— 缺货和下架这两条分支，不给现成的数据就没人测
    {"sku": "SKU-008", "name": "4K 摄像头（缺货）", "category": "视频", "price": "599.00", "stock": 0},
    {"sku": "SKU-009", "name": "旧款蓝牙音箱（已下架）", "category": "音频", "price": "199.00",
     "stock": 8, "active": False},
]


class DemoShopError(Exception):
    """被测系统自己的业务异常。status 是要返回的 HTTP 码，code 给断言用。"""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _money(v) -> str:
    """金额一律字符串两位小数。

    用字符串不是保守：JSON 里的 1299.0 到了各家客户端会变成 1299、1299.0、1298.9999，
    断言 `totalAmount == "1299.00"` 才是稳的。"""
    return f"{Decimal(str(v)).quantize(Decimal('0.01'))}"


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# ── 序列化（对外一律 camelCase，和平台接口口径一致）──

def product_json(p: DemoProduct) -> dict:
    return {
        "sku": p.sku,
        "name": p.name,
        "category": p.category,
        "price": _money(p.price),
        "stock": p.stock,
        "active": p.active,
        "createdAt": _iso(p.created_at),
    }


def order_json(o: DemoOrder) -> dict:
    return {
        "orderNo": o.order_no,
        "customerName": o.customer_name,
        "customerPhone": o.customer_phone,
        "status": o.status,
        "statusLabel": STATUS_LABELS.get(o.status, o.status),
        "totalAmount": _money(o.total_amount),
        "remark": o.remark,
        "createdBy": o.created_by,
        "createdAt": _iso(o.created_at),
        "updatedAt": _iso(o.updated_at),
        "paidAt": _iso(o.paid_at),
        "shippedAt": _iso(o.shipped_at),
        "items": [
            {
                "sku": it.sku,
                "productName": it.product_name,
                "unitPrice": _money(it.unit_price),
                "quantity": it.quantity,
                "subtotal": _money(it.subtotal),
            }
            for it in sorted(o.items, key=lambda x: x.sku)
        ],
    }


# ── 商品 ──

async def seed_products(session: AsyncSession) -> int:
    """缺哪件补哪件。已有的**一个字都不动** —— 测试把库存改成 0 是有意的，
    别在下次启动时"好心"帮它恢复，那会让「缺货」用例莫名其妙变绿。"""
    existing = set((await session.execute(select(DemoProduct.sku))).scalars().all())
    added = 0
    for row in SEED_PRODUCTS:
        if row["sku"] in existing:
            continue
        session.add(DemoProduct(
            sku=row["sku"], name=row["name"], category=row["category"],
            price=Decimal(row["price"]), stock=row["stock"], active=row.get("active", True),
        ))
        added += 1
    if added:
        await session.commit()
    return added


async def reset_demo_data(session: AsyncSession) -> dict:
    """清空订单 + 商品回到出厂值。页面上那个「重置样例数据」按钮。"""
    order_count = (await session.execute(select(func.count()).select_from(DemoOrder))).scalar_one()
    await session.execute(delete(DemoOrderItem))
    await session.execute(delete(DemoOrder))
    await session.execute(delete(DemoProduct))
    for row in SEED_PRODUCTS:
        session.add(DemoProduct(
            sku=row["sku"], name=row["name"], category=row["category"],
            price=Decimal(row["price"]), stock=row["stock"], active=row.get("active", True),
        ))
    await session.commit()
    return {"deletedOrders": int(order_count), "products": len(SEED_PRODUCTS)}


async def list_products(session: AsyncSession, keyword: str = "", only_active: bool = False,
                        page: int = 1, page_size: int = 50) -> dict:
    page, page_size = _paging(page, page_size)
    stmt = select(DemoProduct)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(DemoProduct.name.ilike(like) | DemoProduct.sku.ilike(like))
    if only_active:
        stmt = stmt.where(DemoProduct.active.is_(True))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await session.execute(
        stmt.order_by(DemoProduct.sku).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return {"data": [product_json(p) for p in rows], "total": int(total),
            "page": page, "pageSize": page_size}


async def get_product(session: AsyncSession, sku: str) -> DemoProduct:
    p = (await session.execute(
        select(DemoProduct).where(DemoProduct.sku == sku)
    )).scalar_one_or_none()
    if p is None:
        raise DemoShopError(404, "PRODUCT_NOT_FOUND", f"商品不存在：{sku}")
    return p


async def update_product(session: AsyncSession, sku: str, fields: dict) -> dict:
    p = await get_product(session, sku)
    if fields.get("name") is not None:
        name = str(fields["name"]).strip()
        if not name:
            raise DemoShopError(422, "INVALID_PARAM", "商品名不能为空")
        p.name = name[:120]
    if fields.get("price") is not None:
        try:
            price = Decimal(str(fields["price"]))
        except Exception:
            raise DemoShopError(422, "INVALID_PARAM", "price 不是合法金额") from None
        if price < 0:
            raise DemoShopError(422, "INVALID_PARAM", "price 不能为负")
        p.price = price
    if fields.get("stock") is not None:
        stock = _as_int(fields["stock"], "stock")
        if stock < 0:
            raise DemoShopError(422, "INVALID_PARAM", "stock 不能为负")
        p.stock = stock
    if fields.get("active") is not None:
        p.active = bool(fields["active"])
    await session.commit()
    await session.refresh(p)
    return product_json(p)


# ── 订单 ──

def _as_int(v, field: str) -> int:
    try:
        if isinstance(v, bool):
            raise ValueError
        return int(v)
    except (TypeError, ValueError):
        raise DemoShopError(422, "INVALID_PARAM", f"{field} 必须是整数") from None


def _paging(page, page_size) -> tuple[int, int]:
    page = _as_int(page or 1, "page")
    page_size = _as_int(page_size or 20, "pageSize")
    if page < 1:
        raise DemoShopError(422, "INVALID_PARAM", "page 从 1 开始")
    if page_size < 1:
        raise DemoShopError(422, "INVALID_PARAM", "pageSize 至少为 1")
    # 上限是硬的：不封顶的话 pageSize=1000000 就是一条免费的拖库/打满内存的路
    return page, min(page_size, MAX_PAGE_SIZE)


async def _next_order_no(session: AsyncSession) -> str:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"SO{day}"
    count = (await session.execute(
        select(func.count()).select_from(DemoOrder).where(DemoOrder.order_no.like(f"{prefix}%"))
    )).scalar_one()
    return f"{prefix}{int(count) + 1:04d}"


async def list_orders(session: AsyncSession, status: str = "", keyword: str = "",
                      page: int = 1, page_size: int = 20) -> dict:
    page, page_size = _paging(page, page_size)
    stmt = select(DemoOrder)
    if status:
        if status not in STATUSES:
            raise DemoShopError(422, "INVALID_PARAM", f"status 只能是 {'/'.join(STATUSES)}")
        stmt = stmt.where(DemoOrder.status == status)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(DemoOrder.order_no.ilike(like) | DemoOrder.customer_name.ilike(like))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await session.execute(
        stmt.order_by(DemoOrder.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return {"data": [order_json(o) for o in rows], "total": int(total),
            "page": page, "pageSize": page_size}


async def get_order(session: AsyncSession, order_no: str) -> DemoOrder:
    o = (await session.execute(
        select(DemoOrder).where(DemoOrder.order_no == order_no)
    )).scalar_one_or_none()
    if o is None:
        raise DemoShopError(404, "ORDER_NOT_FOUND", f"订单不存在：{order_no}")
    return o


async def create_order(session: AsyncSession, payload: dict, actor: str = "") -> dict:
    customer = str(payload.get("customerName") or payload.get("customer_name") or "").strip()
    if not customer:
        raise DemoShopError(422, "INVALID_PARAM", "customerName 必填")
    if len(customer) > 60:
        raise DemoShopError(422, "INVALID_PARAM", "customerName 最长 60 字")
    phone = str(payload.get("customerPhone") or payload.get("customer_phone") or "").strip()[:30]
    remark = payload.get("remark")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise DemoShopError(422, "INVALID_PARAM", "items 至少一行")
    if len(items) > MAX_ITEM_LINES:
        raise DemoShopError(422, "INVALID_PARAM", f"items 最多 {MAX_ITEM_LINES} 行")

    seen: set[str] = set()
    normalized: list[tuple[str, int]] = []
    for raw in items:
        if not isinstance(raw, dict):
            raise DemoShopError(422, "INVALID_PARAM", "items 每行必须是对象")
        sku = str(raw.get("sku") or "").strip()
        if not sku:
            raise DemoShopError(422, "INVALID_PARAM", "items[].sku 必填")
        if sku in seen:
            raise DemoShopError(422, "DUPLICATE_ITEM", f"同一个商品出现了两行：{sku}")
        seen.add(sku)
        qty = _as_int(raw.get("quantity", 1), "items[].quantity")
        if qty < 1 or qty > MAX_QUANTITY:
            raise DemoShopError(422, "INVALID_PARAM", f"items[].quantity 只能是 1~{MAX_QUANTITY}")
        normalized.append((sku, qty))

    order = DemoOrder(
        order_no=await _next_order_no(session), customer_name=customer, customer_phone=phone,
        status="pending", remark=remark, created_by=actor or "anonymous",
        total_amount=Decimal("0.00"),
    )
    total = Decimal("0.00")
    for sku, qty in normalized:
        product = await get_product(session, sku)
        if not product.active:
            # 多行订单里前面几行可能已经扣过库存了，这里必须回滚：
            # 一张下不成的订单不该在库存上留下半截痕迹。
            await session.rollback()
            raise DemoShopError(409, "PRODUCT_INACTIVE", f"商品已下架：{sku}")
        # 扣库存用带条件的 UPDATE，不是"先查再减" —— 后者两个人同时下单会把库存扣成负数，
        # 而这正是并发用例要抓的东西：被测系统本身得是对的，用例才验得出别的问题。
        result = await session.execute(
            update(DemoProduct)
            .where(DemoProduct.sku == sku, DemoProduct.stock >= qty)
            .values(stock=DemoProduct.stock - qty)
        )
        if result.rowcount == 0:
            # ⚠ 先把 stock 读出来再 rollback。回滚会把 product 置为过期，
            # 之后再碰 product.stock 会触发一次懒加载 —— 在 async 会话里那是
            # MissingGreenlet，于是「库存不足」这条本该是 409 的路径变成 500。
            left = product.stock
            await session.rollback()
            raise DemoShopError(409, "OUT_OF_STOCK",
                                f"库存不足：{sku} 只剩 {left} 件，要 {qty} 件")
        subtotal = (Decimal(str(product.price)) * qty).quantize(Decimal("0.01"))
        total += subtotal
        order.items.append(DemoOrderItem(
            sku=sku, product_name=product.name, unit_price=product.price,
            quantity=qty, subtotal=subtotal,
        ))
    order.total_amount = total
    session.add(order)
    for _ in range(5):
        try:
            await session.commit()
            break
        except IntegrityError:
            # 同一秒两单撞上同一个流水号：换一个再来，别把 500 甩给调用方
            await session.rollback()
            order.order_no = f"SO{datetime.now(timezone.utc):%Y%m%d}{random.randint(1, 9999):04d}"
            session.add(order)
    else:
        raise DemoShopError(500, "ORDER_NO_CONFLICT", "订单号生成失败，请重试")
    await session.refresh(order)
    return order_json(order)


async def update_order(session: AsyncSession, order_no: str, fields: dict) -> dict:
    o = await get_order(session, order_no)
    if o.status != "pending":
        raise DemoShopError(409, "INVALID_STATUS",
                            f"只有待支付的订单能改，当前是{STATUS_LABELS.get(o.status, o.status)}")
    if fields.get("customerName") is not None:
        name = str(fields["customerName"]).strip()
        if not name:
            raise DemoShopError(422, "INVALID_PARAM", "customerName 不能为空")
        o.customer_name = name[:60]
    if fields.get("customerPhone") is not None:
        o.customer_phone = str(fields["customerPhone"]).strip()[:30]
    if fields.get("remark") is not None:
        o.remark = str(fields["remark"])
    await session.commit()
    await session.refresh(o)
    return order_json(o)


async def transition(session: AsyncSession, order_no: str, action: str) -> dict:
    if action not in ALLOWED_TRANSITIONS:
        raise DemoShopError(404, "UNKNOWN_ACTION", f"没有这个动作：{action}")
    o = await get_order(session, order_no)
    target = ALLOWED_TRANSITIONS[action].get(o.status)
    if target is None:
        raise DemoShopError(
            409, "INVALID_STATUS",
            f"{STATUS_LABELS.get(o.status, o.status)}的订单不能{ACTION_LABELS[action]}",
        )
    now = datetime.now(timezone.utc)
    if action == "cancel" and o.status in RESTOCK_STATUSES:
        for it in o.items:
            await session.execute(
                update(DemoProduct).where(DemoProduct.sku == it.sku)
                .values(stock=DemoProduct.stock + it.quantity)
            )
    o.status = target
    if action == "pay":
        o.paid_at = now
    elif action == "ship":
        o.shipped_at = now
    await session.commit()
    await session.refresh(o)
    return order_json(o)


async def delete_order(session: AsyncSession, order_no: str) -> dict:
    o = await get_order(session, order_no)
    if o.status in RESTOCK_STATUSES:
        for it in o.items:
            await session.execute(
                update(DemoProduct).where(DemoProduct.sku == it.sku)
                .values(stock=DemoProduct.stock + it.quantity)
            )
    await session.delete(o)
    await session.commit()
    return {"orderNo": order_no, "deleted": True}


async def stats(session: AsyncSession) -> dict:
    rows = (await session.execute(
        select(DemoOrder.status, func.count(), func.coalesce(func.sum(DemoOrder.total_amount), 0))
        .group_by(DemoOrder.status)
    )).all()
    by_status = {s: 0 for s in STATUSES}
    revenue = Decimal("0.00")
    total = 0
    for status, count, amount in rows:
        by_status[status] = int(count)
        total += int(count)
        if status in ("paid", "shipped", "completed"):
            revenue += Decimal(str(amount))
    products = (await session.execute(select(func.count()).select_from(DemoProduct))).scalar_one()
    out_of_stock = (await session.execute(
        select(func.count()).select_from(DemoProduct).where(DemoProduct.stock <= 0)
    )).scalar_one()
    return {
        "orders": total,
        "ordersByStatus": by_status,
        "revenue": _money(revenue),
        "products": int(products),
        "outOfStock": int(out_of_stock),
    }
