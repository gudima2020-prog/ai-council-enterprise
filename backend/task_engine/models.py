from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    CheckConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.base import Base
from backend.task_engine.enums import TaskPriority, TaskStatus, TaskType


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class TaskModel(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("task"),
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    task_type: Mapped[str] = mapped_column(
        String(32),
        default=TaskType.SYSTEM.value,
        nullable=False,
        index=True,
    )
    priority: Mapped[str] = mapped_column(
        String(32),
        default=TaskPriority.NORMAL.value,
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default=TaskStatus.CREATED.value,
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    result_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    creator: Mapped[str | None] = mapped_column(String(255), nullable=True)
    executor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_delay_seconds: Mapped[float] = mapped_column(
        Float,
        default=1.0,
        nullable=False,
    )
    timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        default=300,
        nullable=False,
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    runs: Mapped[list["TaskRunModel"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="TaskRunModel.created_at",
    )
    logs: Mapped[list["TaskLogModel"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="TaskLogModel.created_at",
    )
    artifacts: Mapped[list["TaskArtifactModel"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="TaskArtifactModel.created_at",
    )


class TaskRunModel(Base):
    __tablename__ = "task_runs"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("taskrun"),
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default=TaskStatus.CREATED.value,
        nullable=False,
    )
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    task: Mapped[TaskModel] = relationship(back_populates="runs")


class TaskLogModel(Base):
    __tablename__ = "task_logs"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("tasklog"),
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("task_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    level: Mapped[str] = mapped_column(
        String(16),
        default="INFO",
        nullable=False,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    task: Mapped[TaskModel] = relationship(back_populates="logs")


class TaskArtifactModel(Base):
    __tablename__ = "task_artifacts"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("artifact"),
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("task_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    task: Mapped[TaskModel] = relationship(back_populates="artifacts")


class TaskDependencyModel(Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "depends_on_task_id",
            name="uq_task_dependencies_pair",
        ),
        CheckConstraint(
            "task_id <> depends_on_task_id",
            name="ck_task_dependencies_not_self",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("taskdep"),
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    depends_on_task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dependency_type: Mapped[str] = mapped_column(
        String(16),
        default="hard",
        nullable=False,
    )
    required_status: Mapped[str] = mapped_column(
        String(32),
        default=TaskStatus.COMPLETED.value,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class WorkflowTemplateModel(Base):
    __tablename__ = "workflow_templates"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "name",
            "version",
            name="uq_workflow_templates_workspace_name_version",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("wftpl"),
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    definition_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False
    )
    input_schema_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class WorkflowInstanceModel(Base):
    __tablename__ = "workflow_instances"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("wfinst"),
    )
    template_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_templates.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    root_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), default="created", nullable=False, index=True
    )
    input_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    node_task_map_json: Mapped[dict[str, str]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    context_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )



class TaskApprovalModel(Base):
    __tablename__ = "task_approvals"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "gate_key",
            name="uq_task_approvals_task_gate",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("approval"),
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    gate_key: Mapped[str] = mapped_column(
        String(64),
        default="execution",
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        nullable=False,
        index=True,
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    decided_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    decision_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
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
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class TaskAuditEventModel(Base):
    """
    Append-only, hash-chained audit record.

    Identifiers are deliberately stored without foreign keys so the audit
    evidence survives deletion of the related Task, Approval or Workflow.
    """

    __tablename__ = "task_audit_events"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("audit"),
    )
    sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        unique=True,
        index=True,
    )
    event_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        unique=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    task_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    approval_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    workflow_instance_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    actor_type: Mapped[str] = mapped_column(
        String(32),
        default="system",
        nullable=False,
        index=True,
    )
    actor_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    previous_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    event_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )




class TaskBudgetPolicyModel(Base):
    """Workspace or global execution budget and quota policy."""

    __tablename__ = "task_budget_policies"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_task_budget_policies_workspace_name",
        ),
        CheckConstraint(
            "period IN ('daily', 'monthly', 'lifetime')",
            name="ck_task_budget_policies_period",
        ),
        CheckConstraint(
            "enforcement_mode IN ('hard', 'observe')",
            name="ck_task_budget_policies_enforcement",
        ),
        CheckConstraint(
            "limit_usd IS NULL OR limit_usd >= 0",
            name="ck_task_budget_policies_limit",
        ),
        CheckConstraint(
            "max_task_cost_usd IS NULL OR max_task_cost_usd >= 0",
            name="ck_task_budget_policies_max_task_cost",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("budget"),
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        index=True,
    )
    period: Mapped[str] = mapped_column(
        String(16),
        default="monthly",
        nullable=False,
    )
    enforcement_mode: Mapped[str] = mapped_column(
        String(16),
        default="hard",
        nullable=False,
    )
    limit_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 6),
        nullable=True,
    )
    warning_threshold_percent: Mapped[float] = mapped_column(
        Float,
        default=80.0,
        nullable=False,
    )
    max_task_cost_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 6),
        nullable=True,
    )
    max_tasks_per_period: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    max_queued: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    max_running: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    allowed_task_types_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    denied_task_types_json: Mapped[list[str]] = mapped_column(
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
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class TaskCostLedgerModel(Base):
    """
    Append-only task cost ledger.

    Task and policy identifiers intentionally do not use foreign keys so cost
    evidence survives deletion of operational records.
    """

    __tablename__ = "task_cost_ledger"
    __table_args__ = (
        CheckConstraint(
            "entry_type IN ("
            "'admission', 'reservation', 'release', 'charge', "
            "'adjustment', 'override'"
            ")",
            name="ck_task_cost_ledger_entry_type",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("cost"),
    )
    reference_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
        index=True,
    )
    task_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    policy_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    entry_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    amount_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        default=Decimal("0"),
        nullable=False,
    )
    actor_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
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



class TaskDeadLetterModel(Base):
    """Durable snapshot of a Task that exhausted automatic recovery."""

    __tablename__ = "task_dead_letter_entries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'replayed', 'resolved', 'discarded')",
            name="ck_task_dead_letter_entries_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("dlq"),
    )
    task_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="open",
        nullable=False,
        index=True,
    )
    reason_code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    error_type: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    source_event_type: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    task_snapshot_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    failure_snapshot_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    replay_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    last_replayed_task_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    resolved_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    resolution_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
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
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class TaskDeadLetterReplayModel(Base):
    """Replay attempt history for a dead-letter entry."""

    __tablename__ = "task_dead_letter_replays"
    __table_args__ = (
        UniqueConstraint(
            "dead_letter_id",
            "idempotency_key",
            name="uq_task_dead_letter_replay_idempotency",
        ),
        CheckConstraint(
            "status IN ("
            "'created', 'waiting', 'enqueued', 'running', "
            "'completed', 'failed', 'cancelled', 'skipped'"
            ")",
            name="ck_task_dead_letter_replays_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("dlqreplay"),
    )
    dead_letter_id: Mapped[str] = mapped_column(
        ForeignKey("task_dead_letter_entries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_task_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    replay_task_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="created",
        nullable=False,
        index=True,
    )
    actor_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    request_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
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
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
