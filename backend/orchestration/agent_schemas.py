from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def normalize_token(value: str) -> str:
    return value.strip().lower()


def normalize_tokens(values: list[str]) -> list[str]:
    return sorted(
        {
            normalize_token(value)
            for value in values
            if normalize_token(value)
        }
    )


class AgentCapabilityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    proficiency: int = Field(default=50, ge=0, le=100)
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return normalize_token(value)


class AgentCapabilityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proficiency: int | None = Field(default=None, ge=0, le=100)
    enabled: bool | None = None
    metadata: dict[str, Any] | None = None


class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = None
    agent_key: str = Field(
        ...,
        min_length=1,
        max_length=255,
        pattern=r"^[a-zA-Z0-9._:-]+$",
    )
    display_name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    roles: list[str] = Field(default_factory=list, max_length=64)
    tools: list[str] = Field(default_factory=list, max_length=128)
    enabled: bool = True
    status: Literal["available", "busy", "offline", "maintenance"] = "available"
    priority: int = Field(default=50, ge=0, le=100)
    max_concurrency: int = Field(default=1, ge=1, le=100)
    executor_ref: str = Field(..., min_length=1, max_length=255)
    model_slug: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[AgentCapabilityCreate] = Field(
        default_factory=list,
        max_length=256,
    )

    @field_validator("agent_key", "status")
    @classmethod
    def normalize_key_fields(cls, value: str) -> str:
        return normalize_token(value)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        return value.strip()

    @field_validator("roles", "tools")
    @classmethod
    def normalize_list_fields(cls, values: list[str]) -> list[str]:
        return normalize_tokens(values)

    @field_validator("executor_ref", "model_slug")
    @classmethod
    def normalize_optional_refs(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class AgentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    roles: list[str] | None = Field(default=None, max_length=64)
    tools: list[str] | None = Field(default=None, max_length=128)
    enabled: bool | None = None
    status: Literal["available", "busy", "offline", "maintenance"] | None = None
    priority: int | None = Field(default=None, ge=0, le=100)
    max_concurrency: int | None = Field(default=None, ge=1, le=100)
    executor_ref: str | None = Field(default=None, max_length=255)
    model_slug: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] | None = None

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        return None if value is None else value.strip()

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return None if value is None else value.strip()

    @field_validator("roles", "tools")
    @classmethod
    def normalize_list_fields(
        cls,
        values: list[str] | None,
    ) -> list[str] | None:
        return None if values is None else normalize_tokens(values)

    @field_validator("status")
    @classmethod
    def normalize_status(cls, value: str | None) -> str | None:
        return None if value is None else normalize_token(value)

    @field_validator("executor_ref", "model_slug")
    @classmethod
    def normalize_optional_refs(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class AgentAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replace_existing: bool = False
    strict: bool = True


class AgentManualAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., min_length=1, max_length=64)
    force: bool = False
    reason: str | None = Field(default=None, max_length=2000)


class AgentMatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = None
    role: str | None = Field(default=None, max_length=255)
    capability: str | None = Field(default=None, max_length=255)
    required_tools: list[str] = Field(default_factory=list, max_length=128)
    limit: int = Field(default=20, ge=1, le=200)

    @field_validator("role", "capability")
    @classmethod
    def normalize_optional_tokens(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_token(value)
        return normalized or None

    @field_validator("required_tools")
    @classmethod
    def normalize_required_tools(cls, values: list[str]) -> list[str]:
        return normalize_tokens(values)
