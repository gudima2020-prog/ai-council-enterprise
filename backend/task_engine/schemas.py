from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.task_engine.enums import TaskPriority, TaskStatus, TaskType


class TaskCreate(BaseModel):
    workspace_id: str | None = None
    task_type: TaskType = TaskType.SYSTEM
    priority: TaskPriority = TaskPriority.NORMAL
    title: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    creator: str | None = None
    executor: str | None = None
    max_retries: int = Field(default=0, ge=0, le=100)
    retry_delay_seconds: float = Field(default=1.0, ge=0, le=3600)
    timeout_seconds: int = Field(default=300, ge=1, le=86400)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    priority: TaskPriority | None = None
    payload: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    executor: str | None = None
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


class TaskTransitionRequest(BaseModel):
    status: TaskStatus
    reason: str | None = Field(default=None, max_length=2000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskCancelRequest(BaseModel):
    reason: str = Field(
        default="Cancelled by user.",
        min_length=1,
        max_length=2000,
    )


class TaskRunCreate(BaseModel):
    status: TaskStatus = TaskStatus.CREATED
    attempt: int = Field(default=1, ge=1)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskLogCreate(BaseModel):
    run_id: str | None = None
    level: str = Field(default="INFO", min_length=1, max_length=16)
    message: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskArtifactCreate(BaseModel):
    run_id: str | None = None
    artifact_type: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=255)
    path: str = Field(..., min_length=1)
    mime_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    checksum: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str | None
    task_type: str
    priority: str
    status: str
    title: str
    description: str
    payload_json: dict[str, Any]
    result_json: dict[str, Any] | None
    creator: str | None
    executor: str | None
    retry_count: int
    max_retries: int
    retry_delay_seconds: float
    timeout_seconds: int
    cancel_requested_at: datetime | None
    cancel_reason: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
