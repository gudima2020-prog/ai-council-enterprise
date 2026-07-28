from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HumanControlRoutingStrategy(StrEnum):
    FIRST_AVAILABLE = "first_available"
    ROUND_ROBIN = "round_robin"
    BROADCAST = "broadcast"
    PRIMARY_BACKUP = "primary_backup"


class HumanControlFallbackMode(StrEnum):
    BASE_RECIPIENTS = "base_recipients"
    FALLBACK_ACTORS = "fallback_actors"
    BROADCAST_ROLES = "broadcast_roles"
    FAIL_CLOSED = "fail_closed"


class HumanControlAvailabilityStatus(StrEnum):
    AVAILABLE = "available"
    BUSY = "busy"
    OFFLINE = "offline"
    DO_NOT_DISTURB = "do_not_disturb"
    UNKNOWN = "unknown"


class HumanControlAvailabilitySource(StrEnum):
    MANUAL = "manual"
    HEARTBEAT = "heartbeat"
    SCHEDULE = "schedule"
    SYSTEM = "system"


class HumanControlEscalationTrigger(StrEnum):
    ACK_OVERDUE = "ack_overdue"
    DELIVERY_FAILED = "delivery_failed"
    UNROUTABLE = "unroutable"
    MANUAL = "manual"


class HumanControlOnCallScheduleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    schedule_key: str = Field(..., min_length=1, max_length=160, pattern=r"^[a-z0-9_.-]+$")
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=10000)
    timezone: str = Field(default="UTC", min_length=1, max_length=96)
    enabled: bool = True
    routing_strategy: HumanControlRoutingStrategy = HumanControlRoutingStrategy.FIRST_AVAILABLE
    fallback_role_keys: list[str] = Field(default_factory=list, max_length=50)
    created_by: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlOnCallScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    timezone: str | None = Field(default=None, min_length=1, max_length=96)
    enabled: bool | None = None
    routing_strategy: HumanControlRoutingStrategy | None = None
    fallback_role_keys: list[str] | None = Field(default=None, max_length=50)
    actor_id: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] | None = None


class HumanControlOnCallMemberCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    role_key: str | None = Field(default=None, max_length=96)
    priority: int = Field(default=50, ge=0, le=100)
    is_backup: bool = False
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    start_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    max_active_notifications: int = Field(default=0, ge=0, le=10000)
    enabled: bool = True
    created_by: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[int]) -> list[int]:
        if any(day < 0 or day > 6 for day in value):
            raise ValueError("weekdays must contain values from 0 to 6")
        return sorted(set(value))


class HumanControlOnCallMemberUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_key: str | None = Field(default=None, max_length=96)
    priority: int | None = Field(default=None, ge=0, le=100)
    is_backup: bool | None = None
    weekdays: list[int] | None = Field(default=None, max_length=7)
    start_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    max_active_notifications: int | None = Field(default=None, ge=0, le=10000)
    enabled: bool | None = None
    actor_id_updated_by: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] | None = None

    @field_validator("weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and any(day < 0 or day > 6 for day in value):
            raise ValueError("weekdays must contain values from 0 to 6")
        return None if value is None else sorted(set(value))


class HumanControlAvailabilityUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    actor_id: str = Field(..., min_length=1, max_length=255)
    status: HumanControlAvailabilityStatus
    capacity_percent: int = Field(default=100, ge=0, le=100)
    active_notification_limit: int = Field(default=0, ge=0, le=10000)
    source: HumanControlAvailabilitySource = HumanControlAvailabilitySource.MANUAL
    available_until: datetime | None = None
    last_seen_at: datetime | None = None
    note: str = Field(default="", max_length=10000)
    updated_by: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlAvailabilityHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    actor_id: str = Field(..., min_length=1, max_length=255)
    capacity_percent: int = Field(default=100, ge=0, le=100)
    status: HumanControlAvailabilityStatus = HumanControlAvailabilityStatus.AVAILABLE
    active_notification_limit: int = Field(default=0, ge=0, le=10000)
    note: str = Field(default="", max_length=10000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlRoutingRuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    rule_key: str = Field(..., min_length=1, max_length=160, pattern=r"^[a-z0-9_.-]+$")
    name: str = Field(..., min_length=1, max_length=255)
    enabled: bool = True
    rule_priority: int = Field(default=50, ge=0, le=100)
    event_patterns: list[str] = Field(default_factory=lambda: ["human_control.*"], max_length=100)
    source_types: list[str] = Field(default_factory=list, max_length=100)
    risk_levels: list[str] = Field(default_factory=list, max_length=4)
    min_priority: int = Field(default=0, ge=0, le=100)
    schedule_id: str | None = Field(default=None, max_length=64)
    role_keys: list[str] = Field(default_factory=list, max_length=50)
    fallback_actor_ids: list[str] = Field(default_factory=list, max_length=100)
    strategy: HumanControlRoutingStrategy = HumanControlRoutingStrategy.FIRST_AVAILABLE
    availability_required: bool = True
    min_capacity_percent: int = Field(default=1, ge=0, le=100)
    heartbeat_ttl_seconds: int = Field(default=300, ge=10, le=86400)
    max_recipients: int = Field(default=1, ge=1, le=100)
    fallback_mode: HumanControlFallbackMode = HumanControlFallbackMode.BASE_RECIPIENTS
    ack_required: bool | None = None
    ack_timeout_seconds: int | None = Field(default=None, ge=30, le=604800)
    created_by: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlRoutingRuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None
    rule_priority: int | None = Field(default=None, ge=0, le=100)
    event_patterns: list[str] | None = Field(default=None, max_length=100)
    source_types: list[str] | None = Field(default=None, max_length=100)
    risk_levels: list[str] | None = Field(default=None, max_length=4)
    min_priority: int | None = Field(default=None, ge=0, le=100)
    schedule_id: str | None = Field(default=None, max_length=64)
    role_keys: list[str] | None = Field(default=None, max_length=50)
    fallback_actor_ids: list[str] | None = Field(default=None, max_length=100)
    strategy: HumanControlRoutingStrategy | None = None
    availability_required: bool | None = None
    min_capacity_percent: int | None = Field(default=None, ge=0, le=100)
    heartbeat_ttl_seconds: int | None = Field(default=None, ge=10, le=86400)
    max_recipients: int | None = Field(default=None, ge=1, le=100)
    fallback_mode: HumanControlFallbackMode | None = None
    ack_required: bool | None = None
    ack_timeout_seconds: int | None = Field(default=None, ge=30, le=604800)
    actor_id: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] | None = None


class HumanControlRoutingEvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    event_type: str = Field(..., min_length=1, max_length=255)
    source_type: str = Field(default="human_control", max_length=96)
    severity: str = Field(default="medium", pattern=r"^(low|medium|high|critical)$")
    priority: int = Field(default=50, ge=0, le=100)
    role_keys: list[str] = Field(default_factory=list, max_length=50)
    base_recipients: list[str] = Field(default_factory=list, max_length=100)


class HumanControlEscalationRuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    rule_key: str = Field(..., min_length=1, max_length=160, pattern=r"^[a-z0-9_.-]+$")
    name: str = Field(..., min_length=1, max_length=255)
    enabled: bool = True
    rule_priority: int = Field(default=50, ge=0, le=100)
    event_patterns: list[str] = Field(default_factory=lambda: ["human_control.*"], max_length=100)
    source_types: list[str] = Field(default_factory=list, max_length=100)
    risk_levels: list[str] = Field(default_factory=list, max_length=4)
    min_priority: int = Field(default=0, ge=0, le=100)
    trigger_on: list[HumanControlEscalationTrigger] = Field(
        default_factory=lambda: [HumanControlEscalationTrigger.ACK_OVERDUE], max_length=4
    )
    initial_delay_seconds: int = Field(default=0, ge=0, le=604800)
    repeat_interval_seconds: int = Field(default=900, ge=30, le=604800)
    max_escalations: int = Field(default=3, ge=1, le=20)
    target_schedule_id: str | None = Field(default=None, max_length=64)
    target_role_keys: list[str] = Field(default_factory=list, max_length=50)
    target_actor_ids: list[str] = Field(default_factory=list, max_length=100)
    channel_id: str | None = Field(default=None, max_length=64)
    strategy: HumanControlRoutingStrategy = HumanControlRoutingStrategy.FIRST_AVAILABLE
    priority_increment: int = Field(default=10, ge=0, le=100)
    ack_required: bool = True
    ack_timeout_seconds: int = Field(default=900, ge=30, le=604800)
    auto_resolve_on_ack: bool = True
    created_by: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlEscalationRuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None
    rule_priority: int | None = Field(default=None, ge=0, le=100)
    event_patterns: list[str] | None = Field(default=None, max_length=100)
    source_types: list[str] | None = Field(default=None, max_length=100)
    risk_levels: list[str] | None = Field(default=None, max_length=4)
    min_priority: int | None = Field(default=None, ge=0, le=100)
    trigger_on: list[HumanControlEscalationTrigger] | None = Field(default=None, max_length=4)
    initial_delay_seconds: int | None = Field(default=None, ge=0, le=604800)
    repeat_interval_seconds: int | None = Field(default=None, ge=30, le=604800)
    max_escalations: int | None = Field(default=None, ge=1, le=20)
    target_schedule_id: str | None = Field(default=None, max_length=64)
    target_role_keys: list[str] | None = Field(default=None, max_length=50)
    target_actor_ids: list[str] | None = Field(default=None, max_length=100)
    channel_id: str | None = Field(default=None, max_length=64)
    strategy: HumanControlRoutingStrategy | None = None
    priority_increment: int | None = Field(default=None, ge=0, le=100)
    ack_required: bool | None = None
    ack_timeout_seconds: int | None = Field(default=None, ge=30, le=604800)
    auto_resolve_on_ack: bool | None = None
    actor_id: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] | None = None


class HumanControlEscalationManualRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    rule_id: str | None = Field(default=None, max_length=64)
    reason: str = Field(..., min_length=1, max_length=10000)
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    immediate: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlEscalationResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    resolution: str = Field(..., min_length=1, max_length=10000)
    force: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlEscalationScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="system", min_length=1, max_length=255)
    limit: int = Field(default=100, ge=1, le=500)

class HumanControlRoutingActorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(default="", max_length=10000)
    metadata: dict[str, Any] = Field(default_factory=dict)
