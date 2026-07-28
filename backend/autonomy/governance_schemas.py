from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MissionRiskCategory(StrEnum):
    STRATEGIC = "strategic"
    OPERATIONAL = "operational"
    TECHNICAL = "technical"
    FINANCIAL = "financial"
    LEGAL = "legal"
    SAFETY = "safety"
    SECURITY = "security"
    SCHEDULE = "schedule"
    QUALITY = "quality"
    DEPENDENCY = "dependency"
    OTHER = "other"


class MissionRiskStatus(StrEnum):
    IDENTIFIED = "identified"
    MONITORING = "monitoring"
    MITIGATED = "mitigated"
    ACCEPTED = "accepted"
    MATERIALIZED = "materialized"
    CLOSED = "closed"


class MissionRiskSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class MissionRiskDecision(StrEnum):
    MONITOR = "monitor"
    MITIGATE = "mitigate"
    ACCEPT = "accept"
    ESCALATE = "escalate"
    MATERIALIZE = "materialize"
    CLOSE = "close"


class MissionHypothesisStatus(StrEnum):
    PROPOSED = "proposed"
    TESTING = "testing"
    SUPPORTED = "supported"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    INVALIDATED = "invalidated"


class MissionHypothesisDecision(StrEnum):
    SUPPORT = "support"
    REJECT = "reject"
    INCONCLUSIVE = "inconclusive"
    INVALIDATE = "invalidate"
    REOPEN = "reopen"


class MissionCheckpointType(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    RISK = "risk"
    HYPOTHESIS = "hypothesis"
    DEADLINE = "deadline"
    BUDGET = "budget"
    PHASE_GATE = "phase_gate"


class MissionCheckpointStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RESOLVED = "resolved"
    DEFERRED = "deferred"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class MissionCheckpointDecision(StrEnum):
    CONTINUE = "continue"
    PAUSE = "pause"
    REPLAN = "replan"
    CANCEL = "cancel"
    ACCEPT_RISK = "accept_risk"
    REQUEST_REVIEW = "request_review"
    DEFER = "defer"


class MissionRiskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_key: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=20000)
    goal_id: str | None = Field(default=None, max_length=64)
    category: MissionRiskCategory = MissionRiskCategory.OTHER
    probability_percent: float = Field(default=25.0, ge=0, le=100)
    impact_percent: float = Field(default=25.0, ge=0, le=100)
    owner_id: str | None = Field(default=None, max_length=255)
    mitigation_plan: str = Field(default="", max_length=20000)
    contingency_plan: str = Field(default="", max_length=20000)
    trigger_indicators: list[str] = Field(default_factory=list, max_length=100)
    checkpoint_required: bool = True
    requires_human_decision: bool = True
    due_at: datetime | None = None
    next_review_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MissionRiskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=20000)
    goal_id: str | None = Field(default=None, max_length=64)
    category: MissionRiskCategory | None = None
    owner_id: str | None = Field(default=None, max_length=255)
    mitigation_plan: str | None = Field(default=None, max_length=20000)
    contingency_plan: str | None = Field(default=None, max_length=20000)
    trigger_indicators: list[str] | None = Field(default=None, max_length=100)
    checkpoint_required: bool | None = None
    requires_human_decision: bool | None = None
    due_at: datetime | None = None
    next_review_at: datetime | None = None
    metadata: dict[str, Any] | None = None


class MissionRiskAssessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    probability_percent: float | None = Field(default=None, ge=0, le=100)
    impact_percent: float | None = Field(default=None, ge=0, le=100)
    decision: MissionRiskDecision = MissionRiskDecision.MONITOR
    rationale: str = Field(..., min_length=1, max_length=10000)
    indicators: dict[str, Any] = Field(default_factory=dict)
    actor_id: str = Field(default="user", min_length=1, max_length=255)
    automatic: bool = False
    create_checkpoint: bool = True


class MissionRiskMaterializeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    rationale: str = Field(..., min_length=1, max_length=10000)
    impact_percent: float | None = Field(default=None, ge=0, le=100)
    indicators: dict[str, Any] = Field(default_factory=dict)
    create_checkpoint: bool = True


class MissionRiskCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    rationale: str = Field(..., min_length=1, max_length=10000)
    status: MissionRiskStatus = MissionRiskStatus.CLOSED

    @model_validator(mode="after")
    def validate_terminal_status(self) -> "MissionRiskCloseRequest":
        if self.status not in {
            MissionRiskStatus.CLOSED,
            MissionRiskStatus.MITIGATED,
            MissionRiskStatus.ACCEPTED,
        }:
            raise ValueError("Для закрытия допустимы closed, mitigated или accepted.")
        return self


class MissionHypothesisCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_key: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    statement: str = Field(..., min_length=1, max_length=20000)
    rationale: str = Field(default="", max_length=20000)
    goal_id: str | None = Field(default=None, max_length=64)
    confidence_percent: float = Field(default=50.0, ge=0, le=100)
    target_confidence_percent: float = Field(default=80.0, ge=0, le=100)
    min_evidence_count: int = Field(default=2, ge=1, le=1000)
    support_threshold_percent: float = Field(default=70.0, ge=0, le=100)
    reject_threshold_percent: float = Field(default=30.0, ge=0, le=100)
    test_plan: str = Field(default="", max_length=20000)
    success_criteria: str = Field(default="", max_length=20000)
    failure_criteria: str = Field(default="", max_length=20000)
    owner_id: str | None = Field(default=None, max_length=255)
    checkpoint_on_inconclusive: bool = True
    requires_human_decision: bool = True
    due_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_thresholds(self) -> "MissionHypothesisCreate":
        if self.reject_threshold_percent >= self.support_threshold_percent:
            raise ValueError("reject_threshold_percent должен быть меньше support_threshold_percent.")
        return self


class MissionHypothesisUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str | None = Field(default=None, min_length=1, max_length=20000)
    rationale: str | None = Field(default=None, max_length=20000)
    goal_id: str | None = Field(default=None, max_length=64)
    target_confidence_percent: float | None = Field(default=None, ge=0, le=100)
    min_evidence_count: int | None = Field(default=None, ge=1, le=1000)
    support_threshold_percent: float | None = Field(default=None, ge=0, le=100)
    reject_threshold_percent: float | None = Field(default=None, ge=0, le=100)
    test_plan: str | None = Field(default=None, max_length=20000)
    success_criteria: str | None = Field(default=None, max_length=20000)
    failure_criteria: str | None = Field(default=None, max_length=20000)
    owner_id: str | None = Field(default=None, max_length=255)
    checkpoint_on_inconclusive: bool | None = None
    requires_human_decision: bool | None = None
    due_at: datetime | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_thresholds(self) -> "MissionHypothesisUpdate":
        if (
            self.reject_threshold_percent is not None
            and self.support_threshold_percent is not None
            and self.reject_threshold_percent >= self.support_threshold_percent
        ):
            raise ValueError("reject_threshold_percent должен быть меньше support_threshold_percent.")
        return self


class MissionHypothesisEvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    automatic: bool = False
    decision: MissionHypothesisDecision | None = None
    confidence_percent: float | None = Field(default=None, ge=0, le=100)
    evidence_ids: list[str] = Field(default_factory=list, max_length=1000)
    rationale: str = Field(default="", max_length=10000)
    create_checkpoint: bool = True

    @model_validator(mode="after")
    def validate_decision(self) -> "MissionHypothesisEvaluateRequest":
        if not self.automatic and self.decision is None:
            raise ValueError("Для ручной оценки необходимо указать decision.")
        return self


class MissionHypothesisReopenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    rationale: str = Field(..., min_length=1, max_length=10000)
    confidence_percent: float | None = Field(default=None, ge=0, le=100)


class MissionCheckpointCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_key: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    checkpoint_type: MissionCheckpointType = MissionCheckpointType.MANUAL
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=20000)
    goal_id: str | None = Field(default=None, max_length=64)
    cycle_id: str | None = Field(default=None, max_length=64)
    risk_id: str | None = Field(default=None, max_length=64)
    hypothesis_id: str | None = Field(default=None, max_length=64)
    blocking: bool = True
    requires_human: bool = True
    auto_decision_enabled: bool = False
    auto_decision_threshold_percent: float = Field(default=90.0, ge=0, le=100)
    recommended_decision: MissionCheckpointDecision | None = None
    recommendation_confidence_percent: float = Field(default=0.0, ge=0, le=100)
    due_at: datetime | None = None
    expires_at: datetime | None = None
    trigger_conditions: dict[str, Any] = Field(default_factory=dict)
    options: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    context: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_automation(self) -> "MissionCheckpointCreate":
        if self.requires_human:
            self.auto_decision_enabled = False
        if self.auto_decision_enabled and self.recommended_decision not in {
            MissionCheckpointDecision.CONTINUE,
            MissionCheckpointDecision.ACCEPT_RISK,
        }:
            raise ValueError(
                "Автоматическое решение разрешено только для continue или accept_risk."
            )
        if self.expires_at is not None and self.due_at is not None:
            if self.expires_at <= self.due_at:
                raise ValueError("expires_at должен быть позже due_at.")
        return self


class MissionCheckpointEvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="system", min_length=1, max_length=255)
    auto_decide: bool = True
    context: dict[str, Any] = Field(default_factory=dict)


class MissionCheckpointDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: MissionCheckpointDecision
    actor_id: str = Field(default="user", min_length=1, max_length=255)
    rationale: str = Field(..., min_length=1, max_length=10000)
    selected_option: str | None = Field(default=None, max_length=255)
    defer_until: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_defer(self) -> "MissionCheckpointDecisionRequest":
        if self.decision == MissionCheckpointDecision.DEFER and self.defer_until is None:
            raise ValueError("Для решения defer необходимо указать defer_until.")
        return self


class MissionCheckpointCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    rationale: str = Field(..., min_length=1, max_length=10000)


class MissionCheckpointScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_decide: bool = False
    actor_id: str = Field(default="system", min_length=1, max_length=255)
