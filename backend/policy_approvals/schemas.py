from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.policy_approvals.core import (
    DEFAULT_POLICY_APPROVAL_TTL_SECONDS,
    MAX_POLICY_APPROVAL_TTL_SECONDS,
    MIN_POLICY_APPROVAL_TTL_SECONDS,
    PolicyApprovalScope,
)
from backend.runtime_policy import PolicyOperation


_BLOCKED_KEYS = {
    "artifact_bytes",
    "authorization",
    "credential",
    "password",
    "prompt",
    "raw_content",
    "response",
    "secret",
    "token",
}


def _reject_sensitive_keys(value: Any, *, path: str = "$") -> Any:
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            key = str(raw_key).strip().lower()
            if key in _BLOCKED_KEYS:
                raise ValueError(
                    f"Sensitive field {path}.{raw_key} is not allowed "
                    "in policy approval API payloads."
                )
            _reject_sensitive_keys(nested, path=f"{path}.{raw_key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_sensitive_keys(nested, path=f"{path}[{index}]")
    return value


class PolicyApprovalScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: PolicyOperation
    policy_version: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_.:-]+$",
    )
    policy_fingerprint: str = Field(
        ...,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    subject_type: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_.:-]+$",
    )
    subject_id: str = Field(..., min_length=1, max_length=255)
    subject_payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("subject_payload")
    @classmethod
    def validate_subject_payload(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        return _reject_sensitive_keys(value)

    def to_domain(self, *, workspace_id: str) -> PolicyApprovalScope:
        return PolicyApprovalScope(
            workspace_id=workspace_id,
            operation=self.operation,
            policy_version=self.policy_version,
            policy_fingerprint=self.policy_fingerprint,
            subject_type=self.subject_type,
            subject_id=self.subject_id,
            subject_payload=self.subject_payload,
        )


class PolicyApprovalCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: PolicyApprovalScopeRequest
    reason_codes: list[str] = Field(..., min_length=1, max_length=32)
    requested_by: str | None = Field(default=None, max_length=255)
    request_note: str | None = Field(default=None, max_length=4000)
    ttl_seconds: int = Field(
        default=DEFAULT_POLICY_APPROVAL_TTL_SECONDS,
        ge=MIN_POLICY_APPROVAL_TTL_SECONDS,
        le=MAX_POLICY_APPROVAL_TTL_SECONDS,
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        return _reject_sensitive_keys(value)


class PolicyApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str | None = Field(default=None, max_length=255)
    note: str | None = Field(default=None, max_length=4000)
