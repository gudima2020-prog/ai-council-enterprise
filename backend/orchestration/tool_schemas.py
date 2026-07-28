from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ToolKind = Literal["builtin", "plugin", "http", "subprocess"]
ToolRiskLevel = Literal["low", "medium", "high", "critical"]
ToolIsolationMode = Literal["restricted", "trusted"]
PermissionEffect = Literal["allow", "deny"]


def normalize_key(value: str) -> str:
    return value.strip().lower()


class ToolCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = None
    tool_key: str = Field(
        ...,
        min_length=1,
        max_length=255,
        pattern=r"^[a-zA-Z0-9._:-]+$",
    )
    display_name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    kind: ToolKind = "plugin"
    handler_ref: str = Field(..., min_length=1, max_length=255)
    risk_level: ToolRiskLevel = "medium"
    enabled: bool = True
    requires_explicit_allow: bool = False
    isolation_mode: ToolIsolationMode = "restricted"
    timeout_seconds: int = Field(default=60, ge=1, le=86400)
    max_concurrency: int = Field(default=1, ge=1, le=100)
    max_input_bytes: int = Field(default=262144, ge=1, le=67108864)
    max_output_bytes: int = Field(default=1048576, ge=1, le=67108864)
    allow_network: bool = False
    allow_filesystem_read: bool = False
    allow_filesystem_write: bool = False
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool_key")
    @classmethod
    def normalize_tool_key(cls, value: str) -> str:
        return normalize_key(value)

    @field_validator("display_name", "handler_ref")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        return value.strip()


class ToolUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    handler_ref: str | None = Field(default=None, min_length=1, max_length=255)
    risk_level: ToolRiskLevel | None = None
    enabled: bool | None = None
    requires_explicit_allow: bool | None = None
    isolation_mode: ToolIsolationMode | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=86400)
    max_concurrency: int | None = Field(default=None, ge=1, le=100)
    max_input_bytes: int | None = Field(default=None, ge=1, le=67108864)
    max_output_bytes: int | None = Field(default=None, ge=1, le=67108864)
    allow_network: bool | None = None
    allow_filesystem_read: bool | None = None
    allow_filesystem_write: bool | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("display_name", "handler_ref", "description")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return None if value is None else value.strip()


class ToolPermissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = None
    agent_id: str | None = None
    effect: PermissionEffect
    action: Literal["execute"] = "execute"
    constraints: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None
    created_by: str | None = Field(default=None, max_length=255)
    reason: str | None = Field(default=None, max_length=2000)


class ToolPermissionEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = None
    agent_id: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)


class DirectToolExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = None
    agent_id: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = Field(default=None, max_length=255)


class ToolHandlerRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handler_ref: str = Field(..., min_length=1, max_length=255)
