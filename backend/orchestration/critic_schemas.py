from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PlanReviewStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class PlanReviewDecision(StrEnum):
    PASS = "pass"
    REVISE = "revise"
    REJECT = "reject"


class PlanReviewSeverity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ExecutionPlanReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_ref: str = Field(
        default="builtin.critic",
        min_length=1,
        max_length=255,
    )
    threshold: int = Field(default=80, ge=0, le=100)
    auto_fix: bool = True
    max_rounds: int = Field(default=2, ge=1, le=10)
    require_pass: bool = True
    auto_assign_after_fix: bool = True
    strict_assignment: bool = False
    context: dict[str, Any] = Field(default_factory=dict)


class ExecutionPlanFixRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str | None = Field(default=None, max_length=64)
    fix_codes: list[str] = Field(default_factory=list, max_length=100)
    auto_assign_after_fix: bool = True
    strict_assignment: bool = False
