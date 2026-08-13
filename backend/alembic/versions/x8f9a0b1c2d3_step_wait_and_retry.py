"""接口步骤加 wait_ms / retry_timeout_ms / retry_interval_ms

## 为什么

被测系统的配置下发是**异步**的：实测网关从「发布成功」到「网关真的能转发」
要 0.06~0.5s，而且抖动。而接口场景的步骤之间只隔几毫秒 ——
「发布上线」的下一步「打网关」必然抢跑，第一版跑出来就是红的。

外部 CC 只能靠**插入真实断言步骤**（查版本历史、查操作日志）来占住这个时间窗。
能用，但很脆：换台机器、网络慢一点就不够了。它自己的原话是
「这是这次最影响用例质量的一点」。

## 三个字段

- `wait_ms`：发请求前先等。**下策** —— 要么白等，要么不够。
- `retry_timeout_ms`：断言没过就整步重发，直到过了或者超时。**这才是正解**，
  它等的是"它真的好了"，不是"一个拍脑袋的秒数"。0 = 不重试（默认）。
- `retry_interval_ms`：两次重发之间隔多久，默认 300ms。

## 顺带合掉迁移分叉

`w7e8f9a0b1c2` 和 `p9k0l1m2n3o4` 是两个并列的 head（库里 alembic_version 有两行）。
不合的话 `alembic upgrade head` 会报 Multiple head revisions，下一个人得先自己
搞明白该 upgrade 哪一个。这条迁移把两支并回一条。
"""
from alembic import op
import sqlalchemy as sa

revision = "x8f9a0b1c2d3"
down_revision = ("w7e8f9a0b1c2", "p9k0l1m2n3o4")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_test_steps",
                  sa.Column("wait_ms", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("api_test_steps",
                  sa.Column("retry_timeout_ms", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("api_test_steps",
                  sa.Column("retry_interval_ms", sa.Integer(), nullable=False, server_default="300"))


def downgrade() -> None:
    op.drop_column("api_test_steps", "retry_interval_ms")
    op.drop_column("api_test_steps", "retry_timeout_ms")
    op.drop_column("api_test_steps", "wait_ms")
