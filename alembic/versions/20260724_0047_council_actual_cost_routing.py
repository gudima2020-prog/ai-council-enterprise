"""P2-005 actual cost ledger, reservations and quota counters.

Revision ID: 20260724_0047
Revises: 20260724_0046
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260724_0047"
down_revision = "20260724_0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("council_runs") as batch_op:
        batch_op.add_column(sa.Column("cost_reservation_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("actual_cost_usd", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("actual_cost_status", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("actual_input_tokens", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("actual_output_tokens", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("actual_total_tokens", sa.Integer(), nullable=True))
        batch_op.create_index("ix_council_runs_cost_reservation_id", ["cost_reservation_id"], unique=False)

    op.create_table(
        "council_cost_reservations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("estimate_status", sa.String(length=16), nullable=False),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("estimated_tokens", sa.Integer(), nullable=False),
        sa.Column("actual_cost_usd", sa.Float(), nullable=True),
        sa.Column("actual_total_tokens", sa.Integer(), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("actor_id", sa.String(length=255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('reserved', 'settled', 'released', 'expired')",
            name="ck_council_cost_reservations_status",
        ),
        sa.CheckConstraint(
            "estimate_status IN ('known', 'partial', 'unknown')",
            name="ck_council_cost_reservations_estimate_status",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_council_cost_reservations_workspace_id", "council_cost_reservations", ["workspace_id"], unique=False)
    op.create_index("ix_council_cost_reservations_fingerprint", "council_cost_reservations", ["fingerprint"], unique=False)
    op.create_index("ix_council_cost_reservations_status", "council_cost_reservations", ["status"], unique=False)
    op.create_index("ix_council_cost_reservations_run_id", "council_cost_reservations", ["run_id"], unique=False)
    op.create_index("ix_council_cost_reservations_created_at", "council_cost_reservations", ["created_at"], unique=False)

    op.create_table(
        "council_cost_ledger",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("line_key", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("requested_model", sa.String(length=255), nullable=True),
        sa.Column("resolved_model", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("provider_reported_cost_usd", sa.Float(), nullable=True),
        sa.Column("actual_cost_usd", sa.Float(), nullable=True),
        sa.Column("cost_source", sa.String(length=16), nullable=False),
        sa.Column("pricing_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('member', 'synthesis')", name="ck_council_cost_ledger_kind"),
        sa.CheckConstraint(
            "cost_source IN ('provider', 'calculated', 'free', 'unknown')",
            name="ck_council_cost_ledger_source",
        ),
        sa.CheckConstraint("input_tokens IS NULL OR input_tokens >= 0", name="ck_council_cost_ledger_input_tokens"),
        sa.CheckConstraint("output_tokens IS NULL OR output_tokens >= 0", name="ck_council_cost_ledger_output_tokens"),
        sa.CheckConstraint("total_tokens IS NULL OR total_tokens >= 0", name="ck_council_cost_ledger_total_tokens"),
        sa.ForeignKeyConstraint(["run_id"], ["council_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "line_key", name="uq_council_cost_ledger_run_line"),
    )
    op.create_index("ix_council_cost_ledger_run_id", "council_cost_ledger", ["run_id"], unique=False)
    op.create_index("ix_council_cost_ledger_workspace_id", "council_cost_ledger", ["workspace_id"], unique=False)
    op.create_index("ix_council_cost_ledger_resolved_model", "council_cost_ledger", ["resolved_model"], unique=False)
    op.create_index("ix_council_cost_ledger_created_at", "council_cost_ledger", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_council_cost_ledger_created_at", table_name="council_cost_ledger")
    op.drop_index("ix_council_cost_ledger_resolved_model", table_name="council_cost_ledger")
    op.drop_index("ix_council_cost_ledger_workspace_id", table_name="council_cost_ledger")
    op.drop_index("ix_council_cost_ledger_run_id", table_name="council_cost_ledger")
    op.drop_table("council_cost_ledger")

    op.drop_index("ix_council_cost_reservations_created_at", table_name="council_cost_reservations")
    op.drop_index("ix_council_cost_reservations_run_id", table_name="council_cost_reservations")
    op.drop_index("ix_council_cost_reservations_status", table_name="council_cost_reservations")
    op.drop_index("ix_council_cost_reservations_fingerprint", table_name="council_cost_reservations")
    op.drop_index("ix_council_cost_reservations_workspace_id", table_name="council_cost_reservations")
    op.drop_table("council_cost_reservations")

    with op.batch_alter_table("council_runs") as batch_op:
        batch_op.drop_index("ix_council_runs_cost_reservation_id")
        batch_op.drop_column("actual_total_tokens")
        batch_op.drop_column("actual_output_tokens")
        batch_op.drop_column("actual_input_tokens")
        batch_op.drop_column("actual_cost_status")
        batch_op.drop_column("actual_cost_usd")
        batch_op.drop_column("cost_reservation_id")
