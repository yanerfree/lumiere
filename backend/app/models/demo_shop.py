"""样例订单系统（Demo Shop）的三张表 —— **这是被测系统的数据，不是平台的数据**。

平台自己的表记的是「测试怎么做」（用例、计划、报告）；这三张记的是一个
**真实存在的小业务系统**的库存和订单，专门用来给人练手：写用例、写接口场景、
写 UI 脚本，打过去是真的增删改，不是 mock 回放。

为什么要真落库而不是再加一个 mock：mock 回什么由配置决定，断言「下单后库存
应该减 1」在 mock 上永远绿 —— 它根本没有库存这个概念。**没有状态的系统测不出
状态相关的 bug**，而那恰恰是最该练的一类（状态机、并发扣减、幂等）。

命名一律 `demo_` 前缀，和平台表分得开：备份/清库/审计时一眼看出来这几张是玩具数据，
删了不心疼（页面上「重置样例数据」就是清它们）。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base


class DemoProduct(Base):
    """商品。sku 是业务主键 —— 对外接口一律用 sku 定位，不暴露 uuid。

    对外用 sku 不是洁癖：uuid 每次重置样例数据都会变，而用例里写死的路径参数不会跟着变，
    于是「重置一下」就能让一整批用例 404。sku 是稳定的（SKU-001 永远是那件商品）。
    """

    __tablename__ = "demo_products"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sku: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="其他")
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DemoOrder(Base):
    """订单。order_no 同理是对外的业务主键。

    状态机（services/demo_shop_service.py 的 ALLOWED_TRANSITIONS 是唯一出处）：
        pending ──pay──> paid ──ship──> shipped ──complete──> completed
           └────────────cancel────────────┘（只有 pending/paid 能取消，取消会还库存）
    """

    __tablename__ = "demo_orders"
    __table_args__ = (
        Index("ix_demo_orders_status", "status"),
        Index("ix_demo_orders_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    customer_name: Mapped[str] = mapped_column(String(60), nullable=False)
    customer_phone: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["DemoOrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )


class DemoOrderItem(Base):
    """订单行。商品名和单价是**下单时的快照** —— 商品改了价，历史订单金额不能跟着变。

    这条规则本身就是一个值得测的点：改价之后查旧订单，金额必须还是老的。
    """

    __tablename__ = "demo_order_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("demo_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sku: Mapped[str] = mapped_column(String(40), nullable=False)
    product_name: Mapped[str] = mapped_column(String(120), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    order: Mapped[DemoOrder] = relationship(back_populates="items")
