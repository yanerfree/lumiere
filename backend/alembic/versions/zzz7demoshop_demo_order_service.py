"""样例订单系统：demo_products / demo_orders / demo_order_items

Revision ID: zzz7demoshop
Revises: zzz6mockbi

「测试工具 → 订单服务」那一页背后的三张表。**它们装的是被测数据，不是平台数据。**

⚠ 为什么给表名统一加 `demo_` 前缀：这三张表跟平台自己的业务表同住一个库，而它们
是**故意可以被随便删光的**（页面上那个「重置样例数据」按钮就是 `DELETE FROM`）。
前缀是给人看的护栏 —— 以后谁写清库脚本，一眼看得出哪几张表清了没关系、哪几张不行。

⚠ 为什么外部标识用 sku / order_no 而不是 uuid 主键：用例里会写死
`SKU-001`、断言 `orderNo` 的前缀格式。重置一次样例数据 uuid 全变，写好的用例会
集体变红在一个和被测功能无关的地方。sku 是稳定的，重置之后还是那几件商品。

⚠ 为什么订单项里存商品名和单价（看着像冗余）：那是**下单时的快照**。商品改价之后
历史订单的金额不能跟着变 —— 这件事本身就是一个值得测的点，靠 join 商品表就测不了了。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "zzz7demoshop"
down_revision = "zzz6mockbi"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "demo_products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sku", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("category", sa.String(40), nullable=False, server_default="其他"),
        sa.Column("price", sa.Numeric(10, 2), nullable=False, server_default="0.00"),
        sa.Column("stock", sa.Integer, nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        "demo_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("order_no", sa.String(32), nullable=False, unique=True),
        sa.Column("customer_name", sa.String(60), nullable=False),
        sa.Column("customer_phone", sa.String(30), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("remark", sa.Text, nullable=True),
        sa.Column("created_by", sa.String(40), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_demo_orders_status", "demo_orders", ["status"])
    op.create_index("ix_demo_orders_created_at", "demo_orders", ["created_at"])

    op.create_table(
        "demo_order_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # 级联删除是故意的：订单没了，行项目留着就是一堆查不到归属的孤儿，
        # 而「删订单」在这个系统里是个正常操作（用例跑完自己清场）。
        sa.Column("order_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demo_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sku", sa.String(40), nullable=False),
        sa.Column("product_name", sa.String(120), nullable=False),
        sa.Column("unit_price", sa.Numeric(10, 2), nullable=False, server_default="0.00"),
        sa.Column("quantity", sa.Integer, nullable=False, server_default="1"),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
    )
    op.create_index("ix_demo_order_items_order_id", "demo_order_items", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_demo_order_items_order_id", table_name="demo_order_items")
    op.drop_table("demo_order_items")
    op.drop_index("ix_demo_orders_created_at", table_name="demo_orders")
    op.drop_index("ix_demo_orders_status", table_name="demo_orders")
    op.drop_table("demo_orders")
    op.drop_table("demo_products")
