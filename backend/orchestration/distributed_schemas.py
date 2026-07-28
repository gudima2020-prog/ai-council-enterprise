from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkerRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_key: str = Field(..., min_length=1, max_length=255)
    instance_id: str = Field(..., min_length=1, max_length=255)
    hostname: str | None = Field(default=None, max_length=255)
    process_id: int | None = Field(default=None, ge=0)
    queues: list[str] = Field(default_factory=lambda: ["default"])
    capabilities: list[str] = Field(default_factory=list)
    max_concurrency: int = Field(default=1, ge=1, le=1024)
    heartbeat_ttl_seconds: int = Field(default=45, ge=5, le=3600)
    default_lease_seconds: int = Field(default=90, ge=5, le=86400)
    metadata: dict[str, Any] = Field(default_factory=dict)
    force_takeover: bool = False

    @model_validator(mode="after")
    def normalize_lists(self) -> "WorkerRegisterRequest":
        self.queues = sorted({item.strip() for item in self.queues if item.strip()})
        if not self.queues:
            self.queues = ["default"]
        self.capabilities = sorted(
            {item.strip() for item in self.capabilities if item.strip()}
        )
        return self


class WorkerHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: str = Field(..., min_length=1, max_length=255)
    status: str = Field(default="active", pattern="^(active|draining)$")
    metadata: dict[str, Any] | None = None


class WorkerStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=4000)


class WorkItemDispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(..., min_length=1, max_length=64)
    queue_name: str = Field(default="default", min_length=1, max_length=128)
    priority: int = Field(default=0, ge=-1000, le=1000)
    required_capabilities: list[str] = Field(default_factory=list)
    idempotency_key: str | None = Field(default=None, max_length=255)
    max_attempts: int = Field(default=3, ge=1, le=100)
    available_at: datetime | None = None
    run_request: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_capabilities(self) -> "WorkItemDispatchRequest":
        self.required_capabilities = sorted(
            {
                item.strip()
                for item in self.required_capabilities
                if item.strip()
            }
        )
        return self


class WorkerClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: str = Field(..., min_length=1, max_length=255)
    queue_names: list[str] | None = None
    lease_seconds: int | None = Field(default=None, ge=5, le=86400)

    @model_validator(mode="after")
    def normalize_queues(self) -> "WorkerClaimRequest":
        if self.queue_names is not None:
            self.queue_names = sorted(
                {item.strip() for item in self.queue_names if item.strip()}
            )
        return self


class LeaseRenewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(..., min_length=1, max_length=64)
    instance_id: str = Field(..., min_length=1, max_length=255)
    fencing_token: int = Field(..., ge=1)
    lease_seconds: int | None = Field(default=None, ge=5, le=86400)


class LeaseCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(..., min_length=1, max_length=64)
    instance_id: str = Field(..., min_length=1, max_length=255)
    fencing_token: int = Field(..., ge=1)
    result: dict[str, Any] = Field(default_factory=dict)


class LeaseFailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(..., min_length=1, max_length=64)
    instance_id: str = Field(..., min_length=1, max_length=255)
    fencing_token: int = Field(..., ge=1)
    error: str = Field(..., min_length=1, max_length=16000)
    retryable: bool = True
    retry_delay_seconds: int = Field(default=0, ge=0, le=86400)


class WorkItemCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=4000)
