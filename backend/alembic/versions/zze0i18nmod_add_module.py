"""project_i18n_messages 加 module（模块）—— 存下来、可编辑，不再靠键推导

原来「模块」是前端从键的第一段实时推导的（apps → 应用管理）。用户的三连问点破了它：
「为什么要实时推导，如果命名错误了你也推导吗，为什么不能改」——

派生值放在列表上，人默认它能改，实际改不了；键写错了（svc.foo）它就显示 svc，
而这时该改的是键，于是这一列既没用又误导。**改成真字段**：导入时按命名空间预填
中文模块名，之后人和 CC 都能改。

Revision ID: zze0i18nmod
Revises: zzd0fkc1
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "zze0i18nmod"
down_revision = "zzd0fkc1"
branch_labels = None
depends_on = None

# 命名空间 → 中文模块名。只用来给存量数据预填一次，之后以库里的值为准。
_NS = {
    "common": "通用", "services": "服务管理", "subscription": "订阅管理",
    "apps": "应用管理", "auth": "登录认证", "dashboard": "概览",
    "gateway": "网关", "upstream": "负载", "menu": "菜单",
    "tenant": "租户", "application": "应用",
}


def upgrade() -> None:
    op.add_column("project_i18n_messages", sa.Column("module", sa.String(64), nullable=True))
    for ns, label in _NS.items():
        op.execute(
            f"UPDATE project_i18n_messages SET module = '{label}' "
            f"WHERE module IS NULL AND key_text LIKE '{ns}.%'"
        )


def downgrade() -> None:
    op.drop_column("project_i18n_messages", "module")
