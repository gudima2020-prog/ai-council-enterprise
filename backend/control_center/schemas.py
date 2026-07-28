from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HumanControlSourceType(StrEnum):
    TASK_APPROVAL = "task_approval"
    MISSION_CHECKPOINT = "mission_checkpoint"
    MISSION_RESOURCE_ALLOCATION = "mission_resource_allocation"
    WORKSPACE_RESOURCE_RESERVATION = "workspace_resource_reservation"
    MISSION_LEARNING_RUN = "mission_learning_run"


class HumanControlItemStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    SNOOZED = "snoozed"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class HumanControlRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class HumanControlDecisionAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    CONTINUE = "continue"
    PAUSE = "pause"
    REPLAN = "replan"
    CANCEL = "cancel"
    ACCEPT_RISK = "accept_risk"
    REQUEST_REVIEW = "request_review"
    DEFER = "defer"
    APPLY = "apply"
    RELEASE = "release"


class HumanControlSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    actor_id: str = Field(default="system", min_length=1, max_length=255)


class HumanControlClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    ttl_seconds: int = Field(default=1800, ge=60, le=86400)
    force: bool = False
    reason: str = Field(default="Operator claimed decision item.", max_length=4000)


class HumanControlReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    force: bool = False
    reason: str = Field(default="Operator released decision item.", max_length=4000)


class HumanControlSnoozeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    until: datetime
    reason: str = Field(..., min_length=1, max_length=4000)


class HumanControlDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: HumanControlDecisionAction
    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=20000)
    force: bool = False
    selected_option: str | None = Field(default=None, max_length=255)
    defer_until: datetime | None = None
    reserve_now: bool = True
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_defer(self) -> "HumanControlDecisionRequest":
        if self.action == HumanControlDecisionAction.DEFER and self.defer_until is None:
            raise ValueError("Для действия defer необходимо указать defer_until.")
        return self


class HumanControlBulkDecisionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(..., min_length=1, max_length=64)
    decision: HumanControlDecisionRequest


class HumanControlBulkDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[HumanControlBulkDecisionEntry] = Field(
        ...,
        min_length=1,
        max_length=50,
    )
    stop_on_error: bool = False
