from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


SUPPORTED_PROTOCOL_VERSIONS = ("1.0",)
SUPPORTED_PROTOCOL_FEATURES = (
    "durable-events",
    "at-least-once-delivery",
    "idempotent-ack",
    "lease-renewal",
    "dead-letter-replay",
    "fencing-token",
)


class RemoteSessionOpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(..., min_length=1, max_length=64)
    instance_id: str = Field(..., min_length=1, max_length=255)
    protocol_version: str = Field(default="1.0", min_length=1, max_length=32)
    features: list[str] = Field(default_factory=list, max_length=100)
    ttl_seconds: int = Field(default=120, ge=15, le=3600)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("features")
    @classmethod
    def normalize_features(cls, value: list[str]) -> list[str]:
        return sorted({item.strip() for item in value if item.strip()})


class RemoteSessionHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: str = Field(..., min_length=16, max_length=512)
    ttl_seconds: int | None = Field(default=None, ge=15, le=3600)
    metadata: dict[str, Any] | None = None


class RemoteSessionCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: str = Field(..., min_length=16, max_length=512)
    reason: str = Field(default="closed_by_remote", min_length=1, max_length=2000)


class TransportEventPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = Field(default=None, max_length=64)
    session_token: str | None = Field(default=None, max_length=512)
    event_id: str | None = Field(default=None, max_length=255)
    topic: str = Field(..., min_length=1, max_length=255)
    event_type: str = Field(..., min_length=1, max_length=255)
    payload: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, Any] = Field(default_factory=dict)
    target_worker_id: str | None = Field(default=None, max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=255)
    priority: int = Field(default=0, ge=-1000, le=1000)
    available_at: datetime | None = None
    max_attempts: int = Field(default=5, ge=1, le=100)


class TransportEventClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1, max_length=64)
    session_token: str = Field(..., min_length=16, max_length=512)
    topics: list[str] = Field(default_factory=list, max_length=100)
    limit: int = Field(default=10, ge=1, le=100)
    lease_seconds: int = Field(default=60, ge=5, le=3600)
    consumer_key: str = Field(default="remote-executor", min_length=1, max_length=255)

    @field_validator("topics")
    @classmethod
    def normalize_topics(cls, value: list[str]) -> list[str]:
        return sorted({item.strip() for item in value if item.strip()})


class TransportEventAckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1, max_length=64)
    session_token: str = Field(..., min_length=16, max_length=512)
    lease_token: str = Field(..., min_length=16, max_length=512)
    consumer_key: str = Field(default="remote-executor", min_length=1, max_length=255)
    result: dict[str, Any] = Field(default_factory=dict)


class TransportEventNackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1, max_length=64)
    session_token: str = Field(..., min_length=16, max_length=512)
    lease_token: str = Field(..., min_length=16, max_length=512)
    consumer_key: str = Field(default="remote-executor", min_length=1, max_length=255)
    error: str = Field(..., min_length=1, max_length=8000)
    retry_delay_seconds: int = Field(default=5, ge=0, le=86400)
    dead_letter: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class TransportEventReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="owner", min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=4000)
    delay_seconds: int = Field(default=0, ge=0, le=86400)
    reset_attempts: bool = True


class TransportLeaseRenewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1, max_length=64)
    session_token: str = Field(..., min_length=16, max_length=512)
    lease_token: str = Field(..., min_length=16, max_length=512)
    lease_seconds: int = Field(default=60, ge=5, le=3600)


class RemoteWorkClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: str = Field(..., min_length=16, max_length=512)
    queues: list[str] = Field(default_factory=list, max_length=100)
    lease_seconds: int | None = Field(default=None, ge=5, le=3600)

    @field_validator("queues")
    @classmethod
    def normalize_queues(cls, value: list[str]) -> list[str]:
        return sorted({item.strip() for item in value if item.strip()})


class RemoteWorkLeaseRenewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1, max_length=64)
    session_token: str = Field(..., min_length=16, max_length=512)
    fencing_token: int = Field(..., ge=1)
    lease_seconds: int | None = Field(default=None, ge=5, le=3600)


class RemoteWorkLeaseCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1, max_length=64)
    session_token: str = Field(..., min_length=16, max_length=512)
    fencing_token: int = Field(..., ge=1)
    result: dict[str, Any] = Field(default_factory=dict)


class RemoteWorkLeaseFailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1, max_length=64)
    session_token: str = Field(..., min_length=16, max_length=512)
    fencing_token: int = Field(..., ge=1)
    error: str = Field(..., min_length=1, max_length=8000)
    retryable: bool = True
    retry_delay_seconds: int = Field(default=5, ge=0, le=86400)
