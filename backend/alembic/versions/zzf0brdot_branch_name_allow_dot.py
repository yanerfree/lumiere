"""分支名允许点号 —— v2.2.0 这种版本号建不了分支

弹窗占位符本来就写着「如：v2.0」，校验却把点号挡了，提示和规则从一开始就是矛盾的。

规则有三份（前端 rules / pydantic pattern / 这条 CHECK），前两份改了不算数：
DB 约束才是最后一道，违反后被 branch_service 的 `except IntegrityError` 统一
翻译成「分支配置名称已存在」，报错完全指不到真正的原因。

点号写成「只能做分隔符」而不是直接塞进字符集：分支名会被拼成工作目录
{script_base_path}/{name}/（services/git_service.py），无限制放开后名字叫 ".."
就等于往上跳一层目录。分隔符写法天然排除 "."、".."、".hidden"、"v2."、"a..b"。

Revision ID: zzf0brdot
Revises: zze0i18nmod
"""
from __future__ import annotations

from alembic import op

revision = "zzf0brdot"
down_revision = "zze0i18nmod"
branch_labels = None
depends_on = None

_OLD = r"name ~ '^[a-zA-Z0-9_\-]{1,50}$'"
_NEW = r"name ~ '^[a-zA-Z0-9_-]+(\.[a-zA-Z0-9_-]+)*$' AND char_length(name) <= 50"


def upgrade() -> None:
    op.drop_constraint("ck_branch_name_format", "branches", type_="check")
    op.create_check_constraint("ck_branch_name_format", "branches", _NEW)


def downgrade() -> None:
    # 回滚前若已存在带点号的分支名，这条会失败 —— 那是对的，静默丢数据更糟。
    op.drop_constraint("ck_branch_name_format", "branches", type_="check")
    op.create_check_constraint("ck_branch_name_format", "branches", _OLD)
