from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class MissionLearningMode(str, Enum):
    MANUAL = "manual"
    OBSERVE = "observe"
    ADAPTIVE = "adaptive"


class MissionCalibrationScope(str, Enum):
    MISSION = "mission"
    WORKSPACE = "workspace"


class MissionCalibrationStatus(str, Enum):
    PROPOSED = "proposed"
    CURRENT = "current"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class MissionLearningRunStatus(str, Enum):
    PROPOSED = "proposed"
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"


class MissionLearningPolicyUpsert(BaseModel):
    enabled: bool = False
    learning_mode: MissionLearningMode = MissionLearningMode.MANUAL
    capture_outcomes_enabled: bool = True
    auto_calibration_enabled: bool = False
    auto_apply_enabled: bool = False
    require_human_approval: bool = True
    min_samples: int = Field(default=5, ge=1, le=10000)
    calibration_window: int = Field(default=50, ge=1, le=10000)
    max_probability_adjustment_percent: float = Field(
        default=15.0, ge=0, le=50
    )
    max_multiplier_adjustment_percent: float = Field(
        default=30.0, ge=0, le=100
    )
    min_improvement_percent: float = Field(default=2.0, ge=0, le=100)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_automatic_mode(self) -> "MissionLearningPolicyUpsert":
        if self.auto_apply_enabled and not self.auto_calibration_enabled:
            raise ValueError(
                "auto_apply_enabled requires auto_calibration_enabled."
            )
        if self.learning_mode == MissionLearningMode.MANUAL and (
            self.auto_calibration_enabled or self.auto_apply_enabled
        ):
            raise ValueError(
                "Automatic calibration is not allowed in manual learning mode."
            )
        return self


class MissionLearningRunRequest(BaseModel):
    scope_type: MissionCalibrationScope = MissionCalibrationScope.MISSION
    actor_id: str = Field(default="owner", min_length=1, max_length=255)
    reason: str = Field(
        default="Manual Mission learning run.",
        min_length=1,
        max_length=2000,
    )
    apply: bool = False
    automatic: bool = False
    force: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class MissionLearningRunDecisionRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=2000)
    force: bool = False


class MissionForecastOutcomeResolveRequest(BaseModel):
    forecast_id: str | None = Field(default=None, max_length=64)
    actual_status: str = Field(
        pattern="^(completed|failed|cancelled)$"
    )
    actual_cost_usd: float | None = Field(default=None, ge=0)
    actual_cycle_count: int | None = Field(default=None, ge=0)
    actual_completed_at: datetime | None = None
    actor_id: str = Field(default="owner", min_length=1, max_length=255)
    source_ref: str = Field(default="manual", min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MissionLearningReconcileRequest(BaseModel):
    workspace_id: str | None = Field(default=None, max_length=64)
    actor_id: str = Field(default="system", min_length=1, max_length=255)
    run_calibration: bool = False
    apply: bool = False
    force: bool = False


class MissionLearningQuery(BaseModel):
    status: MissionLearningRunStatus | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class MissionCalibrationQuery(BaseModel):
    status: MissionCalibrationStatus | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)
