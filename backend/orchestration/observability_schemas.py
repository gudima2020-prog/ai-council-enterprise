from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExecutionSLOPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_key: str = Field(default="global", min_length=1, max_length=255)
    workspace_id: str | None = Field(default=None, max_length=64)
    name: str = Field(default="Default orchestration SLO", min_length=1, max_length=255)
    enabled: bool = True
    window_minutes: int = Field(default=60, ge=1, le=43200)
    evaluation_interval_seconds: int = Field(default=60, ge=5, le=86400)
    min_sample_size: int = Field(default=5, ge=1, le=1_000_000)
    target_plan_success_rate_pct: float = Field(default=95.0, ge=0, le=100)
    target_step_success_rate_pct: float = Field(default=97.0, ge=0, le=100)
    max_p95_plan_duration_ms: float = Field(default=600_000.0, ge=1)
    max_p95_step_duration_ms: float = Field(default=300_000.0, ge=1)
    max_tool_failure_rate_pct: float = Field(default=5.0, ge=0, le=100)
    max_open_critical_incidents: int = Field(default=0, ge=0, le=1_000_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_scope(self) -> "ExecutionSLOPolicyCreate":
        if self.workspace_id:
            expected = f"workspace:{self.workspace_id}"
            if self.scope_key == "global":
                self.scope_key = expected
            elif self.scope_key != expected:
                raise ValueError(
                    "Для Workspace scope_key должен иметь формат "
                    f"{expected}."
                )
        elif self.scope_key != "global":
            raise ValueError(
                "Без workspace_id поддерживается только scope_key='global'."
            )
        return self


class ExecutionSLOPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None
    window_minutes: int | None = Field(default=None, ge=1, le=43200)
    evaluation_interval_seconds: int | None = Field(default=None, ge=5, le=86400)
    min_sample_size: int | None = Field(default=None, ge=1, le=1_000_000)
    target_plan_success_rate_pct: float | None = Field(default=None, ge=0, le=100)
    target_step_success_rate_pct: float | None = Field(default=None, ge=0, le=100)
    max_p95_plan_duration_ms: float | None = Field(default=None, ge=1)
    max_p95_step_duration_ms: float | None = Field(default=None, ge=1)
    max_tool_failure_rate_pct: float | None = Field(default=None, ge=0, le=100)
    max_open_critical_incidents: int | None = Field(default=None, ge=0, le=1_000_000)
    metadata: dict[str, Any] | None = None


class MetricsCollectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    window_minutes: int = Field(default=60, ge=1, le=43200)
    persist: bool = True


class SLOEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    policy_id: str | None = Field(default=None, max_length=64)
    persist_snapshot: bool = True


class SLOBreachDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=4000)
