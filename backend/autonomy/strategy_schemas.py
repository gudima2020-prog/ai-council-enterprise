from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MissionStrategySelectionMode(StrEnum):
    MANUAL = "manual"
    WEIGHTED = "weighted"
    ADAPTIVE = "adaptive"


class MissionStrategyStatus(StrEnum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    SELECTED = "selected"
    PAUSED = "paused"
    RETIRED = "retired"
    REJECTED = "rejected"


class MissionStrategyEvaluationType(StrEnum):
    INITIAL = "initial"
    MANUAL = "manual"
    ADAPTIVE = "adaptive"
    FEEDBACK = "feedback"


class MissionStrategyAssignmentStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MissionStrategyPolicyUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    selection_mode: MissionStrategySelectionMode = MissionStrategySelectionMode.MANUAL
    require_human_selection: bool = True
    auto_selection_enabled: bool = False
    min_selection_score: float = Field(default=60.0, ge=0, le=100)
    min_improvement_percent: float = Field(default=5.0, ge=0, le=100)
    exploration_weight_percent: float = Field(default=8.0, ge=0, le=50)
    performance_weight_percent: float = Field(default=20.0, ge=0, le=50)
    cooldown_cycles: int = Field(default=1, ge=0, le=1000)
    max_candidates: int = Field(default=20, ge=1, le=500)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_automatic_selection(self) -> "MissionStrategyPolicyUpsert":
        if self.require_human_selection:
            self.auto_selection_enabled = False
        if (
            self.auto_selection_enabled
            and self.selection_mode == MissionStrategySelectionMode.MANUAL
        ):
            raise ValueError(
                "Для auto_selection_enabled нужен режим weighted или adaptive."
            )
        return self


class MissionStrategyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_key: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=30000)
    strategy_hint: str = Field(default="", max_length=30000)
    hypothesis_id: str | None = Field(default=None, max_length=64)
    status: MissionStrategyStatus = MissionStrategyStatus.CANDIDATE
    priority: int = Field(default=50, ge=0, le=100)
    expected_value_percent: float = Field(default=50.0, ge=0, le=100)
    success_probability_percent: float = Field(default=50.0, ge=0, le=100)
    strategic_fit_percent: float = Field(default=50.0, ge=0, le=100)
    feasibility_percent: float = Field(default=50.0, ge=0, le=100)
    evidence_confidence_percent: float = Field(default=50.0, ge=0, le=100)
    risk_percent: float = Field(default=50.0, ge=0, le=100)
    cost_percent: float = Field(default=50.0, ge=0, le=100)
    duration_percent: float = Field(default=50.0, ge=0, le=100)
    constraints: list[str] = Field(default_factory=list, max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MissionStrategyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=30000)
    strategy_hint: str | None = Field(default=None, max_length=30000)
    hypothesis_id: str | None = Field(default=None, max_length=64)
    status: MissionStrategyStatus | None = None
    priority: int | None = Field(default=None, ge=0, le=100)
    expected_value_percent: float | None = Field(default=None, ge=0, le=100)
    success_probability_percent: float | None = Field(default=None, ge=0, le=100)
    strategic_fit_percent: float | None = Field(default=None, ge=0, le=100)
    feasibility_percent: float | None = Field(default=None, ge=0, le=100)
    evidence_confidence_percent: float | None = Field(default=None, ge=0, le=100)
    risk_percent: float | None = Field(default=None, ge=0, le=100)
    cost_percent: float | None = Field(default=None, ge=0, le=100)
    duration_percent: float | None = Field(default=None, ge=0, le=100)
    constraints: list[str] | None = Field(default=None, max_length=200)
    tags: list[str] | None = Field(default=None, max_length=100)
    metadata: dict[str, Any] | None = None


class MissionStrategyEvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    automatic: bool = False
    evaluation_type: MissionStrategyEvaluationType = (
        MissionStrategyEvaluationType.MANUAL
    )
    expected_value_percent: float | None = Field(default=None, ge=0, le=100)
    success_probability_percent: float | None = Field(default=None, ge=0, le=100)
    strategic_fit_percent: float | None = Field(default=None, ge=0, le=100)
    feasibility_percent: float | None = Field(default=None, ge=0, le=100)
    evidence_confidence_percent: float | None = Field(default=None, ge=0, le=100)
    risk_percent: float | None = Field(default=None, ge=0, le=100)
    cost_percent: float | None = Field(default=None, ge=0, le=100)
    duration_percent: float | None = Field(default=None, ge=0, le=100)
    rationale: str = Field(default="", max_length=20000)
    context: dict[str, Any] = Field(default_factory=dict)


class MissionStrategyRankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="system", min_length=1, max_length=255)
    adaptive: bool = True
    persist_evaluations: bool = True
    include_paused: bool = False
    context: dict[str, Any] = Field(default_factory=dict)


class MissionStrategySelectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    rationale: str = Field(..., min_length=1, max_length=20000)
    automatic: bool = False
    force: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class MissionStrategyAutoSelectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="system", min_length=1, max_length=255)
    force: bool = False
    rationale: str = Field(
        default="Adaptive Mission Strategy selection.",
        min_length=1,
        max_length=20000,
    )
    context: dict[str, Any] = Field(default_factory=dict)


class MissionStrategyRetireRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    rationale: str = Field(..., min_length=1, max_length=20000)
    rejected: bool = False


class MissionStrategyFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    reward_percent: float = Field(..., ge=0, le=100)
    outcome_summary: str = Field(default="", max_length=20000)
    status: MissionStrategyAssignmentStatus | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
