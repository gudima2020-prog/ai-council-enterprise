from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.policy_approvals.core import (
    PolicyApprovalRecord,
    PolicyApprovalScope,
    PolicyApprovalStatus,
)
from backend.policy_approvals.models import (
    PolicyApprovalEvidenceModel,
    PolicyApprovalModel,
)
from backend.runtime_policy import PolicyOperation


GENESIS_HASH = "0" * 64
_BLOCKED_EVIDENCE_KEYS = (
    "token",
    "secret",
    "prompt",
    "response",
    "artifact_bytes",
    "raw_content",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _assert_safe_evidence(value: Any, path: str = "payload") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower()
            if any(fragment in normalized for fragment in _BLOCKED_EVIDENCE_KEYS):
                raise ValueError(f"Unsafe policy approval evidence key: {path}.{key}")
            _assert_safe_evidence(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_safe_evidence(child, f"{path}[{index}]")


class PolicyApprovalRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, record: PolicyApprovalRecord) -> PolicyApprovalModel:
        row = PolicyApprovalModel(
            id=record.id,
            workspace_id=record.scope.workspace_id,
            operation=record.scope.operation.value,
            policy_version=record.scope.policy_version,
            policy_fingerprint=record.scope.policy_fingerprint,
            subject_type=record.scope.subject_type,
            subject_id=record.scope.subject_id,
            subject_payload_json=dict(record.scope.subject_payload),
            scope_fingerprint=record.scope_fingerprint,
            reason_codes_json=list(record.reason_codes),
            status=record.status.value,
            requested_by=record.requested_by,
            requested_at=record.requested_at,
            expires_at=record.expires_at,
            request_note=record.request_note,
            token_hash=record.token_hash,
            decided_by=record.decided_by,
            decided_at=record.decided_at,
            decision_note=record.decision_note,
            expired_at=record.expired_at,
            revoked_by=record.revoked_by,
            revoked_at=record.revoked_at,
            revocation_note=record.revocation_note,
            consumed_at=record.consumed_at,
            metadata_json=dict(record.metadata),
            created_at=record.requested_at,
            updated_at=record.updated_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def save(
        self,
        row: PolicyApprovalModel,
        record: PolicyApprovalRecord,
    ) -> PolicyApprovalModel:
        if row.id != record.id:
            raise ValueError("Policy approval identity cannot change.")
        if row.workspace_id != record.scope.workspace_id:
            raise ValueError("Policy approval Workspace cannot change.")

        row.operation = record.scope.operation.value
        row.policy_version = record.scope.policy_version
        row.policy_fingerprint = record.scope.policy_fingerprint
        row.subject_type = record.scope.subject_type
        row.subject_id = record.scope.subject_id
        row.subject_payload_json = dict(record.scope.subject_payload)
        row.scope_fingerprint = record.scope_fingerprint
        row.reason_codes_json = list(record.reason_codes)
        row.status = record.status.value
        row.requested_by = record.requested_by
        row.requested_at = record.requested_at
        row.expires_at = record.expires_at
        row.request_note = record.request_note
        row.token_hash = record.token_hash
        row.decided_by = record.decided_by
        row.decided_at = record.decided_at
        row.decision_note = record.decision_note
        row.expired_at = record.expired_at
        row.revoked_by = record.revoked_by
        row.revoked_at = record.revoked_at
        row.revocation_note = record.revocation_note
        row.consumed_at = record.consumed_at
        row.metadata_json = dict(record.metadata)
        row.updated_at = record.updated_at
        self._session.flush()
        return row

    def get(
        self,
        approval_id: str,
        workspace_id: str,
    ) -> PolicyApprovalModel | None:
        return self._session.scalar(
            select(PolicyApprovalModel).where(
                PolicyApprovalModel.id == approval_id,
                PolicyApprovalModel.workspace_id == workspace_id,
            )
        )

    def find_active(
        self,
        *,
        workspace_id: str,
        scope_fingerprint: str,
    ) -> PolicyApprovalModel | None:
        return self._session.scalar(
            select(PolicyApprovalModel)
            .where(
                PolicyApprovalModel.workspace_id == workspace_id,
                PolicyApprovalModel.scope_fingerprint == scope_fingerprint,
                PolicyApprovalModel.status.in_(
                    (
                        PolicyApprovalStatus.PENDING.value,
                        PolicyApprovalStatus.APPROVED.value,
                    )
                ),
            )
            .order_by(
                PolicyApprovalModel.created_at.desc(),
                PolicyApprovalModel.id.desc(),
            )
            .limit(1)
        )

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
    ) -> list[PolicyApprovalModel]:
        statement = select(PolicyApprovalModel).where(
            PolicyApprovalModel.workspace_id == workspace_id
        )
        if status is not None:
            statement = statement.where(PolicyApprovalModel.status == status.value)
        if operation is not None:
            statement = statement.where(
                PolicyApprovalModel.operation == operation.value
            )
        if subject_type is not None:
            statement = statement.where(
                PolicyApprovalModel.subject_type == subject_type
            )
        if subject_id is not None:
            statement = statement.where(PolicyApprovalModel.subject_id == subject_id)
        statement = (
            statement.order_by(
                PolicyApprovalModel.created_at.desc(),
                PolicyApprovalModel.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        return list(self._session.scalars(statement).all())

    def rows_due_for_expiry(
        self,
        *,
        workspace_id: str | None,
        now: datetime,
    ) -> list[PolicyApprovalModel]:
        statement = select(PolicyApprovalModel).where(
            PolicyApprovalModel.status.in_(
                (
                    PolicyApprovalStatus.PENDING.value,
                    PolicyApprovalStatus.APPROVED.value,
                )
            ),
            PolicyApprovalModel.expires_at <= now,
        )
        if workspace_id is not None:
            statement = statement.where(
                PolicyApprovalModel.workspace_id == workspace_id
            )
        return list(
            self._session.scalars(
                statement.order_by(
                    PolicyApprovalModel.expires_at.asc(),
                    PolicyApprovalModel.id.asc(),
                )
            ).all()
        )

    def to_record(self, row: PolicyApprovalModel) -> PolicyApprovalRecord:
        scope = PolicyApprovalScope(
            workspace_id=row.workspace_id,
            operation=PolicyOperation(row.operation),
            policy_version=row.policy_version,
            policy_fingerprint=row.policy_fingerprint,
            subject_type=row.subject_type,
            subject_id=row.subject_id,
            subject_payload=row.subject_payload_json or {},
        )
        return PolicyApprovalRecord(
            id=row.id,
            scope=scope,
            scope_fingerprint=row.scope_fingerprint,
            reason_codes=tuple(row.reason_codes_json or ()),
            status=PolicyApprovalStatus(row.status),
            requested_by=row.requested_by,
            requested_at=row.requested_at,
            expires_at=row.expires_at,
            request_note=row.request_note,
            token_hash=row.token_hash,
            decided_by=row.decided_by,
            decided_at=row.decided_at,
            decision_note=row.decision_note,
            expired_at=row.expired_at,
            revoked_by=row.revoked_by,
            revoked_at=row.revoked_at,
            revocation_note=row.revocation_note,
            consumed_at=row.consumed_at,
            metadata=row.metadata_json or {},
            updated_at=row.updated_at,
        )

    def append_evidence(
        self,
        *,
        record: PolicyApprovalRecord,
        event_type: str,
        actor_id: str | None,
        payload: dict[str, Any],
        occurred_at: datetime | None = None,
    ) -> PolicyApprovalEvidenceModel:
        safe_payload = json.loads(_canonical(payload))
        _assert_safe_evidence(safe_payload)

        last = self._session.scalar(
            select(PolicyApprovalEvidenceModel)
            .order_by(PolicyApprovalEvidenceModel.sequence.desc())
            .limit(1)
        )
        sequence = 1 if last is None else last.sequence + 1
        previous_hash = GENESIS_HASH if last is None else last.event_hash
        timestamp = _aware(occurred_at or utc_now())
        hash_payload = {
            "sequence": sequence,
            "approval_id": record.id,
            "workspace_id": record.scope.workspace_id,
            "event_type": event_type,
            "actor_id": actor_id,
            "approval_status": record.status.value,
            "payload": safe_payload,
            "occurred_at": timestamp.isoformat(),
            "previous_hash": previous_hash,
        }
        event_hash = hashlib.sha256(
            _canonical(hash_payload).encode("utf-8")
        ).hexdigest()

        row = PolicyApprovalEvidenceModel(
            sequence=sequence,
            approval_id=record.id,
            workspace_id=record.scope.workspace_id,
            event_type=event_type,
            actor_id=actor_id,
            approval_status=record.status.value,
            payload_json=safe_payload,
            occurred_at=timestamp,
            previous_hash=previous_hash,
            event_hash=event_hash,
            created_at=timestamp,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def list_evidence(
        self,
        *,
        approval_id: str,
        workspace_id: str,
    ) -> list[PolicyApprovalEvidenceModel]:
        return list(
            self._session.scalars(
                select(PolicyApprovalEvidenceModel)
                .where(
                    PolicyApprovalEvidenceModel.approval_id == approval_id,
                    PolicyApprovalEvidenceModel.workspace_id == workspace_id,
                )
                .order_by(PolicyApprovalEvidenceModel.sequence.asc())
            ).all()
        )

    @staticmethod
    def public_evidence(row: PolicyApprovalEvidenceModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "sequence": row.sequence,
            "approval_id": row.approval_id,
            "workspace_id": row.workspace_id,
            "event_type": row.event_type,
            "actor_id": row.actor_id,
            "approval_status": row.approval_status,
            "payload": dict(row.payload_json or {}),
            "occurred_at": row.occurred_at,
            "previous_hash": row.previous_hash,
            "event_hash": row.event_hash,
            "created_at": row.created_at,
        }

    def verify_evidence_chain(self) -> bool:
        rows = list(
            self._session.scalars(
                select(PolicyApprovalEvidenceModel).order_by(
                    PolicyApprovalEvidenceModel.sequence.asc()
                )
            ).all()
        )
        expected_previous = GENESIS_HASH
        expected_sequence = 1
        for row in rows:
            if row.sequence != expected_sequence:
                return False
            if not hmac.compare_digest(row.previous_hash, expected_previous):
                return False
            hash_payload = {
                "sequence": row.sequence,
                "approval_id": row.approval_id,
                "workspace_id": row.workspace_id,
                "event_type": row.event_type,
                "actor_id": row.actor_id,
                "approval_status": row.approval_status,
                "payload": row.payload_json or {},
                "occurred_at": _aware(row.occurred_at).isoformat(),
                "previous_hash": row.previous_hash,
            }
            expected_hash = hashlib.sha256(
                _canonical(hash_payload).encode("utf-8")
            ).hexdigest()
            if not hmac.compare_digest(row.event_hash, expected_hash):
                return False
            expected_previous = row.event_hash
            expected_sequence += 1
        return True
