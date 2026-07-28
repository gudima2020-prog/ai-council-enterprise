from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SupervisorActionType(StrEnum):
    OBSERVE = "observe"
    NOTIFY = "notify"
    CANCEL = "cancel"
    REPLAN = "replan"


class SupervisorPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_key: str = Field(default="global", min_length=1, max_length=255)
    workspace_id: str | None = Field(default=None, max_length=64)
    name: str = Field(default="Default supervisor policy", min_length=1, max_length=255)
    enabled: bool = True
    automatic_actions_enabled: bool = False
    check_interval_seconds: int = Field(default=5, ge=1, le=3600)
    stall_timeout_seconds: int = Field(default=900, ge=10, le=604800)
    max_step_runtime_seconds: int = Field(default=600, ge=10, le=604800)
    retry_warning_threshold: int = Field(default=2, ge=1, le=100)
    failure_action: SupervisorActionType = SupervisorActionType.OBSERVE
    stall_action: SupervisorActionType = SupervisorActionType.OBSERVE
    long_running_action: SupervisorActionType = SupervisorActionType.OBSERVE
    retry_action: SupervisorActionType = SupervisorActionType.OBSERVE
    auto_start_replanned: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_scope(self) -> "SupervisorPolicyCreate":
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


class SupervisorPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None
    automatic_actions_enabled: bool | None = None
    check_interval_seconds: int | None = Field(default=None, ge=1, le=3600)
    stall_timeout_seconds: int | None = Field(default=None, ge=10, le=604800)
    max_step_runtime_seconds: int | None = Field(default=None, ge=10, le=604800)
    retry_warning_threshold: int | None = Field(default=None, ge=1, le=100)
    failure_action: SupervisorActionType | None = None
    stall_action: SupervisorActionType | None = None
    long_running_action: SupervisorActionType | None = None
    retry_action: SupervisorActionType | None = None
    auto_start_replanned: bool | None = None
    metadata: dict[str, Any] | None = None


class SupervisorIncidentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=4000)


class SupervisorInterventionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: SupervisorActionType
    reason: str = Field(..., min_length=1, max_length=4000)
    actor_id: str = Field(default="user", min_length=1, max_length=255)
    incident_id: str | None = Field(default=None, max_length=64)
    auto_start_replanned: bool | None = None
    wait: bool = True
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)
