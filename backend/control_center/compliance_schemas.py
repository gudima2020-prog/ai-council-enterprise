from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HumanControlComplianceReportType(StrEnum):
    ACTIVITY_SUMMARY = "activity_summary"
    PRIVILEGED_ACCESS = "privileged_access"
    AUTHENTICATION = "authentication"
    APPROVAL_GOVERNANCE = "approval_governance"
    FULL = "full"


class HumanControlAccessReviewStatus(StrEnum):
    OPEN = "open"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class HumanControlAccessFindingStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    REMEDIATED = "remediated"
    DISMISSED = "dismissed"


class HumanControlAccessFindingDecision(StrEnum):
    ACCEPT = "accept"
    REMEDIATE = "remediate"
    DISMISS = "dismiss"


class HumanControlComplianceReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    report_type: HumanControlComplianceReportType = HumanControlComplianceReportType.FULL
    period_start: datetime | None = None
    period_end: datetime | None = None
    generated_by: str = Field(..., min_length=1, max_length=255)
    include_event_payloads: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_period(self) -> "HumanControlComplianceReportCreate":
        if (
            self.period_start is not None
            and self.period_end is not None
            and self.period_end <= self.period_start
        ):
            raise ValueError("period_end должно быть позже period_start.")
        return self


class HumanControlAccessReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    title: str = Field(default="Проверка привилегированного доступа", min_length=1, max_length=500)
    initiated_by: str = Field(..., min_length=1, max_length=255)
    include_expired: bool = False
    include_sessions: bool = True
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlAccessFindingDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: HumanControlAccessFindingDecision
    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=10000)
    apply_change: bool = False
    force: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlAccessReviewCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=10000)
    force: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlAuditExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    period_start: datetime | None = None
    period_end: datetime | None = None
    actor_id: str | None = Field(default=None, max_length=255)
    event_type: str | None = Field(default=None, max_length=255)
    include_payloads: bool = False
    limit: int = Field(default=1000, ge=1, le=10000)
