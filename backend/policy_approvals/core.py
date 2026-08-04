from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import hmac
import json
import re
import secrets
from typing import Any, Iterable, Mapping

from backend.runtime_policy import PolicyOperation


MIN_POLICY_APPROVAL_TTL_SECONDS = 60
DEFAULT_POLICY_APPROVAL_TTL_SECONDS = 15 * 60
MAX_POLICY_APPROVAL_TTL_SECONDS = 24 * 60 * 60

_SCOPE_SCHEMA_VERSION = "p2-012.1"
_TOKEN_HASH_DOMAIN = b"ai-studio-policy-approval-v1\x00"
_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z0-9_.:-]+$")
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Approval scope and metadata must be JSON-serializable."
        ) from exc


def _normalized_json_object(
    value: Mapping[str, Any] | None,
    *,
    field_name: str,
    max_bytes: int = 32_768,
) -> dict[str, Any]:
    candidate = dict(value or {})
    canonical = _canonical_json(candidate)
    if len(canonical.encode("utf-8")) > max_bytes:
        raise ValueError(
            f"{field_name} exceeds the {max_bytes}-byte canonical limit."
        )
    normalized = json.loads(canonical)
    if not isinstance(normalized, dict):
        raise ValueError(f"{field_name} must be a JSON object.")
    return normalized


def _normalize_identifier(
    value: str,
    *,
    field_name: str,
    max_length: int,
) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty.")
    if len(normalized) > max_length:
        raise ValueError(
            f"{field_name} exceeds the {max_length}-character limit."
        )
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{field_name} contains unsupported characters."
        )
    return normalized


def _normalize_text(
    value: str | None,
    *,
    field_name: str,
    max_length: int,
    required: bool = False,
) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{field_name} is required.")
        return None
    normalized = value.strip()
    if required and not normalized:
        raise ValueError(f"{field_name} is required.")
    if len(normalized) > max_length:
        raise ValueError(
            f"{field_name} exceeds the {max_length}-character limit."
        )
    return normalized or None


def _normalize_sha256(
    value: str,
    *,
    field_name: str,
) -> str:
    normalized = value.strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{field_name} must be a 64-character SHA-256 hex digest."
        )
    return normalized


def _normalize_reason_codes(
    values: Iterable[str],
) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()

    for raw in values:
        code = raw.strip().upper()
        if not code:
            continue
        if len(code) > 128:
            raise ValueError(
                "Approval reason code exceeds 128 characters."
            )
        if not _IDENTIFIER_PATTERN.fullmatch(code):
            raise ValueError(
                f"Unsupported approval reason code: {raw!r}."
            )
        if code not in seen:
            normalized.append(code)
            seen.add(code)

    if not normalized:
        raise ValueError(
            "At least one approval reason code is required."
        )
    if len(normalized) > 32:
        raise ValueError(
            "At most 32 approval reason codes are supported."
        )
    return tuple(normalized)


class PolicyApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    REVOKED = "revoked"
    CONSUMED = "consumed"


class PolicyApprovalDecision(StrEnum):
    APPROVE = "approve"
    DENY = "deny"


class PolicyApprovalError(ValueError):
    pass


class PolicyApprovalStateError(PolicyApprovalError):
    def __init__(
        self,
        message: str,
        *,
        record: PolicyApprovalRecord | None = None,
    ) -> None:
        super().__init__(message)
        self.record = record


class PolicyApprovalExpiredError(PolicyApprovalStateError):
    pass


class PolicyApprovalTokenError(PolicyApprovalError):
    pass


class PolicyApprovalScopeError(PolicyApprovalError):
    pass


@dataclass(frozen=True)
class PolicyApprovalScope:
    """Exact operation scope to which one policy approval is bound.

    ``subject_payload`` must contain only non-sensitive descriptors or hashes.
    Raw prompts, responses, secrets and artifact bytes must never be stored here.
    """

    workspace_id: str | None
    operation: PolicyOperation
    policy_version: str
    policy_fingerprint: str
    subject_type: str
    subject_id: str
    subject_payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        operation = (
            self.operation
            if isinstance(self.operation, PolicyOperation)
            else PolicyOperation(str(self.operation).strip())
        )
        workspace_id = _normalize_text(
            self.workspace_id,
            field_name="workspace_id",
            max_length=64,
        )
        policy_version = _normalize_identifier(
            self.policy_version,
            field_name="policy_version",
            max_length=64,
        )
        policy_fingerprint = _normalize_sha256(
            self.policy_fingerprint,
            field_name="policy_fingerprint",
        )
        subject_type = _normalize_identifier(
            self.subject_type,
            field_name="subject_type",
            max_length=64,
        )
        subject_id = _normalize_text(
            self.subject_id,
            field_name="subject_id",
            max_length=255,
            required=True,
        )
        subject_payload = _normalized_json_object(
            self.subject_payload,
            field_name="subject_payload",
        )

        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "workspace_id", workspace_id)
        object.__setattr__(self, "policy_version", policy_version)
        object.__setattr__(
            self,
            "policy_fingerprint",
            policy_fingerprint,
        )
        object.__setattr__(self, "subject_type", subject_type)
        object.__setattr__(self, "subject_id", subject_id)
        object.__setattr__(
            self,
            "subject_payload",
            subject_payload,
        )

    @property
    def fingerprint(self) -> str:
        canonical = _canonical_json(
            {
                "scope_schema_version": _SCOPE_SCHEMA_VERSION,
                **self.to_dict(),
            }
        )
        return hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "operation": self.operation.value,
            "policy_version": self.policy_version,
            "policy_fingerprint": self.policy_fingerprint,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "subject_payload": dict(self.subject_payload),
        }


@dataclass(frozen=True)
class PolicyApprovalRecord:
    id: str
    scope: PolicyApprovalScope
    scope_fingerprint: str
    reason_codes: tuple[str, ...]
    status: PolicyApprovalStatus
    requested_by: str | None
    requested_at: datetime
    expires_at: datetime
    request_note: str | None = None
    token_hash: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    expired_at: datetime | None = None
    revoked_by: str | None = None
    revoked_at: datetime | None = None
    revocation_note: str | None = None
    consumed_at: datetime | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def terminal(self) -> bool:
        return self.status in {
            PolicyApprovalStatus.DENIED,
            PolicyApprovalStatus.EXPIRED,
            PolicyApprovalStatus.REVOKED,
            PolicyApprovalStatus.CONSUMED,
        }

    def to_public_dict(self) -> dict[str, Any]:
        return self._to_dict(include_token_hash=False)

    def to_storage_dict(self) -> dict[str, Any]:
        return self._to_dict(include_token_hash=True)

    def _to_dict(
        self,
        *,
        include_token_hash: bool,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "scope": self.scope.to_dict(),
            "scope_fingerprint": self.scope_fingerprint,
            "reason_codes": list(self.reason_codes),
            "status": self.status.value,
            "requested_by": self.requested_by,
            "requested_at": self.requested_at,
            "expires_at": self.expires_at,
            "request_note": self.request_note,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "decision_note": self.decision_note,
            "expired_at": self.expired_at,
            "revoked_by": self.revoked_by,
            "revoked_at": self.revoked_at,
            "revocation_note": self.revocation_note,
            "consumed_at": self.consumed_at,
            "metadata": dict(self.metadata),
            "updated_at": self.updated_at,
        }
        if include_token_hash:
            result["token_hash"] = self.token_hash
        return result


@dataclass(frozen=True)
class PolicyApprovalGrant:
    record: PolicyApprovalRecord
    token: str


class PolicyApprovalCore:
    """Pure domain state machine for fingerprint-bound one-time approvals."""

    VERSION = "p2-012.1"

    @classmethod
    def request(
        cls,
        *,
        scope: PolicyApprovalScope,
        reason_codes: Iterable[str],
        requested_by: str | None = None,
        request_note: str | None = None,
        ttl_seconds: int = DEFAULT_POLICY_APPROVAL_TTL_SECONDS,
        metadata: Mapping[str, Any] | None = None,
        now: datetime | None = None,
        approval_id: str | None = None,
    ) -> PolicyApprovalRecord:
        if (
            ttl_seconds < MIN_POLICY_APPROVAL_TTL_SECONDS
            or ttl_seconds > MAX_POLICY_APPROVAL_TTL_SECONDS
        ):
            raise ValueError(
                "ttl_seconds must be between "
                f"{MIN_POLICY_APPROVAL_TTL_SECONDS} and "
                f"{MAX_POLICY_APPROVAL_TTL_SECONDS}."
            )

        current = _as_aware(now or utc_now())
        identifier = (
            _normalize_identifier(
                approval_id,
                field_name="approval_id",
                max_length=64,
            )
            if approval_id is not None
            else f"policy_approval_{secrets.token_hex(16)}"
        )

        return PolicyApprovalRecord(
            id=identifier,
            scope=scope,
            scope_fingerprint=scope.fingerprint,
            reason_codes=_normalize_reason_codes(reason_codes),
            status=PolicyApprovalStatus.PENDING,
            requested_by=_normalize_text(
                requested_by,
                field_name="requested_by",
                max_length=255,
            ),
            requested_at=current,
            expires_at=current + timedelta(seconds=ttl_seconds),
            request_note=_normalize_text(
                request_note,
                field_name="request_note",
                max_length=4000,
            ),
            metadata=_normalized_json_object(
                metadata,
                field_name="metadata",
            ),
            updated_at=current,
        )

    @classmethod
    def approve(
        cls,
        record: PolicyApprovalRecord,
        *,
        decided_by: str,
        note: str | None = None,
        now: datetime | None = None,
        token: str | None = None,
    ) -> PolicyApprovalGrant:
        current_time = _as_aware(now or utc_now())
        current = cls._require_pending(record, current_time)
        actor = _normalize_text(
            decided_by,
            field_name="decided_by",
            max_length=255,
            required=True,
        )
        raw_token = token or secrets.token_urlsafe(32)
        if len(raw_token) < 32:
            raise ValueError(
                "Approval token must contain at least 32 characters."
            )

        approved = replace(
            current,
            status=PolicyApprovalStatus.APPROVED,
            token_hash=cls.hash_token(raw_token),
            decided_by=actor,
            decided_at=current_time,
            decision_note=_normalize_text(
                note,
                field_name="decision_note",
                max_length=4000,
            ),
            updated_at=current_time,
        )
        return PolicyApprovalGrant(
            record=approved,
            token=raw_token,
        )

    @classmethod
    def deny(
        cls,
        record: PolicyApprovalRecord,
        *,
        decided_by: str,
        note: str | None = None,
        now: datetime | None = None,
    ) -> PolicyApprovalRecord:
        current_time = _as_aware(now or utc_now())
        current = cls._require_pending(record, current_time)
        return replace(
            current,
            status=PolicyApprovalStatus.DENIED,
            decided_by=_normalize_text(
                decided_by,
                field_name="decided_by",
                max_length=255,
                required=True,
            ),
            decided_at=current_time,
            decision_note=_normalize_text(
                note,
                field_name="decision_note",
                max_length=4000,
            ),
            updated_at=current_time,
        )

    @classmethod
    def revoke(
        cls,
        record: PolicyApprovalRecord,
        *,
        revoked_by: str,
        note: str | None = None,
        now: datetime | None = None,
    ) -> PolicyApprovalRecord:
        current_time = _as_aware(now or utc_now())
        current = cls.expire(record, now=current_time)

        if current.status == PolicyApprovalStatus.EXPIRED:
            raise PolicyApprovalExpiredError(
                "Policy approval has expired.",
                record=current,
            )
        if current.status not in {
            PolicyApprovalStatus.PENDING,
            PolicyApprovalStatus.APPROVED,
        }:
            raise PolicyApprovalStateError(
                "Only pending or approved policy approvals can be revoked.",
                record=current,
            )

        return replace(
            current,
            status=PolicyApprovalStatus.REVOKED,
            revoked_by=_normalize_text(
                revoked_by,
                field_name="revoked_by",
                max_length=255,
                required=True,
            ),
            revoked_at=current_time,
            revocation_note=_normalize_text(
                note,
                field_name="revocation_note",
                max_length=4000,
            ),
            updated_at=current_time,
        )

    @classmethod
    def expire(
        cls,
        record: PolicyApprovalRecord,
        *,
        now: datetime | None = None,
    ) -> PolicyApprovalRecord:
        current_time = _as_aware(now or utc_now())
        expires_at = _as_aware(record.expires_at)

        if (
            record.status
            in {
                PolicyApprovalStatus.PENDING,
                PolicyApprovalStatus.APPROVED,
            }
            and expires_at <= current_time
        ):
            return replace(
                record,
                status=PolicyApprovalStatus.EXPIRED,
                expired_at=current_time,
                updated_at=current_time,
            )
        return record

    @classmethod
    def consume(
        cls,
        record: PolicyApprovalRecord,
        *,
        token: str,
        scope: PolicyApprovalScope,
        now: datetime | None = None,
    ) -> PolicyApprovalRecord:
        current_time = _as_aware(now or utc_now())
        current = cls.expire(record, now=current_time)

        if current.status == PolicyApprovalStatus.EXPIRED:
            raise PolicyApprovalExpiredError(
                "Policy approval has expired.",
                record=current,
            )
        if current.status != PolicyApprovalStatus.APPROVED:
            raise PolicyApprovalStateError(
                "Policy approval is not in approved state.",
                record=current,
            )

        cls.validate_scope(current, scope)

        if not token or current.token_hash is None:
            raise PolicyApprovalTokenError(
                "Policy approval token is missing."
            )
        supplied_hash = cls.hash_token(token)
        if not hmac.compare_digest(
            supplied_hash,
            current.token_hash,
        ):
            raise PolicyApprovalTokenError(
                "Policy approval token is invalid."
            )

        return replace(
            current,
            status=PolicyApprovalStatus.CONSUMED,
            consumed_at=current_time,
            updated_at=current_time,
        )

    @staticmethod
    def validate_scope(
        record: PolicyApprovalRecord,
        scope: PolicyApprovalScope,
    ) -> None:
        if record.scope.workspace_id != scope.workspace_id:
            raise PolicyApprovalScopeError(
                "Policy approval belongs to another Workspace."
            )
        if record.scope.operation != scope.operation:
            raise PolicyApprovalScopeError(
                "Policy approval belongs to another operation."
            )
        if not hmac.compare_digest(
            record.scope.policy_fingerprint,
            scope.policy_fingerprint,
        ):
            raise PolicyApprovalScopeError(
                "Runtime policy decision fingerprint changed."
            )
        if not hmac.compare_digest(
            record.scope_fingerprint,
            scope.fingerprint,
        ):
            raise PolicyApprovalScopeError(
                "Policy approval subject scope changed."
            )

    @staticmethod
    def hash_token(token: str) -> str:
        if not token:
            raise PolicyApprovalTokenError(
                "Policy approval token is missing."
            )
        digest = hashlib.sha256()
        digest.update(_TOKEN_HASH_DOMAIN)
        digest.update(token.encode("utf-8"))
        return digest.hexdigest()

    @classmethod
    def _require_pending(
        cls,
        record: PolicyApprovalRecord,
        now: datetime,
    ) -> PolicyApprovalRecord:
        current = cls.expire(record, now=now)
        if current.status == PolicyApprovalStatus.EXPIRED:
            raise PolicyApprovalExpiredError(
                "Policy approval has expired.",
                record=current,
            )
        if current.status != PolicyApprovalStatus.PENDING:
            raise PolicyApprovalStateError(
                "Policy approval is not pending.",
                record=current,
            )
        return current
