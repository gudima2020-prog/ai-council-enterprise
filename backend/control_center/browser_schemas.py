from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BrowserSameSite(StrEnum):
    STRICT = "strict"
    LAX = "lax"
    NONE = "none"


class HumanControlBrowserPolicyUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    enabled: bool = False
    csrf_enabled: bool = True
    require_trusted_client: bool = True
    allow_missing_origin: bool = False
    cookie_secure: bool = True
    cookie_samesite: BrowserSameSite = BrowserSameSite.STRICT
    cookie_domain: str | None = Field(default=None, max_length=255)
    session_cookie_name: str = Field(default="hc_browser_session", min_length=1, max_length=128)
    csrf_cookie_name: str = Field(default="hc_csrf", min_length=1, max_length=128)
    csrf_header_name: str = Field(default="X-CSRF-Token", min_length=1, max_length=128)
    session_ttl_seconds: int = Field(default=28800, ge=300, le=2592000)
    session_idle_seconds: int = Field(default=1800, ge=60, le=604800)
    rotate_csrf_on_login: bool = True
    bind_user_agent: bool = True
    bind_client_ip: bool = False
    actor_id: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_cookie_policy(self) -> "HumanControlBrowserPolicyUpsert":
        if self.cookie_samesite == BrowserSameSite.NONE and not self.cookie_secure:
            raise ValueError("SameSite=None requires Secure cookies.")
        return self


class HumanControlTrustedClientCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, max_length=64)
    client_key: str = Field(..., min_length=1, max_length=160)
    name: str = Field(..., min_length=1, max_length=255)
    allowed_origins: list[str] = Field(..., min_length=1, max_length=100)
    enabled: bool = True
    created_by: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanControlTrustedClientUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    allowed_origins: list[str] | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    actor_id: str = Field(..., min_length=1, max_length=255)
    metadata: dict[str, Any] | None = None


class HumanControlBrowserLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=4096)
    workspace_id: str | None = Field(default=None, max_length=64)
    client_key: str | None = Field(default=None, max_length=160)


class HumanControlBrowserLogoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="Browser logout.", min_length=1, max_length=4000)


class HumanControlCsrfRotateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="CSRF token rotation.", min_length=1, max_length=4000)
