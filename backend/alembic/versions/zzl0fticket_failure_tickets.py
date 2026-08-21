"""failure_tickets —— 失败从红到关的跟进单（含复发）

Revision ID: zzl0fticket
Revises: zzk0rvround
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "zzl0fticket"
down_revision = "zzk0rvround"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "failure_tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("case_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("script_type", sa.String(10), nullable=False),
        sa.Column("phenomenon", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("first_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("script_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("script_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reopened_from", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("failure_tickets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("recurrence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cc_analysis", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confirmed_cause", sa.String(32), nullable=True),
        sa.Column("confirmed_note", sa.Text(), nullable=True),
        sa.Column("confirmed_by", sa.String(100), nullable=True),
        sa.Column("disposition", sa.String(32), nullable=True),
        sa.Column("closed_by_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("script_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("closed_reason", sa.Text(), nullable=True),
        sa.Column("closed_by", sa.String(100), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ftickets_open", "failure_tickets", ["case_id", "phenomenon", "status"])


def downgrade() -> None:
    op.drop_index("ix_ftickets_open", table_name="failure_tickets")
    op.drop_table("failure_tickets")
