from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Any
from fnmatch import fnmatchcase
import asyncio
import hashlib
import inspect
import os
import secrets as pysecrets

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from backend.core.events import EventBus
from backend.database.session import session_scope
from backend.secrets.references import SecretReferenceError, parse_secret_reference
from backend.secrets.models import (
    SecretAccessEventModel,
    SecretAccessPolicyModel,
    SecretAccessSettingsModel,
    SecretLeaseModel,
    SecretProviderModel,
    SecretRecordModel,
    SecretVersionModel,
    SecretRotationPolicyModel,
    SecretRotationRunModel,
    SecretHealthCheckModel,
    SecretHealthAlertModel,
)
from backend.secrets.providers import (
    ProviderRegistry,
    SecretProviderAdapter,
    SecretProviderError,
    SecretProviderUnavailable,
    StoredSecretMaterial,
    material_hash,
)
from backend.secrets.schemas import (
    SecretAccessEvaluationRequest,
    SecretAccessPolicyCreate,
    SecretAccessPolicyUpdate,
    SecretAccessSettingsUpsert,
    SecretCreate,
    SecretLeaseCreate,
    SecretLeaseRevokeRequest,
    SecretProviderCreate,
    SecretProviderUpdate,
    SecretResolveContext,
    SecretRotateRequest,
    SecretStateRequest,
    SecretRotationPolicyUpsert,
    SecretRotationRunCreate,
    SecretRotationRunDecision,
    SecretLifecycleScanRequest,
    SecretHealthAlertDecision,
    validate_public_provider_config,
)


SessionContextFactory = Callable[[], AbstractContextManager[Session]]
RotationHandler = Callable[[dict[str, Any]], Awaitable[str] | str]


class SecretManagerError(ValueError):
    pass


class SecretNotFound(SecretManagerError):
    pass


class SecretConflict(SecretManagerError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _scope_key(workspace_id: str | None) -> str:
    return workspace_id or "global"


def _locator(provider_key: str, workspace_id: str | None, secret_key: str) -> str:
    return f"{provider_key}:{_scope_key(workspace_id)}:{secret_key}"


def _reference(provider_key: str, secret_key: str) -> str:
    return f"secret://{provider_key}/{secret_key}"


def _workspace_allowed(resource_workspace_id: str | None, expected_workspace_id: str | None) -> bool:
    if expected_workspace_id is None:
        return True
    return resource_workspace_id in {None, expected_workspace_id}




class SecretManagerService:
    """Metadata-only secret registry with pluggable value providers.

    Plaintext secret values are never stored in SecretRecordModel. Local writable
    storage uses Windows DPAPI and stores only its encrypted payload in the
    version table. Environment and external providers retain their values outside
    the application database.
    """

    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
        provider_registry: ProviderRegistry | None = None,
    ) -> None:
        self.event_bus = event_bus
        self._session_factory = session_factory
        self.providers = provider_registry or ProviderRegistry()
        self._resolution_count = 0
        self._resolution_failures = 0
        self._lease_issued_count = 0
        self._lease_resolved_count = 0
        self._policy_denied_count = 0
        self._rotation_handlers: dict[str, RotationHandler] = {}
        self._monitor_task: asyncio.Task[None] | None = None
        self._monitor_interval_seconds = max(
            30, int(os.getenv("AI_STUDIO_SECRET_MONITOR_SECONDS", "300"))
        )
        self._lifecycle_scan_count = 0
        self._auto_rotation_count = 0

    def register_provider_adapter(self, adapter: SecretProviderAdapter) -> None:
        self.providers.register(adapter)

    def register_rotation_handler(self, key: str, handler: RotationHandler) -> None:
        normalized = str(key).strip()
        if not normalized:
            raise ValueError("Rotation handler key is required.")
        self._rotation_handlers[normalized] = handler

    def seed_builtin_providers(self) -> int:
        created = 0
        definitions = [
            {
                "provider_key": "env",
                "name": "Environment Variables",
                "provider_type": "env",
                "adapter_key": "env",
                "read_only": True,
                "config_json": {},
            },
            {
                "provider_key": "windows-dpapi",
                "name": "Windows DPAPI Local Vault",
                "provider_type": "windows_dpapi",
                "adapter_key": "windows_dpapi",
                "read_only": False,
                "config_json": {"scope": "user"},
            },
        ]
        with self._session_factory() as session:
            for definition in definitions:
                row = session.scalar(
                    select(SecretProviderModel).where(
                        SecretProviderModel.provider_key == definition["provider_key"]
                    )
                )
                if row is None:
                    row = SecretProviderModel(
                        workspace_id=None,
                        enabled=True,
                        health_status="unknown",
                        health_message="",
                        metadata_json={"builtin": True},
                        created_by="system",
                        **definition,
                    )
                    session.add(row)
                    created += 1
                else:
                    row.name = definition["name"]
                    row.provider_type = definition["provider_type"]
                    row.adapter_key = definition["adapter_key"]
                    row.read_only = bool(definition["read_only"])
        return created

    def status(self) -> dict[str, Any]:
        with self._session_factory() as session:
            provider_count = int(session.scalar(select(func.count()).select_from(SecretProviderModel)) or 0)
            secret_count = int(session.scalar(select(func.count()).select_from(SecretRecordModel)) or 0)
            active_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(SecretRecordModel)
                    .where(SecretRecordModel.status == "active")
                )
                or 0
            )
            policy_count = int(session.scalar(select(func.count()).select_from(SecretAccessPolicyModel)) or 0)
            active_lease_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(SecretLeaseModel)
                    .where(SecretLeaseModel.status == "active")
                )
                or 0
            )
            rotation_policy_count = int(
                session.scalar(select(func.count()).select_from(SecretRotationPolicyModel)) or 0
            )
            open_health_alert_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(SecretHealthAlertModel)
                    .where(SecretHealthAlertModel.status == "open")
                ) or 0
            )
        return {
            "running": True,
            "providers": provider_count,
            "registered_adapters": self.providers.keys(),
            "secrets": secret_count,
            "active_secrets": active_count,
            "resolutions": self._resolution_count,
            "resolution_failures": self._resolution_failures,
            "access_policies": policy_count,
            "active_leases": active_lease_count,
            "leases_issued": self._lease_issued_count,
            "leases_resolved": self._lease_resolved_count,
            "policy_denials": self._policy_denied_count,
            "rotation_policies": rotation_policy_count,
            "open_health_alerts": open_health_alert_count,
            "registered_rotation_handlers": sorted(self._rotation_handlers),
            "lifecycle_monitor_running": self._monitor_task is not None and not self._monitor_task.done(),
            "lifecycle_scans": self._lifecycle_scan_count,
            "automatic_rotations": self._auto_rotation_count,
            "plaintext_persisted": False,
            "api_plaintext_read_enabled": False,
            "lease_plaintext_api_enabled": False,
            "reference_format": "secret://provider/secret-key",
            "capabilities": [
                "environment_secret_references",
                "windows_dpapi_encrypted_local_vault",
                "pluggable_external_provider_adapters",
                "versioned_rotation_metadata",
                "workspace_scoping",
                "immutable_access_log",
                "policy_evaluated_access",
                "ephemeral_secret_leases",
                "tool_and_gateway_secret_injection",
                "secret_value_redaction",
                "external_secret_providers",
                "rotation_policy_and_history",
                "scheduled_health_monitoring",
                "managed_random_rotation",
                "external_rotation_handlers",
                "no_plaintext_secret_api",
            ],
        }

    async def create_provider(self, request: SecretProviderCreate) -> dict[str, Any]:
        config = validate_public_provider_config(dict(request.config))
        adapter_key = request.adapter_key or request.provider_type.value
        try:
            adapter = self.providers.get(adapter_key)
            detected_read_only = bool(adapter.read_only)
        except SecretProviderUnavailable:
            adapter = None
            detected_read_only = request.provider_type.value != "windows_dpapi"
        read_only = detected_read_only if request.read_only is None else bool(request.read_only)
        with self._session_factory() as session:
            if session.scalar(
                select(SecretProviderModel).where(
                    SecretProviderModel.provider_key == request.provider_key
                )
            ) is not None:
                raise SecretConflict("Secret provider key already exists.")
            row = SecretProviderModel(
                provider_key=request.provider_key,
                workspace_id=request.workspace_id,
                name=request.name,
                provider_type=request.provider_type.value,
                adapter_key=adapter_key,
                enabled=request.enabled,
                read_only=read_only,
                config_json=config,
                health_status="unknown",
                health_message="",
                metadata_json=dict(request.metadata),
                created_by=request.created_by,
            )
            session.add(row)
            session.flush()
            result = self._provider_dump(row)
        await self.event_bus.publish("secret.provider.created", result)
        return result

    async def update_provider(
        self,
        provider_id: str,
        request: SecretProviderUpdate,
        *,
        expected_workspace_id: str | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(SecretProviderModel, provider_id)
            if row is None:
                raise SecretNotFound("Secret provider not found.")
            if not _workspace_allowed(row.workspace_id, expected_workspace_id):
                raise SecretNotFound("Secret provider not found for this Workspace.")
            if request.name is not None:
                row.name = request.name
            if request.enabled is not None:
                row.enabled = request.enabled
            if request.config is not None:
                row.config_json = validate_public_provider_config(dict(request.config))
            if request.metadata is not None:
                row.metadata_json = dict(request.metadata)
            result = self._provider_dump(row)
        await self.event_bus.publish(
            "secret.provider.updated",
            {**result, "actor_id": request.actor_id},
        )
        return result

    async def check_provider(
        self,
        provider_id: str,
        *,
        actor_id: str,
        expected_workspace_id: str | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(SecretProviderModel, provider_id)
            if row is None:
                raise SecretNotFound("Secret provider not found.")
            if not _workspace_allowed(row.workspace_id, expected_workspace_id):
                raise SecretNotFound("Secret provider not found for this Workspace.")
            try:
                adapter = self.providers.get(row.adapter_key)
                healthy, message = await asyncio.to_thread(
                    adapter.health, dict(row.config_json or {})
                )
                row.health_status = "healthy" if healthy else "unavailable"
                row.health_message = message
            except Exception as exc:
                row.health_status = "error"
                row.health_message = str(exc)
            row.last_checked_at = utc_now()
            result = self._provider_dump(row)
        await self.event_bus.publish(
            "secret.provider.health_checked",
            {**result, "actor_id": actor_id},
        )
        return result

    def list_providers(self, *, workspace_id: str | None = None) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(SecretProviderModel).order_by(SecretProviderModel.provider_key)
            if workspace_id is not None:
                statement = statement.where(
                    (SecretProviderModel.workspace_id == workspace_id)
                    | (SecretProviderModel.workspace_id.is_(None))
                )
            return [self._provider_dump(row) for row in session.scalars(statement).all()]

    async def create_secret(self, request: SecretCreate) -> dict[str, Any]:
        with self._session_factory() as session:
            provider = session.scalar(
                select(SecretProviderModel).where(
                    SecretProviderModel.provider_key == request.provider_key
                )
            )
            if provider is None:
                raise SecretNotFound("Secret provider not found.")
            if not provider.enabled:
                raise SecretConflict("Secret provider is disabled.")
            if provider.workspace_id and request.workspace_id != provider.workspace_id:
                raise SecretConflict("Secret provider belongs to another Workspace.")
            locator = _locator(provider.provider_key, request.workspace_id, request.secret_key)
            if session.scalar(
                select(SecretRecordModel).where(SecretRecordModel.locator_key == locator)
            ) is not None:
                raise SecretConflict("Secret reference already exists.")
            provider_ref = request.provider_ref or request.secret_key
            value = request.value.get_secret_value() if request.value is not None else None
            if provider.read_only and value is not None:
                raise SecretConflict("Read-only providers do not accept secret values.")
            if (
                not provider.read_only
                and value is None
                and provider.provider_type != "external"
            ):
                raise SecretConflict("Writable local secret providers require a value.")

            material = StoredSecretMaterial(
                encrypted_payload=None,
                provider_version="external",
                material_hash=material_hash(provider_ref),
            )
            if value is not None:
                adapter = self.providers.get(provider.adapter_key)
                material = await asyncio.to_thread(
                    adapter.store,
                    provider_ref=provider_ref,
                    value=value,
                    config=dict(provider.config_json or {}),
                )
            row = SecretRecordModel(
                provider_id=provider.id,
                workspace_id=request.workspace_id,
                secret_key=request.secret_key,
                locator_key=locator,
                provider_ref=provider_ref,
                display_name=request.display_name,
                description=request.description,
                status="active",
                current_version=1,
                material_hash=material.material_hash,
                last_rotated_at=utc_now() if value is not None else None,
                expires_at=request.expires_at,
                metadata_json=dict(request.metadata),
                created_by=request.created_by,
            )
            session.add(row)
            session.flush()
            version = SecretVersionModel(
                secret_id=row.id,
                version=1,
                status="current",
                provider_version=material.provider_version,
                encrypted_payload=material.encrypted_payload,
                material_hash=material.material_hash,
                metadata_json={"created": True},
                created_by=request.created_by,
            )
            session.add(version)
            result = self._secret_dump(row, provider_key=provider.provider_key)
            self._append_access_event(
                session,
                secret=row,
                provider=provider,
                actor_id=request.created_by,
                action="create",
                purpose="create secret reference",
                outcome="allowed",
                source="api",
                material_hash_value=material.material_hash,
            )
        await self.event_bus.publish("secret.created", result)
        return result

    async def rotate_secret(
        self,
        secret_id: str,
        request: SecretRotateRequest,
        *,
        expected_workspace_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._rotate_with_value(
            secret_id,
            request.value.get_secret_value(),
            actor_id=request.actor_id,
            reason=request.reason,
            metadata=dict(request.metadata),
            expected_workspace_id=expected_workspace_id,
            source="api",
        )

    async def _rotate_with_value(
        self,
        secret_id: str,
        value: str,
        *,
        actor_id: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
        expected_workspace_id: str | None = None,
        source: str = "runtime",
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            secret = session.get(SecretRecordModel, secret_id)
            if secret is None:
                raise SecretNotFound("Secret not found.")
            if not _workspace_allowed(secret.workspace_id, expected_workspace_id):
                raise SecretNotFound("Secret not found for this Workspace.")
            provider = session.get(SecretProviderModel, secret.provider_id)
            if provider is None:
                raise SecretNotFound("Secret provider not found.")
            if provider.read_only:
                raise SecretConflict("Read-only secrets must be rotated in their provider.")
            adapter_key = provider.adapter_key
            provider_ref = secret.provider_ref
            provider_config = dict(provider.config_json or {})
            old_version = int(secret.current_version)

        adapter = self.providers.get(adapter_key)
        material = await asyncio.to_thread(
            adapter.store,
            provider_ref=provider_ref,
            value=value,
            config=provider_config,
        )

        with self._session_factory() as session:
            secret = session.get(SecretRecordModel, secret_id)
            if secret is None:
                raise SecretNotFound("Secret not found after provider rotation.")
            provider = session.get(SecretProviderModel, secret.provider_id)
            if provider is None:
                raise SecretNotFound("Secret provider not found after rotation.")
            current = session.scalar(
                select(SecretVersionModel).where(
                    SecretVersionModel.secret_id == secret.id,
                    SecretVersionModel.status == "current",
                )
            )
            now = utc_now()
            if current is not None:
                current.status = "retired"
                current.retired_at = now
            next_version = max(old_version, int(secret.current_version)) + 1
            session.add(
                SecretVersionModel(
                    secret_id=secret.id,
                    version=next_version,
                    status="current",
                    provider_version=material.provider_version,
                    encrypted_payload=material.encrypted_payload,
                    material_hash=material.material_hash,
                    metadata_json={"reason": reason, **dict(metadata or {})},
                    created_by=actor_id,
                )
            )
            secret.current_version = next_version
            secret.material_hash = material.material_hash
            secret.last_rotated_at = now
            self._append_access_event(
                session,
                secret=secret,
                provider=provider,
                actor_id=actor_id,
                action="rotate",
                purpose=reason,
                outcome="allowed",
                source=source,
                material_hash_value=material.material_hash,
                metadata=metadata,
            )
            result = self._secret_dump(secret, provider_key=provider.provider_key)
        await self.event_bus.publish(
            "secret.rotated",
            {**result, "actor_id": actor_id, "reason": reason, "source": source},
        )
        return result

    async def set_secret_state(
        self,
        secret_id: str,
        *,
        enabled: bool,
        request: SecretStateRequest,
        expected_workspace_id: str | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            secret = session.get(SecretRecordModel, secret_id)
            if secret is None:
                raise SecretNotFound("Secret not found.")
            if not _workspace_allowed(secret.workspace_id, expected_workspace_id):
                raise SecretNotFound("Secret not found for this Workspace.")
            provider = session.get(SecretProviderModel, secret.provider_id)
            secret.status = "active" if enabled else "disabled"
            self._append_access_event(
                session,
                secret=secret,
                provider=provider,
                actor_id=request.actor_id,
                action="enable" if enabled else "disable",
                purpose=request.reason,
                outcome="allowed",
                source="api",
                material_hash_value=secret.material_hash,
            )
            result = self._secret_dump(
                secret,
                provider_key=provider.provider_key if provider else "unknown",
            )
        await self.event_bus.publish(
            "secret.enabled" if enabled else "secret.disabled",
            {**result, "actor_id": request.actor_id, "reason": request.reason},
        )
        return result

    def list_secrets(
        self,
        *,
        workspace_id: str | None = None,
        provider_key: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = (
                select(SecretRecordModel, SecretProviderModel.provider_key)
                .join(SecretProviderModel, SecretProviderModel.id == SecretRecordModel.provider_id)
                .order_by(SecretRecordModel.created_at.desc())
            )
            if workspace_id is not None:
                statement = statement.where(
                    (SecretRecordModel.workspace_id == workspace_id)
                    | (SecretRecordModel.workspace_id.is_(None))
                )
            if provider_key:
                statement = statement.where(SecretProviderModel.provider_key == provider_key)
            if status:
                statement = statement.where(SecretRecordModel.status == status)
            return [
                self._secret_dump(row, provider_key=key)
                for row, key in session.execute(statement).all()
            ]

    def get_secret(
        self, secret_id: str, *, expected_workspace_id: str | None = None
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(SecretRecordModel, secret_id)
            if row is None:
                raise SecretNotFound("Secret not found.")
            if not _workspace_allowed(row.workspace_id, expected_workspace_id):
                raise SecretNotFound("Secret not found for this Workspace.")
            provider = session.get(SecretProviderModel, row.provider_id)
            return self._secret_dump(
                row, provider_key=provider.provider_key if provider else "unknown"
            )

    def list_versions(
        self, secret_id: str, *, expected_workspace_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            secret = session.get(SecretRecordModel, secret_id)
            if secret is None:
                raise SecretNotFound("Secret not found.")
            if not _workspace_allowed(secret.workspace_id, expected_workspace_id):
                raise SecretNotFound("Secret not found for this Workspace.")
            rows = session.scalars(
                select(SecretVersionModel)
                .where(SecretVersionModel.secret_id == secret_id)
                .order_by(SecretVersionModel.version.desc())
            ).all()
            return [
                {
                    "id": row.id,
                    "secret_id": row.secret_id,
                    "version": row.version,
                    "status": row.status,
                    "provider_version": row.provider_version,
                    "material_hash": row.material_hash,
                    "created_by": row.created_by,
                    "created_at": _iso(row.created_at),
                    "retired_at": _iso(row.retired_at),
                    "metadata": row.metadata_json or {},
                    "encrypted_payload_present": bool(row.encrypted_payload),
                }
                for row in rows
            ]

    def seed_access_defaults(self) -> int:
        with self._session_factory() as session:
            row = session.scalar(
                select(SecretAccessSettingsModel).where(
                    SecretAccessSettingsModel.scope_key == "global"
                )
            )
            if row is not None:
                return 0
            session.add(
                SecretAccessSettingsModel(
                    scope_key="global",
                    workspace_id=None,
                    enabled=True,
                    enforcement_mode="legacy",
                    default_effect="allow",
                    default_lease_seconds=60,
                    max_lease_seconds=900,
                    max_lease_uses=10,
                    metadata_json={"builtin": True},
                    created_by="system",
                    updated_by="system",
                )
            )
        return 1

    def get_access_settings(self, *, workspace_id: str | None = None) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._effective_settings_row(session, workspace_id)
            if row is None:
                return self._default_settings_dump(workspace_id)
            return self._settings_dump(row)

    async def upsert_access_settings(
        self,
        request: SecretAccessSettingsUpsert,
    ) -> dict[str, Any]:
        if request.default_lease_seconds > request.max_lease_seconds:
            raise SecretConflict(
                "default_lease_seconds must not exceed max_lease_seconds."
            )
        scope_key = (
            f"workspace:{request.workspace_id}"
            if request.workspace_id is not None
            else "global"
        )
        with self._session_factory() as session:
            row = session.scalar(
                select(SecretAccessSettingsModel).where(
                    SecretAccessSettingsModel.scope_key == scope_key
                )
            )
            if row is None:
                row = SecretAccessSettingsModel(
                    scope_key=scope_key,
                    workspace_id=request.workspace_id,
                    created_by=request.actor_id,
                    updated_by=request.actor_id,
                )
                session.add(row)
            row.enabled = request.enabled
            row.enforcement_mode = request.enforcement_mode.value
            row.default_effect = request.default_effect.value
            row.default_lease_seconds = request.default_lease_seconds
            row.max_lease_seconds = request.max_lease_seconds
            row.max_lease_uses = request.max_lease_uses
            row.metadata_json = dict(request.metadata)
            row.updated_by = request.actor_id
            session.flush()
            result = self._settings_dump(row)
        await self.event_bus.publish(
            "secret.access.settings.updated",
            {**result, "actor_id": request.actor_id},
        )
        return result

    async def create_access_policy(
        self,
        request: SecretAccessPolicyCreate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            existing = session.scalar(
                select(SecretAccessPolicyModel).where(
                    SecretAccessPolicyModel.policy_key == request.policy_key
                )
            )
            if existing is not None:
                raise SecretConflict("Secret access policy key already exists.")
            row = SecretAccessPolicyModel(
                policy_key=request.policy_key,
                workspace_id=request.workspace_id,
                name=request.name,
                description=request.description,
                enabled=request.enabled,
                priority=request.priority,
                effect=request.effect.value,
                actions_json=self._normalize_patterns(request.actions, default=["resolve", "lease"]),
                secret_patterns_json=self._normalize_patterns(request.secret_patterns, default=["*"]),
                consumer_types_json=self._normalize_patterns(request.consumer_types),
                consumer_keys_json=self._normalize_patterns(request.consumer_keys),
                actor_patterns_json=self._normalize_patterns(request.actor_patterns),
                source_patterns_json=self._normalize_patterns(request.source_patterns),
                purpose_patterns_json=self._normalize_patterns(request.purpose_patterns),
                max_lease_seconds=request.max_lease_seconds,
                max_uses=request.max_uses,
                require_workspace_match=request.require_workspace_match,
                metadata_json=dict(request.metadata),
                created_by=request.created_by,
            )
            session.add(row)
            session.flush()
            result = self._policy_dump(row)
        await self.event_bus.publish("secret.access.policy.created", result)
        return result

    async def update_access_policy(
        self,
        policy_id: str,
        request: SecretAccessPolicyUpdate,
        *,
        expected_workspace_id: str | None = None,
    ) -> dict[str, Any]:
        values = request.model_dump(exclude_unset=True)
        with self._session_factory() as session:
            row = session.get(SecretAccessPolicyModel, policy_id)
            if row is None or not _workspace_allowed(row.workspace_id, expected_workspace_id):
                raise SecretNotFound("Secret access policy not found.")
            simple_fields = {
                "name": "name",
                "description": "description",
                "enabled": "enabled",
                "priority": "priority",
                "max_lease_seconds": "max_lease_seconds",
                "max_uses": "max_uses",
                "require_workspace_match": "require_workspace_match",
            }
            for source, target in simple_fields.items():
                if source in values:
                    setattr(row, target, values[source])
            if "effect" in values:
                effect = values["effect"]
                row.effect = effect.value if hasattr(effect, "value") else str(effect)
            list_fields = {
                "actions": "actions_json",
                "secret_patterns": "secret_patterns_json",
                "consumer_types": "consumer_types_json",
                "consumer_keys": "consumer_keys_json",
                "actor_patterns": "actor_patterns_json",
                "source_patterns": "source_patterns_json",
                "purpose_patterns": "purpose_patterns_json",
            }
            for source, target in list_fields.items():
                if source in values:
                    default = ["*"] if source == "secret_patterns" else []
                    if source == "actions":
                        default = ["resolve", "lease"]
                    setattr(row, target, self._normalize_patterns(values[source], default=default))
            if "metadata" in values:
                row.metadata_json = dict(values["metadata"] or {})
            session.flush()
            result = self._policy_dump(row)
        await self.event_bus.publish(
            "secret.access.policy.updated",
            {**result, "actor_id": request.actor_id},
        )
        return result

    def list_access_policies(
        self,
        *,
        workspace_id: str | None = None,
        include_global: bool = True,
        enabled: bool | None = None,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(SecretAccessPolicyModel)
            if workspace_id is not None:
                if include_global:
                    statement = statement.where(
                        or_(
                            SecretAccessPolicyModel.workspace_id == workspace_id,
                            SecretAccessPolicyModel.workspace_id.is_(None),
                        )
                    )
                else:
                    statement = statement.where(
                        SecretAccessPolicyModel.workspace_id == workspace_id
                    )
            elif not include_global:
                statement = statement.where(
                    SecretAccessPolicyModel.workspace_id.is_not(None)
                )
            if enabled is not None:
                statement = statement.where(
                    SecretAccessPolicyModel.enabled == enabled
                )
            statement = statement.order_by(
                SecretAccessPolicyModel.priority.desc(),
                SecretAccessPolicyModel.created_at.asc(),
            )
            return [self._policy_dump(row) for row in session.scalars(statement).all()]

    async def evaluate_access(
        self,
        secret_id: str,
        request: SecretAccessEvaluationRequest,
        *,
        expected_workspace_id: str | None = None,
    ) -> dict[str, Any]:
        context = SecretResolveContext(
            actor_id=request.actor_id,
            purpose=request.purpose,
            workspace_id=request.workspace_id,
            auth_method=request.auth_method,
            source=request.source,
            consumer_type=request.consumer_type,
            consumer_key=request.consumer_key,
            metadata=request.metadata,
        )
        with self._session_factory() as session:
            secret = session.get(SecretRecordModel, secret_id)
            if secret is None or not _workspace_allowed(secret.workspace_id, expected_workspace_id):
                raise SecretNotFound("Secret not found.")
            provider = session.get(SecretProviderModel, secret.provider_id)
            if provider is None:
                raise SecretNotFound("Secret provider not found.")
            decision = self._evaluate_access_rows(
                session,
                secret=secret,
                provider=provider,
                context=context,
                action=request.action,
            )
        await self.event_bus.publish(
            "secret.access.evaluated",
            {
                "secret_id": secret_id,
                "workspace_id": request.workspace_id,
                "actor_id": request.actor_id,
                "consumer_type": request.consumer_type,
                "consumer_key": request.consumer_key,
                "action": request.action,
                "allowed": decision["allowed"],
                "policy_effect": decision["policy_effect"],
                "enforcement_mode": decision["enforcement_mode"],
            },
        )
        return decision

    async def issue_lease(self, request: SecretLeaseCreate) -> dict[str, Any]:
        with self._session_factory() as session:
            provider, secret = self._resolve_reference_row(
                session,
                request.reference,
                request.workspace_id,
            )
            context = SecretResolveContext(
                actor_id=request.actor_id,
                purpose=request.purpose,
                workspace_id=request.workspace_id,
                auth_method=request.auth_method,
                source=request.source,
                consumer_type=request.consumer_type,
                consumer_key=request.consumer_key,
                correlation_id=request.correlation_id,
                metadata=request.metadata,
            )
            decision = self._evaluate_access_rows(
                session,
                secret=secret,
                provider=provider,
                context=context,
                action="lease",
            )
            if not decision["allowed"]:
                self._policy_denied_count += 1
                self._append_access_event(
                    session,
                    secret=secret,
                    provider=provider,
                    actor_id=request.actor_id,
                    action="lease",
                    purpose=request.purpose,
                    outcome="denied",
                    auth_method=request.auth_method,
                    source=request.source,
                    material_hash_value=secret.material_hash,
                    error="Secret access policy denied lease issuance.",
                    metadata={"decision": decision, **dict(request.metadata)},
                )
                raise SecretConflict("Secret access policy denied lease issuance.")

            settings = decision["settings"]
            requested_ttl = request.ttl_seconds or int(settings["default_lease_seconds"])
            ttl_limit = int(settings["max_lease_seconds"])
            if decision.get("max_lease_seconds") is not None:
                ttl_limit = min(ttl_limit, int(decision["max_lease_seconds"]))
            ttl_seconds = max(1, min(int(requested_ttl), ttl_limit))

            uses_limit = int(settings["max_lease_uses"])
            if decision.get("max_uses") is not None:
                uses_limit = min(uses_limit, int(decision["max_uses"]))
            max_uses = max(1, min(int(request.max_uses), uses_limit))

            token = f"sec_lease_{pysecrets.token_urlsafe(32)}"
            now = utc_now()
            row = SecretLeaseModel(
                token_hash=self._lease_token_hash(token),
                secret_id=secret.id,
                provider_id=provider.id,
                workspace_id=request.workspace_id,
                actor_id=request.actor_id,
                consumer_type=request.consumer_type,
                consumer_key=request.consumer_key,
                purpose=request.purpose,
                source=request.source,
                status="active",
                max_uses=max_uses,
                use_count=0,
                issued_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
                created_by=request.created_by,
                metadata_json={
                    "correlation_id": request.correlation_id,
                    "policy_ids": decision["matched_policy_ids"],
                    **dict(request.metadata),
                },
            )
            session.add(row)
            session.flush()
            result = self._lease_dump(row)
            result["lease_token"] = token
            result["lease_token_returned_once"] = True
            self._append_access_event(
                session,
                secret=secret,
                provider=provider,
                actor_id=request.actor_id,
                action="lease",
                purpose=request.purpose,
                outcome="allowed",
                auth_method=request.auth_method,
                source=request.source,
                material_hash_value=secret.material_hash,
                metadata={
                    "lease_id": row.id,
                    "ttl_seconds": ttl_seconds,
                    "max_uses": max_uses,
                    "consumer_type": request.consumer_type,
                    "consumer_key": request.consumer_key,
                },
            )
        self._lease_issued_count += 1
        await self.event_bus.publish(
            "secret.lease.issued",
            {key: value for key, value in result.items() if key != "lease_token"},
        )
        return result

    async def resolve_lease(
        self,
        lease_token: str,
        context: SecretResolveContext,
    ) -> str:
        token_hash = self._lease_token_hash(lease_token)
        now = utc_now()
        with self._session_factory() as session:
            lease = session.scalar(
                select(SecretLeaseModel).where(SecretLeaseModel.token_hash == token_hash)
            )
            if lease is None:
                raise SecretNotFound("Secret lease not found.")
            expires_at = self._aware(lease.expires_at)
            if lease.status != "active":
                raise SecretConflict(f"Secret lease is {lease.status}.")
            if expires_at <= now:
                lease.status = "expired"
                # The status update is committed because the error is raised only
                # after leaving this session scope.
                expired_lease = lease.id
            else:
                expired_lease = None
            if expired_lease is None:
                if lease.actor_id != context.actor_id:
                    raise SecretConflict("Secret lease actor mismatch.")
                if lease.consumer_type != context.consumer_type:
                    raise SecretConflict("Secret lease consumer type mismatch.")
                if lease.consumer_key != context.consumer_key:
                    raise SecretConflict("Secret lease consumer key mismatch.")
                if lease.workspace_id != context.workspace_id:
                    raise SecretConflict("Secret lease Workspace mismatch.")
                secret = session.get(SecretRecordModel, lease.secret_id)
                provider = session.get(SecretProviderModel, lease.provider_id)
                if secret is None or provider is None:
                    raise SecretNotFound("Secret lease target no longer exists.")
                decision = self._evaluate_access_rows(
                    session,
                    secret=secret,
                    provider=provider,
                    context=context,
                    action="lease",
                )
                if not decision["allowed"]:
                    self._policy_denied_count += 1
                    raise SecretConflict("Secret access policy denied lease use.")
                lease_id = lease.id
                secret_id = secret.id

                # Claim a lease use atomically before material resolution. This
                # makes one-use leases safe against concurrent consumers and
                # intentionally consumes a use even if the downstream provider
                # fails after authorization.
                claimed = session.execute(
                    update(SecretLeaseModel)
                    .where(
                        SecretLeaseModel.id == lease_id,
                        SecretLeaseModel.status == "active",
                        SecretLeaseModel.use_count < SecretLeaseModel.max_uses,
                    )
                    .values(
                        use_count=SecretLeaseModel.use_count + 1,
                        last_used_at=now,
                    )
                )
                if claimed.rowcount != 1:
                    raise SecretConflict("Secret lease has no remaining uses.")
                session.flush()
                refreshed = session.get(SecretLeaseModel, lease_id)
                if refreshed is not None and refreshed.use_count >= refreshed.max_uses:
                    refreshed.status = "consumed"
        if expired_lease is not None:
            raise SecretConflict("Secret lease has expired.")

        lease_context = context.model_copy(update={"lease_id": lease_id})
        value = await self._resolve_secret_id(
            secret_id,
            lease_context,
            enforce_policy=False,
            access_action="lease_resolve",
        )

        self._lease_resolved_count += 1
        await self.event_bus.publish(
            "secret.lease.used",
            {
                "lease_id": lease_id,
                "secret_id": secret_id,
                "actor_id": context.actor_id,
                "workspace_id": context.workspace_id,
                "consumer_type": context.consumer_type,
                "consumer_key": context.consumer_key,
            },
        )
        return value

    async def revoke_lease(
        self,
        lease_id: str,
        request: SecretLeaseRevokeRequest,
        *,
        expected_workspace_id: str | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(SecretLeaseModel, lease_id)
            if row is None or not _workspace_allowed(row.workspace_id, expected_workspace_id):
                raise SecretNotFound("Secret lease not found.")
            if row.status == "active":
                row.status = "revoked"
                row.revoked_at = utc_now()
            metadata = dict(row.metadata_json or {})
            metadata["revoke_reason"] = request.reason
            metadata["revoked_by"] = request.actor_id
            row.metadata_json = metadata
            session.flush()
            result = self._lease_dump(row)
        await self.event_bus.publish(
            "secret.lease.revoked",
            {**result, "actor_id": request.actor_id, "reason": request.reason},
        )
        return result

    def list_leases(
        self,
        *,
        workspace_id: str | None = None,
        secret_id: str | None = None,
        actor_id: str | None = None,
        consumer_type: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(SecretLeaseModel).order_by(SecretLeaseModel.issued_at.desc())
            if workspace_id is not None:
                statement = statement.where(SecretLeaseModel.workspace_id == workspace_id)
            if secret_id:
                statement = statement.where(SecretLeaseModel.secret_id == secret_id)
            if actor_id:
                statement = statement.where(SecretLeaseModel.actor_id == actor_id)
            if consumer_type:
                statement = statement.where(SecretLeaseModel.consumer_type == consumer_type)
            if status:
                statement = statement.where(SecretLeaseModel.status == status)
            statement = statement.limit(max(1, min(1000, int(limit))))
            return [self._lease_dump(row) for row in session.scalars(statement).all()]

    async def reconcile_leases(self) -> dict[str, int]:
        expired = 0
        now = utc_now()
        with self._session_factory() as session:
            rows = session.scalars(
                select(SecretLeaseModel).where(SecretLeaseModel.status == "active")
            ).all()
            for row in rows:
                if self._aware(row.expires_at) <= now:
                    row.status = "expired"
                    expired += 1
        if expired:
            await self.event_bus.publish(
                "secret.lease.reconciled",
                {"expired": expired},
            )
        return {"expired": expired}

    async def verify_secret(
        self,
        secret_id: str,
        *,
        actor_id: str,
        purpose: str,
        auth_method: str = "operator",
        source: str = "api",
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            await self._resolve_secret_id(
                secret_id,
                SecretResolveContext(
                    actor_id=actor_id,
                    purpose=purpose,
                    workspace_id=workspace_id,
                    auth_method=auth_method,
                    source=source,
                ),
            )
            result = {
                "secret_id": secret_id,
                "available": True,
                "actor_id": actor_id,
                "workspace_id": workspace_id,
            }
        except (SecretManagerError, SecretProviderError) as exc:
            result = {
                "secret_id": secret_id,
                "available": False,
                "actor_id": actor_id,
                "workspace_id": workspace_id,
                "error": str(exc),
            }
        await self.event_bus.publish("secret.verified", result)
        return result

    async def resolve_ref(self, reference: str, context: SecretResolveContext) -> str:
        with self._session_factory() as session:
            _provider, secret = self._resolve_reference_row(
                session, reference, context.workspace_id
            )
            secret_id = secret.id
        return await self._resolve_secret_id(secret_id, context)

    async def _resolve_secret_id(
        self,
        secret_id: str,
        context: SecretResolveContext,
        *,
        enforce_policy: bool = True,
        access_action: str = "resolve",
    ) -> str:
        with self._session_factory() as session:
            secret = session.get(SecretRecordModel, secret_id)
            if secret is None:
                raise SecretNotFound("Secret not found.")
            provider = session.get(SecretProviderModel, secret.provider_id)
            if provider is None:
                raise SecretNotFound("Secret provider not found.")
            if enforce_policy:
                decision = self._evaluate_access_rows(
                    session,
                    secret=secret,
                    provider=provider,
                    context=context,
                    action=access_action,
                )
                if not decision["allowed"]:
                    self._append_access_event(
                        session, secret=secret, provider=provider,
                        actor_id=context.actor_id, action=access_action, purpose=context.purpose,
                        outcome="denied", auth_method=context.auth_method, source=context.source,
                        material_hash_value=secret.material_hash,
                        error="Secret access policy denied the request.",
                        metadata={"decision": decision, **dict(context.metadata)},
                    )
                    self._resolution_failures += 1
                    self._policy_denied_count += 1
                    raise SecretConflict("Secret access policy denied the request.")
            if secret.status != "active" or not provider.enabled:
                self._append_access_event(
                    session, secret=secret, provider=provider,
                    actor_id=context.actor_id, action=access_action, purpose=context.purpose,
                    outcome="denied", auth_method=context.auth_method, source=context.source,
                    material_hash_value=secret.material_hash, error="Secret or provider is disabled.",
                    metadata=context.metadata,
                )
                self._resolution_failures += 1
                raise SecretConflict("Secret or provider is disabled.")
            now = utc_now()
            expires_at = secret.expires_at
            if expires_at is not None:
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at <= now:
                    self._append_access_event(
                        session, secret=secret, provider=provider,
                        actor_id=context.actor_id, action=access_action, purpose=context.purpose,
                        outcome="denied", auth_method=context.auth_method, source=context.source,
                        material_hash_value=secret.material_hash, error="Secret has expired.",
                        metadata=context.metadata,
                    )
                    self._resolution_failures += 1
                    raise SecretConflict("Secret has expired.")
            if (
                context.workspace_id is not None
                and provider.workspace_id
                and context.workspace_id != provider.workspace_id
            ):
                self._resolution_failures += 1
                raise SecretConflict("Secret provider belongs to another Workspace.")
            if (
                context.workspace_id is not None
                and secret.workspace_id
                and context.workspace_id != secret.workspace_id
            ):
                self._resolution_failures += 1
                raise SecretConflict("Secret belongs to another Workspace.")
            version = session.scalar(
                select(SecretVersionModel).where(
                    SecretVersionModel.secret_id == secret.id,
                    SecretVersionModel.status == "current",
                )
            )
            if version is None:
                self._resolution_failures += 1
                raise SecretConflict("Secret has no current version.")
            try:
                adapter = self.providers.get(provider.adapter_key)
                value = await asyncio.to_thread(
                    adapter.resolve,
                    provider_ref=secret.provider_ref,
                    encrypted_payload=version.encrypted_payload,
                    provider_version=version.provider_version,
                    config=dict(provider.config_json or {}),
                )
            except Exception as exc:
                self._append_access_event(
                    session, secret=secret, provider=provider,
                    actor_id=context.actor_id, action=access_action, purpose=context.purpose,
                    outcome="error", auth_method=context.auth_method, source=context.source,
                    material_hash_value=secret.material_hash, error=str(exc),
                    metadata=context.metadata,
                )
                self._resolution_failures += 1
                raise
            self._append_access_event(
                session, secret=secret, provider=provider,
                actor_id=context.actor_id, action=access_action, purpose=context.purpose,
                outcome="allowed", auth_method=context.auth_method, source=context.source,
                material_hash_value=secret.material_hash, metadata=context.metadata,
            )
            self._resolution_count += 1
            return value

    async def start_lifecycle_monitor(self) -> None:
        if self._monitor_task is not None and not self._monitor_task.done():
            return
        self._monitor_task = asyncio.create_task(
            self._lifecycle_monitor_loop(), name="secret-lifecycle-monitor"
        )

    async def shutdown_lifecycle_monitor(self) -> None:
        task = self._monitor_task
        self._monitor_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _lifecycle_monitor_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._monitor_interval_seconds)
                try:
                    await self.scan_lifecycle(
                        SecretLifecycleScanRequest(actor_id="system")
                    )
                except Exception as exc:
                    await self.event_bus.publish(
                        "secret.lifecycle.scan_failed", {"error": str(exc)}
                    )
        except asyncio.CancelledError:
            raise

    def get_rotation_policy(
        self,
        secret_id: str,
        *,
        expected_workspace_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            secret = session.get(SecretRecordModel, secret_id)
            if secret is None or not _workspace_allowed(secret.workspace_id, expected_workspace_id):
                raise SecretNotFound("Secret not found.")
            row = session.scalar(
                select(SecretRotationPolicyModel).where(
                    SecretRotationPolicyModel.secret_id == secret_id
                )
            )
            return self._rotation_policy_dump(row) if row is not None else None

    async def upsert_rotation_policy(
        self,
        secret_id: str,
        request: SecretRotationPolicyUpsert,
        *,
        expected_workspace_id: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._session_factory() as session:
            secret = session.get(SecretRecordModel, secret_id)
            if secret is None or not _workspace_allowed(secret.workspace_id, expected_workspace_id):
                raise SecretNotFound("Secret not found.")
            provider = session.get(SecretProviderModel, secret.provider_id)
            if provider is None:
                raise SecretNotFound("Secret provider not found.")
            if request.rotation_mode.value == "managed_random" and provider.read_only:
                raise SecretConflict("Managed random rotation requires a writable provider.")
            if request.rotation_mode.value == "external_handler" and not request.handler_key:
                raise SecretConflict("External handler rotation requires handler_key.")
            row = session.scalar(
                select(SecretRotationPolicyModel).where(
                    SecretRotationPolicyModel.secret_id == secret_id
                )
            )
            anchor = secret.last_rotated_at or secret.created_at or now
            anchor = self._aware(anchor)
            next_rotation = anchor + timedelta(seconds=request.interval_seconds)
            if row is None:
                row = SecretRotationPolicyModel(
                    secret_id=secret.id,
                    workspace_id=secret.workspace_id,
                    created_by=request.actor_id,
                    updated_by=request.actor_id,
                )
                session.add(row)
            row.enabled = request.enabled
            row.rotation_mode = request.rotation_mode.value
            row.interval_seconds = request.interval_seconds
            row.warning_seconds = request.warning_seconds
            row.auto_rotate_enabled = request.auto_rotate_enabled
            row.require_human_approval = request.require_human_approval
            row.handler_key = request.handler_key
            row.random_bytes = request.random_bytes
            row.max_failures = request.max_failures
            row.next_rotation_at = next_rotation
            row.metadata_json = dict(request.metadata)
            row.updated_by = request.actor_id
            session.flush()
            result = self._rotation_policy_dump(row)
        await self.event_bus.publish(
            "secret.rotation.policy.updated",
            {**result, "actor_id": request.actor_id},
        )
        return result

    async def create_rotation_run(
        self,
        secret_id: str,
        request: SecretRotationRunCreate,
        *,
        expected_workspace_id: str | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            secret = session.get(SecretRecordModel, secret_id)
            if secret is None or not _workspace_allowed(secret.workspace_id, expected_workspace_id):
                raise SecretNotFound("Secret not found.")
            policy = session.scalar(
                select(SecretRotationPolicyModel).where(
                    SecretRotationPolicyModel.secret_id == secret_id
                )
            )
            if policy is None:
                raise SecretConflict("Secret has no rotation policy.")
            if request.idempotency_key:
                existing = session.scalar(
                    select(SecretRotationRunModel).where(
                        SecretRotationRunModel.idempotency_key == request.idempotency_key
                    )
                )
                if existing is not None:
                    return self._rotation_run_dump(existing)
            status = "proposed"
            if request.execute_now and (request.force or not policy.require_human_approval):
                status = "running"
            row = SecretRotationRunModel(
                secret_id=secret.id,
                policy_id=policy.id,
                workspace_id=secret.workspace_id,
                status=status,
                trigger_type=request.trigger_type,
                rotation_mode=policy.rotation_mode,
                handler_key=policy.handler_key,
                requested_by=request.actor_id,
                approved_by=request.actor_id if request.force else "",
                reason=request.reason,
                idempotency_key=request.idempotency_key,
                old_version=int(secret.current_version),
                new_version=0,
                started_at=utc_now() if status == "running" else None,
                metadata_json={"force": request.force, **dict(request.metadata)},
            )
            session.add(row)
            session.flush()
            run_id = row.id
            result = self._rotation_run_dump(row)
        await self.event_bus.publish("secret.rotation.run.created", result)
        if result["status"] == "running":
            return await self._execute_rotation_run(run_id)
        return result

    async def decide_rotation_run(
        self,
        run_id: str,
        request: SecretRotationRunDecision,
        *,
        expected_workspace_id: str | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(SecretRotationRunModel, run_id)
            if row is None or not _workspace_allowed(row.workspace_id, expected_workspace_id):
                raise SecretNotFound("Secret rotation run not found.")
            if row.status != "proposed":
                return self._rotation_run_dump(row)
            if not request.approve:
                row.status = "rejected"
                row.approved_by = request.actor_id
                row.reason = f"{row.reason}\nRejected: {request.reason}".strip()
                row.finished_at = utc_now()
                result = self._rotation_run_dump(row)
                execute = False
            else:
                row.status = "running"
                row.approved_by = request.actor_id
                row.started_at = utc_now()
                metadata = dict(row.metadata_json or {})
                metadata.update({"approval_reason": request.reason, "force": request.force})
                row.metadata_json = metadata
                result = self._rotation_run_dump(row)
                execute = True
        await self.event_bus.publish(
            "secret.rotation.run.approved" if execute else "secret.rotation.run.rejected",
            result,
        )
        if execute:
            return await self._execute_rotation_run(run_id)
        return result

    async def _execute_rotation_run(self, run_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(SecretRotationRunModel, run_id)
            if row is None:
                raise SecretNotFound("Secret rotation run not found.")
            if row.status == "completed":
                return self._rotation_run_dump(row)
            if row.status not in {"running", "proposed"}:
                raise SecretConflict(f"Secret rotation run is {row.status}.")
            policy = session.get(SecretRotationPolicyModel, row.policy_id) if row.policy_id else None
            secret = session.get(SecretRecordModel, row.secret_id)
            if policy is None or secret is None:
                raise SecretConflict("Secret rotation run lost its policy or secret.")
            row.status = "running"
            row.started_at = row.started_at or utc_now()
            mode = row.rotation_mode
            handler_key = row.handler_key
            secret_id = row.secret_id
            workspace_id = row.workspace_id
            random_bytes = int(policy.random_bytes)
            reason = row.reason
            requested_by = row.requested_by

        try:
            if mode == "managed_random":
                value = pysecrets.token_urlsafe(random_bytes)
            elif mode == "external_handler":
                handler = self._rotation_handlers.get(handler_key)
                if handler is None:
                    raise SecretConflict(
                        f"Rotation handler {handler_key!r} is not registered."
                    )
                result = handler(
                    {
                        "run_id": run_id,
                        "secret_id": secret_id,
                        "workspace_id": workspace_id,
                        "reason": reason,
                    }
                )
                value = await result if inspect.isawaitable(result) else result
                if not isinstance(value, str) or not value:
                    raise SecretConflict("Rotation handler returned no secret material.")
            else:
                raise SecretConflict(
                    "Monitor-only rotation policy cannot generate secret material."
                )
            rotated = await self._rotate_with_value(
                secret_id,
                value,
                actor_id=requested_by,
                reason=f"Lifecycle rotation: {reason}",
                metadata={"rotation_run_id": run_id, "rotation_mode": mode},
                expected_workspace_id=workspace_id,
                source="secret-lifecycle",
            )
            now = utc_now()
            with self._session_factory() as session:
                row = session.get(SecretRotationRunModel, run_id)
                policy = session.get(SecretRotationPolicyModel, row.policy_id) if row and row.policy_id else None
                if row is None:
                    raise SecretNotFound("Secret rotation run not found after execution.")
                row.status = "completed"
                row.new_version = int(rotated["current_version"])
                row.finished_at = now
                row.error = ""
                if policy is not None:
                    policy.failure_count = 0
                    policy.last_success_at = now
                    policy.next_rotation_at = now + timedelta(seconds=policy.interval_seconds)
                result = self._rotation_run_dump(row)
            self._auto_rotation_count += 1
            await self.event_bus.publish("secret.rotation.completed", result)
            return result
        except Exception as exc:
            now = utc_now()
            with self._session_factory() as session:
                row = session.get(SecretRotationRunModel, run_id)
                policy = session.get(SecretRotationPolicyModel, row.policy_id) if row and row.policy_id else None
                if row is not None:
                    row.status = "failed"
                    row.error = str(exc)[:10000]
                    row.finished_at = now
                    result = self._rotation_run_dump(row)
                else:
                    result = {"id": run_id, "status": "failed", "error": str(exc)}
                if policy is not None:
                    policy.failure_count += 1
                    policy.last_failure_at = now
            await self.event_bus.publish("secret.rotation.failed", result)
            return result

    def list_rotation_runs(
        self,
        *,
        secret_id: str | None = None,
        workspace_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(SecretRotationRunModel).order_by(
                SecretRotationRunModel.created_at.desc()
            )
            if secret_id:
                statement = statement.where(SecretRotationRunModel.secret_id == secret_id)
            if workspace_id is not None:
                statement = statement.where(SecretRotationRunModel.workspace_id == workspace_id)
            if status:
                statement = statement.where(SecretRotationRunModel.status == status)
            statement = statement.limit(max(1, min(1000, int(limit))))
            return [self._rotation_run_dump(row) for row in session.scalars(statement).all()]

    async def scan_lifecycle(self, request: SecretLifecycleScanRequest) -> dict[str, Any]:
        provider_checked = 0
        provider_errors = 0
        if request.check_providers:
            providers = self.list_providers(workspace_id=request.workspace_id)
            for provider in providers:
                if not provider["enabled"]:
                    continue
                try:
                    result = await self.check_provider(
                        provider["id"],
                        actor_id=request.actor_id,
                        expected_workspace_id=request.workspace_id,
                    )
                    provider_checked += 1
                    if result["health_status"] != "healthy":
                        provider_errors += 1
                except Exception:
                    provider_checked += 1
                    provider_errors += 1

        now = utc_now()
        error_since = now - timedelta(seconds=request.recent_error_window_seconds)
        auto_run_ids: list[str] = []
        health_checks = 0
        open_alerts = 0
        with self._session_factory() as session:
            statement = select(SecretRecordModel).where(SecretRecordModel.status == "active")
            if request.workspace_id is not None:
                statement = statement.where(
                    (SecretRecordModel.workspace_id == request.workspace_id)
                    | (SecretRecordModel.workspace_id.is_(None))
                )
            secrets = session.scalars(statement).all()
            for secret in secrets:
                provider = session.get(SecretProviderModel, secret.provider_id)
                if provider is None:
                    continue
                policy = session.scalar(
                    select(SecretRotationPolicyModel).where(
                        SecretRotationPolicyModel.secret_id == secret.id
                    )
                )
                expires_in: int | None = None
                if secret.expires_at is not None:
                    expires_in = int((self._aware(secret.expires_at) - now).total_seconds())
                due_in: int | None = None
                rotation_due = False
                if policy is not None and policy.enabled and policy.next_rotation_at is not None:
                    due_in = int((self._aware(policy.next_rotation_at) - now).total_seconds())
                    rotation_due = due_in <= 0
                recent_errors = int(
                    session.scalar(
                        select(func.count())
                        .select_from(SecretAccessEventModel)
                        .where(
                            SecretAccessEventModel.secret_id == secret.id,
                            SecretAccessEventModel.outcome == "error",
                            SecretAccessEventModel.created_at >= error_since,
                        )
                    ) or 0
                )
                status = "healthy"
                messages: list[str] = []
                alert_key = ""
                severity = "warning"
                if provider.health_status in {"unavailable", "error"}:
                    status = "critical"
                    alert_key = "provider_unhealthy"
                    severity = "critical"
                    messages.append(f"Provider is {provider.health_status}: {provider.health_message}")
                if expires_in is not None and expires_in <= 0:
                    status = "critical"
                    alert_key = "secret_expired"
                    severity = "critical"
                    messages.append("Secret has expired.")
                elif expires_in is not None and expires_in <= 604800:
                    if status == "healthy":
                        status = "warning"
                    alert_key = alert_key or "secret_expiring"
                    messages.append(f"Secret expires in {max(0, expires_in)} seconds.")
                if rotation_due:
                    if status == "healthy":
                        status = "warning"
                    alert_key = alert_key or "rotation_overdue"
                    messages.append("Secret rotation is due.")
                elif policy is not None and due_in is not None and due_in <= policy.warning_seconds:
                    if status == "healthy":
                        status = "warning"
                    alert_key = alert_key or "rotation_due_soon"
                    messages.append(f"Secret rotation is due in {max(0, due_in)} seconds.")
                if recent_errors >= 3:
                    if status == "healthy":
                        status = "warning"
                    alert_key = alert_key or "resolution_errors"
                    messages.append(f"Recent secret resolution errors: {recent_errors}.")
                message = " ".join(messages) or "Secret lifecycle state is healthy."
                session.add(
                    SecretHealthCheckModel(
                        secret_id=secret.id,
                        provider_id=provider.id,
                        workspace_id=secret.workspace_id,
                        status=status,
                        provider_status=provider.health_status,
                        expires_in_seconds=expires_in,
                        rotation_due=rotation_due,
                        rotation_due_in_seconds=due_in,
                        recent_error_count=recent_errors,
                        message=message,
                        details_json={"scan_metadata": dict(request.metadata)},
                    )
                )
                health_checks += 1
                if status in {"warning", "critical"} and alert_key:
                    self._upsert_health_alert(
                        session,
                        secret=secret,
                        provider=provider,
                        alert_key=alert_key,
                        severity="critical" if status == "critical" else severity,
                        message=message,
                    )
                else:
                    self._resolve_matching_health_alerts(session, secret.id, request.actor_id)

                if (
                    request.evaluate_rotation
                    and request.auto_rotate
                    and rotation_due
                    and policy is not None
                    and policy.auto_rotate_enabled
                    and policy.rotation_mode == "managed_random"
                    and policy.failure_count < policy.max_failures
                ):
                    existing = session.scalar(
                        select(SecretRotationRunModel).where(
                            SecretRotationRunModel.secret_id == secret.id,
                            SecretRotationRunModel.status.in_(["proposed", "running"]),
                        )
                    )
                    if existing is None:
                        run = SecretRotationRunModel(
                            secret_id=secret.id,
                            policy_id=policy.id,
                            workspace_id=secret.workspace_id,
                            status="proposed" if policy.require_human_approval else "running",
                            trigger_type="scheduled",
                            rotation_mode=policy.rotation_mode,
                            handler_key=policy.handler_key,
                            requested_by=request.actor_id,
                            approved_by="" if policy.require_human_approval else request.actor_id,
                            reason="Scheduled lifecycle rotation.",
                            old_version=int(secret.current_version),
                            new_version=0,
                            started_at=None if policy.require_human_approval else now,
                            metadata_json={"automatic": True},
                        )
                        session.add(run)
                        session.flush()
                        if not policy.require_human_approval:
                            auto_run_ids.append(run.id)
            open_alerts = int(
                session.scalar(
                    select(func.count())
                    .select_from(SecretHealthAlertModel)
                    .where(SecretHealthAlertModel.status == "open")
                ) or 0
            )

        auto_results: list[dict[str, Any]] = []
        for run_id in auto_run_ids:
            auto_results.append(await self._execute_rotation_run(run_id))
        self._lifecycle_scan_count += 1
        result = {
            "providers_checked": provider_checked,
            "provider_errors": provider_errors,
            "health_checks": health_checks,
            "open_alerts": open_alerts,
            "automatic_rotation_runs": auto_results,
            "scanned_at": _iso(now),
        }
        await self.event_bus.publish("secret.lifecycle.scanned", result)
        return result

    def list_health_checks(
        self,
        *,
        workspace_id: str | None = None,
        secret_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(SecretHealthCheckModel).order_by(
                SecretHealthCheckModel.checked_at.desc()
            )
            if workspace_id is not None:
                statement = statement.where(SecretHealthCheckModel.workspace_id == workspace_id)
            if secret_id:
                statement = statement.where(SecretHealthCheckModel.secret_id == secret_id)
            if status:
                statement = statement.where(SecretHealthCheckModel.status == status)
            statement = statement.limit(max(1, min(1000, int(limit))))
            return [self._health_check_dump(row) for row in session.scalars(statement).all()]

    def list_health_alerts(
        self,
        *,
        workspace_id: str | None = None,
        secret_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(SecretHealthAlertModel).order_by(
                SecretHealthAlertModel.last_seen_at.desc()
            )
            if workspace_id is not None:
                statement = statement.where(SecretHealthAlertModel.workspace_id == workspace_id)
            if secret_id:
                statement = statement.where(SecretHealthAlertModel.secret_id == secret_id)
            if status:
                statement = statement.where(SecretHealthAlertModel.status == status)
            statement = statement.limit(max(1, min(1000, int(limit))))
            return [self._health_alert_dump(row) for row in session.scalars(statement).all()]

    async def decide_health_alert(
        self,
        alert_id: str,
        request: SecretHealthAlertDecision,
        *,
        expected_workspace_id: str | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(SecretHealthAlertModel, alert_id)
            if row is None or not _workspace_allowed(row.workspace_id, expected_workspace_id):
                raise SecretNotFound("Secret health alert not found.")
            row.status = "suppressed" if request.suppress else "resolved"
            row.resolved_at = utc_now()
            row.resolved_by = request.actor_id
            row.resolution_note = request.note
            result = self._health_alert_dump(row)
        await self.event_bus.publish("secret.health.alert.closed", result)
        return result

    @staticmethod
    def _upsert_health_alert(
        session: Session,
        *,
        secret: SecretRecordModel,
        provider: SecretProviderModel,
        alert_key: str,
        severity: str,
        message: str,
    ) -> None:
        row = session.scalar(
            select(SecretHealthAlertModel).where(
                SecretHealthAlertModel.secret_id == secret.id,
                SecretHealthAlertModel.alert_key == alert_key,
                SecretHealthAlertModel.status == "open",
            )
        )
        now = utc_now()
        if row is None:
            session.add(
                SecretHealthAlertModel(
                    secret_id=secret.id,
                    provider_id=provider.id,
                    workspace_id=secret.workspace_id,
                    alert_key=alert_key,
                    severity=severity,
                    status="open",
                    title=alert_key.replace("_", " ").title(),
                    message=message,
                    occurrence_count=1,
                    first_seen_at=now,
                    last_seen_at=now,
                    metadata_json={},
                )
            )
        else:
            row.severity = severity
            row.message = message
            row.occurrence_count += 1
            row.last_seen_at = now

    @staticmethod
    def _resolve_matching_health_alerts(session: Session, secret_id: str, actor_id: str) -> None:
        rows = session.scalars(
            select(SecretHealthAlertModel).where(
                SecretHealthAlertModel.secret_id == secret_id,
                SecretHealthAlertModel.status == "open",
            )
        ).all()
        now = utc_now()
        for row in rows:
            row.status = "resolved"
            row.resolved_at = now
            row.resolved_by = actor_id
            row.resolution_note = "Automatically resolved after a healthy lifecycle scan."

    def list_access_events(
        self,
        *,
        workspace_id: str | None = None,
        secret_id: str | None = None,
        actor_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(SecretAccessEventModel).order_by(
                SecretAccessEventModel.created_at.desc()
            )
            if workspace_id is not None:
                statement = statement.where(SecretAccessEventModel.workspace_id == workspace_id)
            if secret_id:
                statement = statement.where(SecretAccessEventModel.secret_id == secret_id)
            if actor_id:
                statement = statement.where(SecretAccessEventModel.actor_id == actor_id)
            statement = statement.limit(max(1, min(1000, int(limit))))
            return [self._access_dump(row) for row in session.scalars(statement).all()]

    def _resolve_reference_row(
        self,
        session: Session,
        reference: str,
        workspace_id: str | None,
    ) -> tuple[SecretProviderModel, SecretRecordModel]:
        try:
            provider_key, secret_key = parse_secret_reference(reference)
        except SecretReferenceError as exc:
            raise SecretManagerError(str(exc)) from exc
        provider = session.scalar(
            select(SecretProviderModel).where(
                SecretProviderModel.provider_key == provider_key
            )
        )
        if provider is None:
            raise SecretNotFound("Secret provider not found.")
        candidates = session.scalars(
            select(SecretRecordModel).where(
                SecretRecordModel.provider_id == provider.id,
                SecretRecordModel.secret_key == secret_key,
                SecretRecordModel.status == "active",
            )
        ).all()
        secret = self._select_scope(candidates, workspace_id)
        if secret is None:
            raise SecretNotFound("Secret reference not found for this Workspace.")
        return provider, secret

    def _effective_settings_row(
        self,
        session: Session,
        workspace_id: str | None,
    ) -> SecretAccessSettingsModel | None:
        if workspace_id is not None:
            row = session.scalar(
                select(SecretAccessSettingsModel).where(
                    SecretAccessSettingsModel.scope_key == f"workspace:{workspace_id}"
                )
            )
            if row is not None:
                return row
        return session.scalar(
            select(SecretAccessSettingsModel).where(
                SecretAccessSettingsModel.scope_key == "global"
            )
        )

    @staticmethod
    def _default_settings_dump(workspace_id: str | None) -> dict[str, Any]:
        return {
            "id": None,
            "scope_key": f"workspace:{workspace_id}" if workspace_id else "global",
            "workspace_id": workspace_id,
            "enabled": True,
            "enforcement_mode": "legacy",
            "default_effect": "allow",
            "default_lease_seconds": 60,
            "max_lease_seconds": 900,
            "max_lease_uses": 10,
            "metadata": {"implicit_default": True},
            "created_by": "system",
            "updated_by": "system",
            "created_at": None,
            "updated_at": None,
        }

    @staticmethod
    def _normalize_patterns(
        values: list[str] | None,
        *,
        default: list[str] | None = None,
    ) -> list[str]:
        result: list[str] = []
        for value in values or []:
            item = str(value).strip()
            if item and item not in result:
                result.append(item)
        if not result and default is not None:
            return list(default)
        return result

    @staticmethod
    def _patterns_match(value: str, patterns: list[str]) -> bool:
        if not patterns:
            return True
        return any(fnmatchcase(value, pattern) for pattern in patterns)

    @classmethod
    def _action_matches(cls, action: str, patterns: list[str]) -> bool:
        normalized = "lease" if action in {"lease", "lease_resolve", "lease_use"} else action
        return cls._patterns_match(normalized, patterns)

    def _evaluate_access_rows(
        self,
        session: Session,
        *,
        secret: SecretRecordModel,
        provider: SecretProviderModel,
        context: SecretResolveContext,
        action: str,
    ) -> dict[str, Any]:
        settings_row = self._effective_settings_row(session, context.workspace_id)
        settings = (
            self._settings_dump(settings_row)
            if settings_row is not None
            else self._default_settings_dump(context.workspace_id)
        )
        mode = settings["enforcement_mode"] if settings["enabled"] else "legacy"
        reference = _reference(provider.provider_key, secret.secret_key)

        statement = select(SecretAccessPolicyModel).where(
            SecretAccessPolicyModel.enabled.is_(True)
        )
        if context.workspace_id is None:
            statement = statement.where(SecretAccessPolicyModel.workspace_id.is_(None))
        else:
            statement = statement.where(
                or_(
                    SecretAccessPolicyModel.workspace_id == context.workspace_id,
                    SecretAccessPolicyModel.workspace_id.is_(None),
                )
            )
        rows = list(
            session.scalars(
                statement.order_by(
                    SecretAccessPolicyModel.priority.desc(),
                    SecretAccessPolicyModel.created_at.asc(),
                )
            ).all()
        )

        matched: list[SecretAccessPolicyModel] = []
        for row in rows:
            if not self._action_matches(action, list(row.actions_json or [])):
                continue
            if not self._patterns_match(reference, list(row.secret_patterns_json or [])):
                continue
            if not self._patterns_match(context.consumer_type, list(row.consumer_types_json or [])):
                continue
            if not self._patterns_match(context.consumer_key, list(row.consumer_keys_json or [])):
                continue
            if not self._patterns_match(context.actor_id, list(row.actor_patterns_json or [])):
                continue
            if not self._patterns_match(context.source, list(row.source_patterns_json or [])):
                continue
            if not self._patterns_match(context.purpose, list(row.purpose_patterns_json or [])):
                continue
            if (
                row.require_workspace_match
                and secret.workspace_id is not None
                and context.workspace_id != secret.workspace_id
            ):
                continue
            matched.append(row)

        deny_rows = [row for row in matched if row.effect == "deny"]
        allow_rows = [row for row in matched if row.effect == "allow"]
        if deny_rows:
            policy_effect = "deny"
        elif allow_rows:
            policy_effect = "allow"
        else:
            policy_effect = settings["default_effect"]

        would_allow = policy_effect == "allow"
        allowed = True if mode in {"legacy", "audit"} else would_allow

        max_lease_seconds_values = [
            int(row.max_lease_seconds)
            for row in allow_rows
            if row.max_lease_seconds is not None
        ]
        max_uses_values = [
            int(row.max_uses)
            for row in allow_rows
            if row.max_uses is not None
        ]
        return {
            "allowed": allowed,
            "would_allow": would_allow,
            "enforcement_mode": mode,
            "policy_effect": policy_effect,
            "default_effect": settings["default_effect"],
            "secret_id": secret.id,
            "reference": reference,
            "workspace_id": context.workspace_id,
            "consumer_type": context.consumer_type,
            "consumer_key": context.consumer_key,
            "actor_id": context.actor_id,
            "action": action,
            "matched_policy_ids": [row.id for row in matched],
            "matched_policy_keys": [row.policy_key for row in matched],
            "deny_policy_ids": [row.id for row in deny_rows],
            "allow_policy_ids": [row.id for row in allow_rows],
            "max_lease_seconds": min(max_lease_seconds_values) if max_lease_seconds_values else None,
            "max_uses": min(max_uses_values) if max_uses_values else None,
            "settings": settings,
        }

    @staticmethod
    def _lease_token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _settings_dump(row: SecretAccessSettingsModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope_key": row.scope_key,
            "workspace_id": row.workspace_id,
            "enabled": row.enabled,
            "enforcement_mode": row.enforcement_mode,
            "default_effect": row.default_effect,
            "default_lease_seconds": row.default_lease_seconds,
            "max_lease_seconds": row.max_lease_seconds,
            "max_lease_uses": row.max_lease_uses,
            "metadata": row.metadata_json or {},
            "created_by": row.created_by,
            "updated_by": row.updated_by,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }

    @staticmethod
    def _policy_dump(row: SecretAccessPolicyModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "policy_key": row.policy_key,
            "workspace_id": row.workspace_id,
            "name": row.name,
            "description": row.description,
            "enabled": row.enabled,
            "priority": row.priority,
            "effect": row.effect,
            "actions": row.actions_json or [],
            "secret_patterns": row.secret_patterns_json or [],
            "consumer_types": row.consumer_types_json or [],
            "consumer_keys": row.consumer_keys_json or [],
            "actor_patterns": row.actor_patterns_json or [],
            "source_patterns": row.source_patterns_json or [],
            "purpose_patterns": row.purpose_patterns_json or [],
            "max_lease_seconds": row.max_lease_seconds,
            "max_uses": row.max_uses,
            "require_workspace_match": row.require_workspace_match,
            "metadata": row.metadata_json or {},
            "created_by": row.created_by,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }

    @staticmethod
    def _lease_dump(row: SecretLeaseModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "secret_id": row.secret_id,
            "provider_id": row.provider_id,
            "workspace_id": row.workspace_id,
            "actor_id": row.actor_id,
            "consumer_type": row.consumer_type,
            "consumer_key": row.consumer_key,
            "purpose": row.purpose,
            "source": row.source,
            "status": row.status,
            "max_uses": row.max_uses,
            "use_count": row.use_count,
            "issued_at": _iso(row.issued_at),
            "expires_at": _iso(row.expires_at),
            "last_used_at": _iso(row.last_used_at),
            "revoked_at": _iso(row.revoked_at),
            "created_by": row.created_by,
            "metadata": row.metadata_json or {},
            "token_persisted": False,
        }

    @staticmethod
    def _rotation_policy_dump(row: SecretRotationPolicyModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "secret_id": row.secret_id,
            "workspace_id": row.workspace_id,
            "enabled": row.enabled,
            "rotation_mode": row.rotation_mode,
            "interval_seconds": row.interval_seconds,
            "warning_seconds": row.warning_seconds,
            "auto_rotate_enabled": row.auto_rotate_enabled,
            "require_human_approval": row.require_human_approval,
            "handler_key": row.handler_key,
            "random_bytes": row.random_bytes,
            "max_failures": row.max_failures,
            "next_rotation_at": _iso(row.next_rotation_at),
            "last_success_at": _iso(row.last_success_at),
            "last_failure_at": _iso(row.last_failure_at),
            "failure_count": row.failure_count,
            "metadata": row.metadata_json or {},
            "created_by": row.created_by,
            "updated_by": row.updated_by,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }

    @staticmethod
    def _rotation_run_dump(row: SecretRotationRunModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "secret_id": row.secret_id,
            "policy_id": row.policy_id,
            "workspace_id": row.workspace_id,
            "status": row.status,
            "trigger_type": row.trigger_type,
            "rotation_mode": row.rotation_mode,
            "handler_key": row.handler_key,
            "requested_by": row.requested_by,
            "approved_by": row.approved_by,
            "reason": row.reason,
            "idempotency_key": row.idempotency_key,
            "old_version": row.old_version,
            "new_version": row.new_version,
            "started_at": _iso(row.started_at),
            "finished_at": _iso(row.finished_at),
            "error": row.error,
            "metadata": row.metadata_json or {},
            "created_at": _iso(row.created_at),
            "plaintext_persisted": False,
        }

    @staticmethod
    def _health_check_dump(row: SecretHealthCheckModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "secret_id": row.secret_id,
            "provider_id": row.provider_id,
            "workspace_id": row.workspace_id,
            "status": row.status,
            "provider_status": row.provider_status,
            "expires_in_seconds": row.expires_in_seconds,
            "rotation_due": row.rotation_due,
            "rotation_due_in_seconds": row.rotation_due_in_seconds,
            "recent_error_count": row.recent_error_count,
            "message": row.message,
            "details": row.details_json or {},
            "checked_at": _iso(row.checked_at),
        }

    @staticmethod
    def _health_alert_dump(row: SecretHealthAlertModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "secret_id": row.secret_id,
            "provider_id": row.provider_id,
            "workspace_id": row.workspace_id,
            "alert_key": row.alert_key,
            "severity": row.severity,
            "status": row.status,
            "title": row.title,
            "message": row.message,
            "occurrence_count": row.occurrence_count,
            "first_seen_at": _iso(row.first_seen_at),
            "last_seen_at": _iso(row.last_seen_at),
            "resolved_at": _iso(row.resolved_at),
            "resolved_by": row.resolved_by,
            "resolution_note": row.resolution_note,
            "metadata": row.metadata_json or {},
        }

    @staticmethod
    def _select_scope(
        rows: list[SecretRecordModel], workspace_id: str | None
    ) -> SecretRecordModel | None:
        if workspace_id is not None:
            for row in rows:
                if row.workspace_id == workspace_id:
                    return row
        for row in rows:
            if row.workspace_id is None:
                return row
        return None

    @staticmethod
    def _append_access_event(
        session: Session,
        *,
        secret: SecretRecordModel,
        provider: SecretProviderModel | None,
        actor_id: str,
        action: str,
        purpose: str,
        outcome: str,
        source: str,
        material_hash_value: str,
        auth_method: str = "system",
        error: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        session.add(
            SecretAccessEventModel(
                secret_id=secret.id,
                provider_id=provider.id if provider else secret.provider_id,
                workspace_id=secret.workspace_id,
                actor_id=actor_id,
                action=action,
                purpose=purpose,
                outcome=outcome,
                auth_method=auth_method,
                source=source,
                material_hash=material_hash_value,
                error=error[:10000],
                metadata_json=dict(metadata or {}),
            )
        )

    @staticmethod
    def _provider_dump(row: SecretProviderModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "provider_key": row.provider_key,
            "workspace_id": row.workspace_id,
            "name": row.name,
            "provider_type": row.provider_type,
            "adapter_key": row.adapter_key,
            "enabled": row.enabled,
            "read_only": row.read_only,
            "config": row.config_json or {},
            "health_status": row.health_status,
            "health_message": row.health_message,
            "last_checked_at": _iso(row.last_checked_at),
            "metadata": row.metadata_json or {},
            "created_by": row.created_by,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }

    @staticmethod
    def _secret_dump(row: SecretRecordModel, *, provider_key: str) -> dict[str, Any]:
        return {
            "id": row.id,
            "provider_id": row.provider_id,
            "provider_key": provider_key,
            "workspace_id": row.workspace_id,
            "secret_key": row.secret_key,
            "reference": _reference(provider_key, row.secret_key),
            "provider_ref": row.provider_ref,
            "display_name": row.display_name,
            "description": row.description,
            "status": row.status,
            "current_version": row.current_version,
            "material_hash": row.material_hash,
            "last_rotated_at": _iso(row.last_rotated_at),
            "expires_at": _iso(row.expires_at),
            "metadata": row.metadata_json or {},
            "created_by": row.created_by,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
            "plaintext_in_response": False,
        }

    @staticmethod
    def _access_dump(row: SecretAccessEventModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "secret_id": row.secret_id,
            "provider_id": row.provider_id,
            "workspace_id": row.workspace_id,
            "actor_id": row.actor_id,
            "action": row.action,
            "purpose": row.purpose,
            "outcome": row.outcome,
            "auth_method": row.auth_method,
            "source": row.source,
            "material_hash": row.material_hash,
            "error": row.error,
            "metadata": row.metadata_json or {},
            "created_at": _iso(row.created_at),
        }
