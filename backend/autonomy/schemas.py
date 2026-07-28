from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AutonomyMode(StrEnum):
    OBSERVE = "observe"
    SUPERVISED = "supervised"
    AUTONOMOUS = "autonomous"


class MissionStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MissionGoalStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    ACHIEVED = "achieved"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MissionCycleStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MissionCycleTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    EVENT = "event"
    RECOVERY = "recovery"


class AutonomousWorkspacePolicyUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    autonomy_mode: AutonomyMode = AutonomyMode.SUPERVISED
    max_active_missions: int = Field(default=3, ge=1, le=1000)
    max_parallel_cycles: int = Field(default=1, ge=1, le=100)
    cycle_interval_seconds: int = Field(default=3600, ge=30, le=2_592_000)
    require_user_approval: bool = True
    allow_auto_start: bool = False
    planner_ref: str = Field(default="builtin.planner", min_length=1, max_length=255)
    planning_mode: str = Field(default="balanced", pattern="^(fast|balanced|thorough)$")
    auto_validate: bool = True
    auto_assign: bool = True
    strict_assignment: bool = False
    auto_review: bool = True
    review_threshold: int = Field(default=80, ge=0, le=100)
    auto_fix_review: bool = True
    max_review_rounds: int = Field(default=2, ge=1, le=10)
    require_review_pass: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_safety(self) -> "AutonomousWorkspacePolicyUpsert":
        if self.autonomy_mode != AutonomyMode.AUTONOMOUS:
            self.allow_auto_start = False
        if self.require_user_approval:
            self.allow_auto_start = False
        return self


class MissionGoalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal_key: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    title: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    success_criteria: str = ""
    sequence: int = Field(default=0, ge=-100000, le=100000)
    weight: float = Field(default=1.0, gt=0, le=100000)
    depends_on: list[str] = Field(default_factory=list, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_dependencies(self) -> "MissionGoalCreate":
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in self.depends_on:
            item = value.strip()
            if not item or item in seen:
                continue
            if item == self.goal_key:
                raise ValueError("Goal не может зависеть от самого себя.")
            seen.add(item)
            cleaned.append(item)
        self.depends_on = cleaned
        return self


class MissionGoalUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    success_criteria: str | None = None
    sequence: int | None = Field(default=None, ge=-100000, le=100000)
    weight: float | None = Field(default=None, gt=0, le=100000)
    depends_on: list[str] | None = Field(default=None, max_length=100)
    status: MissionGoalStatus | None = None
    metadata: dict[str, Any] | None = None


class MissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=1, max_length=255)
    objective: str = Field(..., min_length=1, max_length=20000)
    success_criteria: str = Field(default="", max_length=20000)
    strategy_hint: str = Field(default="", max_length=20000)
    priority: int = Field(default=50, ge=0, le=100)
    planner_ref: str | None = Field(default=None, min_length=1, max_length=255)
    planning_mode: str | None = Field(default=None, pattern="^(fast|balanced|thorough)$")
    auto_start_plans: bool | None = None
    max_cycles: int = Field(default=100, ge=1, le=100000)
    deadline_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    goals: list[MissionGoalCreate] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def unique_goal_keys(self) -> "MissionCreate":
        keys = [goal.goal_key for goal in self.goals]
        if len(keys) != len(set(keys)):
            raise ValueError("goal_key должен быть уникальным внутри Mission.")
        return self


class MissionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    objective: str | None = Field(default=None, min_length=1, max_length=20000)
    success_criteria: str | None = Field(default=None, max_length=20000)
    strategy_hint: str | None = Field(default=None, max_length=20000)
    priority: int | None = Field(default=None, ge=0, le=100)
    planner_ref: str | None = Field(default=None, min_length=1, max_length=255)
    planning_mode: str | None = Field(default=None, pattern="^(fast|balanced|thorough)$")
    auto_start_plans: bool | None = None
    max_cycles: int | None = Field(default=None, ge=1, le=100000)
    deadline_at: datetime | None = None
    metadata: dict[str, Any] | None = None


class MissionActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    reason: str = Field(default="Mission activated.", min_length=1, max_length=4000)
    first_cycle_at: datetime | None = None


class MissionPauseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=4000)


class MissionDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=4000)


class MissionProgressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal_id: str | None = Field(default=None, max_length=64)
    progress_percent: float | None = Field(default=None, ge=0, le=100)
    progress_delta: float | None = Field(default=None, ge=-100, le=100)
    status: MissionGoalStatus | None = None
    message: str = Field(..., min_length=1, max_length=10000)
    actor_id: str = Field(default="user", min_length=1, max_length=255)
    evidence: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ensure_progress_mode(self) -> "MissionProgressRequest":
        if self.progress_percent is not None and self.progress_delta is not None:
            raise ValueError(
                "Передай progress_percent или progress_delta, но не оба поля."
            )
        if (
            self.goal_id is None
            and self.progress_percent is None
            and self.progress_delta is None
        ):
            raise ValueError(
                "Для общего обновления Mission необходимо указать прогресс."
            )
        return self


class MissionCycleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger: MissionCycleTrigger = MissionCycleTrigger.MANUAL
    goal_ids: list[str] = Field(default_factory=list, max_length=500)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)
    scheduled_for: datetime | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class MissionCycleRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_start: bool | None = None
    wait: bool = False
    wait_timeout_seconds: float = Field(default=30.0, gt=0, le=86_400)
    force: bool = False
    context: dict[str, Any] = Field(default_factory=dict)


class MissionTickRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=25, ge=1, le=500)
