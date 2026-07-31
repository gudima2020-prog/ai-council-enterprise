from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy.orm import Session

from backend.core.events import Event, EventBus
from backend.database.models import WorkspaceModel
from backend.policy_approvals.core import (
    DEFAULT_POLICY_APPROVAL_TTL_SECONDS,
    PolicyApprovalCore,
    PolicyApprovalExpiredError,
    PolicyApprovalGrant,
    PolicyApprovalRecord,
    PolicyApprovalScope,
    PolicyApprovalStatus,
)
from backend.policy_approvals.repository import PolicyApprovalRepository
from backend.runtime_policy import PolicyOperation


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PolicyApprovalServiceError(ValueError):
    pass


class PolicyApprovalNotFoundError(PolicyApprovalServiceError):
    pass


class PolicyApprovalWorkspaceError(PolicyApprovalServiceError):
    pass


@dataclass(frozen=True)
class PolicyApprovalRequestResult:
    record: PolicyApprovalRecord
    created: bool


class PolicyApprovalService:
    def __init__(self, *, session: Session, event_bus: EventBus) -> None:
        self._session = session
        self._event_bus = event_bus
        self._repository = PolicyApprovalRepository(session)

    async def request(
        self,
        *,
        scope: PolicyApprovalScope,
        reason_codes: Iterable[str],
        requested_by: str | None = None,
        request_note: str | None = None,
        ttl_seconds: int = DEFAULT_POLICY_APPROVAL_TTL_SECONDS,
        metadata: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> PolicyApprovalRequestResult:
        workspace_id = self._scope_workspace(scope)
        self._require_workspace(workspace_id)
        current_time = now or utc_now()

        existing = self._repository.find_active(
            workspace_id=workspace_id,
            scope_fingerprint=scope.fingerprint,
        )
        if existing is not None:
            existing_record = self._repository.to_record(existing)
            normalized = PolicyApprovalCore.expire(
                existing_record,
                now=current_time,
            )
            if normalized.status == PolicyApprovalStatus.EXPIRED:
                self._repository.save(existing, normalized)
                self._append_expired(normalized)
                await self._publish("policy_approval.expired", normalized)
            else:
                return PolicyApprovalRequestResult(
                    record=existing_record,
                    created=False,
                )

        record = PolicyApprovalCore.request(
            scope=scope,
            reason_codes=reason_codes,
            requested_by=requested_by,
            request_note=request_note,
            ttl_seconds=ttl_seconds,
            metadata=metadata,
            now=current_time,
        )
        self._repository.create(record)
        self._repository.append_evidence(
            record=record,
            event_type="requested",
            actor_id=record.requested_by,
            payload={
                "operation": record.scope.operation.value,
                "policy_version": record.scope.policy_version,
                "policy_fingerprint": record.scope.policy_fingerprint,
                "scope_fingerprint": record.scope_fingerprint,
                "subject_type": record.scope.subject_type,
                "subject_id": record.scope.subject_id,
                "reason_codes": list(record.reason_codes),
                "expires_at": record.expires_at.isoformat(),
            },
            occurred_at=record.requested_at,
        )
        await self._publish("policy_approval.requested", record)
        return PolicyApprovalRequestResult(record=record, created=True)

    def get(self, *, approval_id: str, workspace_id: str) -> PolicyApprovalRecord:
        row = self._require_row(approval_id, workspace_id)
        return self._repository.to_record(row)

    def list(
        self,
        *,
        workspace_id: str,
        status: PolicyApprovalStatus | None = None,
        operation: PolicyOperation | None = None,
        subject_type: str | None = None,
        subject_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PolicyApprovalRecord]:
        self._require_workspace(workspace_id)
        return [
            self._repository.to_record(row)
            for row in self._repository.list(
                workspace_id=workspace_id,
                status=status,
                operation=operation,
                subject_type=subject_type,
                subject_id=subject_id,
                limit=limit,
                offset=offset,
            )
        ]

    async def approve(
        self,
        *,
        approval_id: str,
        workspace_id: str,
        decided_by: str,
        note: str | None = None,
        now: datetime | None = None,
    ) -> PolicyApprovalGrant:
        row = self._require_row(approval_id, workspace_id)
        try:
            grant = PolicyApprovalCore.approve(
                self._repository.to_record(row),
                decided_by=decided_by,
                note=note,
                now=now or utc_now(),
            )
        except PolicyApprovalExpiredError as exc:
            await self._persist_expired_exception(row, exc)
            raise

        self._repository.save(row, grant.record)
        self._repository.append_evidence(
            record=grant.record,
            event_type="approved",
            actor_id=grant.record.decided_by,
            payload={
                "decision_note": grant.record.decision_note,
                "scope_fingerprint": grant.record.scope_fingerprint,
                "capability_issued": True,
            },
            occurred_at=grant.record.decided_at,
        )
        await self._publish("policy_approval.approved", grant.record)
        return grant

    async def deny(
        self,
        *,
        approval_id: str,
        workspace_id: str,
        decided_by: str,
        note: str | None = None,
        now: datetime | None = None,
    ) -> PolicyApprovalRecord:
        row = self._require_row(approval_id, workspace_id)
        try:
            record = PolicyApprovalCore.deny(
                self._repository.to_record(row),
                decided_by=decided_by,
                note=note,
                now=now or utc_now(),
            )
        except PolicyApprovalExpiredError as exc:
            await self._persist_expired_exception(row, exc)
            raise

        self._repository.save(row, record)
        self._repository.append_evidence(
            record=record,
            event_type="denied",
            actor_id=record.decided_by,
            payload={
                "decision_note": record.decision_note,
                "scope_fingerprint": record.scope_fingerprint,
            },
            occurred_at=record.decided_at,
        )
        await self._publish("policy_approval.denied", record)
        return record

    async def revoke(
        self,
        *,
        approval_id: str,
        workspace_id: str,
        revoked_by: str,
        note: str | None = None,
        now: datetime | None = None,
    ) -> PolicyApprovalRecord:
        row = self._require_row(approval_id, workspace_id)
        try:
            record = PolicyApprovalCore.revoke(
                self._repository.to_record(row),
                revoked_by=revoked_by,
                note=note,
                now=now or utc_now(),
            )
        except PolicyApprovalExpiredError as exc:
            await self._persist_expired_exception(row, exc)
            raise

        self._repository.save(row, record)
        self._repository.append_evidence(
            record=record,
            event_type="revoked",
            actor_id=record.revoked_by,
            payload={
                "revocation_note": record.revocation_note,
                "scope_fingerprint": record.scope_fingerprint,
            },
            occurred_at=record.revoked_at,
        )
        await self._publish("policy_approval.revoked", record)
        return record

    async def consume(
        self,
        *,
        approval_id: str,
        workspace_id: str,
        token: str,
        scope: PolicyApprovalScope,
        now: datetime | None = None,
    ) -> PolicyApprovalRecord:
        row = self._require_row(approval_id, workspace_id)
        try:
            record = PolicyApprovalCore.consume(
                self._repository.to_record(row),
                token=token,
                scope=scope,
                now=now or utc_now(),
            )
        except PolicyApprovalExpiredError as exc:
            await self._persist_expired_exception(row, exc)
            raise

        self._repository.save(row, record)
        self._repository.append_evidence(
            record=record,
            event_type="consumed",
            actor_id=None,
            payload={
                "operation": record.scope.operation.value,
                "scope_fingerprint": record.scope_fingerprint,
                "subject_type": record.scope.subject_type,
                "subject_id": record.scope.subject_id,
            },
            occurred_at=record.consumed_at,
        )
        await self._publish("policy_approval.consumed", record)
        return record

    async def reconcile_expired(
        self,
        *,
        workspace_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if workspace_id is not None:
            self._require_workspace(workspace_id)
        current_time = now or utc_now()
        approval_ids: list[str] = []

        for row in self._repository.rows_due_for_expiry(
            workspace_id=workspace_id,
            now=current_time,
        ):
            current = self._repository.to_record(row)
            record = PolicyApprovalCore.expire(current, now=current_time)
            if record.status != PolicyApprovalStatus.EXPIRED:
                continue
            self._repository.save(row, record)
            self._append_expired(record)
            approval_ids.append(record.id)
            await self._publish("policy_approval.expired", record)

        return {
            "workspace_id": workspace_id,
            "expired_count": len(approval_ids),
            "approval_ids": approval_ids,
        }

    def evidence(
        self,
        *,
        approval_id: str,
        workspace_id: str,
    ) -> list[dict[str, Any]]:
        self._require_row(approval_id, workspace_id)
        return [
            self._repository.public_evidence(row)
            for row in self._repository.list_evidence(
                approval_id=approval_id,
                workspace_id=workspace_id,
            )
        ]

    def verify_evidence_chain(self) -> bool:
        return self._repository.verify_evidence_chain()

    def _append_expired(self, record: PolicyApprovalRecord) -> None:
        self._repository.append_evidence(
            record=record,
            event_type="expired",
            actor_id=None,
            payload={
                "reason": "ttl_elapsed",
                "scope_fingerprint": record.scope_fingerprint,
            },
            occurred_at=record.expired_at,
        )

    async def _persist_expired_exception(self, row, exc) -> None:
        record = exc.record
        if record is None or record.status != PolicyApprovalStatus.EXPIRED:
            return
        self._repository.save(row, record)
        self._append_expired(record)
        await self._publish("policy_approval.expired", record)

    def _require_workspace(self, workspace_id: str) -> None:
        if not workspace_id:
            raise PolicyApprovalWorkspaceError(
                "Policy approvals require a Workspace."
            )
        if self._session.get(WorkspaceModel, workspace_id) is None:
            raise PolicyApprovalWorkspaceError("Workspace not found.")

    def _require_row(self, approval_id: str, workspace_id: str):
        self._require_workspace(workspace_id)
        row = self._repository.get(approval_id, workspace_id)
        if row is None:
            raise PolicyApprovalNotFoundError(
                "Policy approval not found in this Workspace."
            )
        return row

    @staticmethod
    def _scope_workspace(scope: PolicyApprovalScope) -> str:
        if scope.workspace_id is None:
            raise PolicyApprovalWorkspaceError(
                "Policy approvals require a Workspace."
            )
        return scope.workspace_id

    async def _publish(
        self,
        event_type: str,
        record: PolicyApprovalRecord,
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="policy_approval_service",
                workspace_id=record.scope.workspace_id,
                payload={
                    "approval_id": record.id,
                    "workspace_id": record.scope.workspace_id,
                    "status": record.status.value,
                    "operation": record.scope.operation.value,
                    "policy_version": record.scope.policy_version,
                    "policy_fingerprint": record.scope.policy_fingerprint,
                    "scope_fingerprint": record.scope_fingerprint,
                    "subject_type": record.scope.subject_type,
                    "subject_id": record.scope.subject_id,
                    "reason_codes": list(record.reason_codes),
                    "expires_at": record.expires_at.isoformat(),
                },
            )
        )
