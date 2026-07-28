from __future__ import annotations

import hashlib
import hmac
import secrets
from contextlib import AbstractContextManager
from datetime import timedelta
from typing import Any, Callable
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.control_center.auth import HumanControlAuthService, _hash_token
from backend.control_center.auth_schemas import HumanControlLoginRequest
from backend.control_center.browser_schemas import (
    HumanControlBrowserLoginRequest,
    HumanControlBrowserPolicyUpsert,
    HumanControlTrustedClientCreate,
    HumanControlTrustedClientUpdate,
)
from backend.control_center.models import (
    HumanControlBrowserPolicyModel,
    HumanControlSessionModel,
    HumanControlTrustedClientModel,
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
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def normalize_origin(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    raw = value.strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HumanControlError("Invalid browser Origin.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HumanControlError("Invalid browser Origin.")
    path = parsed.path.rstrip("/")
    if path:
        raise HumanControlError("Origin must not contain a path.")
    host = parsed.hostname.lower()
    default_port = 443 if parsed.scheme == "https" else 80
    port = parsed.port
    authority = host if port in (None, default_port) else f"{host}:{port}"
    return f"{parsed.scheme.lower()}://{authority}"


def _fingerprint(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class HumanControlBrowserSecurityService:
    """Trusted browser clients, HttpOnly sessions and CSRF double-submit protection."""

    def __init__(
        self,
        *,
        event_bus: EventBus,
        auth_service: HumanControlAuthService,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._event_bus = event_bus
        self._auth = auth_service
        self._session_factory = session_factory

    def status(self) -> dict[str, Any]:
        now = utc_now()
        with self._session_factory() as session:
            return {
                "policies": int(session.scalar(select(func.count()).select_from(HumanControlBrowserPolicyModel)) or 0),
                "trusted_clients": int(session.scalar(select(func.count()).select_from(HumanControlTrustedClientModel).where(HumanControlTrustedClientModel.enabled.is_(True))) or 0),
                "active_browser_sessions": sum(
                    1
                    for row in session.scalars(
                        select(HumanControlSessionModel).where(
                            HumanControlSessionModel.status == "active",
                            HumanControlSessionModel.expires_at > now,
                            HumanControlSessionModel.idle_expires_at > now,
                        )
                    )
                    if bool((row.metadata_json or {}).get("browser_session"))
                ),
                "capabilities": [
                    "httponly_browser_session",
                    "csrf_double_submit",
                    "origin_policy",
                    "trusted_client_registry",
                    "secure_cookie_policy",
                    "browser_session_binding",
                ],
            }

    def effective_policy(self, workspace_id: str | None = None) -> dict[str, Any]:
        with self._session_factory() as session:
            row = None
            if workspace_id:
                row = session.scalar(select(HumanControlBrowserPolicyModel).where(HumanControlBrowserPolicyModel.workspace_id == workspace_id, HumanControlBrowserPolicyModel.enabled.is_(True)))
            if row is None:
                row = session.scalar(select(HumanControlBrowserPolicyModel).where(HumanControlBrowserPolicyModel.scope_key == "global", HumanControlBrowserPolicyModel.enabled.is_(True)))
            if row is None:
                return {
                    "workspace_id": workspace_id,
                    "enabled": False,
                    "csrf_enabled": True,
                    "require_trusted_client": True,
                    "allow_missing_origin": False,
                    "cookie_secure": True,
                    "cookie_samesite": "strict",
                    "cookie_domain": None,
                    "session_cookie_name": "hc_browser_session",
                    "csrf_cookie_name": "hc_csrf",
                    "csrf_header_name": "X-CSRF-Token",
                    "session_ttl_seconds": 28800,
                    "session_idle_seconds": 1800,
                    "rotate_csrf_on_login": True,
                    "bind_user_agent": True,
                    "bind_client_ip": False,
                }
            return self._policy_to_dict(row)

    async def upsert_policy(self, request: HumanControlBrowserPolicyUpsert) -> dict[str, Any]:
        scope_key = f"workspace:{request.workspace_id}" if request.workspace_id else "global"
        with self._session_factory() as session:
            row = session.scalar(select(HumanControlBrowserPolicyModel).where(HumanControlBrowserPolicyModel.scope_key == scope_key))
            if row is None:
                row = HumanControlBrowserPolicyModel(scope_key=scope_key, workspace_id=request.workspace_id)
                session.add(row)
            row.enabled = request.enabled
            row.csrf_enabled = request.csrf_enabled
            row.require_trusted_client = request.require_trusted_client
            row.allow_missing_origin = request.allow_missing_origin
            row.cookie_secure = request.cookie_secure
            row.cookie_samesite = request.cookie_samesite.value
            row.cookie_domain = request.cookie_domain
            row.session_cookie_name = request.session_cookie_name
            row.csrf_cookie_name = request.csrf_cookie_name
            row.csrf_header_name = request.csrf_header_name
            row.session_ttl_seconds = request.session_ttl_seconds
            row.session_idle_seconds = request.session_idle_seconds
            row.rotate_csrf_on_login = request.rotate_csrf_on_login
            row.bind_user_agent = request.bind_user_agent
            row.bind_client_ip = request.bind_client_ip
            row.metadata_json = dict(request.metadata)
            row.updated_by = request.actor_id
            session.flush()
            result = self._policy_to_dict(row)
        await self._publish("human_control.browser.policy.updated", request.workspace_id, {"actor_id": request.actor_id, "policy_id": result["id"]})
        return result

    async def create_trusted_client(self, request: HumanControlTrustedClientCreate) -> dict[str, Any]:
        origins = sorted({normalize_origin(value) for value in request.allowed_origins})
        with self._session_factory() as session:
            existing = session.scalar(select(HumanControlTrustedClientModel).where(HumanControlTrustedClientModel.workspace_id == request.workspace_id, HumanControlTrustedClientModel.client_key == request.client_key))
            if existing is not None:
                raise HumanControlConflict("Trusted client key already exists in this scope.")
            row = HumanControlTrustedClientModel(
                workspace_id=request.workspace_id,
                client_key=request.client_key,
                name=request.name,
                allowed_origins_json=origins,
                enabled=request.enabled,
                metadata_json=dict(request.metadata),
                created_by=request.created_by,
                updated_by=request.created_by,
            )
            session.add(row)
            session.flush()
            result = self._client_to_dict(row)
        await self._publish("human_control.browser.client.created", request.workspace_id, {"client_id": result["id"], "actor_id": request.created_by})
        return result

    def list_trusted_clients(self, workspace_id: str | None = None, include_disabled: bool = False) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            stmt = select(HumanControlTrustedClientModel).order_by(HumanControlTrustedClientModel.created_at.desc())
            if workspace_id is not None:
                stmt = stmt.where(HumanControlTrustedClientModel.workspace_id == workspace_id)
            if not include_disabled:
                stmt = stmt.where(HumanControlTrustedClientModel.enabled.is_(True))
            return [self._client_to_dict(row) for row in session.scalars(stmt)]

    async def update_trusted_client(self, client_id: str, request: HumanControlTrustedClientUpdate) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(HumanControlTrustedClientModel, client_id)
            if row is None:
                raise HumanControlNotFound("Trusted client not found.")
            if request.name is not None:
                row.name = request.name
            if request.allowed_origins is not None:
                row.allowed_origins_json = sorted({normalize_origin(value) for value in request.allowed_origins})
            if request.enabled is not None:
                row.enabled = request.enabled
            if request.metadata is not None:
                row.metadata_json = dict(request.metadata)
            row.updated_by = request.actor_id
            session.flush()
            result = self._client_to_dict(row)
        await self._publish("human_control.browser.client.updated", row.workspace_id, {"client_id": client_id, "actor_id": request.actor_id})
        return result

    def resolve_trusted_client(self, *, workspace_id: str | None, origin: str | None, client_key: str | None, policy: dict[str, Any] | None = None) -> dict[str, Any] | None:
        policy = policy or self.effective_policy(workspace_id)
        normalized = normalize_origin(origin)
        if normalized is None:
            if policy.get("allow_missing_origin", False):
                return None
            raise HumanControlError("Browser Origin header is required.")
        if not policy.get("require_trusted_client", True):
            return {"id": None, "client_key": client_key, "allowed_origins": [normalized]}
        with self._session_factory() as session:
            stmt = select(HumanControlTrustedClientModel).where(HumanControlTrustedClientModel.enabled.is_(True))
            if client_key:
                stmt = stmt.where(HumanControlTrustedClientModel.client_key == client_key)
            rows = list(session.scalars(stmt))
            candidates = [row for row in rows if row.workspace_id in (None, workspace_id)]
            for row in candidates:
                if normalized in set(row.allowed_origins_json or []):
                    return self._client_to_dict(row)
        raise HumanControlError("Browser client or Origin is not trusted.")

    async def login(self, request: HumanControlBrowserLoginRequest, *, origin: str | None, client_ip: str | None, user_agent: str | None) -> dict[str, Any]:
        policy = self.effective_policy(request.workspace_id)
        if not policy.get("enabled", False):
            raise HumanControlError("Browser sessions are disabled by policy.")
        client = self.resolve_trusted_client(workspace_id=request.workspace_id, origin=origin, client_key=request.client_key, policy=policy)
        login = await self._auth.login(HumanControlLoginRequest(username=request.username, password=request.password, workspace_id=request.workspace_id, client_ip=client_ip, user_agent=user_agent))
        token = login["access_token"]
        csrf_token = "hc_csrf_" + secrets.token_urlsafe(36)
        now = utc_now()
        expires = now + timedelta(seconds=int(policy["session_ttl_seconds"]))
        idle = min(expires, now + timedelta(seconds=int(policy["session_idle_seconds"])))
        with self._session_factory() as session:
            row = session.get(HumanControlSessionModel, login["session"]["id"])
            if row is None:
                raise HumanControlError("Browser session creation failed.")
            row.scopes_json = ["human_control.session", "human_control.browser"]
            row.expires_at = expires
            row.idle_expires_at = idle
            row.metadata_json = {
                **dict(row.metadata_json or {}),
                "browser_session": True,
                "csrf_hash": _hash_token(csrf_token),
                "trusted_client_id": client.get("id") if client else None,
                "client_key": client.get("client_key") if client else request.client_key,
                "origin": normalize_origin(origin),
                "user_agent_hash": _fingerprint(user_agent),
                "client_ip_hash": _fingerprint(client_ip),
            }
            session.flush()
            session_data = self._auth._session_to_dict(row)
            session_data["auth_method"] = "browser"
        await self._publish("human_control.browser.session.created", request.workspace_id, {"session_id": session_data["id"], "identity_id": login["identity"]["id"]})
        return {
            "session": session_data,
            "identity": login["identity"],
            "session_token": token,
            "csrf_token": csrf_token,
            "cookie": self.cookie_settings(policy),
        }

    def authenticate_cookie(self, token: str, *, workspace_id: str | None, origin: str | None, client_ip: str | None, user_agent: str | None, touch: bool = True) -> dict[str, Any]:
        payload = self._auth.authenticate(token, touch=touch)
        policy = self.effective_policy(workspace_id or payload.get("workspace_id"))
        self.resolve_trusted_client(workspace_id=workspace_id or payload.get("workspace_id"), origin=origin, client_key=None, policy=policy)
        with self._session_factory() as session:
            row = session.get(HumanControlSessionModel, payload["credential_id"])
            if row is None:
                raise HumanControlError("Browser session not found.")
            metadata = dict(row.metadata_json or {})
            if not metadata.get("browser_session"):
                raise HumanControlError("Credential is not a browser session.")
            if policy.get("bind_user_agent", True) and metadata.get("user_agent_hash") != _fingerprint(user_agent):
                raise HumanControlError("Browser session user-agent binding failed.")
            if policy.get("bind_client_ip", False) and metadata.get("client_ip_hash") != _fingerprint(client_ip):
                raise HumanControlError("Browser session IP binding failed.")
        return {**payload, "auth_method": "browser"}

    def validate_csrf(self, *, session_id: str, csrf_token: str | None, method: str, policy: dict[str, Any]) -> None:
        if method.upper() not in UNSAFE_METHODS or not policy.get("csrf_enabled", True):
            return
        if not csrf_token:
            raise HumanControlError("CSRF token is required.")
        with self._session_factory() as session:
            row = session.get(HumanControlSessionModel, session_id)
            if row is None or row.status != "active":
                raise HumanControlError("Browser session is not active.")
            expected = str((row.metadata_json or {}).get("csrf_hash") or "")
        if not expected or not hmac.compare_digest(expected, _hash_token(csrf_token)):
            raise HumanControlError("Invalid CSRF token.")

    async def rotate_csrf(self, session_id: str, actor_id: str | None) -> dict[str, Any]:
        token = "hc_csrf_" + secrets.token_urlsafe(36)
        with self._session_factory() as session:
            row = session.get(HumanControlSessionModel, session_id)
            if (
                row is None
                or row.status != "active"
                or not bool((row.metadata_json or {}).get("browser_session"))
            ):
                raise HumanControlNotFound("Active browser session not found.")
            row.metadata_json = {**dict(row.metadata_json or {}), "csrf_hash": _hash_token(token), "csrf_rotated_at": iso(utc_now())}
            workspace_id = row.workspace_id
        await self._publish("human_control.browser.csrf.rotated", workspace_id, {"session_id": session_id, "actor_id": actor_id})
        return {"csrf_token": token, "session_id": session_id}

    async def logout(self, session_id: str, actor_id: str | None, reason: str) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(HumanControlSessionModel, session_id)
            if row is None:
                raise HumanControlNotFound("Browser session not found.")
            row.status = "revoked"
            row.revoked_at = utc_now()
            row.revoked_by = actor_id
            row.revoke_reason = reason
            workspace_id = row.workspace_id
            result = self._auth._session_to_dict(row)
        await self._publish("human_control.browser.session.revoked", workspace_id, {"session_id": session_id, "actor_id": actor_id})
        return result

    @staticmethod
    def cookie_settings(policy: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_cookie_name": policy.get("session_cookie_name", "hc_browser_session"),
            "csrf_cookie_name": policy.get("csrf_cookie_name", "hc_csrf"),
            "csrf_header_name": policy.get("csrf_header_name", "X-CSRF-Token"),
            "secure": bool(policy.get("cookie_secure", True)),
            "httponly": True,
            "samesite": str(policy.get("cookie_samesite") or "strict"),
            "domain": policy.get("cookie_domain"),
            "max_age": int(policy.get("session_ttl_seconds", 28800)),
        }

    async def reconcile(self) -> dict[str, int]:
        now = utc_now()
        expired = 0
        with self._session_factory() as session:
            rows = session.scalars(
                select(HumanControlSessionModel).where(
                    HumanControlSessionModel.status == "active"
                )
            )
            for row in rows:
                if not bool((row.metadata_json or {}).get("browser_session")):
                    continue
                if ensure_utc(row.expires_at) <= now or ensure_utc(row.idle_expires_at) <= now:
                    row.status = "expired"
                    expired += 1
        return {"expired_browser_sessions": expired}

    async def _publish(self, event_type: str, workspace_id: str | None, payload: dict[str, Any]) -> None:
        await self._event_bus.publish(Event(event_type=event_type, source="human_control_browser_security", workspace_id=workspace_id, payload=payload))

    @staticmethod
    def _policy_to_dict(row: HumanControlBrowserPolicyModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope_key": row.scope_key,
            "workspace_id": row.workspace_id,
            "enabled": row.enabled,
            "csrf_enabled": row.csrf_enabled,
            "require_trusted_client": row.require_trusted_client,
            "allow_missing_origin": row.allow_missing_origin,
            "cookie_secure": row.cookie_secure,
            "cookie_samesite": row.cookie_samesite,
            "cookie_domain": row.cookie_domain,
            "session_cookie_name": row.session_cookie_name,
            "csrf_cookie_name": row.csrf_cookie_name,
            "csrf_header_name": row.csrf_header_name,
            "session_ttl_seconds": row.session_ttl_seconds,
            "session_idle_seconds": row.session_idle_seconds,
            "rotate_csrf_on_login": row.rotate_csrf_on_login,
            "bind_user_agent": row.bind_user_agent,
            "bind_client_ip": row.bind_client_ip,
            "metadata": dict(row.metadata_json or {}),
            "updated_by": row.updated_by,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _client_to_dict(row: HumanControlTrustedClientModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "client_key": row.client_key,
            "name": row.name,
            "allowed_origins": list(row.allowed_origins_json or []),
            "enabled": row.enabled,
            "metadata": dict(row.metadata_json or {}),
            "created_by": row.created_by,
            "updated_by": row.updated_by,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }
