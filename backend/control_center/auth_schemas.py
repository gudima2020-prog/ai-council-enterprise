from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AuthEnforcementMode(StrEnum):
    LEGACY = "legacy"
    AUDIT = "audit"
    ENFORCE = "enforce"


class IdentityType(StrEnum):
    HUMAN = "human"
    SERVICE = "service"


class HumanControlAuthPolicyUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str | None = Field(default=None, max_length=64)
    enabled: bool = True
    enforcement_mode: AuthEnforcementMode = AuthEnforcementMode.LEGACY
    session_ttl_seconds: int = Field(default=28800, ge=300, le=2592000)
    session_idle_seconds: int = Field(default=3600, ge=60, le=604800)
    max_failed_attempts: int = Field(default=5, ge=1, le=100)
    lockout_seconds: int = Field(default=900, ge=30, le=86400)
    api_token_max_ttl_days: int = Field(default=90, ge=1, le=3650)
    break_glass_ttl_seconds: int = Field(default=900, ge=60, le=86400)
    break_glass_requires_approval: bool = True
    break_glass_distinct_approver: bool = True
    allowed_api_scopes: list[str] = Field(default_factory=list, max_length=200)
    protected_path_prefixes: list[str] = Field(
        default_factory=lambda: ["/api/human-control"], max_length=100
    )
    public_paths: list[str] = Field(
        default_factory=lambda: [
            "/",
            "/docs*",
            "/redoc*",
            "/openapi.json",
            "/api/health*",
            "/api/human-control/auth/status",
            "/api/human-control/auth/login",
            "/api/human-control/auth/bootstrap-identity",
            "/api/human-control/auth/break-glass/activate",
            "/api/human-control/governance/bootstrap-owner",
        ],
        max_length=200,
    )
    require_actor_binding: bool = True
    require_workspace_binding: bool = True
    reject_unscoped_api_tokens: bool = True
    actor_id: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlIdentityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_id: str = Field(..., min_length=1, max_length=255)
    username: str = Field(..., min_length=3, max_length=255)
    display_name: str = Field(..., min_length=1, max_length=255)
    password: str | None = Field(default=None, min_length=12, max_length=4096)
    identity_type: IdentityType = IdentityType.HUMAN
    created_by: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_human_password(self) -> "HumanControlIdentityCreate":
        if self.identity_type == IdentityType.HUMAN and not self.password:
            raise ValueError("Для human identity требуется пароль.")
        return self


class HumanControlBootstrapIdentityRequest(HumanControlIdentityCreate):
    grant_owner_role: bool = True
    reason: str = Field(default="Initial authenticated owner bootstrap.", min_length=1, max_length=4000)


class HumanControlIdentityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    status: str | None = Field(default=None, pattern=r"^(active|disabled|locked)$")
    password: str | None = Field(default=None, min_length=12, max_length=4096)
    actor_id: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] | None = None


class HumanControlLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=4096)
    workspace_id: str | None = Field(default=None, max_length=64)
    client_ip: str | None = Field(default=None, max_length=128)
    user_agent: str | None = Field(default=None, max_length=2000)


class HumanControlSessionRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=4000)


class HumanControlApiTokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity_id: str = Field(..., min_length=1, max_length=64)
    workspace_id: str | None = Field(default=None, max_length=64)
    name: str = Field(..., min_length=1, max_length=255)
    scopes: list[str] = Field(default_factory=list, max_length=200)
    expires_at: datetime | None = None
    created_by: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlApiTokenRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=4000)


class HumanControlBreakGlassRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested_by_identity_id: str = Field(..., min_length=1, max_length=64)
    workspace_id: str | None = Field(default=None, max_length=64)
    reason: str = Field(..., min_length=10, max_length=10000)
    scopes: list[str] = Field(default_factory=lambda: ["human_control.override"], max_length=100)
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlBreakGlassDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approver_identity_id: str = Field(..., min_length=1, max_length=64)
    approve: bool = True
    reason: str = Field(..., min_length=1, max_length=10000)


class HumanControlBreakGlassActivate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    activation_token: str = Field(..., min_length=20, max_length=4096)
    client_ip: str | None = Field(default=None, max_length=128)
    user_agent: str | None = Field(default=None, max_length=2000)


class HumanControlBreakGlassRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_id: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=10000)
