"""Task budgets, quotas and cost ledger

Revision ID: 20260715_0008
Revises: 20260715_0007
Create Date: 2026-07-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0008"
down_revision: Union[str, Sequence[str], None] = "20260715_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_budget_policies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("period", sa.String(length=16), nullable=False),
        sa.Column("enforcement_mode", sa.String(length=16), nullable=False),
        sa.Column("limit_usd", sa.Numeric(18, 6), nullable=True),
        sa.Column(
            "warning_threshold_percent",
            sa.Float(),
            nullable=False,
        ),
        sa.Column("max_task_cost_usd", sa.Numeric(18, 6), nullable=True),
        sa.Column("max_tasks_per_period", sa.Integer(), nullable=True),
        sa.Column("max_queued", sa.Integer(), nullable=True),
        sa.Column("max_running", sa.Integer(), nullable=True),
        sa.Column("allowed_task_types_json", sa.JSON(), nullable=False),
        sa.Column("denied_task_types_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "period IN ('daily', 'monthly', 'lifetime')",
            name="ck_task_budget_policies_period",
        ),
        sa.CheckConstraint(
            "enforcement_mode IN ('hard', 'observe')",
            name="ck_task_budget_policies_enforcement",
        ),
        sa.CheckConstraint(
            "limit_usd IS NULL OR limit_usd >= 0",
            name="ck_task_budget_policies_limit",
        ),
        sa.CheckConstraint(
            "max_task_cost_usd IS NULL OR max_task_cost_usd >= 0",
            name="ck_task_budget_policies_max_task_cost",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_task_budget_policies_workspace_name",
        ),
    )
    op.create_index(
        "ix_task_budget_policies_workspace_id",
        "task_budget_policies",
        ["workspace_id"],
    )
    op.create_index(
        "ix_task_budget_policies_enabled",
        "task_budget_policies",
        ["enabled"],
    )

    op.create_table(
        "task_cost_ledger",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("reference_id", sa.String(length=255), nullable=True),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("policy_id", sa.String(length=64), nullable=True),
        sa.Column("entry_type", sa.String(length=32), nullable=False),
        sa.Column("amount_usd", sa.Numeric(18, 6), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "entry_type IN ("
            "'admission', 'reservation', 'release', 'charge', "
            "'adjustment', 'override'"
            ")",
            name="ck_task_cost_ledger_entry_type",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference_id"),
    )
    for index_name, columns in (
        ("ix_task_cost_ledger_reference_id", ["reference_id"]),
        ("ix_task_cost_ledger_task_id", ["task_id"]),
        ("ix_task_cost_ledger_workspace_id", ["workspace_id"]),
        ("ix_task_cost_ledger_policy_id", ["policy_id"]),
        ("ix_task_cost_ledger_entry_type", ["entry_type"]),
        ("ix_task_cost_ledger_actor_id", ["actor_id"]),
        ("ix_task_cost_ledger_created_at", ["created_at"]),
    ):
        op.create_index(index_name, "task_cost_ledger", columns)


def downgrade() -> None:
    for index_name in (
        "ix_task_cost_ledger_created_at",
        "ix_task_cost_ledger_actor_id",
        "ix_task_cost_ledger_entry_type",
        "ix_task_cost_ledger_policy_id",
        "ix_task_cost_ledger_workspace_id",
        "ix_task_cost_ledger_task_id",
        "ix_task_cost_ledger_reference_id",
    ):
        op.drop_index(index_name, table_name="task_cost_ledger")
    op.drop_table("task_cost_ledger")

    op.drop_index(
        "ix_task_budget_policies_enabled",
        table_name="task_budget_policies",
    )
    op.drop_index(
        "ix_task_budget_policies_workspace_id",
        table_name="task_budget_policies",
    )
    op.drop_table("task_budget_policies")
