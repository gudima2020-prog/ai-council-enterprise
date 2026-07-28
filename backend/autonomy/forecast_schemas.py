from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class MissionForecastMode(str, Enum):
    MANUAL = "manual"
    HEURISTIC = "heuristic"
    ADAPTIVE = "adaptive"


class MissionForecastStatus(str, Enum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    INVALIDATED = "invalidated"


class MissionScenarioType(str, Enum):
    BASELINE = "baseline"
    OPTIMISTIC = "optimistic"
    PESSIMISTIC = "pessimistic"
    CUSTOM = "custom"


class MissionScenarioStatus(str, Enum):
    DRAFT = "draft"
    EVALUATED = "evaluated"
    SELECTED = "selected"
    ARCHIVED = "archived"


class PortfolioSimulationStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class MissionForecastPolicyUpsert(BaseModel):
    enabled: bool = False
    forecast_mode: MissionForecastMode = MissionForecastMode.MANUAL
    horizon_cycles: int = Field(default=10, ge=1, le=100)
    min_samples: int = Field(default=3, ge=1, le=1000)
    stale_after_seconds: int = Field(default=3600, ge=60, le=2_592_000)
    auto_refresh_enabled: bool = False
    require_human_approval: bool = True
    allow_auto_scenario_selection: bool = False
    confidence_threshold_percent: float = Field(default=70.0, ge=0, le=100)
    max_scenarios: int = Field(default=20, ge=1, le=200)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MissionForecastGenerateRequest(BaseModel):
    horizon_cycles: int | None = Field(default=None, ge=1, le=100)
    method: str = Field(default="builtin.heuristic.v1", min_length=1, max_length=255)
    actor_id: str = Field(default="system", min_length=1, max_length=255)
    reason: str = Field(default="manual forecast", max_length=2000)
    assumptions: dict[str, Any] = Field(default_factory=dict)
    force: bool = False


class MissionForecastInvalidateRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=2000)


class MissionScenarioCreate(BaseModel):
    scenario_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=10000)
    scenario_type: MissionScenarioType = MissionScenarioType.CUSTOM
    overrides: dict[str, Any] = Field(default_factory=dict)
    assumptions: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    evaluate: bool = True


class MissionScenarioUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    scenario_type: MissionScenarioType | None = None
    overrides: dict[str, Any] | None = None
    assumptions: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    expected_version: int | None = Field(default=None, ge=1)


class MissionScenarioEvaluateRequest(BaseModel):
    actor_id: str = Field(default="system", min_length=1, max_length=255)
    reason: str = Field(default="scenario evaluation", max_length=2000)
    refresh_baseline: bool = False


class MissionScenarioSelectionRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=2000)
    automatic: bool = False
    force: bool = False


class MissionScenarioArchiveRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=2000)


class WorkspacePortfolioSimulationRequest(BaseModel):
    name: str = Field(default="Workspace what-if simulation", min_length=1, max_length=255)
    actor_id: str = Field(default="system", min_length=1, max_length=255)
    include_statuses: list[str] = Field(default_factory=lambda: ["active", "paused", "draft"])
    mission_scenario_ids: dict[str, str] = Field(default_factory=dict)
    mission_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    total_budget_usd: float | None = Field(default=None, ge=0)
    max_parallel_missions: int | None = Field(default=None, ge=1, le=1000)
    agent_slots: int | None = Field(default=None, ge=0, le=100000)
    tool_slots: int | None = Field(default=None, ge=0, le=100000)
    compute_units: float | None = Field(default=None, ge=0)
    assumptions: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_statuses(self) -> "WorkspacePortfolioSimulationRequest":
        allowed = {"draft", "active", "paused", "completed", "failed", "cancelled"}
        unknown = sorted(set(self.include_statuses) - allowed)
        if unknown:
            raise ValueError(f"Unknown Mission statuses: {', '.join(unknown)}")
        return self


class WorkspacePortfolioSimulationCompareRequest(BaseModel):
    simulation_ids: list[str] = Field(min_length=2, max_length=20)


class MissionForecastQuery(BaseModel):
    status: MissionForecastStatus | None = None
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class MissionScenarioQuery(BaseModel):
    status: MissionScenarioStatus | None = None
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class PortfolioSimulationQuery(BaseModel):
    created_after: datetime | None = None
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
