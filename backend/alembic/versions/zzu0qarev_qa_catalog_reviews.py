"""qa_catalog_reviews —— QA 场景清单的域级 AI 评审记录

Revision ID: zzu0qarev
Revises: zzt0qarepo
Create Date: 2026-08-26

**不复用 review_batches**：那张表 branch_id NOT NULL，而 QA 域不属于任何分支；
它的队列 worker 还会把行当用例批次捡走。理由写在 models/qa_catalog_review.py。

这张表只存平台自己的产出，**不会有任何东西写回 QA 仓**。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "zzu0qarev"
down_revision = "zzt0qarepo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qa_catalog_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("domain", sa.String(16), nullable=False),
        sa.Column("domain_name", sa.String(120), nullable=False, server_default=""),
        sa.Column("environment_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("environments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("environment_name", sa.String(120), nullable=False, server_default=""),
        sa.Column("commit_sha", sa.String(64), nullable=False, server_default=""),
        sa.Column("branch", sa.String(200), nullable=False, server_default=""),
        sa.Column("actor", sa.String(100), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("scenario_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("script_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_qa_catalog_reviews_project_domain", "qa_catalog_reviews",
                    ["project_id", "domain", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_qa_catalog_reviews_project_domain", table_name="qa_catalog_reviews")
    op.drop_table("qa_catalog_reviews")
