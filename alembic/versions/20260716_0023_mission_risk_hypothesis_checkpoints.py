"""Mission risk register, hypotheses and decision checkpoints

Revision ID: 20260716_0023
Revises: 20260716_0022
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260716_0023"
down_revision: Union[str, Sequence[str], None] = "20260716_0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _indexes(table: str, names: tuple[str, ...]) -> None:
    for name in names:
        op.create_index(f"ix_{table}_{name}", table, [name])


def _drop_indexes(table: str, names: tuple[str, ...]) -> None:
    for name in reversed(names):
        op.drop_index(f"ix_{table}_{name}", table_name=table)


def upgrade() -> None:
    op.create_table(
        "mission_risks",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("goal_id", sa.String(length=64), nullable=True),
        sa.Column("risk_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("probability_percent", sa.Float(), nullable=False),
        sa.Column("impact_percent", sa.Float(), nullable=False),
        sa.Column("exposure_score", sa.Float(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=True),
        sa.Column("mitigation_plan", sa.Text(), nullable=False),
        sa.Column("contingency_plan", sa.Text(), nullable=False),
        sa.Column("trigger_indicators_json", sa.JSON(), nullable=False),
        sa.Column("checkpoint_required", sa.Boolean(), nullable=False),
        sa.Column("requires_human_decision", sa.Boolean(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_assessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("materialized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "category IN ('strategic', 'operational', 'technical', 'financial', "
            "'legal', 'safety', 'security', 'schedule', 'quality', "
            "'dependency', 'other')",
            name="ck_mission_risks_category",
        ),
        sa.CheckConstraint(
            "status IN ('identified', 'monitoring', 'mitigated', 'accepted', "
            "'materialized', 'closed')",
            name="ck_mission_risks_status",
        ),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_mission_risks_severity",
        ),
        sa.CheckConstraint(
            "probability_percent >= 0 AND probability_percent <= 100",
            name="ck_mission_risks_probability",
        ),
        sa.CheckConstraint(
            "impact_percent >= 0 AND impact_percent <= 100",
            name="ck_mission_risks_impact",
        ),
        sa.CheckConstraint(
            "exposure_score >= 0 AND exposure_score <= 100",
            name="ck_mission_risks_exposure",
        ),
        sa.CheckConstraint("version >= 1", name="ck_mission_risks_version"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["goal_id"], ["mission_goals.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mission_id", "risk_key", name="uq_mission_risks_key"
        ),
    )
    _indexes(
        "mission_risks",
        (
            "workspace_id",
            "mission_id",
            "goal_id",
            "category",
            "status",
            "exposure_score",
            "severity",
            "owner_id",
            "due_at",
            "next_review_at",
            "last_assessed_at",
            "created_at",
        ),
    )

    op.create_table(
        "mission_risk_assessments",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("risk_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("probability_percent", sa.Float(), nullable=False),
        sa.Column("impact_percent", sa.Float(), nullable=False),
        sa.Column("exposure_score", sa.Float(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("indicators_json", sa.JSON(), nullable=False),
        sa.Column("previous_status", sa.String(length=32), nullable=False),
        sa.Column("new_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('monitor', 'mitigate', 'accept', 'escalate', "
            "'materialize', 'close')",
            name="ck_mission_risk_assessments_decision",
        ),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_mission_risk_assessments_severity",
        ),
        sa.CheckConstraint(
            "probability_percent >= 0 AND probability_percent <= 100",
            name="ck_mission_risk_assessments_probability",
        ),
        sa.CheckConstraint(
            "impact_percent >= 0 AND impact_percent <= 100",
            name="ck_mission_risk_assessments_impact",
        ),
        sa.CheckConstraint(
            "exposure_score >= 0 AND exposure_score <= 100",
            name="ck_mission_risk_assessments_exposure",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["risk_id"], ["mission_risks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes(
        "mission_risk_assessments",
        (
            "workspace_id",
            "mission_id",
            "risk_id",
            "actor_id",
            "decision",
            "severity",
            "created_at",
        ),
    )

    op.create_table(
        "mission_hypotheses",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("goal_id", sa.String(length=64), nullable=True),
        sa.Column("hypothesis_key", sa.String(length=128), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("confidence_percent", sa.Float(), nullable=False),
        sa.Column("target_confidence_percent", sa.Float(), nullable=False),
        sa.Column("min_evidence_count", sa.Integer(), nullable=False),
        sa.Column("support_threshold_percent", sa.Float(), nullable=False),
        sa.Column("reject_threshold_percent", sa.Float(), nullable=False),
        sa.Column("test_plan", sa.Text(), nullable=False),
        sa.Column("success_criteria", sa.Text(), nullable=False),
        sa.Column("failure_criteria", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=True),
        sa.Column("checkpoint_on_inconclusive", sa.Boolean(), nullable=False),
        sa.Column("requires_human_decision", sa.Boolean(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_ids_json", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('proposed', 'testing', 'supported', 'rejected', "
            "'inconclusive', 'invalidated')",
            name="ck_mission_hypotheses_status",
        ),
        sa.CheckConstraint(
            "confidence_percent >= 0 AND confidence_percent <= 100",
            name="ck_mission_hypotheses_confidence",
        ),
        sa.CheckConstraint(
            "target_confidence_percent >= 0 AND target_confidence_percent <= 100",
            name="ck_mission_hypotheses_target_confidence",
        ),
        sa.CheckConstraint(
            "support_threshold_percent >= 0 AND support_threshold_percent <= 100",
            name="ck_mission_hypotheses_support_threshold",
        ),
        sa.CheckConstraint(
            "reject_threshold_percent >= 0 AND reject_threshold_percent <= 100",
            name="ck_mission_hypotheses_reject_threshold",
        ),
        sa.CheckConstraint(
            "reject_threshold_percent < support_threshold_percent",
            name="ck_mission_hypotheses_threshold_order",
        ),
        sa.CheckConstraint(
            "min_evidence_count >= 1",
            name="ck_mission_hypotheses_min_evidence",
        ),
        sa.CheckConstraint("version >= 1", name="ck_mission_hypotheses_version"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["goal_id"], ["mission_goals.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mission_id", "hypothesis_key", name="uq_mission_hypotheses_key"
        ),
    )
    _indexes(
        "mission_hypotheses",
        (
            "workspace_id",
            "mission_id",
            "goal_id",
            "status",
            "owner_id",
            "due_at",
            "evaluated_at",
            "created_at",
        ),
    )

    op.create_table(
        "mission_hypothesis_evaluations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("hypothesis_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("confidence_percent", sa.Float(), nullable=False),
        sa.Column("support_score_percent", sa.Float(), nullable=False),
        sa.Column("evidence_ids_json", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("calculation_json", sa.JSON(), nullable=False),
        sa.Column("previous_status", sa.String(length=32), nullable=False),
        sa.Column("new_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('support', 'reject', 'inconclusive', 'invalidate', 'reopen')",
            name="ck_mission_hypothesis_evaluations_decision",
        ),
        sa.CheckConstraint(
            "confidence_percent >= 0 AND confidence_percent <= 100",
            name="ck_mission_hypothesis_evaluations_confidence",
        ),
        sa.CheckConstraint(
            "support_score_percent >= 0 AND support_score_percent <= 100",
            name="ck_mission_hypothesis_evaluations_support_score",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["hypothesis_id"], ["mission_hypotheses.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes(
        "mission_hypothesis_evaluations",
        (
            "workspace_id",
            "mission_id",
            "hypothesis_id",
            "actor_id",
            "decision",
            "created_at",
        ),
    )

    op.create_table(
        "mission_decision_checkpoints",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("goal_id", sa.String(length=64), nullable=True),
        sa.Column("cycle_id", sa.String(length=64), nullable=True),
        sa.Column("risk_id", sa.String(length=64), nullable=True),
        sa.Column("hypothesis_id", sa.String(length=64), nullable=True),
        sa.Column("checkpoint_key", sa.String(length=128), nullable=False),
        sa.Column("checkpoint_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("blocking", sa.Boolean(), nullable=False),
        sa.Column("requires_human", sa.Boolean(), nullable=False),
        sa.Column("auto_decision_enabled", sa.Boolean(), nullable=False),
        sa.Column("auto_decision_threshold_percent", sa.Float(), nullable=False),
        sa.Column("recommended_decision", sa.String(length=32), nullable=True),
        sa.Column("recommendation_confidence_percent", sa.Float(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=True),
        sa.Column("decision_rationale", sa.Text(), nullable=True),
        sa.Column("selected_option", sa.String(length=255), nullable=True),
        sa.Column("trigger_conditions_json", sa.JSON(), nullable=False),
        sa.Column("options_json", sa.JSON(), nullable=False),
        sa.Column("context_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "checkpoint_type IN ('manual', 'scheduled', 'risk', 'hypothesis', "
            "'deadline', 'budget', 'phase_gate')",
            name="ck_mission_checkpoints_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'resolved', 'deferred', "
            "'expired', 'cancelled')",
            name="ck_mission_checkpoints_status",
        ),
        sa.CheckConstraint(
            "recommended_decision IS NULL OR recommended_decision IN "
            "('continue', 'pause', 'replan', 'cancel', 'accept_risk', "
            "'request_review', 'defer')",
            name="ck_mission_checkpoints_recommended_decision",
        ),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('continue', 'pause', 'replan', "
            "'cancel', 'accept_risk', 'request_review', 'defer')",
            name="ck_mission_checkpoints_decision",
        ),
        sa.CheckConstraint(
            "auto_decision_threshold_percent >= 0 AND "
            "auto_decision_threshold_percent <= 100",
            name="ck_mission_checkpoints_auto_threshold",
        ),
        sa.CheckConstraint(
            "recommendation_confidence_percent >= 0 AND "
            "recommendation_confidence_percent <= 100",
            name="ck_mission_checkpoints_recommendation_confidence",
        ),
        sa.CheckConstraint("version >= 1", name="ck_mission_checkpoints_version"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["goal_id"], ["mission_goals.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["mission_cycles.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["risk_id"], ["mission_risks.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["hypothesis_id"], ["mission_hypotheses.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mission_id",
            "checkpoint_key",
            name="uq_mission_checkpoints_key",
        ),
    )
    _indexes(
        "mission_decision_checkpoints",
        (
            "workspace_id",
            "mission_id",
            "goal_id",
            "cycle_id",
            "risk_id",
            "hypothesis_id",
            "checkpoint_type",
            "status",
            "recommended_decision",
            "due_at",
            "expires_at",
            "triggered_at",
            "decided_by",
            "decision",
            "created_at",
        ),
    )

    op.create_table(
        "mission_checkpoint_decisions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("selected_option", sa.String(length=255), nullable=True),
        sa.Column("previous_status", sa.String(length=32), nullable=False),
        sa.Column("new_status", sa.String(length=32), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('continue', 'pause', 'replan', 'cancel', "
            "'accept_risk', 'request_review', 'defer')",
            name="ck_mission_checkpoint_decisions_decision",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["checkpoint_id"],
            ["mission_decision_checkpoints.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes(
        "mission_checkpoint_decisions",
        (
            "workspace_id",
            "mission_id",
            "checkpoint_id",
            "actor_id",
            "decision",
            "created_at",
        ),
    )


def downgrade() -> None:
    _drop_indexes(
        "mission_checkpoint_decisions",
        (
            "workspace_id",
            "mission_id",
            "checkpoint_id",
            "actor_id",
            "decision",
            "created_at",
        ),
    )
    op.drop_table("mission_checkpoint_decisions")

    _drop_indexes(
        "mission_decision_checkpoints",
        (
            "workspace_id",
            "mission_id",
            "goal_id",
            "cycle_id",
            "risk_id",
            "hypothesis_id",
            "checkpoint_type",
            "status",
            "recommended_decision",
            "due_at",
            "expires_at",
            "triggered_at",
            "decided_by",
            "decision",
            "created_at",
        ),
    )
    op.drop_table("mission_decision_checkpoints")

    _drop_indexes(
        "mission_hypothesis_evaluations",
        (
            "workspace_id",
            "mission_id",
            "hypothesis_id",
            "actor_id",
            "decision",
            "created_at",
        ),
    )
    op.drop_table("mission_hypothesis_evaluations")

    _drop_indexes(
        "mission_hypotheses",
        (
            "workspace_id",
            "mission_id",
            "goal_id",
            "status",
            "owner_id",
            "due_at",
            "evaluated_at",
            "created_at",
        ),
    )
    op.drop_table("mission_hypotheses")

    _drop_indexes(
        "mission_risk_assessments",
        (
            "workspace_id",
            "mission_id",
            "risk_id",
            "actor_id",
            "decision",
            "severity",
            "created_at",
        ),
    )
    op.drop_table("mission_risk_assessments")

    _drop_indexes(
        "mission_risks",
        (
            "workspace_id",
            "mission_id",
            "goal_id",
            "category",
            "status",
            "exposure_score",
            "severity",
            "owner_id",
            "due_at",
            "next_review_at",
            "last_assessed_at",
            "created_at",
        ),
    )
    op.drop_table("mission_risks")
