from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ExecutionPlanRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_validate: bool = True
    auto_assign: bool = True
    strict_assignment: bool = True
    replace_assignments: bool = False
    wait: bool = False
    wait_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=86_400,
    )


class ExecutionPlanCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        default="Execution Plan cancelled by user.",
        min_length=1,
        max_length=2000,
    )


class RuntimeExecutorRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executor_ref: str = Field(..., min_length=1, max_length=255)
