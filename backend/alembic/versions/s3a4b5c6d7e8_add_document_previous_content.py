"""documents 加 previous_content —— 「优化文字」要有回头路

`POST /documents/{id}/optimize` 会直接把 doc.content 整篇覆盖掉。之前这条路
没有任何调用方，覆盖不到人；现在要给它接页面入口，覆盖就成了真实风险：

一篇文档是"真登录被测系统 + 逐页截图 + AI 写"跑出来的，重做一遍代价很大。
AI 优化后要是更差了、或者把截图引用弄丢了，原文没了就找不回来。

所以存一份优化前的正文，配一个「撤销优化」。只留最近一版 —— 不做版本树，
需要的是"刚才那下点错了能退回去"，不是文档版本管理。

Revision ID: s3a4b5c6d7e8
Revises: r2f3a4b5c6d7
"""
from alembic import op
import sqlalchemy as sa

revision = "s3a4b5c6d7e8"
down_revision = "r2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("previous_content", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "previous_content")
