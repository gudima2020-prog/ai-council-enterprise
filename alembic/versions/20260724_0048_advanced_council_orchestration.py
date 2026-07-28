"""P2-006 advanced Council orchestration modes and audit metadata.

Revision ID: 20260724_0048
Revises: 20260724_0047
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260724_0048"
down_revision = "20260724_0047"
branch_labels = None
depends_on = None


EXECUTION_CHECK = (
    "execution_mode IN ('solo', 'council', 'best_of_n', 'review', "
    "'arbitration', 'delegate')"
)
LEDGER_KIND_CHECK = (
    "kind IN ('member', 'synthesis', 'draft', 'reviewer', 'planner', 'delegate')"
)


def upgrade() -> None:
    with op.batch_alter_table("council_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "execution_mode",
                sa.String(length=24),
                nullable=False,
                server_default="council",
            )
        )
        batch_op.create_index(
            "ix_council_runs_execution_mode", ["execution_mode"], unique=False
        )
        batch_op.drop_constraint("ck_council_runs_member_count", type_="check")
        batch_op.create_check_constraint(
            "ck_council_runs_member_count",
            "member_count >= 1 AND member_count <= 6",
        )
        batch_op.create_check_constraint(
            "ck_council_runs_execution_mode",
            EXECUTION_CHECK,
        )

    with op.batch_alter_table("council_presets") as batch_op:
        batch_op.add_column(
            sa.Column(
                "execution_mode",
                sa.String(length=24),
                nullable=False,
                server_default="council",
            )
        )
        batch_op.add_column(sa.Column("reviewer_provider", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("reviewer_model", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("arbiter_provider", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("arbiter_model", sa.String(length=255), nullable=True))
        batch_op.add_column(
            sa.Column("delegation_max_calls", sa.Integer(), nullable=False, server_default="4")
        )
        batch_op.add_column(
            sa.Column("delegation_max_depth", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.create_check_constraint(
            "ck_council_presets_execution_mode",
            EXECUTION_CHECK,
        )
        batch_op.create_check_constraint(
            "ck_council_presets_delegation_calls",
            "delegation_max_calls >= 1 AND delegation_max_calls <= 8",
        )
        batch_op.create_check_constraint(
            "ck_council_presets_delegation_depth",
            "delegation_max_depth = 1",
        )

    with op.batch_alter_table("council_cost_ledger") as batch_op:
        batch_op.drop_constraint("ck_council_cost_ledger_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_council_cost_ledger_kind",
            LEDGER_KIND_CHECK,
        )


def downgrade() -> None:
    with op.batch_alter_table("council_cost_ledger") as batch_op:
        batch_op.drop_constraint("ck_council_cost_ledger_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_council_cost_ledger_kind",
            "kind IN ('member', 'synthesis')",
        )

    with op.batch_alter_table("council_presets") as batch_op:
        batch_op.drop_constraint("ck_council_presets_delegation_depth", type_="check")
        batch_op.drop_constraint("ck_council_presets_delegation_calls", type_="check")
        batch_op.drop_constraint("ck_council_presets_execution_mode", type_="check")
        batch_op.drop_column("delegation_max_depth")
        batch_op.drop_column("delegation_max_calls")
        batch_op.drop_column("arbiter_model")
        batch_op.drop_column("arbiter_provider")
        batch_op.drop_column("reviewer_model")
        batch_op.drop_column("reviewer_provider")
        batch_op.drop_column("execution_mode")

    with op.batch_alter_table("council_runs") as batch_op:
        batch_op.drop_constraint("ck_council_runs_execution_mode", type_="check")
        batch_op.drop_constraint("ck_council_runs_member_count", type_="check")
        batch_op.create_check_constraint(
            "ck_council_runs_member_count",
            "member_count >= 2 AND member_count <= 6",
        )
        batch_op.drop_index("ix_council_runs_execution_mode")
        batch_op.drop_column("execution_mode")
