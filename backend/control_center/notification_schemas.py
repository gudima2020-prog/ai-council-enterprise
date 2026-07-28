from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.control_center.schemas import HumanControlRiskLevel, HumanControlSourceType


class HumanControlNotificationChannelType(StrEnum):
    IN_APP = "in_app"
    WEBHOOK = "webhook"
    LOG = "log"
    EMAIL = "email"
    SLACK = "slack"
    TELEGRAM = "telegram"
    CUSTOM = "custom"


class HumanControlNotificationStatus(StrEnum):
    PENDING = "pending"
    DELIVERING = "delivering"
    RETRYING = "retrying"
    DELIVERED = "delivered"
    ACKNOWLEDGED = "acknowledged"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class HumanControlNotificationAckStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    OVERDUE = "overdue"


class HumanControlNotificationChannelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    channel_key: str = Field(
        ..., min_length=1, max_length=160, pattern=r"^[a-z0-9_.-]+$"
    )
    name: str = Field(..., min_length=1, max_length=255)
    channel_type: HumanControlNotificationChannelType
    enabled: bool = True
    endpoint_url: str | None = Field(default=None, max_length=2000)
    credential_ref: str | None = Field(default=None, max_length=500)
    config: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=15, ge=1, le=300)
    max_attempts: int = Field(default=5, ge=1, le=20)
    retry_base_seconds: int = Field(default=30, ge=1, le=86400)
    default_ack_required: bool = False
    default_ack_timeout_seconds: int = Field(default=1800, ge=30, le=604800)
    created_by: str = Field(..., min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_endpoint(self) -> "HumanControlNotificationChannelCreate":
        if self.channel_type == HumanControlNotificationChannelType.WEBHOOK:
            if not self.endpoint_url:
                raise ValueError("Для webhook-канала требуется endpoint_url.")
            if not self.endpoint_url.startswith(("https://", "http://")):
                raise ValueError("Webhook endpoint_url должен использовать HTTP или HTTPS.")
        return self


class HumanControlNotificationChannelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None
    endpoint_url: str | None = Field(default=None, max_length=2000)
    credential_ref: str | None = Field(default=None, max_length=500)
    config: dict[str, Any] | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=300)
    max_attempts: int | None = Field(default=None, ge=1, le=20)
    retry_base_seconds: int | None = Field(default=None, ge=1, le=86400)
    default_ack_required: bool | None = None
    default_ack_timeout_seconds: int | None = Field(
        default=None, ge=30, le=604800
    )
    actor_id: str = Field(..., min_length=1, max_length=255)


class HumanControlNotificationSubscriptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    subscription_key: str = Field(
        ..., min_length=1, max_length=160, pattern=r"^[a-z0-9_.-]+$"
    )
    name: str = Field(..., min_length=1, max_length=255)
    channel_id: str = Field(..., min_length=1, max_length=64)
    actor_id: str | None = Field(default=None, max_length=255)
    role_keys: list[str] = Field(default_factory=list, max_length=50)
    event_patterns: list[str] = Field(
        default_factory=lambda: ["human_control.item.*"], max_length=100
    )
    source_types: list[HumanControlSourceType] = Field(default_factory=list)
    risk_levels: list[HumanControlRiskLevel] = Field(default_factory=list)
    min_priority: int = Field(default=0, ge=0, le=100)
    enabled: bool = True
    ack_required: bool | None = None
    ack_timeout_seconds: int | None = Field(default=None, ge=30, le=604800)
    created_by: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_recipient(self) -> "HumanControlNotificationSubscriptionCreate":
        if not self.actor_id and not self.role_keys:
            raise ValueError("Нужно указать actor_id или хотя бы одну role_key.")
        if not self.event_patterns:
            raise ValueError("Нужен хотя бы один event pattern.")
        return self


class HumanControlNotificationSubscriptionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    channel_id: str | None = Field(default=None, min_length=1, max_length=64)
    actor_id: str | None = Field(default=None, max_length=255)
    role_keys: list[str] | None = Field(default=None, max_length=50)
    event_patterns: list[str] | None = Field(default=None, max_length=100)
    source_types: list[HumanControlSourceType] | None = None
    risk_levels: list[HumanControlRiskLevel] | None = None
    min_priority: int | None = Field(default=None, ge=0, le=100)
    enabled: bool | None = None
    ack_required: bool | None = None
    ack_timeout_seconds: int | None = Field(default=None, ge=30, le=604800)
    metadata: dict[str, Any] | None = None
    actor_id_updated_by: str = Field(..., min_length=1, max_length=255)


class HumanControlNotificationAcknowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    note: str = Field(default="", max_length=10000)
    force: bool = False
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlNotificationReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlNotificationRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=10000)
    force: bool = False


class HumanControlNotificationCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=10000)
    force: bool = False


class HumanControlNotificationDispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=100, ge=1, le=500)
    actor_id: str = Field(default="operator", min_length=1, max_length=255)


class HumanControlNotificationChannelTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    recipient_actor_id: str | None = Field(default=None, max_length=255)
    title: str = Field(default="Проверка канала уведомлений", max_length=500)
    body: str = Field(default="Тестовое уведомление AI Studio Enterprise.", max_length=10000)
    require_ack: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlNotificationManualCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    channel_id: str = Field(..., min_length=1, max_length=64)
    recipient_actor_id: str = Field(..., min_length=1, max_length=255)
    title: str = Field(..., min_length=1, max_length=500)
    body: str = Field(default="", max_length=20000)
    severity: HumanControlRiskLevel = HumanControlRiskLevel.MEDIUM
    priority: int = Field(default=50, ge=0, le=100)
    ack_required: bool | None = None
    ack_timeout_seconds: int | None = Field(default=None, ge=30, le=604800)
    expires_at: datetime | None = None
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    created_by: str = Field(..., min_length=1, max_length=255)
    payload: dict[str, Any] = Field(default_factory=dict)
