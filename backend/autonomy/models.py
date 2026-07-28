from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class AutonomousWorkspacePolicyModel(Base):
    __tablename__ = "autonomous_workspace_policies"
    __table_args__ = (
        CheckConstraint(
            "autonomy_mode IN ('observe', 'supervised', 'autonomous')",
            name="ck_autonomous_workspace_policies_mode",
        ),
        CheckConstraint(
            "max_active_missions >= 1",
            name="ck_autonomous_workspace_policies_max_missions",
        ),
        CheckConstraint(
            "max_parallel_cycles >= 1",
            name="ck_autonomous_workspace_policies_max_cycles",
        ),
        CheckConstraint(
            "cycle_interval_seconds >= 30",
            name="ck_autonomous_workspace_policies_interval",
        ),
        UniqueConstraint(
            "workspace_id",
            name="uq_autonomous_workspace_policies_workspace",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("autopol"),
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    autonomy_mode: Mapped[str] = mapped_column(
        String(32),
        default="supervised",
        nullable=False,
        index=True,
    )
    max_active_missions: Mapped[int] = mapped_column(
        Integer,
        default=3,
        nullable=False,
    )
    max_parallel_cycles: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    cycle_interval_seconds: Mapped[int] = mapped_column(
        Integer,
        default=3600,
        nullable=False,
    )
    require_user_approval: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    allow_auto_start: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    planner_ref: Mapped[str] = mapped_column(
        String(255),
        default="builtin.planner",
        nullable=False,
    )
    planning_mode: Mapped[str] = mapped_column(
        String(32),
        default="balanced",
        nullable=False,
    )
    auto_validate: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_assign: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    strict_assignment: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_review: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    review_threshold: Mapped[int] = mapped_column(Integer, default=80, nullable=False)
    auto_fix_review: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_review_rounds: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    require_review_pass: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class WorkspaceMissionModel(Base):
    __tablename__ = "workspace_missions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'active', 'paused', 'completed', 'failed', 'cancelled')",
            name="ck_workspace_missions_status",
        ),
        CheckConstraint(
            "priority >= 0 AND priority <= 100",
            name="ck_workspace_missions_priority",
        ),
        CheckConstraint(
            "progress_percent >= 0 AND progress_percent <= 100",
            name="ck_workspace_missions_progress",
        ),
        CheckConstraint(
            "max_cycles >= 1",
            name="ck_workspace_missions_max_cycles",
        ),
        CheckConstraint(
            "cycle_count >= 0",
            name="ck_workspace_missions_cycle_count",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("mission"),
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    success_criteria: Mapped[str] = mapped_column(Text, default="", nullable=False)
    strategy_hint: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        default="draft",
        nullable=False,
        index=True,
    )
    priority: Mapped[int] = mapped_column(Integer, default=50, nullable=False, index=True)
    planner_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    planning_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    auto_start_plans: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    max_cycles: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    cycle_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    current_cycle_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    next_cycle_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    pause_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    goals: Mapped[list["MissionGoalModel"]] = relationship(
        back_populates="mission",
        cascade="all, delete-orphan",
        order_by="MissionGoalModel.sequence, MissionGoalModel.goal_key",
    )
    cycles: Mapped[list["MissionCycleModel"]] = relationship(
        back_populates="mission",
        cascade="all, delete-orphan",
        order_by="MissionCycleModel.cycle_number",
    )


class MissionGoalModel(Base):
    __tablename__ = "mission_goals"
    __table_args__ = (
        UniqueConstraint("mission_id", "goal_key", name="uq_mission_goals_key"),
        CheckConstraint(
            "status IN ('pending', 'active', 'achieved', 'failed', 'cancelled')",
            name="ck_mission_goals_status",
        ),
        CheckConstraint("weight > 0", name="ck_mission_goals_weight"),
        CheckConstraint(
            "progress_percent >= 0 AND progress_percent <= 100",
            name="ck_mission_goals_progress",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("goal"),
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    goal_key: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    success_criteria: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    progress_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    depends_on_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    mission: Mapped[WorkspaceMissionModel] = relationship(back_populates="goals")


class MissionCycleModel(Base):
    __tablename__ = "mission_cycles"
    __table_args__ = (
        UniqueConstraint("mission_id", "cycle_number", name="uq_mission_cycles_number"),
        UniqueConstraint("idempotency_key", name="uq_mission_cycles_idempotency"),
        CheckConstraint(
            "status IN ('queued', 'planning', 'ready', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_mission_cycles_status",
        ),
        CheckConstraint(
            "trigger IN ('manual', 'scheduled', 'event', 'recovery')",
            name="ck_mission_cycles_trigger",
        ),
        CheckConstraint("cycle_number >= 1", name="ck_mission_cycles_number"),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("mcycle"),
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cycle_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    trigger: Mapped[str] = mapped_column(String(32), default="manual", nullable=False, index=True)
    goal_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    planner_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    execution_plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    mission: Mapped[WorkspaceMissionModel] = relationship(back_populates="cycles")


class MissionProgressUpdateModel(Base):
    __tablename__ = "mission_progress_updates"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("mprogress"),
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    goal_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_goals.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    cycle_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_cycles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    previous_progress_percent: Mapped[float] = mapped_column(Float, nullable=False)
    new_progress_percent: Mapped[float] = mapped_column(Float, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )


class MissionMemoryEntryModel(Base):
    __tablename__ = "mission_memory_entries"
    __table_args__ = (
        UniqueConstraint(
            "mission_id",
            "memory_key",
            name="uq_mission_memory_entries_key",
        ),
        CheckConstraint(
            "category IN ('fact', 'decision', 'artifact', 'lesson', 'constraint', 'summary')",
            name="ck_mission_memory_entries_category",
        ),
        CheckConstraint(
            "importance_score >= 0 AND importance_score <= 100",
            name="ck_mission_memory_entries_importance",
        ),
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 100",
            name="ck_mission_memory_entries_confidence",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_mission_memory_entries_version",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("mmemory"),
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    goal_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_goals.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    memory_key: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(
        String(32),
        default="fact",
        nullable=False,
        index=True,
    )
    content_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(
        String(64),
        default="manual",
        nullable=False,
        index=True,
    )
    source_ref: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    importance_score: Mapped[int] = mapped_column(
        Integer,
        default=50,
        nullable=False,
    )
    confidence_score: Mapped[int] = mapped_column(
        Integer,
        default=50,
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    archived: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    created_by: Mapped[str] = mapped_column(
        String(255),
        default="user",
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class MissionEvidencePolicyModel(Base):
    __tablename__ = "mission_evidence_policies"
    __table_args__ = (
        UniqueConstraint(
            "goal_id",
            name="uq_mission_evidence_policies_goal",
        ),
        CheckConstraint(
            "required_evidence_count >= 1",
            name="ck_mission_evidence_policies_required_count",
        ),
        CheckConstraint(
            "min_individual_score >= 0 AND min_individual_score <= 100",
            name="ck_mission_evidence_policies_individual_score",
        ),
        CheckConstraint(
            "min_average_score >= 0 AND min_average_score <= 100",
            name="ck_mission_evidence_policies_average_score",
        ),
        CheckConstraint(
            "min_distinct_sources >= 1",
            name="ck_mission_evidence_policies_distinct_sources",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("mepolicy"),
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    goal_id: Mapped[str] = mapped_column(
        ForeignKey("mission_goals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    required_evidence_count: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    min_individual_score: Mapped[float] = mapped_column(
        Float,
        default=70.0,
        nullable=False,
    )
    min_average_score: Mapped[float] = mapped_column(
        Float,
        default=75.0,
        nullable=False,
    )
    require_distinct_sources: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    min_distinct_sources: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    require_human_review: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    auto_confirm_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    allowed_evidence_types_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class MissionEvidenceModel(Base):
    __tablename__ = "mission_evidence"
    __table_args__ = (
        UniqueConstraint(
            "goal_id",
            "content_hash",
            name="uq_mission_evidence_goal_hash",
        ),
        CheckConstraint(
            "status IN ('submitted', 'evaluated', 'accepted', 'rejected', 'needs_review')",
            name="ck_mission_evidence_status",
        ),
        CheckConstraint(
            "relevance_score >= 0 AND relevance_score <= 100",
            name="ck_mission_evidence_relevance",
        ),
        CheckConstraint(
            "quality_score >= 0 AND quality_score <= 100",
            name="ck_mission_evidence_quality",
        ),
        CheckConstraint(
            "verifiability_score >= 0 AND verifiability_score <= 100",
            name="ck_mission_evidence_verifiability",
        ),
        CheckConstraint(
            "aggregate_score >= 0 AND aggregate_score <= 100",
            name="ck_mission_evidence_aggregate",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("evidence"),
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    goal_id: Mapped[str] = mapped_column(
        ForeignKey("mission_goals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cycle_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_cycles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    evidence_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(
        String(64),
        default="manual",
        nullable=False,
        index=True,
    )
    source_ref: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    submitted_by: Mapped[str] = mapped_column(
        String(255),
        default="user",
        nullable=False,
        index=True,
    )
    content_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32),
        default="submitted",
        nullable=False,
        index=True,
    )
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    verifiability_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    aggregate_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evaluation_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    reviewed_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class MissionGoalConfirmationModel(Base):
    __tablename__ = "mission_goal_confirmations"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('confirmed', 'rejected', 'reopened')",
            name="ck_mission_goal_confirmations_decision",
        ),
        CheckConstraint(
            "aggregate_score >= 0 AND aggregate_score <= 100",
            name="ck_mission_goal_confirmations_score",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("gconfirm"),
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    goal_id: Mapped[str] = mapped_column(
        ForeignKey("mission_goals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    aggregate_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    previous_status: Mapped[str] = mapped_column(String(32), nullable=False)
    new_status: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )


class MissionRiskModel(Base):
    __tablename__ = "mission_risks"
    __table_args__ = (
        UniqueConstraint("mission_id", "risk_key", name="uq_mission_risks_key"),
        CheckConstraint(
            "category IN ('strategic', 'operational', 'technical', 'financial', "
            "'legal', 'safety', 'security', 'schedule', 'quality', "
            "'dependency', 'other')",
            name="ck_mission_risks_category",
        ),
        CheckConstraint(
            "status IN ('identified', 'monitoring', 'mitigated', 'accepted', "
            "'materialized', 'closed')",
            name="ck_mission_risks_status",
        ),
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_mission_risks_severity",
        ),
        CheckConstraint(
            "probability_percent >= 0 AND probability_percent <= 100",
            name="ck_mission_risks_probability",
        ),
        CheckConstraint(
            "impact_percent >= 0 AND impact_percent <= 100",
            name="ck_mission_risks_impact",
        ),
        CheckConstraint(
            "exposure_score >= 0 AND exposure_score <= 100",
            name="ck_mission_risks_exposure",
        ),
        CheckConstraint("version >= 1", name="ck_mission_risks_version"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("mrisk")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    goal_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_goals.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    risk_key: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    category: Mapped[str] = mapped_column(
        String(32), default="other", nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default="identified", nullable=False, index=True
    )
    probability_percent: Mapped[float] = mapped_column(
        Float, default=25.0, nullable=False
    )
    impact_percent: Mapped[float] = mapped_column(
        Float, default=25.0, nullable=False
    )
    exposure_score: Mapped[float] = mapped_column(
        Float, default=6.25, nullable=False, index=True
    )
    severity: Mapped[str] = mapped_column(
        String(16), default="low", nullable=False, index=True
    )
    owner_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    mitigation_plan: Mapped[str] = mapped_column(Text, default="", nullable=False)
    contingency_plan: Mapped[str] = mapped_column(Text, default="", nullable=False)
    trigger_indicators_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    checkpoint_required: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    requires_human_decision: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    next_review_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_assessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    materialized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class MissionRiskAssessmentModel(Base):
    __tablename__ = "mission_risk_assessments"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('monitor', 'mitigate', 'accept', 'escalate', "
            "'materialize', 'close')",
            name="ck_mission_risk_assessments_decision",
        ),
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_mission_risk_assessments_severity",
        ),
        CheckConstraint(
            "probability_percent >= 0 AND probability_percent <= 100",
            name="ck_mission_risk_assessments_probability",
        ),
        CheckConstraint(
            "impact_percent >= 0 AND impact_percent <= 100",
            name="ck_mission_risk_assessments_impact",
        ),
        CheckConstraint(
            "exposure_score >= 0 AND exposure_score <= 100",
            name="ck_mission_risk_assessments_exposure",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("rassess")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    risk_id: Mapped[str] = mapped_column(
        ForeignKey("mission_risks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    probability_percent: Mapped[float] = mapped_column(Float, nullable=False)
    impact_percent: Mapped[float] = mapped_column(Float, nullable=False)
    exposure_score: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    indicators_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    previous_status: Mapped[str] = mapped_column(String(32), nullable=False)
    new_status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class MissionHypothesisModel(Base):
    __tablename__ = "mission_hypotheses"
    __table_args__ = (
        UniqueConstraint(
            "mission_id", "hypothesis_key", name="uq_mission_hypotheses_key"
        ),
        CheckConstraint(
            "status IN ('proposed', 'testing', 'supported', 'rejected', "
            "'inconclusive', 'invalidated')",
            name="ck_mission_hypotheses_status",
        ),
        CheckConstraint(
            "confidence_percent >= 0 AND confidence_percent <= 100",
            name="ck_mission_hypotheses_confidence",
        ),
        CheckConstraint(
            "target_confidence_percent >= 0 AND target_confidence_percent <= 100",
            name="ck_mission_hypotheses_target_confidence",
        ),
        CheckConstraint(
            "support_threshold_percent >= 0 AND support_threshold_percent <= 100",
            name="ck_mission_hypotheses_support_threshold",
        ),
        CheckConstraint(
            "reject_threshold_percent >= 0 AND reject_threshold_percent <= 100",
            name="ck_mission_hypotheses_reject_threshold",
        ),
        CheckConstraint(
            "reject_threshold_percent < support_threshold_percent",
            name="ck_mission_hypotheses_threshold_order",
        ),
        CheckConstraint(
            "min_evidence_count >= 1",
            name="ck_mission_hypotheses_min_evidence",
        ),
        CheckConstraint("version >= 1", name="ck_mission_hypotheses_version"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hypothesis")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    goal_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_goals.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    hypothesis_key: Mapped[str] = mapped_column(String(128), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="proposed", nullable=False, index=True
    )
    confidence_percent: Mapped[float] = mapped_column(
        Float, default=50.0, nullable=False
    )
    target_confidence_percent: Mapped[float] = mapped_column(
        Float, default=80.0, nullable=False
    )
    min_evidence_count: Mapped[int] = mapped_column(
        Integer, default=2, nullable=False
    )
    support_threshold_percent: Mapped[float] = mapped_column(
        Float, default=70.0, nullable=False
    )
    reject_threshold_percent: Mapped[float] = mapped_column(
        Float, default=30.0, nullable=False
    )
    test_plan: Mapped[str] = mapped_column(Text, default="", nullable=False)
    success_criteria: Mapped[str] = mapped_column(Text, default="", nullable=False)
    failure_criteria: Mapped[str] = mapped_column(Text, default="", nullable=False)
    owner_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    checkpoint_on_inconclusive: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    requires_human_decision: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    evidence_ids_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class MissionHypothesisEvaluationModel(Base):
    __tablename__ = "mission_hypothesis_evaluations"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('support', 'reject', 'inconclusive', 'invalidate', 'reopen')",
            name="ck_mission_hypothesis_evaluations_decision",
        ),
        CheckConstraint(
            "confidence_percent >= 0 AND confidence_percent <= 100",
            name="ck_mission_hypothesis_evaluations_confidence",
        ),
        CheckConstraint(
            "support_score_percent >= 0 AND support_score_percent <= 100",
            name="ck_mission_hypothesis_evaluations_support_score",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("heval")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    hypothesis_id: Mapped[str] = mapped_column(
        ForeignKey("mission_hypotheses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    confidence_percent: Mapped[float] = mapped_column(Float, nullable=False)
    support_score_percent: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_ids_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    calculation_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    previous_status: Mapped[str] = mapped_column(String(32), nullable=False)
    new_status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class MissionDecisionCheckpointModel(Base):
    __tablename__ = "mission_decision_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "mission_id", "checkpoint_key", name="uq_mission_checkpoints_key"
        ),
        CheckConstraint(
            "checkpoint_type IN ('manual', 'scheduled', 'risk', 'hypothesis', "
            "'deadline', 'budget', 'phase_gate')",
            name="ck_mission_checkpoints_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'ready', 'resolved', 'deferred', "
            "'expired', 'cancelled')",
            name="ck_mission_checkpoints_status",
        ),
        CheckConstraint(
            "recommended_decision IS NULL OR recommended_decision IN "
            "('continue', 'pause', 'replan', 'cancel', 'accept_risk', "
            "'request_review', 'defer')",
            name="ck_mission_checkpoints_recommended_decision",
        ),
        CheckConstraint(
            "decision IS NULL OR decision IN ('continue', 'pause', 'replan', "
            "'cancel', 'accept_risk', 'request_review', 'defer')",
            name="ck_mission_checkpoints_decision",
        ),
        CheckConstraint(
            "auto_decision_threshold_percent >= 0 AND "
            "auto_decision_threshold_percent <= 100",
            name="ck_mission_checkpoints_auto_threshold",
        ),
        CheckConstraint(
            "recommendation_confidence_percent >= 0 AND "
            "recommendation_confidence_percent <= 100",
            name="ck_mission_checkpoints_recommendation_confidence",
        ),
        CheckConstraint("version >= 1", name="ck_mission_checkpoints_version"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("checkpoint")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    goal_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_goals.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    cycle_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_cycles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    risk_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_risks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    hypothesis_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_hypotheses.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    checkpoint_key: Mapped[str] = mapped_column(String(128), nullable=False)
    checkpoint_type: Mapped[str] = mapped_column(
        String(32), default="manual", nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False, index=True
    )
    blocking: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    requires_human: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_decision_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    auto_decision_threshold_percent: Mapped[float] = mapped_column(
        Float, default=90.0, nullable=False
    )
    recommended_decision: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True
    )
    recommendation_confidence_percent: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_by: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    decision: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True
    )
    decision_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_option: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trigger_conditions_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    options_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    context_snapshot_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class MissionCheckpointDecisionModel(Base):
    __tablename__ = "mission_checkpoint_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('continue', 'pause', 'replan', 'cancel', "
            "'accept_risk', 'request_review', 'defer')",
            name="ck_mission_checkpoint_decisions_decision",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("cpdecision")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    checkpoint_id: Mapped[str] = mapped_column(
        ForeignKey("mission_decision_checkpoints.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    selected_option: Mapped[str | None] = mapped_column(String(255), nullable=True)
    previous_status: Mapped[str] = mapped_column(String(32), nullable=False)
    new_status: Mapped[str] = mapped_column(String(32), nullable=False)
    context_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class MissionStrategyPolicyModel(Base):
    __tablename__ = "mission_strategy_policies"
    __table_args__ = (
        UniqueConstraint(
            "mission_id",
            name="uq_mission_strategy_policies_mission",
        ),
        CheckConstraint(
            "selection_mode IN ('manual', 'weighted', 'adaptive')",
            name="ck_mission_strategy_policies_mode",
        ),
        CheckConstraint(
            "min_selection_score >= 0 AND min_selection_score <= 100",
            name="ck_mission_strategy_policies_min_score",
        ),
        CheckConstraint(
            "min_improvement_percent >= 0 AND min_improvement_percent <= 100",
            name="ck_mission_strategy_policies_min_improvement",
        ),
        CheckConstraint(
            "exploration_weight_percent >= 0 AND exploration_weight_percent <= 50",
            name="ck_mission_strategy_policies_exploration_weight",
        ),
        CheckConstraint(
            "performance_weight_percent >= 0 AND performance_weight_percent <= 50",
            name="ck_mission_strategy_policies_performance_weight",
        ),
        CheckConstraint(
            "cooldown_cycles >= 0",
            name="ck_mission_strategy_policies_cooldown",
        ),
        CheckConstraint(
            "max_candidates >= 1",
            name="ck_mission_strategy_policies_max_candidates",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("stratpol")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    selection_mode: Mapped[str] = mapped_column(
        String(32), default="manual", nullable=False, index=True
    )
    require_human_selection: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    auto_selection_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    min_selection_score: Mapped[float] = mapped_column(
        Float, default=60.0, nullable=False
    )
    min_improvement_percent: Mapped[float] = mapped_column(
        Float, default=5.0, nullable=False
    )
    exploration_weight_percent: Mapped[float] = mapped_column(
        Float, default=8.0, nullable=False
    )
    performance_weight_percent: Mapped[float] = mapped_column(
        Float, default=20.0, nullable=False
    )
    cooldown_cycles: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    max_candidates: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class MissionStrategyModel(Base):
    __tablename__ = "mission_strategies"
    __table_args__ = (
        UniqueConstraint(
            "mission_id", "strategy_key", name="uq_mission_strategies_key"
        ),
        CheckConstraint(
            "status IN ('draft', 'candidate', 'selected', 'paused', "
            "'retired', 'rejected')",
            name="ck_mission_strategies_status",
        ),
        CheckConstraint(
            "priority >= 0 AND priority <= 100",
            name="ck_mission_strategies_priority",
        ),
        CheckConstraint(
            "expected_value_percent >= 0 AND expected_value_percent <= 100",
            name="ck_mission_strategies_expected_value",
        ),
        CheckConstraint(
            "success_probability_percent >= 0 AND success_probability_percent <= 100",
            name="ck_mission_strategies_success_probability",
        ),
        CheckConstraint(
            "strategic_fit_percent >= 0 AND strategic_fit_percent <= 100",
            name="ck_mission_strategies_strategic_fit",
        ),
        CheckConstraint(
            "feasibility_percent >= 0 AND feasibility_percent <= 100",
            name="ck_mission_strategies_feasibility",
        ),
        CheckConstraint(
            "evidence_confidence_percent >= 0 AND evidence_confidence_percent <= 100",
            name="ck_mission_strategies_evidence_confidence",
        ),
        CheckConstraint(
            "risk_percent >= 0 AND risk_percent <= 100",
            name="ck_mission_strategies_risk",
        ),
        CheckConstraint(
            "cost_percent >= 0 AND cost_percent <= 100",
            name="ck_mission_strategies_cost",
        ),
        CheckConstraint(
            "duration_percent >= 0 AND duration_percent <= 100",
            name="ck_mission_strategies_duration",
        ),
        CheckConstraint(
            "base_score >= 0 AND base_score <= 100",
            name="ck_mission_strategies_base_score",
        ),
        CheckConstraint(
            "adaptive_score >= 0 AND adaptive_score <= 100",
            name="ck_mission_strategies_adaptive_score",
        ),
        CheckConstraint(
            "average_reward_percent >= 0 AND average_reward_percent <= 100",
            name="ck_mission_strategies_average_reward",
        ),
        CheckConstraint(
            "trial_count >= 0 AND success_count >= 0 AND failure_count >= 0",
            name="ck_mission_strategies_counts",
        ),
        CheckConstraint("version >= 1", name="ck_mission_strategies_version"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("strategy")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    hypothesis_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_hypotheses.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    strategy_key: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    strategy_hint: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="candidate", nullable=False, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, default=50, nullable=False, index=True)
    expected_value_percent: Mapped[float] = mapped_column(
        Float, default=50.0, nullable=False
    )
    success_probability_percent: Mapped[float] = mapped_column(
        Float, default=50.0, nullable=False
    )
    strategic_fit_percent: Mapped[float] = mapped_column(
        Float, default=50.0, nullable=False
    )
    feasibility_percent: Mapped[float] = mapped_column(
        Float, default=50.0, nullable=False
    )
    evidence_confidence_percent: Mapped[float] = mapped_column(
        Float, default=50.0, nullable=False
    )
    risk_percent: Mapped[float] = mapped_column(Float, default=50.0, nullable=False)
    cost_percent: Mapped[float] = mapped_column(Float, default=50.0, nullable=False)
    duration_percent: Mapped[float] = mapped_column(
        Float, default=50.0, nullable=False
    )
    base_score: Mapped[float] = mapped_column(
        Float, default=50.0, nullable=False, index=True
    )
    adaptive_score: Mapped[float] = mapped_column(
        Float, default=50.0, nullable=False, index=True
    )
    trial_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    average_reward_percent: Mapped[float] = mapped_column(
        Float, default=50.0, nullable=False
    )
    constraints_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    tags_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    selected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_selected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class MissionStrategyEvaluationModel(Base):
    __tablename__ = "mission_strategy_evaluations"
    __table_args__ = (
        CheckConstraint(
            "evaluation_type IN ('initial', 'manual', 'adaptive', 'feedback')",
            name="ck_mission_strategy_evaluations_type",
        ),
        CheckConstraint(
            "base_score >= 0 AND base_score <= 100",
            name="ck_mission_strategy_evaluations_base_score",
        ),
        CheckConstraint(
            "adaptive_score >= 0 AND adaptive_score <= 100",
            name="ck_mission_strategy_evaluations_adaptive_score",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("strateval")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    strategy_id: Mapped[str] = mapped_column(
        ForeignKey("mission_strategies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    evaluation_type: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )
    expected_value_percent: Mapped[float] = mapped_column(Float, nullable=False)
    success_probability_percent: Mapped[float] = mapped_column(Float, nullable=False)
    strategic_fit_percent: Mapped[float] = mapped_column(Float, nullable=False)
    feasibility_percent: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_confidence_percent: Mapped[float] = mapped_column(Float, nullable=False)
    risk_percent: Mapped[float] = mapped_column(Float, nullable=False)
    cost_percent: Mapped[float] = mapped_column(Float, nullable=False)
    duration_percent: Mapped[float] = mapped_column(Float, nullable=False)
    base_score: Mapped[float] = mapped_column(Float, nullable=False)
    adaptive_score: Mapped[float] = mapped_column(Float, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    calculation_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    context_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class MissionStrategySelectionModel(Base):
    __tablename__ = "mission_strategy_selections"
    __table_args__ = (
        CheckConstraint(
            "selection_mode IN ('manual', 'weighted', 'adaptive')",
            name="ck_mission_strategy_selections_mode",
        ),
        CheckConstraint(
            "score_at_selection >= 0 AND score_at_selection <= 100",
            name="ck_mission_strategy_selections_score",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("stratsel")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    strategy_id: Mapped[str] = mapped_column(
        ForeignKey("mission_strategies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    previous_strategy_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_strategies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    selection_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )
    score_at_selection: Mapped[float] = mapped_column(Float, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class MissionStrategyAssignmentModel(Base):
    __tablename__ = "mission_strategy_assignments"
    __table_args__ = (
        UniqueConstraint(
            "cycle_id", name="uq_mission_strategy_assignments_cycle"
        ),
        CheckConstraint(
            "status IN ('planned', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_mission_strategy_assignments_status",
        ),
        CheckConstraint(
            "selection_mode IN ('manual', 'weighted', 'adaptive')",
            name="ck_mission_strategy_assignments_mode",
        ),
        CheckConstraint(
            "score_at_assignment >= 0 AND score_at_assignment <= 100",
            name="ck_mission_strategy_assignments_score",
        ),
        CheckConstraint(
            "reward_percent IS NULL OR (reward_percent >= 0 AND reward_percent <= 100)",
            name="ck_mission_strategy_assignments_reward",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("stratassign")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cycle_id: Mapped[str] = mapped_column(
        ForeignKey("mission_cycles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    strategy_id: Mapped[str] = mapped_column(
        ForeignKey("mission_strategies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), default="planned", nullable=False, index=True
    )
    selection_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )
    selected_by: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    score_at_assignment: Mapped[float] = mapped_column(Float, nullable=False)
    selection_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    reward_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class MissionResourcePolicyModel(Base):
    __tablename__ = "mission_resource_policies"
    __table_args__ = (
        UniqueConstraint("mission_id", name="uq_mission_resource_policies_mission"),
        CheckConstraint(
            "allocation_mode IN ('manual', 'balanced', 'adaptive')",
            name="ck_mission_resource_policies_mode",
        ),
        CheckConstraint("total_budget_usd >= 0", name="ck_mission_resource_policies_total_budget"),
        CheckConstraint("default_cycle_budget_usd >= 0", name="ck_mission_resource_policies_default_cycle_budget"),
        CheckConstraint("max_cycle_budget_usd >= 0", name="ck_mission_resource_policies_max_cycle_budget"),
        CheckConstraint(
            "reserve_percent >= 0 AND reserve_percent <= 100",
            name="ck_mission_resource_policies_reserve_percent",
        ),
        CheckConstraint("max_parallel_cycles >= 1", name="ck_mission_resource_policies_parallel_cycles"),
        CheckConstraint("agent_slots >= 1", name="ck_mission_resource_policies_agent_slots"),
        CheckConstraint("tool_slots >= 1", name="ck_mission_resource_policies_tool_slots"),
        CheckConstraint("compute_units >= 0", name="ck_mission_resource_policies_compute_units"),
        CheckConstraint("planning_horizon_cycles >= 1", name="ck_mission_resource_policies_horizon"),
        CheckConstraint(
            "min_rebalance_improvement_percent >= 0 AND min_rebalance_improvement_percent <= 100",
            name="ck_mission_resource_policies_rebalance_improvement",
        ),
        CheckConstraint(
            "overrun_tolerance_percent >= 0 AND overrun_tolerance_percent <= 100",
            name="ck_mission_resource_policies_overrun_tolerance",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("respol")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allocation_mode: Mapped[str] = mapped_column(
        String(32), default="manual", nullable=False, index=True
    )
    require_human_approval: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    auto_allocation_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    auto_rebalance_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    total_budget_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    default_cycle_budget_usd: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    max_cycle_budget_usd: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    reserve_percent: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
    max_parallel_cycles: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    agent_slots: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    tool_slots: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    compute_units: Mapped[float] = mapped_column(Float, default=4.0, nullable=False)
    planning_horizon_cycles: Mapped[int] = mapped_column(
        Integer, default=10, nullable=False
    )
    min_rebalance_improvement_percent: Mapped[float] = mapped_column(
        Float, default=10.0, nullable=False
    )
    overrun_tolerance_percent: Mapped[float] = mapped_column(
        Float, default=10.0, nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class MissionResourceAllocationModel(Base):
    __tablename__ = "mission_resource_allocations"
    __table_args__ = (
        UniqueConstraint("cycle_id", name="uq_mission_resource_allocations_cycle"),
        CheckConstraint(
            "status IN ('pending_approval', 'approved', 'reserved', 'active', "
            "'released', 'consumed', 'exceeded', 'cancelled')",
            name="ck_mission_resource_allocations_status",
        ),
        CheckConstraint("budget_usd >= 0", name="ck_mission_resource_allocations_budget"),
        CheckConstraint("actual_cost_usd >= 0", name="ck_mission_resource_allocations_actual_cost"),
        CheckConstraint("agent_slots >= 0", name="ck_mission_resource_allocations_agent_slots"),
        CheckConstraint("tool_slots >= 0", name="ck_mission_resource_allocations_tool_slots"),
        CheckConstraint("compute_units >= 0", name="ck_mission_resource_allocations_compute_units"),
        CheckConstraint("version >= 1", name="ck_mission_resource_allocations_version"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("resalloc")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cycle_id: Mapped[str] = mapped_column(
        ForeignKey("mission_cycles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_strategies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default="pending_approval", nullable=False, index=True
    )
    automatic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(255), default="system", nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    budget_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    actual_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    agent_slots: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    tool_slots: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    compute_units: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    admission_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class MissionResourceUsageModel(Base):
    __tablename__ = "mission_resource_usage"
    __table_args__ = (
        UniqueConstraint(
            "mission_id", "idempotency_key", name="uq_mission_resource_usage_idempotency"
        ),
        CheckConstraint(
            "category IN ('llm', 'tool', 'compute', 'storage', 'network', 'human', 'other')",
            name="ck_mission_resource_usage_category",
        ),
        CheckConstraint("quantity >= 0", name="ck_mission_resource_usage_quantity"),
        CheckConstraint("cost_usd >= 0", name="ck_mission_resource_usage_cost"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("resusage")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cycle_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_cycles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    allocation_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_resource_allocations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    strategy_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_strategies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    quantity: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    unit: Mapped[str] = mapped_column(String(64), default="unit", nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), default="manual", nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), default="system", nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class MissionCapacityPlanModel(Base):
    __tablename__ = "mission_capacity_plans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'recommended', 'applied', 'rejected')",
            name="ck_mission_capacity_plans_status",
        ),
        CheckConstraint("sample_cycles >= 0", name="ck_mission_capacity_plans_sample_cycles"),
        CheckConstraint("confidence_percent >= 0 AND confidence_percent <= 100", name="ck_mission_capacity_plans_confidence"),
        CheckConstraint("recommended_cycle_budget_usd >= 0", name="ck_mission_capacity_plans_cycle_budget"),
        CheckConstraint("recommended_parallel_cycles >= 1", name="ck_mission_capacity_plans_parallel_cycles"),
        CheckConstraint("recommended_agent_slots >= 1", name="ck_mission_capacity_plans_agent_slots"),
        CheckConstraint("recommended_tool_slots >= 1", name="ck_mission_capacity_plans_tool_slots"),
        CheckConstraint("recommended_compute_units >= 0", name="ck_mission_capacity_plans_compute_units"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("capplan")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default="recommended", nullable=False, index=True
    )
    actor_id: Mapped[str] = mapped_column(String(255), default="system", nullable=False)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sample_cycles: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    confidence_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    recommended_cycle_budget_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    recommended_parallel_cycles: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    recommended_agent_slots: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    recommended_tool_slots: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    recommended_compute_units: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    calculation_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MissionSchedulePolicyModel(Base):
    __tablename__ = "mission_schedule_policies"
    __table_args__ = (
        UniqueConstraint("mission_id", name="uq_mission_schedule_policies_mission"),
        CheckConstraint(
            "schedule_mode IN ('manual', 'fixed', 'adaptive')",
            name="ck_mission_schedule_policies_mode",
        ),
        CheckConstraint(
            "overdue_action IN ('observe', 'pause', 'escalate')",
            name="ck_mission_schedule_policies_overdue_action",
        ),
        CheckConstraint(
            "base_interval_seconds >= 30",
            name="ck_mission_schedule_policies_base_interval",
        ),
        CheckConstraint(
            "min_interval_seconds >= 30",
            name="ck_mission_schedule_policies_min_interval",
        ),
        CheckConstraint(
            "max_interval_seconds >= min_interval_seconds",
            name="ck_mission_schedule_policies_max_interval",
        ),
        CheckConstraint(
            "base_interval_seconds >= min_interval_seconds AND base_interval_seconds <= max_interval_seconds",
            name="ck_mission_schedule_policies_interval_range",
        ),
        CheckConstraint(
            "target_cycles_per_day >= 0 AND target_cycles_per_day <= 96",
            name="ck_mission_schedule_policies_target_cycles",
        ),
        CheckConstraint(
            "adaptation_sample_cycles >= 1",
            name="ck_mission_schedule_policies_sample_cycles",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("schedpol")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    schedule_mode: Mapped[str] = mapped_column(
        String(32), default="manual", nullable=False, index=True
    )
    timezone_name: Mapped[str] = mapped_column(
        String(128), default="UTC", nullable=False
    )
    base_interval_seconds: Mapped[int] = mapped_column(
        Integer, default=3600, nullable=False
    )
    min_interval_seconds: Mapped[int] = mapped_column(
        Integer, default=300, nullable=False
    )
    max_interval_seconds: Mapped[int] = mapped_column(
        Integer, default=86400, nullable=False
    )
    adaptive_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    require_human_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_apply_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enforce_manual_cycles: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allowed_weekdays_json: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    quiet_hours_start: Mapped[str | None] = mapped_column(String(5), nullable=True)
    quiet_hours_end: Mapped[str | None] = mapped_column(String(5), nullable=True)
    deadline_warning_seconds: Mapped[int] = mapped_column(Integer, default=86400, nullable=False)
    overdue_action: Mapped[str] = mapped_column(
        String(32), default="observe", nullable=False, index=True
    )
    target_cycles_per_day: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    adaptation_sample_cycles: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class MissionScheduleWindowModel(Base):
    __tablename__ = "mission_schedule_windows"
    __table_args__ = (
        CheckConstraint("priority >= 0 AND priority <= 100", name="ck_mission_schedule_windows_priority"),
        CheckConstraint("max_cycles >= 0", name="ck_mission_schedule_windows_max_cycles"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("schedwin")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    weekdays_json: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    start_time: Mapped[str] = mapped_column(String(5), default="00:00", nullable=False)
    end_time: Mapped[str] = mapped_column(String(5), default="23:59", nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=50, nullable=False, index=True)
    max_cycles: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class MissionScheduleEvaluationModel(Base):
    __tablename__ = "mission_schedule_evaluations"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('keep', 'accelerate', 'slow_down', 'pause')",
            name="ck_mission_schedule_evaluations_decision",
        ),
        CheckConstraint(
            "urgency_score >= 0 AND urgency_score <= 100",
            name="ck_mission_schedule_evaluations_urgency",
        ),
        CheckConstraint(
            "failure_rate_percent >= 0 AND failure_rate_percent <= 100",
            name="ck_mission_schedule_evaluations_failure_rate",
        ),
        CheckConstraint(
            "progress_velocity_percent >= 0 AND progress_velocity_percent <= 100",
            name="ck_mission_schedule_evaluations_progress_velocity",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("schedeval")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cycle_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_cycles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_schedule_policies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_id: Mapped[str] = mapped_column(String(255), default="system", nullable=False, index=True)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    previous_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    recommended_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    applied_interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    urgency_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    failure_rate_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    progress_velocity_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class MissionDeadlineEventModel(Base):
    __tablename__ = "mission_deadline_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('approaching', 'overdue', 'recovered')",
            name="ck_mission_deadline_events_type",
        ),
        CheckConstraint(
            "severity IN ('info', 'warning', 'critical')",
            name="ck_mission_deadline_events_severity",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("deadline")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    resolved_by: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    resolution_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class MissionDependencyModel(Base):
    __tablename__ = "mission_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "mission_id",
            "depends_on_mission_id",
            name="uq_mission_dependencies_pair",
        ),
        CheckConstraint(
            "dependency_type IN ('hard', 'soft', 'informational')",
            name="ck_mission_dependencies_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'satisfied', 'waived', 'failed')",
            name="ck_mission_dependencies_status",
        ),
        CheckConstraint(
            "priority >= 0 AND priority <= 100",
            name="ck_mission_dependencies_priority",
        ),
        CheckConstraint(
            "mission_id != depends_on_mission_id",
            name="ck_mission_dependencies_not_self",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("mdep")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    depends_on_mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dependency_type: Mapped[str] = mapped_column(
        String(32), default="hard", nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False, index=True
    )
    required_statuses_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    allow_failed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=50, nullable=False, index=True)
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    satisfied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    waived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    waived_by: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    waiver_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class MissionPortfolioPolicyModel(Base):
    __tablename__ = "mission_portfolio_policies"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_mission_portfolio_policies_workspace"),
        CheckConstraint(
            "prioritization_mode IN ('manual', 'weighted', 'adaptive')",
            name="ck_mission_portfolio_policies_mode",
        ),
        CheckConstraint(
            "max_parallel_missions >= 1",
            name="ck_mission_portfolio_policies_parallel",
        ),
        CheckConstraint(
            "min_selection_score >= 0 AND min_selection_score <= 100",
            name="ck_mission_portfolio_policies_min_score",
        ),
        CheckConstraint(
            "rebalance_interval_seconds >= 30",
            name="ck_mission_portfolio_policies_interval",
        ),
        CheckConstraint(
            "priority_weight >= 0 AND priority_weight <= 100",
            name="ck_mission_portfolio_policies_priority_weight",
        ),
        CheckConstraint(
            "progress_weight >= 0 AND progress_weight <= 100",
            name="ck_mission_portfolio_policies_progress_weight",
        ),
        CheckConstraint(
            "deadline_weight >= 0 AND deadline_weight <= 100",
            name="ck_mission_portfolio_policies_deadline_weight",
        ),
        CheckConstraint(
            "dependency_weight >= 0 AND dependency_weight <= 100",
            name="ck_mission_portfolio_policies_dependency_weight",
        ),
        CheckConstraint(
            "strategy_weight >= 0 AND strategy_weight <= 100",
            name="ck_mission_portfolio_policies_strategy_weight",
        ),
        CheckConstraint(
            "risk_penalty_weight >= 0 AND risk_penalty_weight <= 100",
            name="ck_mission_portfolio_policies_risk_weight",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("portpol")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    prioritization_mode: Mapped[str] = mapped_column(
        String(32), default="manual", nullable=False, index=True
    )
    require_human_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_rebalance_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enforce_cycle_admission: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_parallel_missions: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    min_selection_score: Mapped[float] = mapped_column(Float, default=50.0, nullable=False)
    rebalance_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    priority_weight: Mapped[float] = mapped_column(Float, default=35.0, nullable=False)
    progress_weight: Mapped[float] = mapped_column(Float, default=15.0, nullable=False)
    deadline_weight: Mapped[float] = mapped_column(Float, default=20.0, nullable=False)
    dependency_weight: Mapped[float] = mapped_column(Float, default=20.0, nullable=False)
    strategy_weight: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
    risk_penalty_weight: Mapped[float] = mapped_column(Float, default=15.0, nullable=False)
    last_rebalanced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class MissionPortfolioEvaluationModel(Base):
    __tablename__ = "mission_portfolio_evaluations"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('select', 'keep', 'defer', 'block')",
            name="ck_mission_portfolio_evaluations_decision",
        ),
        CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_mission_portfolio_evaluations_score",
        ),
        CheckConstraint("rank >= 1", name="ck_mission_portfolio_evaluations_rank"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("porteval")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_portfolio_policies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_id: Mapped[str] = mapped_column(String(255), default="system", nullable=False, index=True)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    components_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class MissionPortfolioAssignmentModel(Base):
    __tablename__ = "mission_portfolio_assignments"
    __table_args__ = (
        UniqueConstraint("mission_id", name="uq_mission_portfolio_assignments_mission"),
        CheckConstraint(
            "status IN ('selected', 'deferred', 'blocked', 'released')",
            name="ck_mission_portfolio_assignments_status",
        ),
        CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_mission_portfolio_assignments_score",
        ),
        CheckConstraint(
            "rank >= 0",
            name="ck_mission_portfolio_assignments_rank",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("portassign")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_portfolio_policies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default="deferred", nullable=False, index=True
    )
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False, index=True)
    rank: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    manual_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), default="system", nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class WorkspaceResourcePolicyModel(Base):
    __tablename__ = "workspace_resource_policies"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            name="uq_workspace_resource_policies_workspace",
        ),
        CheckConstraint(
            "allocation_mode IN ('manual', 'weighted', 'adaptive')",
            name="ck_workspace_resource_policies_mode",
        ),
        CheckConstraint(
            "total_budget_usd >= 0",
            name="ck_workspace_resource_policies_total_budget",
        ),
        CheckConstraint(
            "default_cycle_budget_usd >= 0",
            name="ck_workspace_resource_policies_default_cycle_budget",
        ),
        CheckConstraint(
            "max_cycle_budget_usd >= 0",
            name="ck_workspace_resource_policies_max_cycle_budget",
        ),
        CheckConstraint(
            "reserve_percent >= 0 AND reserve_percent <= 100",
            name="ck_workspace_resource_policies_reserve_percent",
        ),
        CheckConstraint(
            "max_parallel_cycles >= 1",
            name="ck_workspace_resource_policies_parallel_cycles",
        ),
        CheckConstraint(
            "agent_slots >= 1",
            name="ck_workspace_resource_policies_agent_slots",
        ),
        CheckConstraint(
            "tool_slots >= 1",
            name="ck_workspace_resource_policies_tool_slots",
        ),
        CheckConstraint(
            "compute_units >= 0",
            name="ck_workspace_resource_policies_compute_units",
        ),
        CheckConstraint(
            "min_mission_guarantee_percent >= 0 AND "
            "min_mission_guarantee_percent <= 100",
            name="ck_workspace_resource_policies_min_guarantee",
        ),
        CheckConstraint(
            "overcommit_tolerance_percent >= 0 AND "
            "overcommit_tolerance_percent <= 100",
            name="ck_workspace_resource_policies_overcommit",
        ),
        CheckConstraint(
            "rebalance_interval_seconds >= 30",
            name="ck_workspace_resource_policies_rebalance_interval",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("wrespol")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allocation_mode: Mapped[str] = mapped_column(
        String(32), default="manual", nullable=False, index=True
    )
    require_human_approval: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    auto_rebalance_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    enforce_cycle_admission: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    total_budget_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    default_cycle_budget_usd: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    max_cycle_budget_usd: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    reserve_percent: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
    max_parallel_cycles: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    agent_slots: Mapped[int] = mapped_column(Integer, default=16, nullable=False)
    tool_slots: Mapped[int] = mapped_column(Integer, default=16, nullable=False)
    compute_units: Mapped[float] = mapped_column(Float, default=16.0, nullable=False)
    min_mission_guarantee_percent: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    overcommit_tolerance_percent: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    rebalance_interval_seconds: Mapped[int] = mapped_column(
        Integer, default=300, nullable=False
    )
    last_rebalanced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class WorkspaceResourceReservationModel(Base):
    __tablename__ = "workspace_resource_reservations"
    __table_args__ = (
        UniqueConstraint(
            "cycle_id",
            name="uq_workspace_resource_reservations_cycle",
        ),
        UniqueConstraint(
            "allocation_id",
            name="uq_workspace_resource_reservations_allocation",
        ),
        CheckConstraint(
            "status IN ('pending_approval', 'reserved', 'active', 'released', "
            "'consumed', 'exceeded', 'cancelled')",
            name="ck_workspace_resource_reservations_status",
        ),
        CheckConstraint(
            "budget_usd >= 0",
            name="ck_workspace_resource_reservations_budget",
        ),
        CheckConstraint(
            "actual_cost_usd >= 0",
            name="ck_workspace_resource_reservations_actual_cost",
        ),
        CheckConstraint(
            "agent_slots >= 0",
            name="ck_workspace_resource_reservations_agent_slots",
        ),
        CheckConstraint(
            "tool_slots >= 0",
            name="ck_workspace_resource_reservations_tool_slots",
        ),
        CheckConstraint(
            "compute_units >= 0",
            name="ck_workspace_resource_reservations_compute_units",
        ),
        CheckConstraint(
            "priority_score >= 0 AND priority_score <= 100",
            name="ck_workspace_resource_reservations_priority_score",
        ),
        CheckConstraint(
            "rank >= 0",
            name="ck_workspace_resource_reservations_rank",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_workspace_resource_reservations_version",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("wresv")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cycle_id: Mapped[str] = mapped_column(
        ForeignKey("mission_cycles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    allocation_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_resource_allocations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspace_resource_policies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), default="pending_approval", nullable=False, index=True
    )
    automatic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    actor_id: Mapped[str] = mapped_column(
        String(255), default="system", nullable=False, index=True
    )
    approved_by: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    budget_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    actual_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    agent_slots: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    tool_slots: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    compute_units: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    priority_score: Mapped[float] = mapped_column(Float, default=50.0, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    forced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    admission_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reserved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class WorkspaceResourceConflictModel(Base):
    __tablename__ = "workspace_resource_conflicts"
    __table_args__ = (
        UniqueConstraint(
            "dedupe_key",
            name="uq_workspace_resource_conflicts_dedupe",
        ),
        CheckConstraint(
            "status IN ('open', 'resolved', 'waived', 'cancelled')",
            name="ck_workspace_resource_conflicts_status",
        ),
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_workspace_resource_conflicts_severity",
        ),
        CheckConstraint(
            "recommended_action IN ('defer', 'preempt', 'rebalance', "
            "'increase_capacity', 'force', 'manual_review')",
            name="ck_workspace_resource_conflicts_recommended_action",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("wconflict")
    )
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cycle_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_cycles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reservation_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspace_resource_reservations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    allocation_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_resource_allocations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), default="open", nullable=False, index=True
    )
    severity: Mapped[str] = mapped_column(
        String(16), default="medium", nullable=False, index=True
    )
    resource_types_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    requested_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    available_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    shortfall_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    conflicting_reservation_ids_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    recommended_action: Mapped[str] = mapped_column(
        String(32), default="manual_review", nullable=False, index=True
    )
    resolution_action: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True
    )
    resolution_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class WorkspaceResourceRebalanceModel(Base):
    __tablename__ = "workspace_resource_rebalances"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'recommended', 'applied', 'rejected')",
            name="ck_workspace_resource_rebalances_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("wrebalance")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspace_resource_policies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), default="draft", nullable=False, index=True
    )
    actor_id: Mapped[str] = mapped_column(
        String(255), default="system", nullable=False, index=True
    )
    automatic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    proposal_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    applied_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class MissionForecastPolicyModel(Base):
    __tablename__ = "mission_forecast_policies"
    __table_args__ = (
        UniqueConstraint("mission_id", name="uq_mission_forecast_policies_mission"),
        CheckConstraint(
            "forecast_mode IN ('manual', 'heuristic', 'adaptive')",
            name="ck_mission_forecast_policies_mode",
        ),
        CheckConstraint(
            "horizon_cycles >= 1",
            name="ck_mission_forecast_policies_horizon",
        ),
        CheckConstraint(
            "min_samples >= 1",
            name="ck_mission_forecast_policies_min_samples",
        ),
        CheckConstraint(
            "stale_after_seconds >= 60",
            name="ck_mission_forecast_policies_stale",
        ),
        CheckConstraint(
            "confidence_threshold_percent >= 0 AND confidence_threshold_percent <= 100",
            name="ck_mission_forecast_policies_confidence",
        ),
        CheckConstraint(
            "max_scenarios >= 1",
            name="ck_mission_forecast_policies_max_scenarios",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("forecastpol")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    forecast_mode: Mapped[str] = mapped_column(
        String(32), default="manual", nullable=False, index=True
    )
    horizon_cycles: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    min_samples: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    stale_after_seconds: Mapped[int] = mapped_column(
        Integer, default=3600, nullable=False
    )
    auto_refresh_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    require_human_approval: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    allow_auto_scenario_selection: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    confidence_threshold_percent: Mapped[float] = mapped_column(
        Float, default=70.0, nullable=False
    )
    max_scenarios: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class MissionForecastModel(Base):
    __tablename__ = "mission_forecasts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('current', 'superseded', 'invalidated')",
            name="ck_mission_forecasts_status",
        ),
        CheckConstraint(
            "horizon_cycles >= 1",
            name="ck_mission_forecasts_horizon",
        ),
        CheckConstraint(
            "sample_count >= 0",
            name="ck_mission_forecasts_sample_count",
        ),
        CheckConstraint(
            "success_probability_percent >= 0 AND success_probability_percent <= 100",
            name="ck_mission_forecasts_success_probability",
        ),
        CheckConstraint(
            "completion_probability_percent >= 0 AND completion_probability_percent <= 100",
            name="ck_mission_forecasts_completion_probability",
        ),
        CheckConstraint(
            "expected_progress_percent >= 0 AND expected_progress_percent <= 100",
            name="ck_mission_forecasts_expected_progress",
        ),
        CheckConstraint(
            "confidence_percent >= 0 AND confidence_percent <= 100",
            name="ck_mission_forecasts_confidence",
        ),
        CheckConstraint(
            "risk_score >= 0 AND risk_score <= 100",
            name="ck_mission_forecasts_risk",
        ),
        CheckConstraint(
            "expected_remaining_cycles >= 0",
            name="ck_mission_forecasts_remaining_cycles",
        ),
        CheckConstraint(
            "expected_cost_usd >= 0",
            name="ck_mission_forecasts_cost",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("forecast")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_forecast_policies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), default="current", nullable=False, index=True
    )
    method: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    horizon_cycles: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    success_probability_percent: Mapped[float] = mapped_column(
        Float, default=50.0, nullable=False, index=True
    )
    completion_probability_percent: Mapped[float] = mapped_column(
        Float, default=50.0, nullable=False, index=True
    )
    expected_progress_percent: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    confidence_percent: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False, index=True
    )
    risk_score: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False, index=True
    )
    expected_remaining_cycles: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    expected_cost_usd: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    p50_completion_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    p90_completion_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    assumptions_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    drivers_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    metrics_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class MissionScenarioModel(Base):
    __tablename__ = "mission_scenarios"
    __table_args__ = (
        UniqueConstraint("mission_id", "scenario_key", name="uq_mission_scenarios_key"),
        CheckConstraint(
            "scenario_type IN ('baseline', 'optimistic', 'pessimistic', 'custom')",
            name="ck_mission_scenarios_type",
        ),
        CheckConstraint(
            "status IN ('draft', 'evaluated', 'selected', 'archived')",
            name="ck_mission_scenarios_status",
        ),
        CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_mission_scenarios_score",
        ),
        CheckConstraint("version >= 1", name="ck_mission_scenarios_version"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("scenario")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    baseline_forecast_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_forecasts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    scenario_key: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    scenario_type: Mapped[str] = mapped_column(
        String(32), default="custom", nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default="draft", nullable=False, index=True
    )
    overrides_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    assumptions_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    result_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False, index=True)
    selected_by: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    selection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    selected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class WorkspacePortfolioSimulationModel(Base):
    __tablename__ = "workspace_portfolio_simulations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('completed', 'failed')",
            name="ck_workspace_portfolio_simulations_status",
        ),
        CheckConstraint(
            "mission_count >= 0",
            name="ck_workspace_portfolio_simulations_mission_count",
        ),
        CheckConstraint(
            "selected_count >= 0",
            name="ck_workspace_portfolio_simulations_selected_count",
        ),
        CheckConstraint(
            "portfolio_score >= 0 AND portfolio_score <= 100",
            name="ck_workspace_portfolio_simulations_score",
        ),
        CheckConstraint(
            "expected_cost_usd >= 0",
            name="ck_workspace_portfolio_simulations_cost",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("portsim")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="completed", nullable=False, index=True
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    mission_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    selected_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    portfolio_score: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False, index=True
    )
    expected_cost_usd: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    expected_successful_missions: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    input_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    result_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    assumptions_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class MissionLearningPolicyModel(Base):
    __tablename__ = "mission_learning_policies"
    __table_args__ = (
        UniqueConstraint("mission_id", name="uq_mission_learning_policies_mission"),
        CheckConstraint(
            "learning_mode IN ('manual', 'observe', 'adaptive')",
            name="ck_mission_learning_policies_mode",
        ),
        CheckConstraint("min_samples >= 1", name="ck_mission_learning_policies_min_samples"),
        CheckConstraint(
            "calibration_window >= 1",
            name="ck_mission_learning_policies_window",
        ),
        CheckConstraint(
            "max_probability_adjustment_percent >= 0 AND "
            "max_probability_adjustment_percent <= 50",
            name="ck_mission_learning_policies_probability_adjustment",
        ),
        CheckConstraint(
            "max_multiplier_adjustment_percent >= 0 AND "
            "max_multiplier_adjustment_percent <= 100",
            name="ck_mission_learning_policies_multiplier_adjustment",
        ),
        CheckConstraint(
            "min_improvement_percent >= 0 AND min_improvement_percent <= 100",
            name="ck_mission_learning_policies_improvement",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("learnpol")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    learning_mode: Mapped[str] = mapped_column(
        String(32), default="manual", nullable=False, index=True
    )
    capture_outcomes_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    auto_calibration_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    auto_apply_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    require_human_approval: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    min_samples: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    calibration_window: Mapped[int] = mapped_column(
        Integer, default=50, nullable=False
    )
    max_probability_adjustment_percent: Mapped[float] = mapped_column(
        Float, default=15.0, nullable=False
    )
    max_multiplier_adjustment_percent: Mapped[float] = mapped_column(
        Float, default=30.0, nullable=False
    )
    min_improvement_percent: Mapped[float] = mapped_column(
        Float, default=2.0, nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class MissionForecastOutcomeModel(Base):
    __tablename__ = "mission_forecast_outcomes"
    __table_args__ = (
        UniqueConstraint("forecast_id", name="uq_mission_forecast_outcomes_forecast"),
        UniqueConstraint(
            "source_event_id", "forecast_id",
            name="uq_mission_forecast_outcomes_source_forecast",
        ),
        CheckConstraint(
            "actual_status IN ('completed', 'failed', 'cancelled')",
            name="ck_mission_forecast_outcomes_status",
        ),
        CheckConstraint(
            "predicted_success_percent >= 0 AND predicted_success_percent <= 100",
            name="ck_mission_forecast_outcomes_predicted_success",
        ),
        CheckConstraint(
            "predicted_completion_percent >= 0 AND predicted_completion_percent <= 100",
            name="ck_mission_forecast_outcomes_predicted_completion",
        ),
        CheckConstraint(
            "success_brier_score >= 0 AND success_brier_score <= 1",
            name="ck_mission_forecast_outcomes_success_brier",
        ),
        CheckConstraint(
            "completion_brier_score >= 0 AND completion_brier_score <= 1",
            name="ck_mission_forecast_outcomes_completion_brier",
        ),
        CheckConstraint("actual_cost_usd >= 0", name="ck_mission_forecast_outcomes_cost"),
        CheckConstraint(
            "actual_remaining_cycles >= 0",
            name="ck_mission_forecast_outcomes_remaining_cycles",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("foutcome")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    forecast_id: Mapped[str] = mapped_column(
        ForeignKey("mission_forecasts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_event_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    actual_status: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )
    actual_success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    actual_completion: Mapped[bool] = mapped_column(Boolean, nullable=False)
    predicted_success_percent: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_completion_percent: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    predicted_remaining_cycles: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    predicted_p50_completion_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actual_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    actual_remaining_cycles: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    actual_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    success_brier_score: Mapped[float] = mapped_column(Float, nullable=False)
    completion_brier_score: Mapped[float] = mapped_column(Float, nullable=False)
    success_absolute_error_percent: Mapped[float] = mapped_column(
        Float, nullable=False
    )
    completion_absolute_error_percent: Mapped[float] = mapped_column(
        Float, nullable=False
    )
    cost_absolute_error_usd: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    cost_relative_error_percent: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    cycles_absolute_error: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    duration_absolute_error_seconds: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    details_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class MissionForecastCalibrationModel(Base):
    __tablename__ = "mission_forecast_calibrations"
    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('mission', 'workspace')",
            name="ck_mission_forecast_calibrations_scope",
        ),
        CheckConstraint(
            "status IN ('proposed', 'current', 'superseded', 'rejected')",
            name="ck_mission_forecast_calibrations_status",
        ),
        CheckConstraint("sample_count >= 0", name="ck_mission_forecast_calibrations_samples"),
        CheckConstraint(
            "success_bias_percent >= -50 AND success_bias_percent <= 50",
            name="ck_mission_forecast_calibrations_success_bias",
        ),
        CheckConstraint(
            "completion_bias_percent >= -50 AND completion_bias_percent <= 50",
            name="ck_mission_forecast_calibrations_completion_bias",
        ),
        CheckConstraint(
            "cost_multiplier >= 0.1 AND cost_multiplier <= 10",
            name="ck_mission_forecast_calibrations_cost_multiplier",
        ),
        CheckConstraint(
            "cycle_multiplier >= 0.1 AND cycle_multiplier <= 10",
            name="ck_mission_forecast_calibrations_cycle_multiplier",
        ),
        CheckConstraint(
            "duration_multiplier >= 0.1 AND duration_multiplier <= 10",
            name="ck_mission_forecast_calibrations_duration_multiplier",
        ),
        CheckConstraint(
            "confidence_multiplier >= 0.1 AND confidence_multiplier <= 2",
            name="ck_mission_forecast_calibrations_confidence_multiplier",
        ),
        CheckConstraint(
            "baseline_score >= 0 AND baseline_score <= 100",
            name="ck_mission_forecast_calibrations_baseline",
        ),
        CheckConstraint(
            "calibrated_score >= 0 AND calibrated_score <= 100",
            name="ck_mission_forecast_calibrations_calibrated",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("fcal")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_learning_policies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32), default="proposed", nullable=False, index=True
    )
    method: Mapped[str] = mapped_column(
        String(255), default="builtin.calibration.v1", nullable=False, index=True
    )
    sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    success_bias_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    completion_bias_percent: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    cost_multiplier: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    cycle_multiplier: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    duration_multiplier: Mapped[float] = mapped_column(
        Float, default=1.0, nullable=False
    )
    confidence_multiplier: Mapped[float] = mapped_column(
        Float, default=1.0, nullable=False
    )
    baseline_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    calibrated_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    improvement_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    success_brier_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    completion_brier_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    expected_calibration_error_percent: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    actor_id: Mapped[str] = mapped_column(
        String(255), default="system", nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class MissionLearningRunModel(Base):
    __tablename__ = "mission_learning_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed', 'applied', 'rejected', 'failed')",
            name="ck_mission_learning_runs_status",
        ),
        CheckConstraint("sample_count >= 0", name="ck_mission_learning_runs_samples"),
        CheckConstraint(
            "baseline_score >= 0 AND baseline_score <= 100",
            name="ck_mission_learning_runs_baseline",
        ),
        CheckConstraint(
            "candidate_score >= 0 AND candidate_score <= 100",
            name="ck_mission_learning_runs_candidate",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("learnrun")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_learning_policies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    calibration_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_forecast_calibrations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), default="proposed", nullable=False, index=True
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    scope_type: Mapped[str] = mapped_column(
        String(32), default="mission", nullable=False, index=True
    )
    sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    baseline_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    candidate_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    improvement_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_summary_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    recommendations_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    applied_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MissionLearningSignalModel(Base):
    __tablename__ = "mission_learning_signals"
    __table_args__ = (
        UniqueConstraint(
            "source_event_id", "signal_type",
            name="uq_mission_learning_signals_source_type",
        ),
        CheckConstraint(
            "reward_percent >= 0 AND reward_percent <= 100",
            name="ck_mission_learning_signals_reward",
        ),
        CheckConstraint(
            "confidence_percent >= 0 AND confidence_percent <= 100",
            name="ck_mission_learning_signals_confidence",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("learnsig")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cycle_id: Mapped[str | None] = mapped_column(
        ForeignKey("mission_cycles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_event_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    signal_type: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    reward_percent: Mapped[float] = mapped_column(Float, default=50.0, nullable=False)
    confidence_percent: Mapped[float] = mapped_column(
        Float, default=100.0, nullable=False
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
