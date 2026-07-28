from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from backend.task_engine.enums import TaskPriority


class DeadLetterReplayRequest(BaseModel):
    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=2000)
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    payload_patch: dict[str, Any] = Field(default_factory=dict)
    priority: TaskPriority | None = None
    max_retries: int | None = Field(default=None, ge=0, le=100)
    retry_delay_seconds: float | None = Field(
        default=None,
        ge=0,
        le=3600,
    )
    timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        le=86400,
    )
    force: bool = False


class DeadLetterDiscardRequest(BaseModel):
    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=2000)


class DeadLetterCaptureRequest(BaseModel):
    reason_code: str = Field(
        default="manual_capture",
        min_length=1,
        max_length=64,
    )
    error_type: str | None = Field(default=None, max_length=255)
    error_message: str | None = Field(default=None, max_length=10000)
    actor_id: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)
