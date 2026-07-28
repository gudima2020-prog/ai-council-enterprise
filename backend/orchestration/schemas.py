from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.orchestration.enums import (
    ExecutionPlanStatus,
    ExecutionStepType,
)


def normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def normalize_dependencies(values: list[str]) -> list[str]:
    return list(
        dict.fromkeys(
            value.strip()
            for value in values
            if value.strip()
        )
    )


class ExecutionPlanStepCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_key: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    sequence: int = Field(default=0, ge=0, le=100_000)
    step_type: ExecutionStepType = ExecutionStepType.AGENT
    title: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    agent_role: str | None = Field(default=None, max_length=255)
    capability: str | None = Field(default=None, max_length=255)
    tool_name: str | None = Field(default=None, max_length=255)
    input: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, max_length=100)
    condition: dict[str, Any] | None = None
    timeout_seconds: int = Field(default=300, ge=1, le=86_400)
    max_retries: int = Field(default=0, ge=0, le=100)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        return value.strip()

    @field_validator("agent_role", "capability", "tool_name")
    @classmethod
    def normalize_optional_fields(cls, value: str | None) -> str | None:
        return normalize_optional(value)

    @field_validator("depends_on")
    @classmethod
    def normalize_depends_on(cls, values: list[str]) -> list[str]:
        return normalize_dependencies(values)


class ExecutionPlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = None
    source_task_id: str | None = None
    title: str = Field(..., min_length=1, max_length=255)
    objective: str = Field(..., min_length=1)
    strategy: str = ""
    version: int = Field(default=1, ge=1)
    max_parallel_steps: int = Field(default=4, ge=1, le=100)
    planner: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)
    steps: list[ExecutionPlanStepCreate] = Field(
        default_factory=list,
        max_length=500,
    )

    @field_validator("title", "objective")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("planner")
    @classmethod
    def normalize_planner(cls, value: str | None) -> str | None:
        return normalize_optional(value)


class ExecutionPlanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    objective: str | None = Field(default=None, min_length=1)
    strategy: str | None = None
    max_parallel_steps: int | None = Field(default=None, ge=1, le=100)
    planner: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] | None = None

    @field_validator("title", "objective")
    @classmethod
    def normalize_required_text(cls, value: str | None) -> str | None:
        return None if value is None else value.strip()

    @field_validator("planner")
    @classmethod
    def normalize_planner(cls, value: str | None) -> str | None:
        return normalize_optional(value)


class ExecutionPlanStepUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    sequence: int | None = Field(default=None, ge=0, le=100_000)
    step_type: ExecutionStepType | None = None
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    agent_role: str | None = Field(default=None, max_length=255)
    capability: str | None = Field(default=None, max_length=255)
    tool_name: str | None = Field(default=None, max_length=255)
    input: dict[str, Any] | None = None
    depends_on: list[str] | None = Field(default=None, max_length=100)
    condition: dict[str, Any] | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=86_400)
    max_retries: int | None = Field(default=None, ge=0, le=100)
    metadata: dict[str, Any] | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        return None if value is None else value.strip()

    @field_validator("agent_role", "capability", "tool_name")
    @classmethod
    def normalize_optional_fields(cls, value: str | None) -> str | None:
        return normalize_optional(value)

    @field_validator("depends_on")
    @classmethod
    def normalize_depends_on(
        cls,
        values: list[str] | None,
    ) -> list[str] | None:
        if values is None:
            return None
        return normalize_dependencies(values)


class ExecutionPlanTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ExecutionPlanStatus
    reason: str | None = Field(default=None, max_length=2000)
