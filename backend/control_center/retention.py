from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatchcase
from typing import Any

from sqlalchemy import event as sqlalchemy_event, func, select
from sqlalchemy.inspection import inspect as sqlalchemy_inspect
from sqlalchemy.orm import Session

from backend.control_center.compliance import GENESIS_HASH, redact_json, sha256_json
from backend.control_center.models import (
    HumanControlComplianceReportModel,
    HumanControlEvidenceArchiveItemModel,
    HumanControlEvidenceArchiveModel,
    HumanControlExternalAuditPackageModel,
    HumanControlLegalHoldModel,
    HumanControlNotificationModel,
    HumanControlOperatorAuditEventModel,
    HumanControlRetentionPolicyModel,
    HumanControlRetentionRunModel,
    HumanControlSecurityEventModel,
)
from backend.control_center.retention_schemas import (
    HumanControlEvidenceArchiveCreate,
    HumanControlEvidenceArchiveRevokeRequest,
    HumanControlExternalAuditPackageCreate,
    HumanControlExternalAuditPackageRevokeRequest,
    HumanControlLegalHoldCreate,
    HumanControlLegalHoldReleaseRequest,
    HumanControlRetentionPolicyUpsert,
    HumanControlRetentionRunRequest,
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
SUPPORTED_SOURCE_TYPES = {
    "operator_audit",
    "compliance_report",
    "security_event",
    "notification",
}
TERMINAL_NOTIFICATION_STATUSES = {
    "delivered",
    "acknowledged",
    "failed",
    "cancelled",
    "expired",
}


class HumanControlEvidenceImmutabilityError(RuntimeError):
    pass


@sqlalchemy_event.listens_for(HumanControlEvidenceArchiveItemModel, "before_update")
def _block_archive_item_update(mapper, connection, target) -> None:
    raise HumanControlEvidenceImmutabilityError(
        "Evidence archive items are immutable."
    )


@sqlalchemy_event.listens_for(HumanControlEvidenceArchiveItemModel, "before_delete")
def _block_archive_item_delete(mapper, connection, target) -> None:
    raise HumanControlEvidenceImmutabilityError(
        "Evidence archive items cannot be deleted."
    )


@sqlalchemy_event.listens_for(HumanControlEvidenceArchiveModel, "before_update")
def _protect_sealed_archive(mapper, connection, target) -> None:
    state = sqlalchemy_inspect(target)
    original = state.attrs.status.history.deleted
    previous_status = original[0] if original else target.status
    if previous_status in {"sealed", "revoked"}:
        allowed = {"status", "revoked_by", "revoke_reason", "revoked_at"}
        changed = {
            attr.key
            for attr in state.attrs
            if attr.history.has_changes()
        }
        if not changed.issubset(allowed):
            raise HumanControlEvidenceImmutabilityError(
                "Sealed evidence archives are immutable."
            )


@sqlalchemy_event.listens_for(HumanControlEvidenceArchiveModel, "before_delete")
def _block_archive_delete(mapper, connection, target) -> None:
    raise HumanControlEvidenceImmutabilityError(
        "Evidence archives cannot be deleted."
    )


@sqlalchemy_event.listens_for(HumanControlExternalAuditPackageModel, "before_update")
def _protect_external_package(mapper, connection, target) -> None:
    state = sqlalchemy_inspect(target)
    original = state.attrs.status.history.deleted
    previous_status = original[0] if original else target.status
    if previous_status in {"sealed", "revoked", "expired"}:
        allowed = {"status", "revoked_by", "revoke_reason", "revoked_at"}
        changed = {
            attr.key
            for attr in state.attrs
            if attr.history.has_changes()
        }
        if not changed.issubset(allowed):
            raise HumanControlEvidenceImmutabilityError(
                "Sealed external-audit packages are immutable."
            )


@sqlalchemy_event.listens_for(HumanControlExternalAuditPackageModel, "before_delete")
def _block_external_package_delete(mapper, connection, target) -> None:
    raise HumanControlEvidenceImmutabilityError(
        "External-audit packages cannot be deleted."
    )


def _scope_key(workspace_id: str | None, key: str) -> str:
    prefix = "global" if workspace_id is None else f"workspace:{workspace_id}"
    return f"{prefix}:{key}"


def _date(value: datetime | None) -> datetime | None:
    return ensure_utc(value) if value is not None else None


class HumanControlRetentionService:
    """Retention, legal hold and hash-sealed evidence archive service."""

    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._write_lock = asyncio.Lock()
        self._retention_lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        now = utc_now()
        with self._session_factory() as session:
            policies = session.scalar(
                select(func.count()).select_from(HumanControlRetentionPolicyModel)
            ) or 0
            active_holds = session.scalar(
                select(func.count())
                .select_from(HumanControlLegalHoldModel)
                .where(
                    HumanControlLegalHoldModel.status == "active",
                    (HumanControlLegalHoldModel.expires_at.is_(None))
                    | (HumanControlLegalHoldModel.expires_at > now),
                )
            ) or 0
            archives = session.scalar(
                select(func.count()).select_from(HumanControlEvidenceArchiveModel)
            ) or 0
            packages = session.scalar(
                select(func.count()).select_from(HumanControlExternalAuditPackageModel)
            ) or 0
            runs = session.scalar(
                select(func.count()).select_from(HumanControlRetentionRunModel)
            ) or 0
        return {
            "service": "human_control_retention",
            "policies": int(policies),
            "active_legal_holds": int(active_holds),
            "evidence_archives": int(archives),
            "external_audit_packages": int(packages),
            "retention_runs": int(runs),
            "hash_algorithm": "SHA-256",
            "archive_semantics": "hash-sealed immutable manifest",
            "destructive_retention_default": "disabled",
        }

    async def upsert_policy(
        self,
        request: HumanControlRetentionPolicyUpsert,
    ) -> dict[str, Any]:
        scope = _scope_key(request.workspace_id, "retention")
        with self._session_factory() as session:
            row = session.scalar(
                select(HumanControlRetentionPolicyModel).where(
                    HumanControlRetentionPolicyModel.scope_key == scope
                )
            )
            if row is None:
                row = HumanControlRetentionPolicyModel(
                    scope_key=scope,
                    workspace_id=request.workspace_id,
                )
                session.add(row)
            for field in (
                "enabled",
                "enforcement_mode",
                "default_retention_days",
                "security_event_retention_days",
                "notification_retention_days",
                "compliance_report_retention_days",
                "operator_audit_retention_days",
                "archive_before_purge",
                "require_human_approval",
                "purge_batch_size",
                "legal_hold_override_enabled",
            ):
                value = getattr(request, field)
                setattr(row, field, getattr(value, "value", value))
            row.metadata_json = redact_json(request.metadata)
            row.updated_by = request.actor_id
            session.flush()
            result = self._policy_to_dict(row)
        await self._publish(
            "human_control.retention.policy.updated",
            request.workspace_id,
            {"policy_id": result["id"], "actor_id": request.actor_id},
        )
        return result

    def get_policy(self, workspace_id: str | None) -> dict[str, Any]:
        row = self._effective_policy_row(workspace_id)
        if row is None:
            return {
                "id": None,
                "scope_key": _scope_key(workspace_id, "retention"),
                "workspace_id": workspace_id,
                "enabled": False,
                "enforcement_mode": "observe",
                "default_retention_days": 365,
                "security_event_retention_days": 365,
                "notification_retention_days": 180,
                "compliance_report_retention_days": 2555,
                "operator_audit_retention_days": 2555,
                "archive_before_purge": True,
                "require_human_approval": True,
                "purge_batch_size": 500,
                "legal_hold_override_enabled": True,
                "metadata": {},
                "inherited": False,
            }
        result = self._policy_to_dict(row)
        result["inherited"] = row.workspace_id != workspace_id
        return result

    def list_policies(self) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(HumanControlRetentionPolicyModel).order_by(
                    HumanControlRetentionPolicyModel.created_at.desc()
                )
            ).all()
            return [self._policy_to_dict(row) for row in rows]

    async def create_legal_hold(
        self,
        request: HumanControlLegalHoldCreate,
    ) -> dict[str, Any]:
        scope = _scope_key(request.workspace_id, f"hold:{request.hold_key}")
        with self._session_factory() as session:
            existing = session.scalar(
                select(HumanControlLegalHoldModel).where(
                    HumanControlLegalHoldModel.scope_key == scope
                )
            )
            if existing is not None:
                raise HumanControlConflict(
                    f"Legal hold {request.hold_key!r} уже существует."
                )
            row = HumanControlLegalHoldModel(
                scope_key=scope,
                workspace_id=request.workspace_id,
                hold_key=request.hold_key,
                title=request.title,
                reason=request.reason,
                status="active",
                target_types_json=sorted(set(request.target_types)),
                target_ids_json=sorted(set(request.target_ids)),
                event_patterns_json=sorted(set(request.event_patterns)),
                actor_ids_json=sorted(set(request.actor_ids)),
                custodian_ids_json=sorted(set(request.custodian_ids)),
                period_start=_date(request.period_start),
                period_end=_date(request.period_end),
                expires_at=_date(request.expires_at),
                created_by=request.created_by,
                metadata_json=redact_json(request.metadata),
            )
            session.add(row)
            session.flush()
            result = self._hold_to_dict(row)
        await self._publish(
            "human_control.retention.legal_hold.created",
            request.workspace_id,
            {"legal_hold_id": result["id"], "actor_id": request.created_by},
        )
        return result

    def list_legal_holds(
        self,
        *,
        workspace_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        self._expire_holds()
        with self._session_factory() as session:
            statement = select(HumanControlLegalHoldModel)
            if workspace_id is not None:
                statement = statement.where(
                    HumanControlLegalHoldModel.workspace_id == workspace_id
                )
            if status is not None:
                statement = statement.where(HumanControlLegalHoldModel.status == status)
            rows = session.scalars(
                statement.order_by(HumanControlLegalHoldModel.created_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            return [self._hold_to_dict(row) for row in rows]

    def get_legal_hold(self, hold_id: str) -> dict[str, Any] | None:
        self._expire_holds()
        with self._session_factory() as session:
            row = session.get(HumanControlLegalHoldModel, hold_id)
            return None if row is None else self._hold_to_dict(row)

    async def release_legal_hold(
        self,
        hold_id: str,
        request: HumanControlLegalHoldReleaseRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(HumanControlLegalHoldModel, hold_id)
            if row is None:
                raise HumanControlNotFound("Legal hold не найден.")
            if row.status != "active" and not request.force:
                raise HumanControlConflict(
                    f"Legal hold имеет статус {row.status!r}."
                )
            row.status = "released"
            row.released_by = request.actor_id
            row.release_reason = request.reason
            row.released_at = utc_now()
            row.metadata_json = {
                **(row.metadata_json or {}),
                **redact_json(request.metadata),
            }
            session.flush()
            result = self._hold_to_dict(row)
        await self._publish(
            "human_control.retention.legal_hold.released",
            result["workspace_id"],
            {"legal_hold_id": hold_id, "actor_id": request.actor_id},
        )
        return result

    async def create_archive(
        self,
        request: HumanControlEvidenceArchiveCreate,
    ) -> dict[str, Any]:
        unsupported = set(request.source_types) - SUPPORTED_SOURCE_TYPES
        if unsupported:
            raise HumanControlError(
                "Неподдерживаемые source_types: " + ", ".join(sorted(unsupported))
            )
        scope = _scope_key(request.workspace_id, f"archive:{request.archive_key}")
        async with self._write_lock:
            with self._session_factory() as session:
                existing = session.scalar(
                    select(HumanControlEvidenceArchiveModel).where(
                        HumanControlEvidenceArchiveModel.scope_key == scope
                    )
                )
                if existing is not None:
                    return self._archive_to_dict(existing)
                previous_statement = select(HumanControlEvidenceArchiveModel).where(
                    HumanControlEvidenceArchiveModel.status == "sealed"
                )
                if request.workspace_id is None:
                    previous_statement = previous_statement.where(
                        HumanControlEvidenceArchiveModel.workspace_id.is_(None)
                    )
                else:
                    previous_statement = previous_statement.where(
                        HumanControlEvidenceArchiveModel.workspace_id
                        == request.workspace_id
                    )
                previous = session.scalar(
                    previous_statement
                    .order_by(HumanControlEvidenceArchiveModel.sealed_at.desc())
                    .limit(1)
                )
                previous_hash = GENESIS_HASH if previous is None else previous.archive_hash
                hold = None
                if request.legal_hold_id:
                    hold = session.get(HumanControlLegalHoldModel, request.legal_hold_id)
                    if hold is None:
                        raise HumanControlNotFound("Связанный legal hold не найден.")
                sources = self._collect_sources(session, request)
                item_hashes: list[str] = []
                row = HumanControlEvidenceArchiveModel(
                    scope_key=scope,
                    workspace_id=request.workspace_id,
                    archive_key=request.archive_key,
                    archive_type=request.archive_type.value,
                    title=request.title,
                    description=request.description,
                    status="building",
                    legal_hold_id=request.legal_hold_id,
                    period_start=_date(request.period_start),
                    period_end=_date(request.period_end),
                    source_types_json=list(request.source_types),
                    item_count=0,
                    manifest_json={},
                    manifest_hash=GENESIS_HASH,
                    previous_archive_hash=previous_hash,
                    archive_hash=GENESIS_HASH,
                    created_by=request.created_by,
                    metadata_json=redact_json(request.metadata),
                )
                session.add(row)
                session.flush()
                for ordinal, source in enumerate(sources, start=1):
                    content = redact_json(source["content"])
                    content_hash = sha256_json(content)
                    item_hashes.append(content_hash)
                    session.add(
                        HumanControlEvidenceArchiveItemModel(
                            archive_id=row.id,
                            ordinal=ordinal,
                            source_type=source["source_type"],
                            source_id=source["source_id"],
                            occurred_at=source.get("occurred_at"),
                            content_json=content,
                            content_hash=content_hash,
                        )
                    )
                sealed_at = utc_now()
                manifest = {
                    "version": "1.0",
                    "archive_id": row.id,
                    "archive_key": request.archive_key,
                    "archive_type": request.archive_type.value,
                    "workspace_id": request.workspace_id,
                    "legal_hold_id": request.legal_hold_id,
                    "period_start": iso(_date(request.period_start)),
                    "period_end": iso(_date(request.period_end)),
                    "source_types": list(request.source_types),
                    "item_count": len(sources),
                    "item_hashes": item_hashes,
                    "created_by": request.created_by,
                    "sealed_at": iso(sealed_at),
                    "previous_archive_hash": previous_hash,
                }
                manifest_hash = sha256_json(manifest)
                archive_hash = sha256_json(
                    {
                        "manifest_hash": manifest_hash,
                        "previous_archive_hash": previous_hash,
                        "item_hashes": item_hashes,
                    }
                )
                row.item_count = len(sources)
                row.manifest_json = manifest
                row.manifest_hash = manifest_hash
                row.archive_hash = archive_hash
                row.status = "sealed"
                row.sealed_by = request.created_by
                row.sealed_at = sealed_at
                session.flush()
                result = self._archive_to_dict(row)
        await self._publish(
            "human_control.retention.archive.sealed",
            request.workspace_id,
            {
                "archive_id": result["id"],
                "archive_hash": result["archive_hash"],
                "item_count": result["item_count"],
                "actor_id": request.created_by,
            },
        )
        return result

    def list_archives(
        self,
        *,
        workspace_id: str | None = None,
        status: str | None = None,
        archive_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlEvidenceArchiveModel)
            if workspace_id is not None:
                statement = statement.where(
                    HumanControlEvidenceArchiveModel.workspace_id == workspace_id
                )
            if status is not None:
                statement = statement.where(HumanControlEvidenceArchiveModel.status == status)
            if archive_type is not None:
                statement = statement.where(
                    HumanControlEvidenceArchiveModel.archive_type == archive_type
                )
            rows = session.scalars(
                statement.order_by(HumanControlEvidenceArchiveModel.created_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            return [self._archive_to_dict(row) for row in rows]

    def get_archive(
        self,
        archive_id: str,
        *,
        include_items: bool = False,
        item_limit: int = 1000,
        item_offset: int = 0,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(HumanControlEvidenceArchiveModel, archive_id)
            if row is None:
                return None
            result = self._archive_to_dict(row)
            if include_items:
                items = session.scalars(
                    select(HumanControlEvidenceArchiveItemModel)
                    .where(HumanControlEvidenceArchiveItemModel.archive_id == archive_id)
                    .order_by(HumanControlEvidenceArchiveItemModel.ordinal)
                    .limit(item_limit)
                    .offset(item_offset)
                ).all()
                result["items"] = [self._archive_item_to_dict(item) for item in items]
            return result

    def verify_archive(self, archive_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(HumanControlEvidenceArchiveModel, archive_id)
            if row is None:
                raise HumanControlNotFound("Evidence archive не найден.")
            items = session.scalars(
                select(HumanControlEvidenceArchiveItemModel)
                .where(HumanControlEvidenceArchiveItemModel.archive_id == archive_id)
                .order_by(HumanControlEvidenceArchiveItemModel.ordinal)
            ).all()
            errors: list[str] = []
            item_hashes: list[str] = []
            for expected_ordinal, item in enumerate(items, start=1):
                if item.ordinal != expected_ordinal:
                    errors.append(
                        f"Нарушена последовательность item ordinal={item.ordinal}."
                    )
                calculated = sha256_json(item.content_json)
                item_hashes.append(calculated)
                if calculated != item.content_hash:
                    errors.append(f"Повреждён item {item.id}.")
            manifest_hash = sha256_json(row.manifest_json)
            if manifest_hash != row.manifest_hash:
                errors.append("Manifest hash не совпадает.")
            archive_hash = sha256_json(
                {
                    "manifest_hash": manifest_hash,
                    "previous_archive_hash": row.previous_archive_hash,
                    "item_hashes": item_hashes,
                }
            )
            if archive_hash != row.archive_hash:
                errors.append("Archive hash не совпадает.")
            if len(items) != row.item_count:
                errors.append("Количество items не совпадает с manifest.")
            return {
                "archive_id": archive_id,
                "valid": not errors,
                "errors": errors,
                "item_count": len(items),
                "manifest_hash": manifest_hash,
                "archive_hash": archive_hash,
                "stored_archive_hash": row.archive_hash,
            }

    async def revoke_archive(
        self,
        archive_id: str,
        request: HumanControlEvidenceArchiveRevokeRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(HumanControlEvidenceArchiveModel, archive_id)
            if row is None:
                raise HumanControlNotFound("Evidence archive не найден.")
            if row.status == "revoked":
                return self._archive_to_dict(row)
            row.status = "revoked"
            row.revoked_by = request.actor_id
            row.revoke_reason = request.reason
            row.revoked_at = utc_now()
            session.flush()
            result = self._archive_to_dict(row)
        await self._publish(
            "human_control.retention.archive.revoked",
            result["workspace_id"],
            {"archive_id": archive_id, "actor_id": request.actor_id},
        )
        return result

    async def run_retention(
        self,
        request: HumanControlRetentionRunRequest,
    ) -> dict[str, Any]:
        async with self._retention_lock:
            policy = self._ensure_policy_row(request.workspace_id, request.actor_id)
            with self._session_factory() as session:
                existing = session.scalar(
                    select(HumanControlRetentionRunModel).where(
                        HumanControlRetentionRunModel.idempotency_key
                        == request.idempotency_key
                    )
                )
                if existing is not None:
                    return self._run_to_dict(existing)

            if request.apply:
                if not policy.enabled and not request.force:
                    raise HumanControlConflict("Retention policy отключена.")
                if policy.enforcement_mode != "purge" and not request.force:
                    raise HumanControlConflict(
                        "Для удаления enforcement_mode должен быть purge."
                    )
                if policy.require_human_approval and not request.force:
                    raise HumanControlConflict(
                        "Политика требует явного force после human approval."
                    )

            now = utc_now()
            cutoffs = {
                "security_event": now
                - timedelta(days=policy.security_event_retention_days),
                "notification": now
                - timedelta(days=policy.notification_retention_days),
                "compliance_report": now
                - timedelta(days=policy.compliance_report_retention_days),
                "operator_audit": now
                - timedelta(days=policy.operator_audit_retention_days),
            }
            eligible = self._eligible_records(
                workspace_id=request.workspace_id,
                cutoffs=cutoffs,
                batch_size=policy.purge_batch_size,
            )
            held, deletable = self._partition_held(
                workspace_id=request.workspace_id,
                records=eligible,
                legal_hold_override_enabled=policy.legal_hold_override_enabled,
            )

            archive = None
            if (
                request.apply
                and policy.archive_before_purge
                and any(deletable.values())
            ):
                source_types = [key for key, values in deletable.items() if values]
                source_ids = [item_id for values in deletable.values() for item_id in values]
                archive = await self.create_archive(
                    HumanControlEvidenceArchiveCreate(
                        workspace_id=request.workspace_id,
                        archive_key=f"retention-{request.idempotency_key}",
                        archive_type="retention",
                        title=f"Retention archive {request.idempotency_key}",
                        description=request.reason,
                        period_end=now,
                        source_types=source_types,
                        source_ids=source_ids,
                        max_items=max(1, len(source_ids)),
                        created_by=request.actor_id,
                        metadata={"retention_run": request.idempotency_key},
                    )
                )

            deleted = {key: 0 for key in eligible}
            errors: list[str] = []
            if request.apply:
                try:
                    deleted = self._delete_records(deletable)
                except Exception as exc:
                    errors.append(str(exc))

            with self._session_factory() as session:
                row = HumanControlRetentionRunModel(
                    policy_id=policy.id,
                    workspace_id=request.workspace_id,
                    idempotency_key=request.idempotency_key,
                    run_mode="apply" if request.apply else "preview",
                    status="failed" if errors else "completed",
                    started_by=request.actor_id,
                    cutoff_json={key: iso(value) for key, value in cutoffs.items()},
                    eligible_json={key: len(value) for key, value in eligible.items()},
                    held_json={key: len(value) for key, value in held.items()},
                    archived_json={
                        "archive_id": None if archive is None else archive["id"],
                        "item_count": 0 if archive is None else archive["item_count"],
                    },
                    deleted_json=deleted,
                    errors_json=errors,
                    evidence_archive_id=None if archive is None else archive["id"],
                    metadata_json={
                        **redact_json(request.metadata),
                        "reason": request.reason,
                        "force": request.force,
                    },
                    completed_at=utc_now(),
                )
                session.add(row)
                policy_row = session.get(HumanControlRetentionPolicyModel, policy.id)
                if policy_row is not None:
                    policy_row.last_evaluated_at = utc_now()
                session.flush()
                result = self._run_to_dict(row)
        await self._publish(
            "human_control.retention.run.completed",
            request.workspace_id,
            {
                "retention_run_id": result["id"],
                "run_mode": result["run_mode"],
                "status": result["status"],
                "actor_id": request.actor_id,
            },
        )
        return result

    def list_retention_runs(
        self,
        *,
        workspace_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlRetentionRunModel)
            if workspace_id is not None:
                statement = statement.where(
                    HumanControlRetentionRunModel.workspace_id == workspace_id
                )
            rows = session.scalars(
                statement.order_by(HumanControlRetentionRunModel.started_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            return [self._run_to_dict(row) for row in rows]

    async def create_external_package(
        self,
        request: HumanControlExternalAuditPackageCreate,
    ) -> dict[str, Any]:
        scope = _scope_key(request.workspace_id, f"audit-package:{request.package_key}")
        with self._session_factory() as session:
            existing = session.scalar(
                select(HumanControlExternalAuditPackageModel).where(
                    HumanControlExternalAuditPackageModel.scope_key == scope
                )
            )
            if existing is not None:
                return self._package_to_dict(existing)
            archives = []
            for archive_id in request.archive_ids:
                archive = session.get(HumanControlEvidenceArchiveModel, archive_id)
                if archive is None:
                    raise HumanControlNotFound(f"Archive {archive_id} не найден.")
                if archive.status != "sealed":
                    raise HumanControlConflict(
                        f"Archive {archive_id} не имеет статус sealed."
                    )
                if request.workspace_id is not None and archive.workspace_id != request.workspace_id:
                    raise HumanControlConflict("Archive принадлежит другому Workspace.")
                archives.append(
                    {
                        "id": archive.id,
                        "archive_key": archive.archive_key,
                        "archive_type": archive.archive_type,
                        "item_count": archive.item_count,
                        "manifest_hash": archive.manifest_hash,
                        "archive_hash": archive.archive_hash,
                        "sealed_at": iso(archive.sealed_at),
                    }
                )
            reports = []
            for report_id in request.report_ids:
                report = session.get(HumanControlComplianceReportModel, report_id)
                if report is None:
                    raise HumanControlNotFound(f"Report {report_id} не найден.")
                if request.workspace_id is not None and report.workspace_id != request.workspace_id:
                    raise HumanControlConflict("Report принадлежит другому Workspace.")
                reports.append(
                    {
                        "id": report.id,
                        "report_type": report.report_type,
                        "period_start": iso(report.period_start),
                        "period_end": iso(report.period_end),
                        "evidence_hash": report.evidence_hash,
                        "generated_at": iso(report.generated_at),
                    }
                )
            created_at = utc_now()
            manifest = {
                "version": "1.0",
                "package_key": request.package_key,
                "workspace_id": request.workspace_id,
                "title": request.title,
                "auditor_name": request.auditor_name,
                "archives": archives,
                "reports": reports,
                "generated_by": request.generated_by,
                "created_at": iso(created_at),
                "access_expires_at": iso(_date(request.access_expires_at)),
                "disclaimer": (
                    "Hash-sealed technical evidence package; not a qualified "
                    "electronic signature or notarisation."
                ),
            }
            package_hash = sha256_json(manifest)
            row = HumanControlExternalAuditPackageModel(
                scope_key=scope,
                workspace_id=request.workspace_id,
                package_key=request.package_key,
                title=request.title,
                auditor_name=request.auditor_name,
                status="sealed",
                archive_ids_json=list(request.archive_ids),
                report_ids_json=list(request.report_ids),
                manifest_json=manifest,
                package_hash=package_hash,
                generated_by=request.generated_by,
                access_expires_at=_date(request.access_expires_at),
                sealed_at=created_at,
                metadata_json=redact_json(request.metadata),
                created_at=created_at,
            )
            session.add(row)
            session.flush()
            result = self._package_to_dict(row)
        await self._publish(
            "human_control.retention.external_package.sealed",
            request.workspace_id,
            {
                "package_id": result["id"],
                "package_hash": result["package_hash"],
                "actor_id": request.generated_by,
            },
        )
        return result

    def list_external_packages(
        self,
        *,
        workspace_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        self._expire_packages()
        with self._session_factory() as session:
            statement = select(HumanControlExternalAuditPackageModel)
            if workspace_id is not None:
                statement = statement.where(
                    HumanControlExternalAuditPackageModel.workspace_id == workspace_id
                )
            if status is not None:
                statement = statement.where(
                    HumanControlExternalAuditPackageModel.status == status
                )
            rows = session.scalars(
                statement.order_by(HumanControlExternalAuditPackageModel.created_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            return [self._package_to_dict(row) for row in rows]

    def get_external_package(self, package_id: str) -> dict[str, Any] | None:
        self._expire_packages()
        with self._session_factory() as session:
            row = session.get(HumanControlExternalAuditPackageModel, package_id)
            return None if row is None else self._package_to_dict(row)

    def export_external_package(
        self,
        package_id: str,
        *,
        include_archive_items: bool = False,
        max_archive_items: int = 5000,
    ) -> dict[str, Any]:
        self._expire_packages()
        with self._session_factory() as session:
            row = session.get(HumanControlExternalAuditPackageModel, package_id)
            if row is None:
                raise HumanControlNotFound("External audit package не найден.")
            if row.status != "sealed":
                raise HumanControlConflict(
                    f"Package имеет статус {row.status!r}."
                )
            archives = []
            remaining = max_archive_items
            for archive_id in row.archive_ids_json or []:
                archive = session.get(HumanControlEvidenceArchiveModel, archive_id)
                if archive is None:
                    continue
                archive_data = self._archive_to_dict(archive)
                if include_archive_items and remaining > 0:
                    items = session.scalars(
                        select(HumanControlEvidenceArchiveItemModel)
                        .where(HumanControlEvidenceArchiveItemModel.archive_id == archive_id)
                        .order_by(HumanControlEvidenceArchiveItemModel.ordinal)
                        .limit(remaining)
                    ).all()
                    archive_data["items"] = [
                        self._archive_item_to_dict(item) for item in items
                    ]
                    remaining -= len(items)
                archives.append(archive_data)
            reports = []
            for report_id in row.report_ids_json or []:
                report = session.get(HumanControlComplianceReportModel, report_id)
                if report is not None:
                    reports.append(
                        {
                            "id": report.id,
                            "report_type": report.report_type,
                            "period_start": iso(report.period_start),
                            "period_end": iso(report.period_end),
                            "summary": report.summary_json,
                            "report": report.report_json,
                            "evidence_hash": report.evidence_hash,
                            "generated_at": iso(report.generated_at),
                        }
                    )
            export = {
                "package": self._package_to_dict(row),
                "archives": archives,
                "reports": reports,
            }
            export["export_hash"] = sha256_json(export)
            return export

    async def revoke_external_package(
        self,
        package_id: str,
        request: HumanControlExternalAuditPackageRevokeRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(HumanControlExternalAuditPackageModel, package_id)
            if row is None:
                raise HumanControlNotFound("External audit package не найден.")
            if row.status == "revoked":
                return self._package_to_dict(row)
            row.status = "revoked"
            row.revoked_by = request.actor_id
            row.revoke_reason = request.reason
            row.revoked_at = utc_now()
            session.flush()
            result = self._package_to_dict(row)
        await self._publish(
            "human_control.retention.external_package.revoked",
            result["workspace_id"],
            {"package_id": package_id, "actor_id": request.actor_id},
        )
        return result

    def dashboard(self, workspace_id: str | None = None) -> dict[str, Any]:
        policy = self.get_policy(workspace_id)
        holds = self.list_legal_holds(workspace_id=workspace_id, limit=1000)
        archives = self.list_archives(workspace_id=workspace_id, limit=1000)
        packages = self.list_external_packages(workspace_id=workspace_id, limit=1000)
        runs = self.list_retention_runs(workspace_id=workspace_id, limit=20)
        return {
            "workspace_id": workspace_id,
            "policy": policy,
            "legal_holds": {
                "active": sum(1 for row in holds if row["status"] == "active"),
                "released": sum(1 for row in holds if row["status"] == "released"),
                "expired": sum(1 for row in holds if row["status"] == "expired"),
            },
            "archives": {
                "total": len(archives),
                "sealed": sum(1 for row in archives if row["status"] == "sealed"),
                "revoked": sum(1 for row in archives if row["status"] == "revoked"),
                "items": sum(row["item_count"] for row in archives),
            },
            "external_packages": {
                "total": len(packages),
                "sealed": sum(1 for row in packages if row["status"] == "sealed"),
                "revoked": sum(1 for row in packages if row["status"] == "revoked"),
                "expired": sum(1 for row in packages if row["status"] == "expired"),
            },
            "recent_retention_runs": runs,
        }

    def _collect_sources(
        self,
        session: Session,
        request: HumanControlEvidenceArchiveCreate,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        source_id_filter = set(request.source_ids)
        period_start = _date(request.period_start)
        period_end = _date(request.period_end)

        if "operator_audit" in request.source_types:
            statement = select(HumanControlOperatorAuditEventModel)
            if request.workspace_id is not None:
                statement = statement.where(
                    HumanControlOperatorAuditEventModel.workspace_id == request.workspace_id
                )
            if period_start is not None:
                statement = statement.where(
                    HumanControlOperatorAuditEventModel.occurred_at >= period_start
                )
            if period_end is not None:
                statement = statement.where(
                    HumanControlOperatorAuditEventModel.occurred_at <= period_end
                )
            if request.actor_ids:
                statement = statement.where(
                    HumanControlOperatorAuditEventModel.actor_id.in_(request.actor_ids)
                )
            rows = session.scalars(
                statement.order_by(HumanControlOperatorAuditEventModel.sequence)
                .limit(request.max_items)
            ).all()
            for row in rows:
                if source_id_filter and row.id not in source_id_filter:
                    continue
                if request.event_patterns and not any(
                    fnmatchcase(row.event_type, pattern)
                    for pattern in request.event_patterns
                ):
                    continue
                result.append(
                    {
                        "source_type": "operator_audit",
                        "source_id": row.id,
                        "occurred_at": row.occurred_at,
                        "content": {
                            "sequence": row.sequence,
                            "event_id": row.event_id,
                            "workspace_id": row.workspace_id,
                            "event_type": row.event_type,
                            "source": row.source,
                            "actor_id": row.actor_id,
                            "identity_id": row.identity_id,
                            "auth_method": row.auth_method,
                            "action": row.action,
                            "resource_type": row.resource_type,
                            "resource_id": row.resource_id,
                            "outcome": row.outcome,
                            "risk_level": row.risk_level,
                            "correlation_id": row.correlation_id,
                            "payload": row.payload_json,
                            "occurred_at": iso(row.occurred_at),
                            "recorded_at": iso(row.recorded_at),
                            "previous_hash": row.previous_hash,
                            "event_hash": row.event_hash,
                        },
                    }
                )

        if "compliance_report" in request.source_types and len(result) < request.max_items:
            statement = select(HumanControlComplianceReportModel)
            if request.workspace_id is not None:
                statement = statement.where(
                    HumanControlComplianceReportModel.workspace_id == request.workspace_id
                )
            if period_start is not None:
                statement = statement.where(
                    HumanControlComplianceReportModel.generated_at >= period_start
                )
            if period_end is not None:
                statement = statement.where(
                    HumanControlComplianceReportModel.generated_at <= period_end
                )
            rows = session.scalars(
                statement.order_by(HumanControlComplianceReportModel.generated_at)
                .limit(request.max_items - len(result))
            ).all()
            for row in rows:
                if source_id_filter and row.id not in source_id_filter:
                    continue
                result.append(
                    {
                        "source_type": "compliance_report",
                        "source_id": row.id,
                        "occurred_at": row.generated_at,
                        "content": {
                            "workspace_id": row.workspace_id,
                            "report_type": row.report_type,
                            "period_start": iso(row.period_start),
                            "period_end": iso(row.period_end),
                            "generated_by": row.generated_by,
                            "summary": row.summary_json,
                            "report": row.report_json,
                            "evidence_hash": row.evidence_hash,
                            "metadata": row.metadata_json,
                            "generated_at": iso(row.generated_at),
                        },
                    }
                )

        if "security_event" in request.source_types and len(result) < request.max_items:
            statement = select(HumanControlSecurityEventModel)
            if request.workspace_id is not None:
                statement = statement.where(
                    HumanControlSecurityEventModel.workspace_id == request.workspace_id
                )
            if period_start is not None:
                statement = statement.where(HumanControlSecurityEventModel.created_at >= period_start)
            if period_end is not None:
                statement = statement.where(HumanControlSecurityEventModel.created_at <= period_end)
            if request.actor_ids:
                statement = statement.where(
                    HumanControlSecurityEventModel.actor_id.in_(request.actor_ids)
                )
            rows = session.scalars(
                statement.order_by(HumanControlSecurityEventModel.created_at)
                .limit(request.max_items - len(result))
            ).all()
            for row in rows:
                if source_id_filter and row.id not in source_id_filter:
                    continue
                if request.event_patterns and not any(
                    fnmatchcase(row.event_type, pattern)
                    for pattern in request.event_patterns
                ):
                    continue
                result.append(
                    {
                        "source_type": "security_event",
                        "source_id": row.id,
                        "occurred_at": row.created_at,
                        "content": {
                            "workspace_id": row.workspace_id,
                            "identity_id": row.identity_id,
                            "actor_id": row.actor_id,
                            "event_type": row.event_type,
                            "success": row.success,
                            "client_ip": row.client_ip,
                            "user_agent": row.user_agent,
                            "details": row.details_json,
                            "created_at": iso(row.created_at),
                        },
                    }
                )

        if "notification" in request.source_types and len(result) < request.max_items:
            statement = select(HumanControlNotificationModel)
            if request.workspace_id is not None:
                statement = statement.where(
                    HumanControlNotificationModel.workspace_id == request.workspace_id
                )
            if period_start is not None:
                statement = statement.where(HumanControlNotificationModel.created_at >= period_start)
            if period_end is not None:
                statement = statement.where(HumanControlNotificationModel.created_at <= period_end)
            rows = session.scalars(
                statement.order_by(HumanControlNotificationModel.created_at)
                .limit(request.max_items - len(result))
            ).all()
            for row in rows:
                if source_id_filter and row.id not in source_id_filter:
                    continue
                if request.event_patterns and not any(
                    fnmatchcase(row.event_type, pattern)
                    for pattern in request.event_patterns
                ):
                    continue
                result.append(
                    {
                        "source_type": "notification",
                        "source_id": row.id,
                        "occurred_at": row.created_at,
                        "content": {
                            "workspace_id": row.workspace_id,
                            "recipient_actor_id": row.recipient_actor_id,
                            "event_type": row.event_type,
                            "source_type": row.source_type,
                            "source_id": row.source_id,
                            "severity": row.severity,
                            "priority": row.priority,
                            "title": row.title,
                            "body": row.body,
                            "payload": row.payload_json,
                            "status": row.status,
                            "ack_status": row.ack_status,
                            "created_at": iso(row.created_at),
                        },
                    }
                )
        return result[: request.max_items]

    def _eligible_records(
        self,
        *,
        workspace_id: str | None,
        cutoffs: dict[str, datetime],
        batch_size: int,
    ) -> dict[str, list[str]]:
        with self._session_factory() as session:
            security = select(HumanControlSecurityEventModel.id).where(
                HumanControlSecurityEventModel.created_at < cutoffs["security_event"]
            )
            notifications = select(HumanControlNotificationModel.id).where(
                HumanControlNotificationModel.created_at < cutoffs["notification"],
                HumanControlNotificationModel.status.in_(TERMINAL_NOTIFICATION_STATUSES),
            )
            reports = select(HumanControlComplianceReportModel.id).where(
                HumanControlComplianceReportModel.generated_at < cutoffs["compliance_report"]
            )
            audits = select(HumanControlOperatorAuditEventModel.id).where(
                HumanControlOperatorAuditEventModel.occurred_at < cutoffs["operator_audit"]
            )
            if workspace_id is not None:
                security = security.where(HumanControlSecurityEventModel.workspace_id == workspace_id)
                notifications = notifications.where(HumanControlNotificationModel.workspace_id == workspace_id)
                reports = reports.where(HumanControlComplianceReportModel.workspace_id == workspace_id)
                audits = audits.where(HumanControlOperatorAuditEventModel.workspace_id == workspace_id)
            return {
                "security_event": list(session.scalars(security.limit(batch_size)).all()),
                "notification": list(session.scalars(notifications.limit(batch_size)).all()),
                # Immutable evidence is never physically deleted. It is shown in preview
                # so an operator can archive it or extend the retention rule.
                "compliance_report": list(session.scalars(reports.limit(batch_size)).all()),
                "operator_audit": list(session.scalars(audits.limit(batch_size)).all()),
            }

    def _partition_held(
        self,
        *,
        workspace_id: str | None,
        records: dict[str, list[str]],
        legal_hold_override_enabled: bool,
    ) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        held = {key: [] for key in records}
        deletable = {key: [] for key in records}
        if not legal_hold_override_enabled:
            return held, {key: list(values) for key, values in records.items()}
        now = utc_now()
        with self._session_factory() as session:
            holds = session.scalars(
                select(HumanControlLegalHoldModel).where(
                    HumanControlLegalHoldModel.status == "active",
                    (HumanControlLegalHoldModel.workspace_id == workspace_id)
                    | (HumanControlLegalHoldModel.workspace_id.is_(None)),
                    (HumanControlLegalHoldModel.expires_at.is_(None))
                    | (HumanControlLegalHoldModel.expires_at > now),
                )
            ).all()
        for source_type, ids in records.items():
            for source_id in ids:
                is_held = any(
                    self._hold_matches(hold, source_type, source_id)
                    for hold in holds
                )
                (held if is_held else deletable)[source_type].append(source_id)
        # Append-only evidence is retained regardless of hold state.
        for immutable_type in ("operator_audit", "compliance_report"):
            held[immutable_type].extend(deletable[immutable_type])
            deletable[immutable_type] = []
        return held, deletable

    @staticmethod
    def _hold_matches(
        hold: HumanControlLegalHoldModel,
        source_type: str,
        source_id: str,
    ) -> bool:
        types = set(hold.target_types_json or [])
        ids = set(hold.target_ids_json or [])
        if ids and source_id in ids:
            return True
        if types and source_type in types:
            return True
        if not types and not ids:
            return True
        return False

    def _delete_records(self, records: dict[str, list[str]]) -> dict[str, int]:
        deleted = {key: 0 for key in records}
        with self._session_factory() as session:
            for row_id in records.get("security_event", []):
                row = session.get(HumanControlSecurityEventModel, row_id)
                if row is not None:
                    session.delete(row)
                    deleted["security_event"] += 1
            for row_id in records.get("notification", []):
                row = session.get(HumanControlNotificationModel, row_id)
                if row is not None:
                    session.delete(row)
                    deleted["notification"] += 1
        return deleted

    def _ensure_policy_row(
        self,
        workspace_id: str | None,
        actor_id: str,
    ) -> HumanControlRetentionPolicyModel:
        row = self._effective_policy_row(workspace_id)
        if row is not None:
            return row
        scope = _scope_key(workspace_id, "retention")
        with self._session_factory() as session:
            row = HumanControlRetentionPolicyModel(
                scope_key=scope,
                workspace_id=workspace_id,
                enabled=False,
                enforcement_mode="observe",
                updated_by=actor_id,
            )
            session.add(row)
            session.flush()
            session.expunge(row)
            return row

    def _effective_policy_row(
        self,
        workspace_id: str | None,
    ) -> HumanControlRetentionPolicyModel | None:
        with self._session_factory() as session:
            if workspace_id is not None:
                row = session.scalar(
                    select(HumanControlRetentionPolicyModel).where(
                        HumanControlRetentionPolicyModel.workspace_id == workspace_id
                    )
                )
                if row is not None:
                    session.expunge(row)
                    return row
            row = session.scalar(
                select(HumanControlRetentionPolicyModel).where(
                    HumanControlRetentionPolicyModel.workspace_id.is_(None)
                )
            )
            if row is not None:
                session.expunge(row)
            return row

    def _expire_holds(self) -> None:
        now = utc_now()
        with self._session_factory() as session:
            rows = session.scalars(
                select(HumanControlLegalHoldModel).where(
                    HumanControlLegalHoldModel.status == "active",
                    HumanControlLegalHoldModel.expires_at.is_not(None),
                    HumanControlLegalHoldModel.expires_at <= now,
                )
            ).all()
            for row in rows:
                row.status = "expired"

    def _expire_packages(self) -> None:
        now = utc_now()
        with self._session_factory() as session:
            rows = session.scalars(
                select(HumanControlExternalAuditPackageModel).where(
                    HumanControlExternalAuditPackageModel.status == "sealed",
                    HumanControlExternalAuditPackageModel.access_expires_at.is_not(None),
                    HumanControlExternalAuditPackageModel.access_expires_at <= now,
                )
            ).all()
            for row in rows:
                row.status = "expired"

    @staticmethod
    def _policy_to_dict(row: HumanControlRetentionPolicyModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope_key": row.scope_key,
            "workspace_id": row.workspace_id,
            "enabled": row.enabled,
            "enforcement_mode": row.enforcement_mode,
            "default_retention_days": row.default_retention_days,
            "security_event_retention_days": row.security_event_retention_days,
            "notification_retention_days": row.notification_retention_days,
            "compliance_report_retention_days": row.compliance_report_retention_days,
            "operator_audit_retention_days": row.operator_audit_retention_days,
            "archive_before_purge": row.archive_before_purge,
            "require_human_approval": row.require_human_approval,
            "purge_batch_size": row.purge_batch_size,
            "legal_hold_override_enabled": row.legal_hold_override_enabled,
            "metadata": row.metadata_json,
            "updated_by": row.updated_by,
            "last_evaluated_at": iso(row.last_evaluated_at),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _hold_to_dict(row: HumanControlLegalHoldModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope_key": row.scope_key,
            "workspace_id": row.workspace_id,
            "hold_key": row.hold_key,
            "title": row.title,
            "reason": row.reason,
            "status": row.status,
            "target_types": row.target_types_json,
            "target_ids": row.target_ids_json,
            "event_patterns": row.event_patterns_json,
            "actor_ids": row.actor_ids_json,
            "custodian_ids": row.custodian_ids_json,
            "period_start": iso(row.period_start),
            "period_end": iso(row.period_end),
            "expires_at": iso(row.expires_at),
            "created_by": row.created_by,
            "released_by": row.released_by,
            "release_reason": row.release_reason,
            "metadata": row.metadata_json,
            "created_at": iso(row.created_at),
            "released_at": iso(row.released_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _archive_to_dict(row: HumanControlEvidenceArchiveModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope_key": row.scope_key,
            "workspace_id": row.workspace_id,
            "archive_key": row.archive_key,
            "archive_type": row.archive_type,
            "title": row.title,
            "description": row.description,
            "status": row.status,
            "legal_hold_id": row.legal_hold_id,
            "period_start": iso(row.period_start),
            "period_end": iso(row.period_end),
            "source_types": row.source_types_json,
            "item_count": row.item_count,
            "manifest": row.manifest_json,
            "manifest_hash": row.manifest_hash,
            "previous_archive_hash": row.previous_archive_hash,
            "archive_hash": row.archive_hash,
            "created_by": row.created_by,
            "sealed_by": row.sealed_by,
            "revoked_by": row.revoked_by,
            "revoke_reason": row.revoke_reason,
            "metadata": row.metadata_json,
            "created_at": iso(row.created_at),
            "sealed_at": iso(row.sealed_at),
            "revoked_at": iso(row.revoked_at),
        }

    @staticmethod
    def _archive_item_to_dict(row: HumanControlEvidenceArchiveItemModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "archive_id": row.archive_id,
            "ordinal": row.ordinal,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "occurred_at": iso(row.occurred_at),
            "content": row.content_json,
            "content_hash": row.content_hash,
            "created_at": iso(row.created_at),
        }

    @staticmethod
    def _run_to_dict(row: HumanControlRetentionRunModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "policy_id": row.policy_id,
            "workspace_id": row.workspace_id,
            "idempotency_key": row.idempotency_key,
            "run_mode": row.run_mode,
            "status": row.status,
            "started_by": row.started_by,
            "cutoffs": row.cutoff_json,
            "eligible": row.eligible_json,
            "held": row.held_json,
            "archived": row.archived_json,
            "deleted": row.deleted_json,
            "errors": row.errors_json,
            "evidence_archive_id": row.evidence_archive_id,
            "metadata": row.metadata_json,
            "started_at": iso(row.started_at),
            "completed_at": iso(row.completed_at),
        }

    @staticmethod
    def _package_to_dict(row: HumanControlExternalAuditPackageModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope_key": row.scope_key,
            "workspace_id": row.workspace_id,
            "package_key": row.package_key,
            "title": row.title,
            "auditor_name": row.auditor_name,
            "status": row.status,
            "archive_ids": row.archive_ids_json,
            "report_ids": row.report_ids_json,
            "manifest": row.manifest_json,
            "package_hash": row.package_hash,
            "generated_by": row.generated_by,
            "access_expires_at": iso(row.access_expires_at),
            "sealed_at": iso(row.sealed_at),
            "revoked_at": iso(row.revoked_at),
            "revoked_by": row.revoked_by,
            "revoke_reason": row.revoke_reason,
            "metadata": row.metadata_json,
            "created_at": iso(row.created_at),
        }

    async def _publish(
        self,
        event_type: str,
        workspace_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="human_control.retention",
                workspace_id=workspace_id,
                payload=redact_json(payload),
            )
        )
