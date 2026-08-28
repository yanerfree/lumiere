"""qa_page_surveys / qa_page_survey_items —— 页面枚举爬取的事实账本

Revision ID: zzx0qasrv
Revises: zzw0roleck
Create Date: 2026-08-29

**不复用 review_batches / qa_catalog_reviews**：那两张存的是结论，这张存的是事实
（爬到了什么），可信度来源不同；爬取按角色分片，一趟横跨多个域，没有域码可挂。
理由写在 models/qa_page_survey.py。

`uq_qa_page_survey_items_key` 是**硬约束不是优化**：key 重复意味着 anchor 推断塌了，
让它在写入时炸，比在 diff 里表现成「新增 40 项」好查。写入路径不许 ON CONFLICT。

这张表不会让平台往被测环境写任何东西；凭证在落库前整个键 drop 掉，不是脱敏。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "zzx0qasrv"
down_revision = "zzw0roleck"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qa_page_surveys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("env_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("environments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("env_name", sa.String(120), nullable=False, server_default=""),
        sa.Column("build_fingerprint", sa.String(120), nullable=False, server_default=""),
        sa.Column("route_table_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("roles", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("ledger", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.UniqueConstraint("project_id", "env_id", "build_fingerprint", "started_at",
                            name="uq_qa_page_surveys_run"),
    )
    op.create_index("ix_qa_page_surveys_project_env_status", "qa_page_surveys",
                    ["project_id", "env_id", "status"])

    op.create_table(
        "qa_page_survey_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("survey_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("qa_page_surveys.id", ondelete="CASCADE"), nullable=False),
        # 冗余列：AD-6 要的索引是 (project_id, page_path)，对账跨 survey 按页扫，
        # 挂在 survey 上的索引帮不上忙。见 models/qa_page_survey.py 里那段注释。
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(500), nullable=False),
        sa.Column("page_path", sa.String(300), nullable=False, server_default=""),
        sa.Column("page_title", sa.String(200), nullable=False, server_default=""),
        sa.Column("anchor", sa.String(400), nullable=False, server_default=""),
        sa.Column("anchor_kind", sa.String(20), nullable=False, server_default=""),
        sa.Column("label", sa.String(200), nullable=False, server_default=""),
        sa.Column("control_type", sa.String(40), nullable=False, server_default=""),
        sa.Column("state", sa.String(20), nullable=False, server_default="present"),
        sa.Column("roles_visible", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("endpoints", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("first_seen_survey_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("qa_page_surveys.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_seen_survey_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("qa_page_surveys.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("survey_id", "key", name="uq_qa_page_survey_items_key"),
    )
    op.create_index("ix_qa_page_survey_items_project_page", "qa_page_survey_items",
                    ["project_id", "page_path"])
    op.create_index("ix_qa_page_survey_items_key", "qa_page_survey_items", ["key"])


def downgrade() -> None:
    op.drop_index("ix_qa_page_survey_items_key", table_name="qa_page_survey_items")
    op.drop_index("ix_qa_page_survey_items_project_page", table_name="qa_page_survey_items")
    op.drop_table("qa_page_survey_items")
    op.drop_index("ix_qa_page_surveys_project_env_status", table_name="qa_page_surveys")
    op.drop_table("qa_page_surveys")
