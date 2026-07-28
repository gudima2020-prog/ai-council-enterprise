from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlannerMode(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    THOROUGH = "thorough"


class PlannerRunType(StrEnum):
    GENERATE = "generate"
    REPLAN = "replan"


class PlannerRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ExecutionPlanGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = None
    source_task_id: str | None = None
    title: str | None = Field(default=None, max_length=255)
    objective: str | None = None
    strategy_hint: str = ""
    planner_ref: str = Field(
        default="builtin.planner",
        min_length=1,
        max_length=255,
    )
    mode: PlannerMode = PlannerMode.BALANCED
    max_parallel_steps: int = Field(default=4, ge=1, le=100)
    auto_validate: bool = True
    auto_assign: bool = True
    strict_assignment: bool = False
    auto_start: bool = False
    wait: bool = False
    wait_timeout_seconds: float = Field(default=30.0, gt=0, le=86_400)
    auto_replan: bool = False
    max_replans: int = Field(default=1, ge=0, le=20)
    auto_review: bool = True
    review_threshold: int = Field(default=80, ge=0, le=100)
    auto_fix_review: bool = True
    max_review_rounds: int = Field(default=2, ge=1, le=10)
    require_review_pass: bool = True
    context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ensure_source_or_objective(self) -> "ExecutionPlanGenerationRequest":
        if not self.source_task_id and not (self.objective or "").strip():
            raise ValueError(
                "Необходимо передать source_task_id или objective."
            )
        return self


class ExecutionPlanReplanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=4000)
    failed_step_id: str | None = Field(default=None, max_length=64)
    planner_ref: str | None = Field(default=None, max_length=255)
    mode: PlannerMode | None = None
    preserve_completed_outputs: bool = True
    auto_validate: bool = True
    auto_assign: bool = True
    strict_assignment: bool = False
    auto_start: bool = False
    wait: bool = False
    wait_timeout_seconds: float = Field(default=30.0, gt=0, le=86_400)
    force: bool = False
    auto_review: bool = True
    review_threshold: int = Field(default=80, ge=0, le=100)
    auto_fix_review: bool = True
    max_review_rounds: int = Field(default=2, ge=1, le=10)
    require_review_pass: bool = True
    context: dict[str, Any] = Field(default_factory=dict)


class PlannerRunListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runs: list[dict[str, Any]]
