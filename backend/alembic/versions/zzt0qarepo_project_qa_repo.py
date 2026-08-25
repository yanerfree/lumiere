"""projects 增加 qa_repo（只读 QA 仓配置）

Revision ID: zzt0qarepo
Revises: zzr0aiusage
Create Date: 2026-08-25

QA 仓是**别人的仓库**（黑盒验收仓，产品代码不在里面）。这里只存"去哪儿读"，
平台对它永远只读：clone --bare + fetch + git show，不写、不 push、不建 worktree。
形状: {"url": str, "branch": str, "catalogPath": str, "caseGlobs": [str, ...]}
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "zzt0qarepo"
down_revision = "zzr0aiusage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("qa_repo", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "qa_repo")
