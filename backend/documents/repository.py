from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.documents.models import (
    DocumentEventModel,
    DocumentModel,
)


GENESIS_HASH = "0" * 64
_BLOCKED_EVENT_KEYS = {
    "bytes",
    "content",
    "file_bytes",
    "prompt",
    "raw_content",
    "response",
    "secret",
    "text",
    "token",
}


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


def _assert_safe_event(
    value: Any,
    path: str = "payload",
) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).casefold()
            if normalized in _BLOCKED_EVENT_KEYS:
                raise ValueError(
                    f"Unsafe document event key: {path}.{key}"
                )
            _assert_safe_event(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_safe_event(child, f"{path}[{index}]")


class DocumentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        workspace_id: str,
        original_filename: str,
        safe_filename: str,
        document_format: str,
        declared_mime_type: str,
        detected_mime_type: str,
        content_encoding: str | None,
        size_bytes: int,
        content_sha256: str,
        intake_fingerprint: str,
        classification: str,
        storage_key: str,
        uploaded_by: str | None,
        metadata: dict[str, Any],
        now: datetime,
    ) -> DocumentModel:
        row = DocumentModel(
            workspace_id=workspace_id,
            original_filename=original_filename,
            safe_filename=safe_filename,
            document_format=document_format,
            declared_mime_type=declared_mime_type,
            detected_mime_type=detected_mime_type,
            content_encoding=content_encoding,
            size_bytes=size_bytes,
            content_sha256=content_sha256,
            intake_fingerprint=intake_fingerprint,
            classification=classification,
            storage_key=storage_key,
            storage_state="ready",
            status="active",
            uploaded_by=uploaded_by,
            deleted_by=None,
            deleted_at=None,
            metadata_json=dict(metadata),
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def get(
        self,
        *,
        document_id: str,
        workspace_id: str,
        include_deleted: bool = False,
    ) -> DocumentModel | None:
        statement = select(DocumentModel).where(
            DocumentModel.id == document_id,
            DocumentModel.workspace_id == workspace_id,
        )
        if not include_deleted:
            statement = statement.where(
                DocumentModel.status == "active"
            )
        return self._session.scalar(statement)

    def find_by_intake_fingerprint(
        self,
        *,
        workspace_id: str,
        intake_fingerprint: str,
    ) -> DocumentModel | None:
        return self._session.scalar(
            select(DocumentModel).where(
                DocumentModel.workspace_id == workspace_id,
                DocumentModel.intake_fingerprint
                == intake_fingerprint,
            )
        )

    def list(
        self,
        *,
        workspace_id: str,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentModel]:
        statement = select(DocumentModel).where(
            DocumentModel.workspace_id == workspace_id
        )
        if not include_deleted:
            statement = statement.where(
                DocumentModel.status == "active"
            )
        statement = (
            statement.order_by(
                DocumentModel.created_at.desc(),
                DocumentModel.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        return list(self._session.scalars(statement).all())

    def restore(
        self,
        row: DocumentModel,
        *,
        original_filename: str,
        safe_filename: str,
        declared_mime_type: str,
        detected_mime_type: str,
        content_encoding: str | None,
        storage_key: str,
        uploaded_by: str | None,
        metadata: dict[str, Any],
        now: datetime,
    ) -> DocumentModel:
        row.original_filename = original_filename
        row.safe_filename = safe_filename
        row.declared_mime_type = declared_mime_type
        row.detected_mime_type = detected_mime_type
        row.content_encoding = content_encoding
        row.storage_key = storage_key
        row.storage_state = "ready"
        row.status = "active"
        row.uploaded_by = uploaded_by
        row.deleted_by = None
        row.deleted_at = None
        row.metadata_json = dict(metadata)
        row.updated_at = now
        self._session.flush()
        return row

    def mark_deleted(
        self,
        row: DocumentModel,
        *,
        deleted_by: str | None,
        now: datetime,
    ) -> DocumentModel:
        row.status = "deleted"
        row.storage_state = "released"
        row.deleted_by = deleted_by
        row.deleted_at = now
        row.updated_at = now
        self._session.flush()
        return row

    def mark_missing(
        self,
        row: DocumentModel,
        *,
        now: datetime,
    ) -> DocumentModel:
        row.storage_state = "missing"
        row.updated_at = now
        self._session.flush()
        return row

    def active_storage_references(
        self,
        *,
        storage_key: str,
        excluding_document_id: str | None = None,
    ) -> int:
        statement = select(func.count(DocumentModel.id)).where(
            DocumentModel.storage_key == storage_key,
            DocumentModel.status == "active",
        )
        if excluding_document_id is not None:
            statement = statement.where(
                DocumentModel.id != excluding_document_id
            )
        return int(self._session.scalar(statement) or 0)

    def append_event(
        self,
        *,
        document: DocumentModel,
        event_type: str,
        actor_id: str | None,
        payload: dict[str, Any],
        occurred_at: datetime | None = None,
    ) -> DocumentEventModel:
        safe_payload = json.loads(_canonical(payload))
        _assert_safe_event(safe_payload)

        last = self._session.scalar(
            select(DocumentEventModel)
            .order_by(DocumentEventModel.sequence.desc())
            .limit(1)
        )
        sequence = 1 if last is None else last.sequence + 1
        previous_hash = (
            GENESIS_HASH if last is None else last.event_hash
        )
        timestamp = _aware(occurred_at or utc_now())
        hash_payload = {
            "sequence": sequence,
            "document_id": document.id,
            "workspace_id": document.workspace_id,
            "event_type": event_type,
            "actor_id": actor_id,
            "payload": safe_payload,
            "occurred_at": timestamp.isoformat(),
            "previous_hash": previous_hash,
        }
        event_hash = hashlib.sha256(
            _canonical(hash_payload).encode("utf-8")
        ).hexdigest()

        row = DocumentEventModel(
            sequence=sequence,
            document_id=document.id,
            workspace_id=document.workspace_id,
            event_type=event_type,
            actor_id=actor_id,
            payload_json=safe_payload,
            occurred_at=timestamp,
            previous_hash=previous_hash,
            event_hash=event_hash,
            created_at=timestamp,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def list_events(
        self,
        *,
        document_id: str,
        workspace_id: str,
    ) -> list[DocumentEventModel]:
        return list(
            self._session.scalars(
                select(DocumentEventModel)
                .where(
                    DocumentEventModel.document_id
                    == document_id,
                    DocumentEventModel.workspace_id
                    == workspace_id,
                )
                .order_by(DocumentEventModel.sequence.asc())
            ).all()
        )

    def verify_event_chain(self) -> bool:
        rows = list(
            self._session.scalars(
                select(DocumentEventModel).order_by(
                    DocumentEventModel.sequence.asc()
                )
            ).all()
        )
        expected_previous = GENESIS_HASH
        expected_sequence = 1
        for row in rows:
            if row.sequence != expected_sequence:
                return False
            if not hmac.compare_digest(
                row.previous_hash,
                expected_previous,
            ):
                return False
            hash_payload = {
                "sequence": row.sequence,
                "document_id": row.document_id,
                "workspace_id": row.workspace_id,
                "event_type": row.event_type,
                "actor_id": row.actor_id,
                "payload": row.payload_json or {},
                "occurred_at": _aware(
                    row.occurred_at
                ).isoformat(),
                "previous_hash": row.previous_hash,
            }
            expected_hash = hashlib.sha256(
                _canonical(hash_payload).encode("utf-8")
            ).hexdigest()
            if not hmac.compare_digest(
                row.event_hash,
                expected_hash,
            ):
                return False
            expected_previous = row.event_hash
            expected_sequence += 1
        return True
