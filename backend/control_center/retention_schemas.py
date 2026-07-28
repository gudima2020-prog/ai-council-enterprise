from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HumanControlRetentionMode(StrEnum):
    OBSERVE = "observe"
    ARCHIVE = "archive"
    PURGE = "purge"


class HumanControlArchiveType(StrEnum):
    AUDIT = "audit"
    COMPLIANCE = "compliance"
    LEGAL_HOLD = "legal_hold"
    RETENTION = "retention"
    EXTERNAL_AUDIT = "external_audit"
    CUSTOM = "custom"


class HumanControlRetentionPolicyUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    enabled: bool = False
    enforcement_mode: HumanControlRetentionMode = HumanControlRetentionMode.OBSERVE
    default_retention_days: int = Field(default=365, ge=1, le=36500)
    security_event_retention_days: int = Field(default=365, ge=1, le=36500)
    notification_retention_days: int = Field(default=180, ge=1, le=36500)
    compliance_report_retention_days: int = Field(default=2555, ge=1, le=36500)
    operator_audit_retention_days: int = Field(default=2555, ge=1, le=36500)
    archive_before_purge: bool = True
    require_human_approval: bool = True
    purge_batch_size: int = Field(default=500, ge=1, le=10000)
    legal_hold_override_enabled: bool = True
    actor_id: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlLegalHoldCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    hold_key: str = Field(..., min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    title: str = Field(..., min_length=1, max_length=500)
    reason: str = Field(..., min_length=1, max_length=20000)
    target_types: list[str] = Field(default_factory=list, max_length=100)
    target_ids: list[str] = Field(default_factory=list, max_length=5000)
    event_patterns: list[str] = Field(default_factory=list, max_length=100)
    actor_ids: list[str] = Field(default_factory=list, max_length=1000)
    custodian_ids: list[str] = Field(default_factory=list, max_length=1000)
    period_start: datetime | None = None
    period_end: datetime | None = None
    expires_at: datetime | None = None
    created_by: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_dates(self) -> "HumanControlLegalHoldCreate":
        if self.period_start and self.period_end and self.period_end <= self.period_start:
            raise ValueError("period_end должно быть позже period_start.")
        return self


class HumanControlLegalHoldReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=20000)
    force: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlEvidenceArchiveCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    archive_key: str = Field(..., min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    archive_type: HumanControlArchiveType = HumanControlArchiveType.CUSTOM
    title: str = Field(..., min_length=1, max_length=500)
    description: str = Field(default="", max_length=20000)
    legal_hold_id: str | None = Field(default=None, max_length=64)
    period_start: datetime | None = None
    period_end: datetime | None = None
    source_types: list[str] = Field(
        default_factory=lambda: ["operator_audit", "compliance_report"],
        max_length=20,
    )
    actor_ids: list[str] = Field(default_factory=list, max_length=1000)
    event_patterns: list[str] = Field(default_factory=list, max_length=100)
    source_ids: list[str] = Field(default_factory=list, max_length=5000)
    max_items: int = Field(default=5000, ge=1, le=20000)
    created_by: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_period(self) -> "HumanControlEvidenceArchiveCreate":
        if self.period_start and self.period_end and self.period_end <= self.period_start:
            raise ValueError("period_end должно быть позже period_start.")
        return self


class HumanControlEvidenceArchiveRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=20000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlRetentionRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    apply: bool = False
    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(default="", max_length=20000)
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    force: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlExternalAuditPackageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    package_key: str = Field(..., min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    title: str = Field(..., min_length=1, max_length=500)
    auditor_name: str = Field(..., min_length=1, max_length=500)
    archive_ids: list[str] = Field(default_factory=list, max_length=1000)
    report_ids: list[str] = Field(default_factory=list, max_length=1000)
    access_expires_at: datetime | None = None
    generated_by: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_evidence(self) -> "HumanControlExternalAuditPackageCreate":
        if not self.archive_ids and not self.report_ids:
            raise ValueError("Нужно указать хотя бы один archive_id или report_id.")
        return self


class HumanControlExternalAuditPackageRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=20000)
    metadata: dict[str, Any] = Field(default_factory=dict)
