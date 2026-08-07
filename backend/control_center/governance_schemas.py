from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.control_center.schemas import (
    HumanControlDecisionAction,
    HumanControlRiskLevel,
    HumanControlSourceType,
)


class HumanControlPermission(StrEnum):
    VIEW = "human_control.view"
    CLAIM = "human_control.claim"
    SNOOZE = "human_control.snooze"
    DECIDE = "human_control.decide"
    DECIDE_HIGH = "human_control.decide.high"
    DECIDE_CRITICAL = "human_control.decide.critical"
    ACCEPT_RISK = "human_control.accept_risk"
    APPROVE_BUDGET = "human_control.approve_budget"
    APPLY_LEARNING = "human_control.apply_learning"
    APPROVAL_INITIATE = "human_control.approval.initiate"
    APPROVAL_VOTE = "human_control.approval.vote"
    ESCALATE = "human_control.escalate"
    OVERRIDE = "human_control.override"
    MANAGE_ROLES = "human_control.manage_roles"
    MANAGE_POLICIES = "human_control.manage_policies"
    AUDIT = "human_control.audit"
    AUTH_VIEW = "human_control.auth.view"
    AUTH_MANAGE = "human_control.auth.manage"
    AUTH_TOKEN_MANAGE = "human_control.auth.token_manage"
    AUTH_BREAK_GLASS = "human_control.auth.break_glass"
    NOTIFICATION_VIEW = "human_control.notification.view"
    NOTIFICATION_ACK = "human_control.notification.ack"
    NOTIFICATION_MANAGE = "human_control.notification.manage"
    COMPLIANCE_VIEW = "human_control.compliance.view"
    COMPLIANCE_MANAGE = "human_control.compliance.manage"
    ACCESS_REVIEW = "human_control.access_review"
    RETENTION_VIEW = "human_control.retention.view"
    RETENTION_MANAGE = "human_control.retention.manage"
    LEGAL_HOLD_MANAGE = "human_control.legal_hold.manage"
    EVIDENCE_EXPORT = "human_control.evidence.export"
    DOCUMENT_VIEW = "document.view"
    DOCUMENT_UPLOAD = "document.upload"
    DOCUMENT_EXTRACT = "document.extract"
    DOCUMENT_OCR = "document.ocr"
    DOCUMENT_AI = "document.ai"
    DOCUMENT_DELETE = "document.delete"
    DOCUMENT_AUDIT = "document.audit"
    SECRET_VIEW = "secret.view"
    SECRET_MANAGE = "secret.manage"
    SECRET_ROTATE = "secret.rotate"
    SECRET_AUDIT = "secret.audit"


class HumanControlApprovalVoteDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    ABSTAIN = "abstain"


class HumanControlApprovalCaseStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    EXECUTING = "executing"
    EXECUTED = "executed"
    EXECUTION_FAILED = "execution_failed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class HumanControlEscalationStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class HumanControlRoleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    role_key: str = Field(..., min_length=1, max_length=96, pattern=r"^[a-z0-9_.-]+$")
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=10000)
    permissions: list[str] = Field(default_factory=list, max_length=100)
    allowed_risk_levels: list[HumanControlRiskLevel] = Field(default_factory=list)
    allowed_source_types: list[HumanControlSourceType] = Field(default_factory=list)
    allowed_actions: list[HumanControlDecisionAction] = Field(default_factory=list)
    enabled: bool = True
    actor_id: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    permissions: list[str] | None = Field(default=None, max_length=100)
    allowed_risk_levels: list[HumanControlRiskLevel] | None = None
    allowed_source_types: list[HumanControlSourceType] | None = None
    allowed_actions: list[HumanControlDecisionAction] | None = None
    enabled: bool | None = None
    actor_id: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] | None = None


class HumanControlBootstrapOwnerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(
        default="Initial Human Control owner bootstrap.",
        min_length=1,
        max_length=4000,
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlRoleBindingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    actor_id: str = Field(..., min_length=1, max_length=255)
    role_id: str | None = Field(default=None, max_length=64)
    role_key: str | None = Field(default=None, max_length=96)
    granted_by: str = Field(..., min_length=1, max_length=255)
    valid_from: datetime | None = None
    expires_at: datetime | None = None
    reason: str = Field(..., min_length=1, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_role_reference(self) -> "HumanControlRoleBindingCreate":
        if bool(self.role_id) == bool(self.role_key):
            raise ValueError("Укажите ровно одно поле: role_id или role_key.")
        if (
            self.valid_from is not None
            and self.expires_at is not None
            and self.expires_at <= self.valid_from
        ):
            raise ValueError("expires_at должно быть позже valid_from.")
        return self


class HumanControlRoleBindingRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=4000)


class HumanControlApprovalStepSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_key: str = Field(..., min_length=1, max_length=96, pattern=r"^[a-z0-9_.-]+$")
    title: str = Field(..., min_length=1, max_length=255)
    required_role_keys: list[str] = Field(default_factory=list, max_length=20)
    min_approvals: int = Field(default=1, ge=1, le=20)
    ttl_seconds: int | None = Field(default=None, ge=60, le=2592000)
    escalation_role_keys: list[str] = Field(default_factory=list, max_length=20)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlApprovalPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    policy_key: str = Field(..., min_length=1, max_length=96, pattern=r"^[a-z0-9_.-]+$")
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=10000)
    enabled: bool = True
    priority: int = Field(default=50, ge=0, le=100)
    source_types: list[HumanControlSourceType] = Field(default_factory=list)
    action_kinds: list[str] = Field(default_factory=list, max_length=50)
    risk_levels: list[HumanControlRiskLevel] = Field(default_factory=list)
    decision_actions: list[HumanControlDecisionAction] = Field(default_factory=list)
    steps: list[HumanControlApprovalStepSpec] = Field(..., min_length=1, max_length=20)
    distinct_approvers: bool = True
    prohibit_source_requester_approval: bool = True
    prohibit_case_initiator_approval: bool = False
    rejection_mode: str = Field(default="any", pattern=r"^(any|majority)$")
    case_ttl_seconds: int = Field(default=86400, ge=300, le=2592000)
    default_step_ttl_seconds: int = Field(default=3600, ge=60, le=2592000)
    escalation_after_seconds: int = Field(default=3600, ge=60, le=2592000)
    actor_id: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_steps(self) -> "HumanControlApprovalPolicyCreate":
        keys = [step.step_key for step in self.steps]
        if len(keys) != len(set(keys)):
            raise ValueError("step_key должен быть уникальным внутри политики.")
        return self


class HumanControlApprovalPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=100)
    source_types: list[HumanControlSourceType] | None = None
    action_kinds: list[str] | None = Field(default=None, max_length=50)
    risk_levels: list[HumanControlRiskLevel] | None = None
    decision_actions: list[HumanControlDecisionAction] | None = None
    steps: list[HumanControlApprovalStepSpec] | None = Field(
        default=None,
        min_length=1,
        max_length=20,
    )
    distinct_approvers: bool | None = None
    prohibit_source_requester_approval: bool | None = None
    prohibit_case_initiator_approval: bool | None = None
    rejection_mode: str | None = Field(default=None, pattern=r"^(any|majority)$")
    case_ttl_seconds: int | None = Field(default=None, ge=300, le=2592000)
    default_step_ttl_seconds: int | None = Field(default=None, ge=60, le=2592000)
    escalation_after_seconds: int | None = Field(default=None, ge=60, le=2592000)
    actor_id: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_steps(self) -> "HumanControlApprovalPolicyUpdate":
        if self.steps is not None:
            keys = [step.step_key for step in self.steps]
            if len(keys) != len(set(keys)):
                raise ValueError("step_key должен быть уникальным внутри политики.")
        return self


class HumanControlApprovalVoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    decision: HumanControlApprovalVoteDecision
    reason: str = Field(..., min_length=1, max_length=20000)
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    force: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlApprovalCaseCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=20000)
    force: bool = False


class HumanControlApprovalCaseRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=20000)
    force: bool = False


class HumanControlEscalationScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    actor_id: str = Field(default="system", min_length=1, max_length=255)


class HumanControlManualEscalationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=20000)
    target_role_keys: list[str] = Field(default_factory=list, max_length=20)
    assigned_to: str | None = Field(default=None, max_length=255)
    due_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlEscalationAcknowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(default="Escalation acknowledged.", max_length=4000)


class HumanControlEscalationResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    resolution: str = Field(..., min_length=1, max_length=20000)
