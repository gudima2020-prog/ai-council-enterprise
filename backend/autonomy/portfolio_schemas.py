from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MissionDependencyType(StrEnum):
    HARD = "hard"
    SOFT = "soft"
    INFORMATIONAL = "informational"


class MissionDependencyStatus(StrEnum):
    PENDING = "pending"
    SATISFIED = "satisfied"
    WAIVED = "waived"
    FAILED = "failed"


class MissionPortfolioMode(StrEnum):
    MANUAL = "manual"
    WEIGHTED = "weighted"
    ADAPTIVE = "adaptive"


class MissionPortfolioAssignmentStatus(StrEnum):
    SELECTED = "selected"
    DEFERRED = "deferred"
    BLOCKED = "blocked"
    RELEASED = "released"


class MissionDependencyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    depends_on_mission_id: str = Field(..., min_length=1, max_length=64)
    dependency_type: MissionDependencyType = MissionDependencyType.HARD
    required_statuses: list[str] = Field(default_factory=lambda: ["completed"])
    allow_failed: bool = False
    priority: int = Field(default=50, ge=0, le=100)
    rationale: str = Field(default="", max_length=20_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("required_statuses")
    @classmethod
    def validate_required_statuses(cls, value: list[str]) -> list[str]:
        allowed = {"draft", "active", "paused", "completed", "failed", "cancelled"}
        normalized = sorted({str(item).strip().lower() for item in value if str(item).strip()})
        if not normalized:
            raise ValueError("required_statuses cannot be empty")
        unknown = sorted(set(normalized) - allowed)
        if unknown:
            raise ValueError(f"Unknown Mission statuses: {', '.join(unknown)}")
        return normalized


class MissionDependencyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dependency_type: MissionDependencyType | None = None
    required_statuses: list[str] | None = None
    allow_failed: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=100)
    rationale: str | None = Field(default=None, max_length=20_000)
    metadata: dict[str, Any] | None = None

    @field_validator("required_statuses")
    @classmethod
    def validate_required_statuses(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        allowed = {"draft", "active", "paused", "completed", "failed", "cancelled"}
        normalized = sorted({str(item).strip().lower() for item in value if str(item).strip()})
        if not normalized:
            raise ValueError("required_statuses cannot be empty")
        unknown = sorted(set(normalized) - allowed)
        if unknown:
            raise ValueError(f"Unknown Mission statuses: {', '.join(unknown)}")
        return normalized


class MissionDependencyWaiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=20_000)


class MissionPortfolioPolicyUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    prioritization_mode: MissionPortfolioMode = MissionPortfolioMode.MANUAL
    require_human_approval: bool = True
    auto_rebalance_enabled: bool = False
    enforce_cycle_admission: bool = False
    max_parallel_missions: int = Field(default=3, ge=1, le=1000)
    min_selection_score: float = Field(default=50.0, ge=0, le=100)
    rebalance_interval_seconds: int = Field(default=300, ge=30, le=2_592_000)
    priority_weight: float = Field(default=35.0, ge=0, le=100)
    progress_weight: float = Field(default=15.0, ge=0, le=100)
    deadline_weight: float = Field(default=20.0, ge=0, le=100)
    dependency_weight: float = Field(default=20.0, ge=0, le=100)
    strategy_weight: float = Field(default=10.0, ge=0, le=100)
    risk_penalty_weight: float = Field(default=15.0, ge=0, le=100)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_automatic_mode(self) -> "MissionPortfolioPolicyUpsert":
        if self.require_human_approval:
            self.auto_rebalance_enabled = False
        if self.auto_rebalance_enabled and self.prioritization_mode == MissionPortfolioMode.MANUAL:
            raise ValueError("auto_rebalance_enabled requires weighted or adaptive mode")
        positive = (
            self.priority_weight
            + self.progress_weight
            + self.deadline_weight
            + self.dependency_weight
            + self.strategy_weight
        )
        if positive <= 0:
            raise ValueError("At least one positive portfolio weight must be greater than zero")
        return self


class MissionPortfolioRankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="system", min_length=1, max_length=255)
    automatic: bool = False
    persist: bool = True
    include_paused: bool = False
    context: dict[str, Any] = Field(default_factory=dict)


class MissionPortfolioRebalanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    automatic: bool = False
    apply: bool = False
    force: bool = False
    include_paused: bool = False
    rationale: str = Field(default="Mission portfolio rebalance requested.", min_length=1, max_length=20_000)
    context: dict[str, Any] = Field(default_factory=dict)


class MissionPortfolioAssignmentOverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: MissionPortfolioAssignmentStatus
    actor_id: str = Field(default="user", min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=20_000)
    force: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
