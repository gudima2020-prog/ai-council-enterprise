from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.control_center.auth_schemas import (
    HumanControlApiTokenCreate,
    HumanControlApiTokenRevoke,
    HumanControlAuthPolicyUpsert,
    HumanControlBootstrapIdentityRequest,
    HumanControlBreakGlassActivate,
    HumanControlBreakGlassDecision,
    HumanControlBreakGlassRequestCreate,
    HumanControlBreakGlassRevoke,
    HumanControlIdentityCreate,
    HumanControlIdentityUpdate,
    HumanControlLoginRequest,
    HumanControlSessionRevokeRequest,
)
from backend.control_center.governance import HumanControlGovernanceService
from backend.control_center.governance_schemas import HumanControlBootstrapOwnerRequest
from backend.control_center.models import (
    HumanControlApiTokenModel,
    HumanControlAuthPolicyModel,
    HumanControlBreakGlassModel,
    HumanControlIdentityModel,
    HumanControlSecurityEventModel,
    HumanControlSessionModel,
)
from backend.control_center.service import (
    HumanControlConflict,
    HumanControlError,
    HumanControlNotFound,
    ensure_utc,
    iso,
    utc_now,
)
from backend.core.events import Event, EventBus
from backend.database.session import session_scope

SessionContextFactory = Callable[[], AbstractContextManager[Session]]
PBKDF2_ITERATIONS = 310_000


def _normalize_username(value: str) -> str:
    return value.strip().casefold()


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _password_digest(password: str, salt: bytes, iterations: int) -> str:
    raw = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return base64.b64encode(raw).decode("ascii")


def _make_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> tuple[str, str, int]:
    salt = secrets.token_bytes(24)
    return (
        _password_digest(password, salt, iterations),
        base64.b64encode(salt).decode("ascii"),
        iterations,
    )


def _verify_password(password: str, digest: str, salt: str, iterations: int) -> bool:
    calculated = _password_digest(password, base64.b64decode(salt), iterations)
    return hmac.compare_digest(calculated, digest)


class HumanControlAuthService:
    """Authenticated operator identities, sessions, API tokens and break-glass."""

    def __init__(
        self,
        *,
        event_bus: EventBus,
        governance: HumanControlGovernanceService | None = None,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._event_bus = event_bus
        self._governance = governance
        self._session_factory = session_factory

    def status(self) -> dict[str, Any]:
        now = utc_now()
        with self._session_factory() as session:
            def count(model: Any, *criteria: Any) -> int:
                stmt = select(func.count()).select_from(model)
                if criteria:
                    stmt = stmt.where(*criteria)
                return int(session.scalar(stmt) or 0)

            return {
                "identities": count(HumanControlIdentityModel),
                "active_identities": count(
                    HumanControlIdentityModel,
                    HumanControlIdentityModel.status == "active",
                ),
                "active_sessions": count(
                    HumanControlSessionModel,
                    HumanControlSessionModel.status == "active",
                    HumanControlSessionModel.expires_at > now,
                    HumanControlSessionModel.idle_expires_at > now,
                ),
                "active_api_tokens": count(
                    HumanControlApiTokenModel,
                    HumanControlApiTokenModel.status == "active",
                    or_(
                        HumanControlApiTokenModel.expires_at.is_(None),
                        HumanControlApiTokenModel.expires_at > now,
                    ),
                ),
                "open_break_glass": count(
                    HumanControlBreakGlassModel,
                    HumanControlBreakGlassModel.status.in_(["requested", "approved", "active"]),
                ),
                "security_events": count(HumanControlSecurityEventModel),
                "password_scheme": "PBKDF2-HMAC-SHA256",
                "token_storage": "SHA-256 hashes only",
                "capabilities": [
                    "operator_identities",
                    "password_sessions",
                    "scoped_api_tokens",
                    "lockout_protection",
                    "break_glass_two_person_control",
                    "security_event_journal",
                ],
            }

    def effective_policy(self, workspace_id: str | None = None) -> dict[str, Any]:
        with self._session_factory() as session:
            row = None
            if workspace_id:
                row = session.scalar(
                    select(HumanControlAuthPolicyModel).where(
                        HumanControlAuthPolicyModel.workspace_id == workspace_id,
                        HumanControlAuthPolicyModel.enabled.is_(True),
                    )
                )
            if row is None:
                row = session.scalar(
                    select(HumanControlAuthPolicyModel).where(
                        HumanControlAuthPolicyModel.scope_key == "global",
                        HumanControlAuthPolicyModel.enabled.is_(True),
                    )
                )
            if row is None:
                return {
                    "workspace_id": workspace_id,
                    "enabled": False,
                    "enforcement_mode": "legacy",
                    "session_ttl_seconds": 28800,
                    "session_idle_seconds": 3600,
                    "max_failed_attempts": 5,
                    "lockout_seconds": 900,
                    "api_token_max_ttl_days": 90,
                    "break_glass_ttl_seconds": 900,
                    "break_glass_requires_approval": True,
                    "break_glass_distinct_approver": True,
                    "allowed_api_scopes": [],
                    "protected_path_prefixes": ["/api/human-control"],
                    "public_paths": [
                        "/", "/docs*", "/redoc*", "/openapi.json",
                        "/api/health*",
                        "/api/human-control/auth/status",
                        "/api/human-control/auth/login",
                        "/api/human-control/auth/bootstrap-identity",
                        "/api/human-control/auth/break-glass/activate",
                        "/api/human-control/governance/bootstrap-owner",
                    ],
                    "require_actor_binding": True,
                    "require_workspace_binding": True,
                    "reject_unscoped_api_tokens": True,
                }
            return self._policy_to_dict(row)

    async def upsert_policy(self, request: HumanControlAuthPolicyUpsert) -> dict[str, Any]:
        scope_key = f"workspace:{request.workspace_id}" if request.workspace_id else "global"
        with self._session_factory() as session:
            row = session.scalar(
                select(HumanControlAuthPolicyModel).where(
                    HumanControlAuthPolicyModel.scope_key == scope_key
                )
            )
            if row is None:
                row = HumanControlAuthPolicyModel(scope_key=scope_key, workspace_id=request.workspace_id)
                session.add(row)
            row.enabled = request.enabled
            row.enforcement_mode = request.enforcement_mode.value
            row.session_ttl_seconds = request.session_ttl_seconds
            row.session_idle_seconds = request.session_idle_seconds
            row.max_failed_attempts = request.max_failed_attempts
            row.lockout_seconds = request.lockout_seconds
            row.api_token_max_ttl_days = request.api_token_max_ttl_days
            row.break_glass_ttl_seconds = request.break_glass_ttl_seconds
            row.break_glass_requires_approval = request.break_glass_requires_approval
            row.break_glass_distinct_approver = request.break_glass_distinct_approver
            row.allowed_api_scopes_json = list(request.allowed_api_scopes)
            row.protected_path_prefixes_json = list(request.protected_path_prefixes)
            row.public_paths_json = list(request.public_paths)
            row.require_actor_binding = request.require_actor_binding
            row.require_workspace_binding = request.require_workspace_binding
            row.reject_unscoped_api_tokens = request.reject_unscoped_api_tokens
            row.metadata_json = dict(request.metadata)
            row.updated_by = request.actor_id
            session.flush()
            result = self._policy_to_dict(row)
        await self._publish("human_control.auth.policy.updated", request.workspace_id, result)
        return result

    async def bootstrap_identity(self, request: HumanControlBootstrapIdentityRequest) -> dict[str, Any]:
        with self._session_factory() as session:
            existing = int(session.scalar(select(func.count()).select_from(HumanControlIdentityModel)) or 0)
        if existing:
            raise HumanControlConflict("Authenticated identity bootstrap already completed.")
        identity = await self.create_identity(HumanControlIdentityCreate(**request.model_dump(exclude={"grant_owner_role", "reason"})))
        if request.grant_owner_role and self._governance is not None:
            await self._governance.bootstrap_owner(
                HumanControlBootstrapOwnerRequest(
                    workspace_id=None,
                    actor_id=request.actor_id,
                    reason=request.reason,
                    metadata={"identity_id": identity["id"], "authenticated_bootstrap": True},
                )
            )
        return identity

    async def create_identity(self, request: HumanControlIdentityCreate) -> dict[str, Any]:
        normalized = _normalize_username(request.username)
        now = utc_now()
        with self._session_factory() as session:
            duplicate = session.scalar(
                select(HumanControlIdentityModel).where(
                    or_(
                        HumanControlIdentityModel.actor_id == request.actor_id,
                        HumanControlIdentityModel.username_normalized == normalized,
                    )
                )
            )
            if duplicate is not None:
                raise HumanControlConflict("actor_id or username already exists.")
            digest = salt = None
            iterations = PBKDF2_ITERATIONS
            if request.password:
                digest, salt, iterations = _make_password(request.password)
            row = HumanControlIdentityModel(
                actor_id=request.actor_id,
                username=request.username.strip(),
                username_normalized=normalized,
                display_name=request.display_name,
                identity_type=request.identity_type.value,
                status="active",
                password_hash=digest,
                password_salt=salt,
                password_iterations=iterations,
                password_changed_at=now if digest else None,
                metadata_json=dict(request.metadata),
                created_by=request.created_by,
            )
            session.add(row)
            session.flush()
            result = self._identity_to_dict(row)
        await self._record_event("identity.created", True, result["id"], request.actor_id, None, None, {})
        return result

    def list_identities(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            stmt = select(HumanControlIdentityModel).order_by(HumanControlIdentityModel.created_at)
            if status:
                stmt = stmt.where(HumanControlIdentityModel.status == status)
            return [self._identity_to_dict(row) for row in session.scalars(stmt)]

    async def update_identity(self, identity_id: str, request: HumanControlIdentityUpdate) -> dict[str, Any]:
        now = utc_now()
        with self._session_factory() as session:
            row = session.get(HumanControlIdentityModel, identity_id)
            if row is None:
                raise HumanControlNotFound("Identity not found.")
            if request.display_name is not None:
                row.display_name = request.display_name
            if request.status is not None:
                row.status = request.status
                if request.status == "active":
                    row.locked_until = None
                    row.failed_attempts = 0
            if request.password is not None:
                row.password_hash, row.password_salt, row.password_iterations = _make_password(request.password)
                row.password_changed_at = now
                row.failed_attempts = 0
                row.locked_until = None
            if request.metadata is not None:
                row.metadata_json = dict(request.metadata)
            session.flush()
            result = self._identity_to_dict(row)
        await self._record_event("identity.updated", True, identity_id, row.actor_id, None, None, {"updated_by": request.actor_id})
        return result

    async def login(self, request: HumanControlLoginRequest) -> dict[str, Any]:
        normalized = _normalize_username(request.username)
        now = utc_now()
        policy = self.effective_policy(request.workspace_id)
        identity_id = actor_id = None
        with self._session_factory() as session:
            row = session.scalar(
                select(HumanControlIdentityModel).where(
                    HumanControlIdentityModel.username_normalized == normalized
                )
            )
            valid = bool(
                row
                and row.status != "disabled"
                and not (row.locked_until and ensure_utc(row.locked_until) > now)
                and row.password_hash
                and row.password_salt
                and _verify_password(
                    request.password,
                    row.password_hash,
                    row.password_salt,
                    row.password_iterations,
                )
            )
            if not valid:
                if row is not None:
                    identity_id, actor_id = row.id, row.actor_id
                    row.failed_attempts += 1
                    if row.failed_attempts >= int(policy["max_failed_attempts"]):
                        row.status = "locked"
                        row.locked_until = now + timedelta(seconds=int(policy["lockout_seconds"]))
                error_details = {"username": normalized}
            else:
                identity_id, actor_id = row.id, row.actor_id
                row.failed_attempts = 0
                row.locked_until = None
                row.status = "active"
                row.last_login_at = now
                row.last_login_ip = request.client_ip
                token = "hc_sess_" + secrets.token_urlsafe(36)
                expires = now + timedelta(seconds=int(policy["session_ttl_seconds"]))
                idle = min(expires, now + timedelta(seconds=int(policy["session_idle_seconds"])))
                session_row = HumanControlSessionModel(
                    identity_id=row.id,
                    workspace_id=request.workspace_id,
                    token_hash=_hash_token(token),
                    token_prefix=token[:20],
                    status="active",
                    auth_method="password",
                    scopes_json=["human_control.session"],
                    client_ip=request.client_ip,
                    user_agent=request.user_agent,
                    expires_at=expires,
                    idle_expires_at=idle,
                    last_seen_at=now,
                    metadata_json={},
                )
                session.add(session_row)
                session.flush()
                result = {
                    "session": self._session_to_dict(session_row),
                    "identity": self._identity_to_dict(row),
                    "access_token": token,
                    "token_type": "Bearer",
                }
        if not valid:
            await self._record_event("login.failed", False, identity_id, actor_id, request.client_ip, request.user_agent, error_details)
            raise HumanControlError("Invalid credentials.")
        await self._record_event("login.succeeded", True, identity_id, actor_id, request.client_ip, request.user_agent, {})
        return result

    def authenticate(self, token: str, *, touch: bool = True) -> dict[str, Any]:
        raw = token.removeprefix("Bearer ").strip()
        digest = _hash_token(raw)
        now = utc_now()
        with self._session_factory() as session:
            session_row = session.scalar(
                select(HumanControlSessionModel).where(HumanControlSessionModel.token_hash == digest)
            )
            if session_row is not None:
                if (
                    session_row.status != "active"
                    or ensure_utc(session_row.expires_at) <= now
                    or ensure_utc(session_row.idle_expires_at) <= now
                ):
                    session_row.status = "expired"
                    raise HumanControlError("Session expired or revoked.")
                identity = session.get(HumanControlIdentityModel, session_row.identity_id)
                if identity is None or identity.status != "active":
                    raise HumanControlError("Identity is not active.")
                if touch:
                    policy = self.effective_policy(session_row.workspace_id)
                    session_row.last_seen_at = now
                    session_row.idle_expires_at = min(
                        ensure_utc(session_row.expires_at),
                        now + timedelta(seconds=int(policy["session_idle_seconds"])),
                    )
                return self._principal(identity, session_row.workspace_id, session_row.auth_method, session_row.scopes_json, session_row.id)

            api_row = session.scalar(
                select(HumanControlApiTokenModel).where(HumanControlApiTokenModel.token_hash == digest)
            )
            if api_row is None or api_row.status != "active":
                raise HumanControlError("Invalid bearer token.")
            if api_row.expires_at and ensure_utc(api_row.expires_at) <= now:
                api_row.status = "expired"
                raise HumanControlError("API token expired.")
            identity = session.get(HumanControlIdentityModel, api_row.identity_id)
            if identity is None or identity.status != "active":
                raise HumanControlError("Identity is not active.")
            if touch:
                api_row.last_used_at = now
            return self._principal(identity, api_row.workspace_id, "api_token", api_row.scopes_json, api_row.id)

    def list_sessions(self, identity_id: str | None = None) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            stmt = select(HumanControlSessionModel).order_by(HumanControlSessionModel.created_at.desc())
            if identity_id:
                stmt = stmt.where(HumanControlSessionModel.identity_id == identity_id)
            return [self._session_to_dict(row) for row in session.scalars(stmt)]

    async def revoke_session(self, session_id: str, request: HumanControlSessionRevokeRequest) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(HumanControlSessionModel, session_id)
            if row is None:
                raise HumanControlNotFound("Session not found.")
            row.status = "revoked"
            row.revoked_at = utc_now()
            row.revoked_by = request.actor_id
            row.revoke_reason = request.reason
            result = self._session_to_dict(row)
        await self._publish("human_control.auth.session.revoked", None, {"session_id": session_id, "actor_id": request.actor_id})
        return result

    async def create_api_token(self, request: HumanControlApiTokenCreate) -> dict[str, Any]:
        policy = self.effective_policy(request.workspace_id)
        now = utc_now()
        if request.expires_at:
            max_expiry = now + timedelta(days=int(policy["api_token_max_ttl_days"]))
            if ensure_utc(request.expires_at) > max_expiry:
                raise HumanControlConflict("API token expiry exceeds policy limit.")
        allowed = set(policy.get("allowed_api_scopes") or [])
        if allowed and not set(request.scopes).issubset(allowed):
            raise HumanControlConflict("Requested API scopes are not allowed by policy.")
        raw = "hc_api_" + secrets.token_urlsafe(40)
        with self._session_factory() as session:
            identity = session.get(HumanControlIdentityModel, request.identity_id)
            if identity is None or identity.status != "active":
                raise HumanControlNotFound("Active identity not found.")
            row = HumanControlApiTokenModel(
                identity_id=identity.id,
                workspace_id=request.workspace_id,
                name=request.name,
                token_hash=_hash_token(raw),
                token_prefix=raw[:20],
                scopes_json=list(request.scopes),
                status="active",
                expires_at=request.expires_at,
                created_by=request.created_by,
                metadata_json=dict(request.metadata),
            )
            session.add(row)
            session.flush()
            result = self._api_token_to_dict(row)
        result["token"] = raw
        await self._record_event("api_token.created", True, request.identity_id, identity.actor_id, None, None, {"token_id": result["id"]})
        return result

    def list_api_tokens(self, identity_id: str | None = None) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            stmt = select(HumanControlApiTokenModel).order_by(HumanControlApiTokenModel.created_at.desc())
            if identity_id:
                stmt = stmt.where(HumanControlApiTokenModel.identity_id == identity_id)
            return [self._api_token_to_dict(row) for row in session.scalars(stmt)]

    async def revoke_api_token(self, token_id: str, request: HumanControlApiTokenRevoke) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(HumanControlApiTokenModel, token_id)
            if row is None:
                raise HumanControlNotFound("API token not found.")
            row.status = "revoked"
            row.revoked_at = utc_now()
            row.revoked_by = request.actor_id
            row.revoke_reason = request.reason
            result = self._api_token_to_dict(row)
        await self._publish("human_control.auth.api_token.revoked", row.workspace_id, {"token_id": token_id})
        return result

    async def request_break_glass(self, request: HumanControlBreakGlassRequestCreate) -> dict[str, Any]:
        policy = self.effective_policy(request.workspace_id)
        with self._session_factory() as session:
            existing = session.scalar(
                select(HumanControlBreakGlassModel).where(
                    HumanControlBreakGlassModel.request_idempotency_key == request.idempotency_key
                )
            )
            if existing is not None:
                return self._break_glass_to_dict(existing)
            identity = session.get(HumanControlIdentityModel, request.requested_by_identity_id)
            if identity is None or identity.status != "active":
                raise HumanControlNotFound("Requesting identity not found.")
            row = HumanControlBreakGlassModel(
                workspace_id=request.workspace_id,
                requested_by_identity_id=identity.id,
                request_idempotency_key=request.idempotency_key,
                reason=request.reason,
                scopes_json=list(request.scopes),
                status="requested" if policy["break_glass_requires_approval"] else "approved",
                metadata_json=dict(request.metadata),
            )
            session.add(row)
            session.flush()
            result = self._break_glass_to_dict(row)
        await self._record_event("break_glass.requested", True, identity.id, identity.actor_id, None, None, {"request_id": result["id"]})
        return result

    async def decide_break_glass(self, request_id: str, request: HumanControlBreakGlassDecision) -> dict[str, Any]:
        raw_token = None
        now = utc_now()
        with self._session_factory() as session:
            row = session.get(HumanControlBreakGlassModel, request_id)
            if row is None:
                raise HumanControlNotFound("Break-glass request not found.")
            if row.status not in {"requested", "approved"}:
                raise HumanControlConflict("Break-glass request is not pending.")
            policy = self.effective_policy(row.workspace_id)
            if policy["break_glass_distinct_approver"] and row.requested_by_identity_id == request.approver_identity_id:
                raise HumanControlConflict("Break-glass approver must be a different identity.")
            approver = session.get(HumanControlIdentityModel, request.approver_identity_id)
            if approver is None or approver.status != "active":
                raise HumanControlNotFound("Approver identity not found.")
            row.approved_by_identity_id = approver.id
            row.resolution_reason = request.reason
            if request.approve:
                raw_token = "hc_bg_" + secrets.token_urlsafe(36)
                row.activation_token_hash = _hash_token(raw_token)
                row.activation_token_prefix = raw_token[:20]
                row.status = "approved"
                row.approved_at = now
                row.expires_at = now + timedelta(seconds=int(policy["break_glass_ttl_seconds"]))
            else:
                row.status = "rejected"
                row.resolved_at = now
            result = self._break_glass_to_dict(row)
        if raw_token:
            result["activation_token"] = raw_token
        await self._record_event("break_glass.approved" if request.approve else "break_glass.rejected", True, request.approver_identity_id, approver.actor_id, None, None, {"request_id": request_id})
        return result

    async def activate_break_glass(self, request: HumanControlBreakGlassActivate) -> dict[str, Any]:
        now = utc_now()
        digest = _hash_token(request.activation_token)
        with self._session_factory() as session:
            grant = session.scalar(
                select(HumanControlBreakGlassModel).where(
                    HumanControlBreakGlassModel.activation_token_hash == digest
                )
            )
            if grant is None or grant.status != "approved":
                raise HumanControlError("Invalid break-glass activation token.")
            if grant.expires_at is None or ensure_utc(grant.expires_at) <= now:
                grant.status = "expired"
                raise HumanControlError("Break-glass approval expired.")
            identity = session.get(HumanControlIdentityModel, grant.requested_by_identity_id)
            if identity is None or identity.status != "active":
                raise HumanControlError("Requesting identity is not active.")
            raw = "hc_sess_" + secrets.token_urlsafe(36)
            expires = ensure_utc(grant.expires_at)
            session_row = HumanControlSessionModel(
                identity_id=identity.id,
                workspace_id=grant.workspace_id,
                token_hash=_hash_token(raw),
                token_prefix=raw[:20],
                status="active",
                auth_method="break_glass",
                scopes_json=list(grant.scopes_json or []),
                client_ip=request.client_ip,
                user_agent=request.user_agent,
                expires_at=expires,
                idle_expires_at=expires,
                last_seen_at=now,
                metadata_json={"break_glass_id": grant.id},
            )
            session.add(session_row)
            grant.status = "active"
            grant.activated_at = now
            grant.activation_token_hash = None
            session.flush()
            result = {
                "session": self._session_to_dict(session_row),
                "identity": self._identity_to_dict(identity),
                "access_token": raw,
                "token_type": "Bearer",
                "break_glass": self._break_glass_to_dict(grant),
            }
        await self._record_event("break_glass.activated", True, identity.id, identity.actor_id, request.client_ip, request.user_agent, {"request_id": grant.id})
        return result

    async def revoke_break_glass(self, request_id: str, request: HumanControlBreakGlassRevoke) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(HumanControlBreakGlassModel, request_id)
            if row is None:
                raise HumanControlNotFound("Break-glass request not found.")
            row.status = "revoked"
            row.resolved_at = utc_now()
            row.resolution_reason = request.reason
            sessions = list(
                session.scalars(
                    select(HumanControlSessionModel).where(
                        HumanControlSessionModel.auth_method == "break_glass",
                        HumanControlSessionModel.status == "active",
                    )
                )
            )
            for session_row in sessions:
                if (session_row.metadata_json or {}).get("break_glass_id") == row.id:
                    session_row.status = "revoked"
                    session_row.revoked_at = utc_now()
                    session_row.revoked_by = request.actor_id
                    session_row.revoke_reason = request.reason
            result = self._break_glass_to_dict(row)
        await self._publish("human_control.auth.break_glass.revoked", row.workspace_id, {"request_id": request_id, "actor_id": request.actor_id})
        return result

    def list_break_glass(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            stmt = select(HumanControlBreakGlassModel).order_by(HumanControlBreakGlassModel.requested_at.desc())
            if status:
                stmt = stmt.where(HumanControlBreakGlassModel.status == status)
            return [self._break_glass_to_dict(row) for row in session.scalars(stmt)]

    def security_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(HumanControlSecurityEventModel)
                .order_by(HumanControlSecurityEventModel.created_at.desc())
                .limit(max(1, min(limit, 500)))
            )
            return [self._security_event_to_dict(row) for row in rows]

    async def reconcile(self) -> dict[str, int]:
        now = utc_now()
        sessions = tokens = grants = 0
        with self._session_factory() as session:
            for row in session.scalars(
                select(HumanControlSessionModel).where(HumanControlSessionModel.status == "active")
            ):
                if ensure_utc(row.expires_at) <= now or ensure_utc(row.idle_expires_at) <= now:
                    row.status = "expired"; sessions += 1
            for row in session.scalars(
                select(HumanControlApiTokenModel).where(
                    HumanControlApiTokenModel.status == "active",
                    HumanControlApiTokenModel.expires_at.is_not(None),
                )
            ):
                if ensure_utc(row.expires_at) <= now:
                    row.status = "expired"; tokens += 1
            for row in session.scalars(
                select(HumanControlBreakGlassModel).where(
                    HumanControlBreakGlassModel.status.in_(["approved", "active"]),
                    HumanControlBreakGlassModel.expires_at.is_not(None),
                )
            ):
                if ensure_utc(row.expires_at) <= now:
                    row.status = "expired"; row.resolved_at = now; grants += 1
        return {"expired_sessions": sessions, "expired_api_tokens": tokens, "expired_break_glass": grants}

    async def record_access_event(
        self,
        *,
        event_type: str,
        success: bool,
        workspace_id: str | None,
        identity_id: str | None,
        actor_id: str | None,
        client_ip: str | None,
        user_agent: str | None,
        details: dict[str, Any],
    ) -> None:
        await self._record_event(
            event_type, success, identity_id, actor_id, client_ip, user_agent,
            details, workspace_id=workspace_id,
        )

    async def _record_event(self, event_type: str, success: bool, identity_id: str | None, actor_id: str | None, client_ip: str | None, user_agent: str | None, details: dict[str, Any], workspace_id: str | None = None) -> None:
        with self._session_factory() as session:
            row = HumanControlSecurityEventModel(
                workspace_id=workspace_id,
                identity_id=identity_id,
                actor_id=actor_id,
                event_type=event_type,
                success=success,
                client_ip=client_ip,
                user_agent=user_agent,
                details_json=dict(details),
            )
            session.add(row)
        await self._publish(f"human_control.auth.{event_type}", workspace_id, {"identity_id": identity_id, "actor_id": actor_id, "success": success, **details})

    async def _publish(self, event_type: str, workspace_id: str | None, payload: dict[str, Any]) -> None:
        await self._event_bus.publish(Event(event_type=event_type, source="human_control_auth", workspace_id=workspace_id, payload=payload))

    @staticmethod
    def _principal(identity: HumanControlIdentityModel, workspace_id: str | None, auth_method: str, scopes: list[str], credential_id: str) -> dict[str, Any]:
        return {"identity_id": identity.id, "actor_id": identity.actor_id, "username": identity.username, "display_name": identity.display_name, "identity_type": identity.identity_type, "workspace_id": workspace_id, "auth_method": auth_method, "scopes": list(scopes or []), "credential_id": credential_id}

    @staticmethod
    def _policy_to_dict(row: HumanControlAuthPolicyModel) -> dict[str, Any]:
        return {"id": row.id, "scope_key": row.scope_key, "workspace_id": row.workspace_id, "enabled": row.enabled, "enforcement_mode": row.enforcement_mode, "session_ttl_seconds": row.session_ttl_seconds, "session_idle_seconds": row.session_idle_seconds, "max_failed_attempts": row.max_failed_attempts, "lockout_seconds": row.lockout_seconds, "api_token_max_ttl_days": row.api_token_max_ttl_days, "break_glass_ttl_seconds": row.break_glass_ttl_seconds, "break_glass_requires_approval": row.break_glass_requires_approval, "break_glass_distinct_approver": row.break_glass_distinct_approver, "allowed_api_scopes": list(row.allowed_api_scopes_json or []), "protected_path_prefixes": list(row.protected_path_prefixes_json or ["/api/human-control"]), "public_paths": list(row.public_paths_json or []), "require_actor_binding": row.require_actor_binding, "require_workspace_binding": row.require_workspace_binding, "reject_unscoped_api_tokens": row.reject_unscoped_api_tokens, "metadata": dict(row.metadata_json or {}), "updated_by": row.updated_by, "created_at": iso(row.created_at), "updated_at": iso(row.updated_at)}

    @staticmethod
    def _identity_to_dict(row: HumanControlIdentityModel) -> dict[str, Any]:
        return {"id": row.id, "actor_id": row.actor_id, "username": row.username, "display_name": row.display_name, "identity_type": row.identity_type, "status": row.status, "failed_attempts": row.failed_attempts, "locked_until": iso(row.locked_until), "password_changed_at": iso(row.password_changed_at), "last_login_at": iso(row.last_login_at), "metadata": dict(row.metadata_json or {}), "created_by": row.created_by, "created_at": iso(row.created_at), "updated_at": iso(row.updated_at)}

    @staticmethod
    def _session_to_dict(row: HumanControlSessionModel) -> dict[str, Any]:
        return {"id": row.id, "identity_id": row.identity_id, "workspace_id": row.workspace_id, "token_prefix": row.token_prefix, "status": row.status, "auth_method": row.auth_method, "scopes": list(row.scopes_json or []), "created_at": iso(row.created_at), "expires_at": iso(row.expires_at), "idle_expires_at": iso(row.idle_expires_at), "last_seen_at": iso(row.last_seen_at), "revoked_at": iso(row.revoked_at), "revoked_by": row.revoked_by, "revoke_reason": row.revoke_reason}

    @staticmethod
    def _api_token_to_dict(row: HumanControlApiTokenModel) -> dict[str, Any]:
        return {"id": row.id, "identity_id": row.identity_id, "workspace_id": row.workspace_id, "name": row.name, "token_prefix": row.token_prefix, "scopes": list(row.scopes_json or []), "status": row.status, "expires_at": iso(row.expires_at), "last_used_at": iso(row.last_used_at), "created_by": row.created_by, "created_at": iso(row.created_at), "revoked_at": iso(row.revoked_at), "revoked_by": row.revoked_by, "revoke_reason": row.revoke_reason, "metadata": dict(row.metadata_json or {})}

    @staticmethod
    def _break_glass_to_dict(row: HumanControlBreakGlassModel) -> dict[str, Any]:
        return {"id": row.id, "workspace_id": row.workspace_id, "requested_by_identity_id": row.requested_by_identity_id, "approved_by_identity_id": row.approved_by_identity_id, "reason": row.reason, "scopes": list(row.scopes_json or []), "status": row.status, "activation_token_prefix": row.activation_token_prefix, "requested_at": iso(row.requested_at), "approved_at": iso(row.approved_at), "activated_at": iso(row.activated_at), "expires_at": iso(row.expires_at), "resolved_at": iso(row.resolved_at), "resolution_reason": row.resolution_reason, "metadata": dict(row.metadata_json or {})}

    @staticmethod
    def _security_event_to_dict(row: HumanControlSecurityEventModel) -> dict[str, Any]:
        return {"id": row.id, "workspace_id": row.workspace_id, "identity_id": row.identity_id, "actor_id": row.actor_id, "event_type": row.event_type, "success": row.success, "client_ip": row.client_ip, "user_agent": row.user_agent, "details": dict(row.details_json or {}), "created_at": iso(row.created_at)}
