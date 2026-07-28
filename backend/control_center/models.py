from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class HumanControlItemModel(Base):
    __tablename__ = "human_control_items"
    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "source_id",
            name="uq_human_control_items_source",
        ),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'snoozed', 'resolved', "
            "'expired', 'superseded')",
            name="ck_human_control_items_status",
        ),
        CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_human_control_items_risk",
        ),
        CheckConstraint(
            "priority >= 0 AND priority <= 100",
            name="ck_human_control_items_priority",
        ),
        Index(
            "ix_human_control_items_inbox",
            "workspace_id",
            "status",
            "risk_level",
            "priority",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("hcitem"),
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    source_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    source_status: Mapped[str] = mapped_column(
        String(64),
        default="pending",
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        nullable=False,
        index=True,
    )
    action_kind: Mapped[str] = mapped_column(
        String(64),
        default="approval",
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    risk_level: Mapped[str] = mapped_column(
        String(16),
        default="medium",
        nullable=False,
        index=True,
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        default=50,
        nullable=False,
        index=True,
    )
    requires_human: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    decision_options_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    assigned_to: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    snoozed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    resolution: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    resolved_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
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


class HumanControlActionModel(Base):
    __tablename__ = "human_control_actions"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_human_control_actions_idempotency",
        ),
        CheckConstraint(
            "action_type IN ('sync', 'claim', 'release', 'snooze', "
            "'decision', 'source_resolved')",
            name="ck_human_control_actions_type",
        ),
        Index(
            "ix_human_control_actions_history",
            "workspace_id",
            "item_id",
            "actor_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("hcaction"),
    )
    item_id: Mapped[str] = mapped_column(
        ForeignKey("human_control_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[str] = mapped_column(
        String(255),
        default="system",
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    requested_action: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    previous_status: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    new_status: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    result_json: Mapped[dict[str, Any]] = mapped_column(
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


class HumanControlRoleModel(Base):
    __tablename__ = "human_control_roles"
    __table_args__ = (
        UniqueConstraint("scope_key", name="uq_human_control_roles_scope_key"),
        CheckConstraint(
            "length(role_key) >= 1",
            name="ck_human_control_roles_role_key",
        ),
        Index(
            "ix_human_control_roles_lookup",
            "workspace_id",
            "role_key",
            "enabled",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("hcrole"),
    )
    scope_key: Mapped[str] = mapped_column(String(320), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    role_key: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    permissions_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    allowed_risk_levels_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    allowed_source_types_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    allowed_actions_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_by: Mapped[str] = mapped_column(
        String(255),
        default="system",
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


class HumanControlRoleBindingModel(Base):
    __tablename__ = "human_control_role_bindings"
    __table_args__ = (
        UniqueConstraint(
            "binding_key",
            name="uq_human_control_role_bindings_key",
        ),
        Index(
            "ix_human_control_role_bindings_effective",
            "workspace_id",
            "actor_id",
            "enabled",
            "expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("hcbinding"),
    )
    binding_key: Mapped[str] = mapped_column(String(512), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role_id: Mapped[str] = mapped_column(
        ForeignKey("human_control_roles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    granted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
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


class HumanControlApprovalPolicyModel(Base):
    __tablename__ = "human_control_approval_policies"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            name="uq_human_control_approval_policies_scope_key",
        ),
        CheckConstraint(
            "priority >= 0 AND priority <= 100",
            name="ck_human_control_approval_policies_priority",
        ),
        CheckConstraint(
            "rejection_mode IN ('any', 'majority')",
            name="ck_human_control_approval_policies_rejection_mode",
        ),
        Index(
            "ix_human_control_approval_policies_match",
            "workspace_id",
            "enabled",
            "priority",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("hcpolicy"),
    )
    scope_key: Mapped[str] = mapped_column(String(320), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    policy_key: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    source_types_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    action_kinds_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    risk_levels_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    decision_actions_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    steps_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    distinct_approvers: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    prohibit_source_requester_approval: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    prohibit_case_initiator_approval: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    rejection_mode: Mapped[str] = mapped_column(
        String(16),
        default="any",
        nullable=False,
    )
    case_ttl_seconds: Mapped[int] = mapped_column(
        Integer,
        default=86400,
        nullable=False,
    )
    default_step_ttl_seconds: Mapped[int] = mapped_column(
        Integer,
        default=3600,
        nullable=False,
    )
    escalation_after_seconds: Mapped[int] = mapped_column(
        Integer,
        default=3600,
        nullable=False,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
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


class HumanControlApprovalCaseModel(Base):
    __tablename__ = "human_control_approval_cases"
    __table_args__ = (
        UniqueConstraint(
            "request_idempotency_key",
            name="uq_human_control_approval_cases_idempotency",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'executing', 'executed', "
            "'execution_failed', 'rejected', 'expired', 'cancelled')",
            name="ck_human_control_approval_cases_status",
        ),
        Index(
            "ix_human_control_approval_cases_queue",
            "workspace_id",
            "status",
            "due_at",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("hccase"),
    )
    item_id: Mapped[str] = mapped_column(
        ForeignKey("human_control_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("human_control_approval_policies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    requested_action: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    requested_by: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    source_requester: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    request_idempotency_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        nullable=False,
        index=True,
    )
    current_step_sequence: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    total_steps: Mapped[int] = mapped_column(Integer, nullable=False)
    distinct_approvers: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    prohibit_source_requester_approval: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    prohibit_case_initiator_approval: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    request_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    final_result_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    resolved_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    resolution_reason: Mapped[str] = mapped_column(
        Text,
        default="",
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


class HumanControlApprovalStepModel(Base):
    __tablename__ = "human_control_approval_steps"
    __table_args__ = (
        UniqueConstraint(
            "case_id",
            "sequence",
            name="uq_human_control_approval_steps_sequence",
        ),
        UniqueConstraint(
            "case_id",
            "step_key",
            name="uq_human_control_approval_steps_key",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'skipped', 'expired')",
            name="ck_human_control_approval_steps_status",
        ),
        CheckConstraint(
            "min_approvals >= 1",
            name="ck_human_control_approval_steps_min_approvals",
        ),
        Index(
            "ix_human_control_approval_steps_active",
            "case_id",
            "status",
            "sequence",
            "due_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("hcstep"),
    )
    case_id: Mapped[str] = mapped_column(
        ForeignKey("human_control_approval_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_key: Mapped[str] = mapped_column(String(96), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        nullable=False,
        index=True,
    )
    required_role_keys_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    escalation_role_keys_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    min_approvals: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    approvals_received: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    rejections_received: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    escalated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    escalated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
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
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class HumanControlApprovalVoteModel(Base):
    __tablename__ = "human_control_approval_votes"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_human_control_approval_votes_idempotency",
        ),
        UniqueConstraint(
            "step_id",
            "actor_id",
            name="uq_human_control_approval_votes_actor_step",
        ),
        CheckConstraint(
            "decision IN ('approve', 'reject', 'abstain')",
            name="ck_human_control_approval_votes_decision",
        ),
        Index(
            "ix_human_control_approval_votes_history",
            "case_id",
            "step_id",
            "actor_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("hcvote"),
    )
    case_id: Mapped[str] = mapped_column(
        ForeignKey("human_control_approval_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[str] = mapped_column(
        ForeignKey("human_control_approval_steps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    role_keys_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
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
        index=True,
    )


class HumanControlEscalationModel(Base):
    __tablename__ = "human_control_escalations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved', 'cancelled')",
            name="ck_human_control_escalations_status",
        ),
        CheckConstraint(
            "escalation_type IN ('timeout', 'manual', 'critical', 'unassigned')",
            name="ck_human_control_escalations_type",
        ),
        Index(
            "ix_human_control_escalations_queue",
            "workspace_id",
            "status",
            "due_at",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("hcescalation"),
    )
    case_id: Mapped[str] = mapped_column(
        ForeignKey("human_control_approval_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[str | None] = mapped_column(
        ForeignKey("human_control_approval_steps.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    escalation_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="open",
        nullable=False,
        index=True,
    )
    source_role_keys_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    target_role_keys_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    assigned_to: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    acknowledged_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    resolved_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    resolution: Mapped[str] = mapped_column(Text, default="", nullable=False)
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


class HumanControlAuthPolicyModel(Base):
    __tablename__ = "human_control_auth_policies"
    __table_args__ = (
        UniqueConstraint("scope_key", name="uq_human_control_auth_policies_scope"),
        CheckConstraint(
            "enforcement_mode IN ('legacy', 'audit', 'enforce')",
            name="ck_human_control_auth_policies_mode",
        ),
        Index(
            "ix_human_control_auth_policies_effective",
            "workspace_id",
            "enabled",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcauthpol")
    )
    scope_key: Mapped[str] = mapped_column(String(320), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enforcement_mode: Mapped[str] = mapped_column(
        String(16), default="legacy", nullable=False, index=True
    )
    session_ttl_seconds: Mapped[int] = mapped_column(
        Integer, default=28800, nullable=False
    )
    session_idle_seconds: Mapped[int] = mapped_column(
        Integer, default=3600, nullable=False
    )
    max_failed_attempts: Mapped[int] = mapped_column(
        Integer, default=5, nullable=False
    )
    lockout_seconds: Mapped[int] = mapped_column(
        Integer, default=900, nullable=False
    )
    api_token_max_ttl_days: Mapped[int] = mapped_column(
        Integer, default=90, nullable=False
    )
    break_glass_ttl_seconds: Mapped[int] = mapped_column(
        Integer, default=900, nullable=False
    )
    break_glass_requires_approval: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    break_glass_distinct_approver: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    allowed_api_scopes_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    protected_path_prefixes_json: Mapped[list[str]] = mapped_column(
        JSON, default=lambda: ["/api/human-control"], nullable=False
    )
    public_paths_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    require_actor_binding: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    require_workspace_binding: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    reject_unscoped_api_tokens: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    updated_by: Mapped[str] = mapped_column(
        String(255), default="system", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class HumanControlIdentityModel(Base):
    __tablename__ = "human_control_identities"
    __table_args__ = (
        UniqueConstraint("actor_id", name="uq_human_control_identities_actor"),
        UniqueConstraint("username_normalized", name="uq_human_control_identities_username"),
        CheckConstraint(
            "identity_type IN ('human', 'service')",
            name="ck_human_control_identities_type",
        ),
        CheckConstraint(
            "status IN ('active', 'disabled', 'locked')",
            name="ck_human_control_identities_status",
        ),
        Index(
            "ix_human_control_identities_lookup",
            "username_normalized",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcident")
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    username_normalized: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    identity_type: Mapped[str] = mapped_column(
        String(16), default="human", nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), default="active", nullable=False, index=True
    )
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_salt: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_iterations: Mapped[int] = mapped_column(
        Integer, default=310000, nullable=False
    )
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_ip: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class HumanControlSessionModel(Base):
    __tablename__ = "human_control_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_human_control_sessions_token"),
        CheckConstraint(
            "status IN ('active', 'revoked', 'expired')",
            name="ck_human_control_sessions_status",
        ),
        CheckConstraint(
            "auth_method IN ('password', 'api_token', 'break_glass')",
            name="ck_human_control_sessions_method",
        ),
        Index(
            "ix_human_control_sessions_active",
            "identity_id",
            "status",
            "expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcsess")
    )
    identity_id: Mapped[str] = mapped_column(
        ForeignKey("human_control_identities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="active", nullable=False, index=True
    )
    auth_method: Mapped[str] = mapped_column(String(16), nullable=False)
    scopes_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    client_ip: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    idle_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    revoke_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )


class HumanControlApiTokenModel(Base):
    __tablename__ = "human_control_api_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_human_control_api_tokens_hash"),
        CheckConstraint(
            "status IN ('active', 'revoked', 'expired')",
            name="ck_human_control_api_tokens_status",
        ),
        Index(
            "ix_human_control_api_tokens_active",
            "identity_id",
            "status",
            "expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcapitok")
    )
    identity_id: Mapped[str] = mapped_column(
        ForeignKey("human_control_identities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token_prefix: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    scopes_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="active", nullable=False, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    revoke_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )


class HumanControlBreakGlassModel(Base):
    __tablename__ = "human_control_break_glass"
    __table_args__ = (
        UniqueConstraint(
            "request_idempotency_key",
            name="uq_human_control_break_glass_idempotency",
        ),
        UniqueConstraint(
            "activation_token_hash",
            name="uq_human_control_break_glass_token",
        ),
        CheckConstraint(
            "status IN ('requested', 'approved', 'active', 'used', 'rejected', "
            "'revoked', 'expired')",
            name="ck_human_control_break_glass_status",
        ),
        Index(
            "ix_human_control_break_glass_queue",
            "workspace_id",
            "status",
            "expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcbg")
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    requested_by_identity_id: Mapped[str] = mapped_column(
        ForeignKey("human_control_identities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    approved_by_identity_id: Mapped[str | None] = mapped_column(
        ForeignKey("human_control_identities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    request_idempotency_key: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    scopes_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="requested", nullable=False, index=True
    )
    activation_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    activation_token_prefix: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )


class HumanControlSecurityEventModel(Base):
    __tablename__ = "human_control_security_events"
    __table_args__ = (
        Index(
            "ix_human_control_security_events_history",
            "workspace_id",
            "actor_id",
            "event_type",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcsecevt")
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    identity_id: Mapped[str | None] = mapped_column(
        ForeignKey("human_control_identities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    client_ip: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class HumanControlBrowserPolicyModel(Base):
    __tablename__ = "human_control_browser_policies"
    __table_args__ = (
        UniqueConstraint("scope_key", name="uq_human_control_browser_policies_scope"),
        CheckConstraint(
            "cookie_samesite IN ('strict', 'lax', 'none')",
            name="ck_human_control_browser_policies_samesite",
        ),
        Index(
            "ix_human_control_browser_policies_effective",
            "workspace_id",
            "enabled",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcbpol")
    )
    scope_key: Mapped[str] = mapped_column(String(320), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    csrf_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    require_trusted_client: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_missing_origin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cookie_secure: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    cookie_samesite: Mapped[str] = mapped_column(String(16), default="strict", nullable=False)
    cookie_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    session_cookie_name: Mapped[str] = mapped_column(
        String(128), default="hc_browser_session", nullable=False
    )
    csrf_cookie_name: Mapped[str] = mapped_column(
        String(128), default="hc_csrf", nullable=False
    )
    csrf_header_name: Mapped[str] = mapped_column(
        String(128), default="X-CSRF-Token", nullable=False
    )
    session_ttl_seconds: Mapped[int] = mapped_column(Integer, default=28800, nullable=False)
    session_idle_seconds: Mapped[int] = mapped_column(Integer, default=1800, nullable=False)
    rotate_csrf_on_login: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    bind_user_agent: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    bind_client_ip: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), default="system", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class HumanControlTrustedClientModel(Base):
    __tablename__ = "human_control_trusted_clients"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "client_key", name="uq_human_control_trusted_clients_key"
        ),
        Index(
            "ix_human_control_trusted_clients_lookup",
            "workspace_id",
            "enabled",
            "client_key",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hctclient")
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    client_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    allowed_origins_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class HumanControlNotificationChannelModel(Base):
    __tablename__ = "human_control_notification_channels"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            name="uq_human_control_notification_channels_scope",
        ),
        CheckConstraint(
            "channel_type IN ('in_app', 'webhook', 'log', 'email', "
            "'slack', 'telegram', 'custom')",
            name="ck_human_control_notification_channels_type",
        ),
        CheckConstraint(
            "timeout_seconds >= 1 AND timeout_seconds <= 300",
            name="ck_human_control_notification_channels_timeout",
        ),
        CheckConstraint(
            "max_attempts >= 1 AND max_attempts <= 20",
            name="ck_human_control_notification_channels_attempts",
        ),
        Index(
            "ix_human_control_notification_channels_lookup",
            "workspace_id",
            "enabled",
            "channel_type",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcchannel")
    )
    scope_key: Mapped[str] = mapped_column(String(320), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    channel_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    endpoint_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    config_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    retry_base_seconds: Mapped[int] = mapped_column(
        Integer, default=30, nullable=False
    )
    default_ack_required: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    default_ack_timeout_seconds: Mapped[int] = mapped_column(
        Integer, default=1800, nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class HumanControlNotificationSubscriptionModel(Base):
    __tablename__ = "human_control_notification_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            name="uq_human_control_notification_subscriptions_scope",
        ),
        CheckConstraint(
            "min_priority >= 0 AND min_priority <= 100",
            name="ck_human_control_notification_subscriptions_priority",
        ),
        Index(
            "ix_human_control_notification_subscriptions_match",
            "workspace_id",
            "enabled",
            "channel_id",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcsub")
    )
    scope_key: Mapped[str] = mapped_column(String(320), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    subscription_key: Mapped[str] = mapped_column(
        String(160), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_id: Mapped[str] = mapped_column(
        ForeignKey("human_control_notification_channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    role_keys_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    event_patterns_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    source_types_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    risk_levels_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    min_priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ack_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ack_timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class HumanControlNotificationModel(Base):
    __tablename__ = "human_control_notifications"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_human_control_notifications_idempotency",
        ),
        CheckConstraint(
            "status IN ('pending', 'delivering', 'retrying', 'delivered', "
            "'acknowledged', 'failed', 'cancelled', 'expired')",
            name="ck_human_control_notifications_status",
        ),
        CheckConstraint(
            "ack_status IN ('not_required', 'pending', 'acknowledged', 'overdue')",
            name="ck_human_control_notifications_ack_status",
        ),
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_human_control_notifications_severity",
        ),
        CheckConstraint(
            "priority >= 0 AND priority <= 100",
            name="ck_human_control_notifications_priority",
        ),
        Index(
            "ix_human_control_notifications_queue",
            "status",
            "available_at",
            "priority",
            "created_at",
        ),
        Index(
            "ix_human_control_notifications_recipient",
            "workspace_id",
            "recipient_actor_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcnotif")
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    item_id: Mapped[str | None] = mapped_column(
        ForeignKey("human_control_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    channel_id: Mapped[str | None] = mapped_column(
        ForeignKey("human_control_notification_channels.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    subscription_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "human_control_notification_subscriptions.id", ondelete="SET NULL"
        ),
        nullable=True,
        index=True,
    )
    recipient_actor_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_type: Mapped[str | None] = mapped_column(
        String(96), nullable=True, index=True
    )
    source_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    severity: Mapped[str] = mapped_column(
        String(16), default="medium", nullable=False, index=True
    )
    priority: Mapped[int] = mapped_column(
        Integer, default=50, nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False, index=True
    )
    ack_required: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    ack_status: Mapped[str] = mapped_column(
        String(32), default="not_required", nullable=False, index=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    delivery_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    ack_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    acknowledged_by: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    acknowledgement_note: Mapped[str] = mapped_column(
        Text, default="", nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_by: Mapped[str] = mapped_column(
        String(255), default="system", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class HumanControlNotificationAttemptModel(Base):
    __tablename__ = "human_control_notification_attempts"
    __table_args__ = (
        UniqueConstraint(
            "notification_id",
            "attempt_number",
            name="uq_human_control_notification_attempts_number",
        ),
        CheckConstraint(
            "status IN ('started', 'delivered', 'failed')",
            name="ck_human_control_notification_attempts_status",
        ),
        Index(
            "ix_human_control_notification_attempts_history",
            "notification_id",
            "attempt_number",
            "started_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcattempt")
    )
    notification_id: Mapped[str] = mapped_column(
        ForeignKey("human_control_notifications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel_id: Mapped[str | None] = mapped_column(
        ForeignKey("human_control_notification_channels.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    adapter: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class HumanControlNotificationReceiptModel(Base):
    __tablename__ = "human_control_notification_receipts"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_human_control_notification_receipts_idempotency",
        ),
        CheckConstraint(
            "receipt_type IN ('delivered', 'read', 'acknowledged', 'overdue')",
            name="ck_human_control_notification_receipts_type",
        ),
        Index(
            "ix_human_control_notification_receipts_history",
            "notification_id",
            "receipt_type",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcreceipt")
    )
    notification_id: Mapped[str] = mapped_column(
        ForeignKey("human_control_notifications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    receipt_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class HumanControlOnCallScheduleModel(Base):
    __tablename__ = "human_control_on_call_schedules"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            name="uq_human_control_on_call_schedules_scope",
        ),
        CheckConstraint(
            "routing_strategy IN ('first_available', 'round_robin', "
            "'broadcast', 'primary_backup')",
            name="ck_human_control_on_call_schedules_strategy",
        ),
        Index(
            "ix_human_control_on_call_schedules_lookup",
            "workspace_id",
            "enabled",
            "schedule_key",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hconsched")
    )
    scope_key: Mapped[str] = mapped_column(String(320), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    schedule_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    timezone: Mapped[str] = mapped_column(String(96), default="UTC", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    routing_strategy: Mapped[str] = mapped_column(
        String(32), default="first_available", nullable=False
    )
    fallback_role_keys_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class HumanControlOnCallMemberModel(Base):
    __tablename__ = "human_control_on_call_members"
    __table_args__ = (
        UniqueConstraint(
            "schedule_id",
            "actor_id",
            name="uq_human_control_on_call_members_actor",
        ),
        CheckConstraint(
            "priority >= 0 AND priority <= 100",
            name="ck_human_control_on_call_members_priority",
        ),
        CheckConstraint(
            "max_active_notifications >= 0",
            name="ck_human_control_on_call_members_load",
        ),
        Index(
            "ix_human_control_on_call_members_active",
            "schedule_id",
            "enabled",
            "priority",
            "is_backup",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hconmember")
    )
    schedule_id: Mapped[str] = mapped_column(
        ForeignKey("human_control_on_call_schedules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role_key: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    is_backup: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    weekdays_json: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    start_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    end_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    max_active_notifications: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class HumanControlOperatorAvailabilityModel(Base):
    __tablename__ = "human_control_operator_availability"
    __table_args__ = (
        UniqueConstraint(
            "workspace_actor_key",
            name="uq_human_control_operator_availability_actor",
        ),
        CheckConstraint(
            "status IN ('available', 'busy', 'offline', 'do_not_disturb', "
            "'unknown')",
            name="ck_human_control_operator_availability_status",
        ),
        CheckConstraint(
            "source IN ('manual', 'heartbeat', 'schedule', 'system')",
            name="ck_human_control_operator_availability_source",
        ),
        CheckConstraint(
            "capacity_percent >= 0 AND capacity_percent <= 100",
            name="ck_human_control_operator_availability_capacity",
        ),
        Index(
            "ix_human_control_operator_availability_lookup",
            "workspace_id",
            "status",
            "available_until",
            "last_seen_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcavail")
    )
    workspace_actor_key: Mapped[str] = mapped_column(String(384), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32), default="unknown", nullable=False, index=True
    )
    capacity_percent: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    active_notification_limit: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    source: Mapped[str] = mapped_column(
        String(32), default="manual", nullable=False, index=True
    )
    available_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class HumanControlNotificationRoutingRuleModel(Base):
    __tablename__ = "human_control_notification_routing_rules"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            name="uq_human_control_notification_routing_rules_scope",
        ),
        CheckConstraint(
            "strategy IN ('first_available', 'round_robin', 'broadcast', "
            "'primary_backup')",
            name="ck_human_control_notification_routing_rules_strategy",
        ),
        CheckConstraint(
            "fallback_mode IN ('base_recipients', 'fallback_actors', "
            "'broadcast_roles', 'fail_closed')",
            name="ck_human_control_notification_routing_rules_fallback",
        ),
        CheckConstraint(
            "min_priority >= 0 AND min_priority <= 100",
            name="ck_human_control_notification_routing_rules_priority",
        ),
        CheckConstraint(
            "min_capacity_percent >= 0 AND min_capacity_percent <= 100",
            name="ck_human_control_notification_routing_rules_capacity",
        ),
        CheckConstraint(
            "max_recipients >= 1 AND max_recipients <= 100",
            name="ck_human_control_notification_routing_rules_recipients",
        ),
        Index(
            "ix_human_control_notification_routing_rules_match",
            "workspace_id",
            "enabled",
            "rule_priority",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcroute")
    )
    scope_key: Mapped[str] = mapped_column(String(320), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    rule_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rule_priority: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    event_patterns_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    source_types_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    risk_levels_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    min_priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    schedule_id: Mapped[str | None] = mapped_column(
        ForeignKey("human_control_on_call_schedules.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    role_keys_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    fallback_actor_ids_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    strategy: Mapped[str] = mapped_column(
        String(32), default="first_available", nullable=False
    )
    availability_required: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    min_capacity_percent: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False
    )
    heartbeat_ttl_seconds: Mapped[int] = mapped_column(
        Integer, default=300, nullable=False
    )
    max_recipients: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    fallback_mode: Mapped[str] = mapped_column(
        String(32), default="base_recipients", nullable=False
    )
    ack_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ack_timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_selected_actor_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class HumanControlNotificationEscalationRuleModel(Base):
    __tablename__ = "human_control_notification_escalation_rules"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            name="uq_human_control_notification_escalation_rules_scope",
        ),
        CheckConstraint(
            "min_priority >= 0 AND min_priority <= 100",
            name="ck_human_control_notification_escalation_rules_priority",
        ),
        CheckConstraint(
            "max_escalations >= 1 AND max_escalations <= 20",
            name="ck_human_control_notification_escalation_rules_count",
        ),
        CheckConstraint(
            "priority_increment >= 0 AND priority_increment <= 100",
            name="ck_human_control_notification_escalation_rules_increment",
        ),
        Index(
            "ix_human_control_notification_escalation_rules_match",
            "workspace_id",
            "enabled",
            "rule_priority",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcescrule")
    )
    scope_key: Mapped[str] = mapped_column(String(320), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    rule_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rule_priority: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    event_patterns_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    source_types_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    risk_levels_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    min_priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    trigger_on_json: Mapped[list[str]] = mapped_column(
        JSON, default=lambda: ["ack_overdue"], nullable=False
    )
    initial_delay_seconds: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    repeat_interval_seconds: Mapped[int] = mapped_column(
        Integer, default=900, nullable=False
    )
    max_escalations: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    target_schedule_id: Mapped[str | None] = mapped_column(
        ForeignKey("human_control_on_call_schedules.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    target_role_keys_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    target_actor_ids_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    channel_id: Mapped[str | None] = mapped_column(
        ForeignKey("human_control_notification_channels.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    strategy: Mapped[str] = mapped_column(
        String(32), default="first_available", nullable=False
    )
    priority_increment: Mapped[int] = mapped_column(
        Integer, default=10, nullable=False
    )
    ack_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ack_timeout_seconds: Mapped[int] = mapped_column(
        Integer, default=900, nullable=False
    )
    auto_resolve_on_ack: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class HumanControlNotificationEscalationModel(Base):
    __tablename__ = "human_control_notification_escalations"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_human_control_notification_escalations_idempotency",
        ),
        CheckConstraint(
            "status IN ('open', 'resolved', 'exhausted', 'cancelled')",
            name="ck_human_control_notification_escalations_status",
        ),
        CheckConstraint(
            "trigger_type IN ('ack_overdue', 'delivery_failed', 'unroutable', "
            "'manual')",
            name="ck_human_control_notification_escalations_trigger",
        ),
        Index(
            "ix_human_control_notification_escalations_queue",
            "status",
            "next_escalation_at",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcnesc")
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    original_notification_id: Mapped[str | None] = mapped_column(
        ForeignKey("human_control_notifications.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    rule_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "human_control_notification_escalation_rules.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default="open", nullable=False, index=True
    )
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    escalation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_escalation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_escalated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    spawned_notification_ids_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    resolved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution: Mapped[str] = mapped_column(Text, default="", nullable=False)
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class HumanControlNotificationEscalationAttemptModel(Base):
    __tablename__ = "human_control_notification_escalation_attempts"
    __table_args__ = (
        UniqueConstraint(
            "escalation_id",
            "sequence",
            name="uq_human_control_notification_escalation_attempts_sequence",
        ),
        CheckConstraint(
            "status IN ('started', 'routed', 'no_candidate', 'failed')",
            name="ck_human_control_notification_escalation_attempts_status",
        ),
        Index(
            "ix_human_control_notification_escalation_attempts_history",
            "escalation_id",
            "sequence",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcnescattempt")
    )
    escalation_id: Mapped[str] = mapped_column(
        ForeignKey("human_control_notification_escalations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    target_actor_ids_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    notification_ids_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class HumanControlOperatorAuditEventModel(Base):
    __tablename__ = "human_control_operator_audit_events"
    __table_args__ = (
        UniqueConstraint(
            "sequence",
            name="uq_human_control_operator_audit_events_sequence",
        ),
        UniqueConstraint(
            "event_id",
            name="uq_human_control_operator_audit_events_event",
        ),
        CheckConstraint(
            "outcome IN ('success', 'failure', 'unknown')",
            name="ck_human_control_operator_audit_events_outcome",
        ),
        CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_human_control_operator_audit_events_risk",
        ),
        Index(
            "ix_human_control_operator_audit_events_history",
            "workspace_id",
            "actor_id",
            "event_type",
            "occurred_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcopaudit")
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    event_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    identity_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    auth_method: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(
        String(16), default="unknown", nullable=False, index=True
    )
    risk_level: Mapped[str] = mapped_column(
        String(16), default="medium", nullable=False, index=True
    )
    correlation_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class HumanControlComplianceReportModel(Base):
    __tablename__ = "human_control_compliance_reports"
    __table_args__ = (
        CheckConstraint(
            "report_type IN ('activity_summary', 'privileged_access', "
            "'authentication', 'approval_governance', 'full')",
            name="ck_human_control_compliance_reports_type",
        ),
        Index(
            "ix_human_control_compliance_reports_history",
            "workspace_id",
            "report_type",
            "generated_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcreport")
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    report_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generated_by: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    report_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class HumanControlAccessReviewModel(Base):
    __tablename__ = "human_control_access_reviews"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_human_control_access_reviews_idempotency",
        ),
        CheckConstraint(
            "status IN ('open', 'completed', 'cancelled')",
            name="ck_human_control_access_reviews_status",
        ),
        Index(
            "ix_human_control_access_reviews_queue",
            "workspace_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcaccessreview")
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False, index=True)
    initiated_by: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    include_expired: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    include_sessions: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    completed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completion_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class HumanControlAccessReviewFindingModel(Base):
    __tablename__ = "human_control_access_review_findings"
    __table_args__ = (
        UniqueConstraint(
            "review_id",
            "fingerprint",
            name="uq_human_control_access_review_findings_fingerprint",
        ),
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_human_control_access_review_findings_severity",
        ),
        CheckConstraint(
            "status IN ('open', 'accepted', 'remediated', 'dismissed')",
            name="ck_human_control_access_review_findings_status",
        ),
        Index(
            "ix_human_control_access_review_findings_queue",
            "workspace_id",
            "status",
            "severity",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcaccessfinding")
    )
    review_id: Mapped[str] = mapped_column(
        ForeignKey("human_control_access_reviews.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    identity_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    finding_type: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    recommended_action: Mapped[str] = mapped_column(Text, default="", nullable=False)
    resolution: Mapped[str] = mapped_column(Text, default="", nullable=False)
    resolved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class HumanControlRetentionPolicyModel(Base):
    __tablename__ = "human_control_retention_policies"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            name="uq_human_control_retention_policies_scope",
        ),
        CheckConstraint(
            "enforcement_mode IN ('observe', 'archive', 'purge')",
            name="ck_human_control_retention_policies_mode",
        ),
        CheckConstraint(
            "default_retention_days >= 1",
            name="ck_human_control_retention_policies_default_days",
        ),
        CheckConstraint(
            "purge_batch_size >= 1 AND purge_batch_size <= 10000",
            name="ck_human_control_retention_policies_batch",
        ),
        Index(
            "ix_human_control_retention_policies_effective",
            "workspace_id",
            "enabled",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcretpol")
    )
    scope_key: Mapped[str] = mapped_column(String(320), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enforcement_mode: Mapped[str] = mapped_column(
        String(16), default="observe", nullable=False, index=True
    )
    default_retention_days: Mapped[int] = mapped_column(
        Integer, default=365, nullable=False
    )
    security_event_retention_days: Mapped[int] = mapped_column(
        Integer, default=365, nullable=False
    )
    notification_retention_days: Mapped[int] = mapped_column(
        Integer, default=180, nullable=False
    )
    compliance_report_retention_days: Mapped[int] = mapped_column(
        Integer, default=2555, nullable=False
    )
    operator_audit_retention_days: Mapped[int] = mapped_column(
        Integer, default=2555, nullable=False
    )
    archive_before_purge: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    require_human_approval: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    purge_batch_size: Mapped[int] = mapped_column(
        Integer, default=500, nullable=False
    )
    legal_hold_override_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    updated_by: Mapped[str] = mapped_column(
        String(255), default="system", nullable=False
    )
    last_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class HumanControlLegalHoldModel(Base):
    __tablename__ = "human_control_legal_holds"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            name="uq_human_control_legal_holds_scope",
        ),
        CheckConstraint(
            "status IN ('active', 'released', 'expired')",
            name="ck_human_control_legal_holds_status",
        ),
        Index(
            "ix_human_control_legal_holds_active",
            "workspace_id",
            "status",
            "expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hchold")
    )
    scope_key: Mapped[str] = mapped_column(String(320), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    hold_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="active", nullable=False, index=True
    )
    target_types_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    target_ids_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    event_patterns_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    actor_ids_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    custodian_ids_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    released_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    release_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class HumanControlEvidenceArchiveModel(Base):
    __tablename__ = "human_control_evidence_archives"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            name="uq_human_control_evidence_archives_scope",
        ),
        CheckConstraint(
            "status IN ('building', 'sealed', 'revoked')",
            name="ck_human_control_evidence_archives_status",
        ),
        CheckConstraint(
            "archive_type IN ('audit', 'compliance', 'legal_hold', "
            "'retention', 'external_audit', 'custom')",
            name="ck_human_control_evidence_archives_type",
        ),
        Index(
            "ix_human_control_evidence_archives_history",
            "workspace_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcarchive")
    )
    scope_key: Mapped[str] = mapped_column(String(320), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    archive_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    archive_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="building", nullable=False, index=True
    )
    legal_hold_id: Mapped[str | None] = mapped_column(
        ForeignKey("human_control_legal_holds.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_types_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    item_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    previous_archive_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    archive_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sealed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    revoke_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    sealed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class HumanControlEvidenceArchiveItemModel(Base):
    __tablename__ = "human_control_evidence_archive_items"
    __table_args__ = (
        UniqueConstraint(
            "archive_id",
            "ordinal",
            name="uq_human_control_evidence_archive_items_ordinal",
        ),
        UniqueConstraint(
            "archive_id",
            "source_type",
            "source_id",
            name="uq_human_control_evidence_archive_items_source",
        ),
        Index(
            "ix_human_control_evidence_archive_items_lookup",
            "archive_id",
            "source_type",
            "ordinal",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcarchiveitem")
    )
    archive_id: Mapped[str] = mapped_column(
        ForeignKey("human_control_evidence_archives.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    content_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class HumanControlRetentionRunModel(Base):
    __tablename__ = "human_control_retention_runs"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_human_control_retention_runs_idempotency",
        ),
        CheckConstraint(
            "run_mode IN ('preview', 'apply')",
            name="ck_human_control_retention_runs_mode",
        ),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_human_control_retention_runs_status",
        ),
        Index(
            "ix_human_control_retention_runs_history",
            "workspace_id",
            "status",
            "started_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcretrun")
    )
    policy_id: Mapped[str] = mapped_column(
        ForeignKey("human_control_retention_policies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    run_mode: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(16), default="running", nullable=False, index=True
    )
    started_by: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    cutoff_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    eligible_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    held_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    archived_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    deleted_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    errors_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    evidence_archive_id: Mapped[str | None] = mapped_column(
        ForeignKey("human_control_evidence_archives.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class HumanControlExternalAuditPackageModel(Base):
    __tablename__ = "human_control_external_audit_packages"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            name="uq_human_control_external_audit_packages_scope",
        ),
        CheckConstraint(
            "status IN ('draft', 'sealed', 'revoked', 'expired')",
            name="ck_human_control_external_audit_packages_status",
        ),
        Index(
            "ix_human_control_external_audit_packages_history",
            "workspace_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("hcauditpkg")
    )
    scope_key: Mapped[str] = mapped_column(String(320), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    package_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    auditor_name: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="draft", nullable=False, index=True
    )
    archive_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    report_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    package_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    generated_by: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    access_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    revoke_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class HumanControlBackupModel(Base):
    __tablename__ = "human_control_backups"
    __table_args__ = (
        UniqueConstraint("backup_key", name="uq_human_control_backups_key"),
        CheckConstraint("status IN ('creating','ready','verified','failed','restored','revoked')", name="ck_human_control_backups_status"),
        Index("ix_human_control_backups_history", "workspace_id", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("hcbackup"))
    backup_key: Mapped[str] = mapped_column(String(160), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="creating", nullable=False, index=True)
    backup_type: Mapped[str] = mapped_column(String(32), default="control_center", nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)


class HumanControlRestoreRunModel(Base):
    __tablename__ = "human_control_restore_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_human_control_restore_runs_idempotency"),
        CheckConstraint("status IN ('planned','validated','completed','failed','cancelled')", name="ck_human_control_restore_runs_status"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("hcrestore"))
    backup_id: Mapped[str] = mapped_column(ForeignKey("human_control_backups.id", ondelete="CASCADE"), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="planned", nullable=False, index=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    validation_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
