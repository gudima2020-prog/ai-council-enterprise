from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalGateDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: bool = True
    gate_key: str = Field(
        default="execution",
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_.:-]+$",
    )
    prompt: str = Field(
        default="Подтвердите выполнение задачи.",
        min_length=1,
        max_length=4000,
    )
    expires_in_seconds: int | None = Field(
        default=None,
        ge=60,
        le=2_592_000,
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("gate_key")
    @classmethod
    def normalize_gate_key(cls, value: str) -> str:
        return value.strip().lower()


class TaskApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate_key: str = Field(
        default="execution",
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_.:-]+$",
    )
    prompt: str = Field(
        default="Подтвердите выполнение задачи.",
        min_length=1,
        max_length=4000,
    )
    requested_by: str | None = Field(default=None, max_length=255)
    expires_in_seconds: int | None = Field(
        default=None,
        ge=60,
        le=2_592_000,
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("gate_key")
    @classmethod
    def normalize_gate_key(cls, value: str) -> str:
        return value.strip().lower()


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ApprovalDecision
    decided_by: str = Field(..., min_length=1, max_length=255)
    note: str | None = Field(default=None, max_length=4000)
