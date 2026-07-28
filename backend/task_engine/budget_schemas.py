from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class BudgetPeriod(StrEnum):
    DAILY = "daily"
    MONTHLY = "monthly"
    LIFETIME = "lifetime"


class BudgetEnforcementMode(StrEnum):
    HARD = "hard"
    OBSERVE = "observe"


class BudgetPolicyCreate(BaseModel):
    workspace_id: str | None = None
    name: str = Field(..., min_length=1, max_length=255)
    enabled: bool = True
    period: BudgetPeriod = BudgetPeriod.MONTHLY
    enforcement_mode: BudgetEnforcementMode = BudgetEnforcementMode.HARD
    limit_usd: float | None = Field(default=None, ge=0)
    warning_threshold_percent: float = Field(default=80, ge=0, le=100)
    max_task_cost_usd: float | None = Field(default=None, ge=0)
    max_tasks_per_period: int | None = Field(default=None, ge=1)
    max_queued: int | None = Field(default=None, ge=1)
    max_running: int | None = Field(default=None, ge=1)
    allowed_task_types: list[str] = Field(default_factory=list)
    denied_task_types: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("allowed_task_types", "denied_task_types")
    @classmethod
    def normalize_types(cls, values: list[str]) -> list[str]:
        return sorted({item.strip().lower() for item in values if item.strip()})

    @model_validator(mode="after")
    def validate_type_lists(self) -> "BudgetPolicyCreate":
        overlap = set(self.allowed_task_types) & set(self.denied_task_types)
        if overlap:
            raise ValueError(
                "Task type cannot be both allowed and denied: "
                + ", ".join(sorted(overlap))
            )
        return self


class BudgetPolicyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None
    period: BudgetPeriod | None = None
    enforcement_mode: BudgetEnforcementMode | None = None
    limit_usd: float | None = Field(default=None, ge=0)
    warning_threshold_percent: float | None = Field(default=None, ge=0, le=100)
    max_task_cost_usd: float | None = Field(default=None, ge=0)
    max_tasks_per_period: int | None = Field(default=None, ge=1)
    max_queued: int | None = Field(default=None, ge=1)
    max_running: int | None = Field(default=None, ge=1)
    allowed_task_types: list[str] | None = None
    denied_task_types: list[str] | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("allowed_task_types", "denied_task_types")
    @classmethod
    def normalize_types(
        cls,
        values: list[str] | None,
    ) -> list[str] | None:
        if values is None:
            return None
        return sorted({item.strip().lower() for item in values if item.strip()})


class AdmissionEvaluateRequest(BaseModel):
    reserve: bool = False
    actor_id: str | None = Field(default=None, max_length=255)
    source: str = Field(default="api", min_length=1, max_length=64)


class AdmissionOverrideRequest(BaseModel):
    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=2000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskCostChargeRequest(BaseModel):
    amount_usd: float = Field(..., ge=0)
    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(default="Manual cost charge.", min_length=1, max_length=2000)
    metadata: dict[str, Any] = Field(default_factory=dict)
