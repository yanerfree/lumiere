"""add project_i18n_messages (project-level i18n dictionary for UI copy)

Revision ID: c4e6d8f0a2b1
Revises: a8b9c0d1e2f3
Create Date: 2026-07-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c4e6d8f0a2b1"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_i18n_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key_text", sa.String(length=500), nullable=False),
        sa.Column("translations", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=True),
        sa.Column("source", sa.String(length=20), server_default=sa.text("'harvested'"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "key_text", name="uq_i18n_project_keytext"),
    )
    op.create_index("ix_project_i18n_messages_project_id", "project_i18n_messages", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_project_i18n_messages_project_id", table_name="project_i18n_messages")
    op.drop_table("project_i18n_messages")
