from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class SecretProviderModel(Base):
    __tablename__ = "secret_providers"
    __table_args__ = (
        UniqueConstraint("provider_key", name="uq_secret_providers_key"),
        CheckConstraint(
            "provider_type IN ('env','windows_dpapi','external')",
            name="ck_secret_providers_type",
        ),
        CheckConstraint(
            "health_status IN ('unknown','healthy','unavailable','error')",
            name="ck_secret_providers_health",
        ),
        Index(
            "ix_secret_providers_effective",
            "workspace_id",
            "enabled",
            "provider_type",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("secprov")
    )
    provider_key: Mapped[str] = mapped_column(String(120), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    adapter_key: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    read_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    health_status: Mapped[str] = mapped_column(
        String(16), default="unknown", nullable=False, index=True
    )
    health_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class SecretRecordModel(Base):
    __tablename__ = "secret_records"
    __table_args__ = (
        UniqueConstraint("locator_key", name="uq_secret_records_locator"),
        CheckConstraint(
            "status IN ('active','disabled')",
            name="ck_secret_records_status",
        ),
        CheckConstraint("current_version >= 0", name="ck_secret_records_version"),
        Index(
            "ix_secret_records_lookup",
            "workspace_id",
            "provider_id",
            "status",
            "secret_key",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("secret")
    )
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("secret_providers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    secret_key: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    locator_key: Mapped[str] = mapped_column(String(512), nullable=False)
    provider_ref: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="active", nullable=False, index=True
    )
    current_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    material_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    last_rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class SecretVersionModel(Base):
    __tablename__ = "secret_versions"
    __table_args__ = (
        UniqueConstraint(
            "secret_id", "version", name="uq_secret_versions_secret_version"
        ),
        CheckConstraint(
            "status IN ('current','retired')",
            name="ck_secret_versions_status",
        ),
        Index(
            "ix_secret_versions_history",
            "secret_id",
            "status",
            "version",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("secver")
    )
    secret_id: Mapped[str] = mapped_column(
        ForeignKey("secret_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    provider_version: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    encrypted_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    material_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SecretAccessEventModel(Base):
    __tablename__ = "secret_access_events"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('allowed','denied','error')",
            name="ck_secret_access_events_outcome",
        ),
        Index(
            "ix_secret_access_events_history",
            "workspace_id",
            "secret_id",
            "actor_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("secaccess")
    )
    secret_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(Text, default="", nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    auth_method: Mapped[str] = mapped_column(String(32), default="system", nullable=False)
    source: Mapped[str] = mapped_column(String(255), default="runtime", nullable=False)
    material_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


def _immutable_access_event(*_: Any, **__: Any) -> None:
    raise ValueError("Secret access events are immutable.")


event.listen(SecretAccessEventModel, "before_update", _immutable_access_event)
event.listen(SecretAccessEventModel, "before_delete", _immutable_access_event)


class SecretAccessSettingsModel(Base):
    __tablename__ = "secret_access_settings"
    __table_args__ = (
        UniqueConstraint("scope_key", name="uq_secret_access_settings_scope"),
        CheckConstraint(
            "enforcement_mode IN ('legacy','audit','enforce')",
            name="ck_secret_access_settings_mode",
        ),
        CheckConstraint(
            "default_effect IN ('allow','deny')",
            name="ck_secret_access_settings_default_effect",
        ),
        CheckConstraint("default_lease_seconds >= 1", name="ck_secret_access_settings_default_ttl"),
        CheckConstraint("max_lease_seconds >= 1", name="ck_secret_access_settings_max_ttl"),
        CheckConstraint("max_lease_uses >= 1", name="ck_secret_access_settings_max_uses"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("secset")
    )
    scope_key: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enforcement_mode: Mapped[str] = mapped_column(
        String(16), default="legacy", nullable=False
    )
    default_effect: Mapped[str] = mapped_column(
        String(16), default="allow", nullable=False
    )
    default_lease_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    max_lease_seconds: Mapped[int] = mapped_column(Integer, default=900, nullable=False)
    max_lease_uses: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class SecretAccessPolicyModel(Base):
    __tablename__ = "secret_access_policies"
    __table_args__ = (
        UniqueConstraint("policy_key", name="uq_secret_access_policies_key"),
        CheckConstraint("effect IN ('allow','deny')", name="ck_secret_access_policies_effect"),
        Index(
            "ix_secret_access_policies_effective",
            "workspace_id",
            "enabled",
            "priority",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("secpol")
    )
    policy_key: Mapped[str] = mapped_column(String(160), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    effect: Mapped[str] = mapped_column(String(16), nullable=False)
    actions_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    secret_patterns_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    consumer_types_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    consumer_keys_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    actor_patterns_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    source_patterns_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    purpose_patterns_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    max_lease_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    require_workspace_match: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class SecretLeaseModel(Base):
    __tablename__ = "secret_leases"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_secret_leases_token_hash"),
        CheckConstraint(
            "status IN ('active','consumed','revoked','expired')",
            name="ck_secret_leases_status",
        ),
        CheckConstraint("max_uses >= 1", name="ck_secret_leases_max_uses"),
        CheckConstraint("use_count >= 0", name="ck_secret_leases_use_count"),
        Index(
            "ix_secret_leases_active",
            "workspace_id",
            "secret_id",
            "status",
            "expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("seclea")
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    secret_id: Mapped[str] = mapped_column(
        ForeignKey("secret_records.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    consumer_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    consumer_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)
    max_uses: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    use_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)



class SecretRotationPolicyModel(Base):
    __tablename__ = "secret_rotation_policies"
    __table_args__ = (
        UniqueConstraint("secret_id", name="uq_secret_rotation_policy_secret"),
        CheckConstraint(
            "rotation_mode IN ('monitor_only','managed_random','external_handler')",
            name="ck_secret_rotation_policy_mode",
        ),
        CheckConstraint("interval_seconds >= 60", name="ck_secret_rotation_policy_interval"),
        CheckConstraint("warning_seconds >= 0", name="ck_secret_rotation_policy_warning"),
        CheckConstraint("random_bytes >= 16", name="ck_secret_rotation_policy_random_bytes"),
        CheckConstraint("max_failures >= 1", name="ck_secret_rotation_policy_max_failures"),
        Index("ix_secret_rotation_policy_due", "enabled", "next_rotation_at"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("secrotpol")
    )
    secret_id: Mapped[str] = mapped_column(
        ForeignKey("secret_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rotation_mode: Mapped[str] = mapped_column(String(32), default="monitor_only", nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=2592000, nullable=False)
    warning_seconds: Mapped[int] = mapped_column(Integer, default=604800, nullable=False)
    auto_rotate_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    require_human_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    handler_key: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    random_bytes: Mapped[int] = mapped_column(Integer, default=32, nullable=False)
    max_failures: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    next_rotation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class SecretRotationRunModel(Base):
    __tablename__ = "secret_rotation_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_secret_rotation_runs_idempotency"),
        CheckConstraint(
            "status IN ('proposed','running','completed','failed','rejected','cancelled')",
            name="ck_secret_rotation_runs_status",
        ),
        CheckConstraint(
            "trigger_type IN ('manual','scheduled','expiry','health')",
            name="ck_secret_rotation_runs_trigger",
        ),
        Index("ix_secret_rotation_runs_history", "secret_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("secrotrun")
    )
    secret_id: Mapped[str] = mapped_column(
        ForeignKey("secret_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("secret_rotation_policies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="proposed", nullable=False, index=True)
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False)
    rotation_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    handler_key: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    old_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    new_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)


class SecretHealthCheckModel(Base):
    __tablename__ = "secret_health_checks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('healthy','warning','critical','error')",
            name="ck_secret_health_checks_status",
        ),
        Index("ix_secret_health_checks_history", "workspace_id", "secret_id", "checked_at"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("sechealth")
    )
    secret_id: Mapped[str] = mapped_column(
        ForeignKey("secret_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    provider_status: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_in_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rotation_due: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rotation_due_in_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recent_error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)


class SecretHealthAlertModel(Base):
    __tablename__ = "secret_health_alerts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open','resolved','suppressed')",
            name="ck_secret_health_alerts_status",
        ),
        CheckConstraint(
            "severity IN ('warning','critical')",
            name="ck_secret_health_alerts_severity",
        ),
        Index("ix_secret_health_alerts_open", "workspace_id", "status", "severity", "first_seen_at"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("secalert")
    )
    secret_id: Mapped[str] = mapped_column(
        ForeignKey("secret_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    alert_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    resolution_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
