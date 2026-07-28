from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class SecretProviderType(StrEnum):
    ENV = "env"
    WINDOWS_DPAPI = "windows_dpapi"
    EXTERNAL = "external"


class SecretProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_key: str = Field(..., min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_.-]+$")
    workspace_id: str | None = Field(default=None, max_length=64)
    name: str = Field(..., min_length=1, max_length=255)
    provider_type: SecretProviderType
    adapter_key: str | None = Field(default=None, max_length=120)
    enabled: bool = True
    read_only: bool | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    created_by: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SecretProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    actor_id: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] | None = None


class SecretCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_key: str = Field(..., min_length=1, max_length=120)
    workspace_id: str | None = Field(default=None, max_length=64)
    secret_key: str = Field(..., min_length=1, max_length=240, pattern=r"^[A-Za-z0-9._/-]+$")
    provider_ref: str | None = Field(default=None, max_length=2048)
    display_name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=10000)
    value: SecretStr | None = None
    expires_at: datetime | None = None
    created_by: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SecretRotateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: SecretStr
    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=10000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SecretStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=10000)


class SecretVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    purpose: str = Field(default="operator verification", max_length=10000)
    auth_method: str = Field(default="operator", max_length=32)
    source: str = Field(default="api", max_length=255)


class SecretResolveContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    purpose: str = Field(..., min_length=1, max_length=10000)
    workspace_id: str | None = Field(default=None, max_length=64)
    auth_method: str = Field(default="system", max_length=32)
    source: str = Field(default="runtime", max_length=255)
    consumer_type: str = Field(default="system", min_length=1, max_length=64)
    consumer_key: str = Field(default="runtime", min_length=1, max_length=255)
    correlation_id: str | None = Field(default=None, max_length=255)
    lease_id: str | None = Field(default=None, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)


SENSITIVE_CONFIG_TOKENS = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "credential",
}


def validate_public_provider_config(config: dict[str, Any]) -> dict[str, Any]:
    def walk(value: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).strip().lower().replace("-", "_")
                looks_sensitive = any(
                    token in normalized for token in SENSITIVE_CONFIG_TOKENS
                )
                reference_field = normalized.endswith("_ref") and (
                    item is None
                    or (isinstance(item, str) and item.startswith("secret://"))
                )
                if looks_sensitive and not reference_field:
                    raise ValueError(
                        "Provider config must not contain secret material; "
                        f"use a secret:// reference instead ({'.'.join(path + (str(key),))})."
                    )
                walk(item, path + (str(key),))
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                walk(item, path + (str(index),))

    walk(config)
    return config



class SecretAccessEnforcementMode(StrEnum):
    LEGACY = "legacy"
    AUDIT = "audit"
    ENFORCE = "enforce"


class SecretPolicyEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class SecretAccessSettingsUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    enabled: bool = True
    enforcement_mode: SecretAccessEnforcementMode = SecretAccessEnforcementMode.LEGACY
    default_effect: SecretPolicyEffect = SecretPolicyEffect.ALLOW
    default_lease_seconds: int = Field(default=60, ge=1, le=86400)
    max_lease_seconds: int = Field(default=900, ge=1, le=86400)
    max_lease_uses: int = Field(default=10, ge=1, le=1000)
    actor_id: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SecretAccessPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_key: str = Field(..., min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    workspace_id: str | None = Field(default=None, max_length=64)
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=10000)
    enabled: bool = True
    priority: int = Field(default=100, ge=-100000, le=100000)
    effect: SecretPolicyEffect
    actions: list[str] = Field(default_factory=lambda: ["resolve", "lease"], max_length=20)
    secret_patterns: list[str] = Field(default_factory=lambda: ["*"], max_length=100)
    consumer_types: list[str] = Field(default_factory=list, max_length=50)
    consumer_keys: list[str] = Field(default_factory=list, max_length=100)
    actor_patterns: list[str] = Field(default_factory=list, max_length=100)
    source_patterns: list[str] = Field(default_factory=list, max_length=100)
    purpose_patterns: list[str] = Field(default_factory=list, max_length=100)
    max_lease_seconds: int | None = Field(default=None, ge=1, le=86400)
    max_uses: int | None = Field(default=None, ge=1, le=1000)
    require_workspace_match: bool = True
    created_by: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SecretAccessPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=-100000, le=100000)
    effect: SecretPolicyEffect | None = None
    actions: list[str] | None = Field(default=None, max_length=20)
    secret_patterns: list[str] | None = Field(default=None, max_length=100)
    consumer_types: list[str] | None = Field(default=None, max_length=50)
    consumer_keys: list[str] | None = Field(default=None, max_length=100)
    actor_patterns: list[str] | None = Field(default=None, max_length=100)
    source_patterns: list[str] | None = Field(default=None, max_length=100)
    purpose_patterns: list[str] | None = Field(default=None, max_length=100)
    max_lease_seconds: int | None = Field(default=None, ge=1, le=86400)
    max_uses: int | None = Field(default=None, ge=1, le=1000)
    require_workspace_match: bool | None = None
    actor_id: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] | None = None


class SecretAccessEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(default="resolve", min_length=1, max_length=64)
    actor_id: str = Field(..., min_length=1, max_length=255)
    purpose: str = Field(..., min_length=1, max_length=10000)
    workspace_id: str | None = Field(default=None, max_length=64)
    auth_method: str = Field(default="operator", max_length=32)
    source: str = Field(default="api", max_length=255)
    consumer_type: str = Field(default="operator", min_length=1, max_length=64)
    consumer_key: str = Field(default="control-center", min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SecretLeaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: str = Field(..., min_length=10, max_length=2048)
    workspace_id: str | None = Field(default=None, max_length=64)
    actor_id: str = Field(..., min_length=1, max_length=255)
    consumer_type: str = Field(..., min_length=1, max_length=64)
    consumer_key: str = Field(..., min_length=1, max_length=255)
    purpose: str = Field(..., min_length=1, max_length=10000)
    source: str = Field(default="api", max_length=255)
    auth_method: str = Field(default="operator", max_length=32)
    ttl_seconds: int | None = Field(default=None, ge=1, le=86400)
    max_uses: int = Field(default=1, ge=1, le=1000)
    created_by: str = Field(..., min_length=1, max_length=255)
    correlation_id: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SecretLeaseRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=10000)



class SecretRotationMode(StrEnum):
    MONITOR_ONLY = "monitor_only"
    MANAGED_RANDOM = "managed_random"
    EXTERNAL_HANDLER = "external_handler"


class SecretRotationPolicyUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    rotation_mode: SecretRotationMode = SecretRotationMode.MONITOR_ONLY
    interval_seconds: int = Field(default=2592000, ge=60, le=31536000)
    warning_seconds: int = Field(default=604800, ge=0, le=31536000)
    auto_rotate_enabled: bool = False
    require_human_approval: bool = True
    handler_key: str = Field(default="", max_length=160)
    random_bytes: int = Field(default=32, ge=16, le=256)
    max_failures: int = Field(default=3, ge=1, le=100)
    actor_id: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SecretRotationRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=10000)
    trigger_type: str = Field(default="manual", pattern=r"^(manual|scheduled|expiry|health)$")
    execute_now: bool = False
    force: bool = False
    idempotency_key: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SecretRotationRunDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    approve: bool = True
    reason: str = Field(..., min_length=1, max_length=10000)
    force: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class SecretLifecycleScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    actor_id: str = Field(default="system", min_length=1, max_length=255)
    check_providers: bool = True
    evaluate_rotation: bool = True
    auto_rotate: bool = True
    recent_error_window_seconds: int = Field(default=3600, ge=60, le=604800)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SecretHealthAlertDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=255)
    note: str = Field(..., min_length=1, max_length=10000)
    suppress: bool = False
