"""Mission memory, evidence evaluation and goal confirmation

Revision ID: 20260716_0022
Revises: 20260716_0021
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260716_0022"
down_revision: Union[str, Sequence[str], None] = "20260716_0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mission_memory_entries",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("goal_id", sa.String(length=64), nullable=True),
        sa.Column("memory_key", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=True),
        sa.Column("importance_score", sa.Integer(), nullable=False),
        sa.Column("confidence_score", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "category IN ('fact', 'decision', 'artifact', 'lesson', 'constraint', 'summary')",
            name="ck_mission_memory_entries_category",
        ),
        sa.CheckConstraint(
            "importance_score >= 0 AND importance_score <= 100",
            name="ck_mission_memory_entries_importance",
        ),
        sa.CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 100",
            name="ck_mission_memory_entries_confidence",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_mission_memory_entries_version",
        ),
        sa.ForeignKeyConstraint(["goal_id"], ["mission_goals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mission_id", "memory_key", name="uq_mission_memory_entries_key"),
    )
    for name in (
        "workspace_id", "mission_id", "goal_id", "category", "source_type",
        "source_ref", "archived", "expires_at", "created_by", "created_at",
    ):
        op.create_index(
            f"ix_mission_memory_entries_{name}",
            "mission_memory_entries",
            [name],
        )

    op.create_table(
        "mission_evidence_policies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("goal_id", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("required_evidence_count", sa.Integer(), nullable=False),
        sa.Column("min_individual_score", sa.Float(), nullable=False),
        sa.Column("min_average_score", sa.Float(), nullable=False),
        sa.Column("require_distinct_sources", sa.Boolean(), nullable=False),
        sa.Column("min_distinct_sources", sa.Integer(), nullable=False),
        sa.Column("require_human_review", sa.Boolean(), nullable=False),
        sa.Column("auto_confirm_enabled", sa.Boolean(), nullable=False),
        sa.Column("allowed_evidence_types_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "required_evidence_count >= 1",
            name="ck_mission_evidence_policies_required_count",
        ),
        sa.CheckConstraint(
            "min_individual_score >= 0 AND min_individual_score <= 100",
            name="ck_mission_evidence_policies_individual_score",
        ),
        sa.CheckConstraint(
            "min_average_score >= 0 AND min_average_score <= 100",
            name="ck_mission_evidence_policies_average_score",
        ),
        sa.CheckConstraint(
            "min_distinct_sources >= 1",
            name="ck_mission_evidence_policies_distinct_sources",
        ),
        sa.ForeignKeyConstraint(["goal_id"], ["mission_goals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("goal_id", name="uq_mission_evidence_policies_goal"),
    )
    for name in ("mission_id", "goal_id", "created_at"):
        op.create_index(
            f"ix_mission_evidence_policies_{name}",
            "mission_evidence_policies",
            [name],
        )

    op.create_table(
        "mission_evidence",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("goal_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=True),
        sa.Column("evidence_type", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=True),
        sa.Column("submitted_by", sa.String(length=255), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("relevance_score", sa.Float(), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=False),
        sa.Column("verifiability_score", sa.Float(), nullable=False),
        sa.Column("aggregate_score", sa.Float(), nullable=False),
        sa.Column("evaluation_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(length=255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('submitted', 'evaluated', 'accepted', 'rejected', 'needs_review')",
            name="ck_mission_evidence_status",
        ),
        sa.CheckConstraint(
            "relevance_score >= 0 AND relevance_score <= 100",
            name="ck_mission_evidence_relevance",
        ),
        sa.CheckConstraint(
            "quality_score >= 0 AND quality_score <= 100",
            name="ck_mission_evidence_quality",
        ),
        sa.CheckConstraint(
            "verifiability_score >= 0 AND verifiability_score <= 100",
            name="ck_mission_evidence_verifiability",
        ),
        sa.CheckConstraint(
            "aggregate_score >= 0 AND aggregate_score <= 100",
            name="ck_mission_evidence_aggregate",
        ),
        sa.ForeignKeyConstraint(["cycle_id"], ["mission_cycles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["goal_id"], ["mission_goals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("goal_id", "content_hash", name="uq_mission_evidence_goal_hash"),
    )
    for name in (
        "workspace_id", "mission_id", "goal_id", "cycle_id", "evidence_type",
        "source_type", "source_ref", "submitted_by", "content_hash", "status",
        "evaluated_at", "reviewed_by", "created_at",
    ):
        op.create_index(
            f"ix_mission_evidence_{name}",
            "mission_evidence",
            [name],
        )

    op.create_table(
        "mission_goal_confirmations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("goal_id", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_ids_json", sa.JSON(), nullable=False),
        sa.Column("aggregate_score", sa.Float(), nullable=False),
        sa.Column("previous_status", sa.String(length=32), nullable=False),
        sa.Column("new_status", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('confirmed', 'rejected', 'reopened')",
            name="ck_mission_goal_confirmations_decision",
        ),
        sa.CheckConstraint(
            "aggregate_score >= 0 AND aggregate_score <= 100",
            name="ck_mission_goal_confirmations_score",
        ),
        sa.ForeignKeyConstraint(["goal_id"], ["mission_goals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for name in (
        "workspace_id", "mission_id", "goal_id", "decision", "actor_id", "created_at",
    ):
        op.create_index(
            f"ix_mission_goal_confirmations_{name}",
            "mission_goal_confirmations",
            [name],
        )


def downgrade() -> None:
    for name in reversed((
        "workspace_id", "mission_id", "goal_id", "decision", "actor_id", "created_at",
    )):
        op.drop_index(
            f"ix_mission_goal_confirmations_{name}",
            table_name="mission_goal_confirmations",
        )
    op.drop_table("mission_goal_confirmations")

    for name in reversed((
        "workspace_id", "mission_id", "goal_id", "cycle_id", "evidence_type",
        "source_type", "source_ref", "submitted_by", "content_hash", "status",
        "evaluated_at", "reviewed_by", "created_at",
    )):
        op.drop_index(f"ix_mission_evidence_{name}", table_name="mission_evidence")
    op.drop_table("mission_evidence")

    for name in reversed(("mission_id", "goal_id", "created_at")):
        op.drop_index(
            f"ix_mission_evidence_policies_{name}",
            table_name="mission_evidence_policies",
        )
    op.drop_table("mission_evidence_policies")

    for name in reversed((
        "workspace_id", "mission_id", "goal_id", "category", "source_type",
        "source_ref", "archived", "expires_at", "created_by", "created_at",
    )):
        op.drop_index(
            f"ix_mission_memory_entries_{name}",
            table_name="mission_memory_entries",
        )
    op.drop_table("mission_memory_entries")
