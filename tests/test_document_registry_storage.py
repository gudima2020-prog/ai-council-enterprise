from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database.models import WorkspaceModel
from backend.documents import (
    DocumentIntakeRequest,
    DocumentRegistryService,
    DocumentStorageError,
    DocumentWorkspaceError,
    ManagedDocumentStorage,
)
from backend.documents.models import (
    DocumentEventModel,
    DocumentModel,
)
from backend.runtime_policy import DataClassification


NOW = datetime(2026, 8, 4, 13, 0, tzinfo=timezone.utc)
PDF_BYTES = b"%PDF-1.7\nregistry test\n%%EOF"


def make_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
    )
    Base.metadata.create_all(engine)
    return engine


def add_workspace(
    session: Session,
    workspace_id: str,
) -> None:
    session.add(
        WorkspaceModel(
            id=workspace_id,
            name=f"Documents {workspace_id}",
            description="",
            workspace_type="documents",
            status="active",
            metadata_json={},
        )
    )
    session.flush()


def request(
    workspace_id: str,
    filename: str = "report.pdf",
) -> DocumentIntakeRequest:
    return DocumentIntakeRequest(
        workspace_id=workspace_id,
        filename=filename,
        content_type="application/pdf",
        classification=DataClassification.CONFIDENTIAL,
    )


def service(
    session: Session,
    tmp_path: Path,
    event_bus: EventBus | None = None,
) -> DocumentRegistryService:
    return DocumentRegistryService(
        session=session,
        event_bus=event_bus or EventBus(),
        storage=ManagedDocumentStorage(
            tmp_path / "documents"
        ),
    )


@pytest.mark.asyncio
async def test_upload_persists_registry_blob_and_safe_event(
    tmp_path: Path,
) -> None:
    engine = make_engine()
    bus = EventBus()
    with Session(engine) as session:
        add_workspace(session, "workspace_a")
        registry = service(session, tmp_path, bus)

        result = await registry.upload(
            request=request("workspace_a"),
            content=PDF_BYTES,
            actor_id="operator",
            metadata={"source": "manual"},
            now=NOW,
        )

        assert result.created is True
        assert result.restored is False
        assert result.storage_created is True
        row = session.get(
            DocumentModel,
            result.record.id,
        )
        assert row is not None
        assert row.status == "active"
        assert row.classification == "confidential"
        assert registry.read_content(
            document_id=row.id,
            workspace_id="workspace_a",
        ) == PDF_BYTES
        events = registry.events(
            document_id=row.id,
            workspace_id="workspace_a",
        )
        assert [item["event_type"] for item in events] == [
            "document.uploaded"
        ]
        assert "original_filename" not in events[0]["payload"]
        assert "content" not in events[0]["payload"]
        assert registry.verify_event_chain() is True
        history = bus.history()
        assert history[-1]["event"]["event_type"] == (
            "document.uploaded"
        )
        assert PDF_BYTES not in str(history).encode("utf-8")
    engine.dispose()


@pytest.mark.asyncio
async def test_same_active_upload_is_idempotent(
    tmp_path: Path,
) -> None:
    engine = make_engine()
    with Session(engine) as session:
        add_workspace(session, "workspace_a")
        registry = service(session, tmp_path)

        first = await registry.upload(
            request=request("workspace_a"),
            content=PDF_BYTES,
            now=NOW,
        )
        second = await registry.upload(
            request=request("workspace_a"),
            content=PDF_BYTES,
            now=NOW,
        )

        assert first.record.id == second.record.id
        assert second.created is False
        assert second.restored is False
        assert len(
            list(
                session.scalars(
                    select(DocumentModel)
                ).all()
            )
        ) == 1
        assert len(
            list(
                session.scalars(
                    select(DocumentEventModel)
                ).all()
            )
        ) == 1
    engine.dispose()


@pytest.mark.asyncio
async def test_registry_is_workspace_isolated(
    tmp_path: Path,
) -> None:
    engine = make_engine()
    with Session(engine) as session:
        add_workspace(session, "workspace_a")
        add_workspace(session, "workspace_b")
        registry = service(session, tmp_path)

        uploaded = await registry.upload(
            request=request("workspace_a"),
            content=PDF_BYTES,
            now=NOW,
        )

        assert len(
            registry.list(workspace_id="workspace_a")
        ) == 1
        assert registry.list(
            workspace_id="workspace_b"
        ) == []
        with pytest.raises(Exception) as captured:
            registry.get(
                document_id=uploaded.record.id,
                workspace_id="workspace_b",
            )
        assert getattr(
            captured.value,
            "code",
            None,
        ) == "DOCUMENT_NOT_FOUND"
    engine.dispose()


@pytest.mark.asyncio
async def test_delete_removes_unshared_blob_and_keeps_tombstone(
    tmp_path: Path,
) -> None:
    engine = make_engine()
    with Session(engine) as session:
        add_workspace(session, "workspace_a")
        registry = service(session, tmp_path)
        uploaded = await registry.upload(
            request=request("workspace_a"),
            content=PDF_BYTES,
            now=NOW,
        )
        storage_path = registry._storage.resolve(
            uploaded.record.storage_key
        )
        assert storage_path.is_file()

        deleted = await registry.delete(
            document_id=uploaded.record.id,
            workspace_id="workspace_a",
            actor_id="operator",
            now=NOW,
        )

        assert deleted.deleted is True
        assert deleted.storage_deleted is True
        assert not storage_path.exists()
        assert registry.list(
            workspace_id="workspace_a"
        ) == []
        tombstone = registry.get(
            document_id=uploaded.record.id,
            workspace_id="workspace_a",
            include_deleted=True,
        )
        assert tombstone.status == "deleted"
        assert tombstone.storage_state == "released"
        assert [
            item["event_type"]
            for item in registry.events(
                document_id=uploaded.record.id,
                workspace_id="workspace_a",
            )
        ] == [
            "document.uploaded",
            "document.deleted",
        ]
    engine.dispose()


@pytest.mark.asyncio
async def test_shared_blob_is_removed_after_last_reference(
    tmp_path: Path,
) -> None:
    engine = make_engine()
    with Session(engine) as session:
        add_workspace(session, "workspace_a")
        add_workspace(session, "workspace_b")
        registry = service(session, tmp_path)
        first = await registry.upload(
            request=request("workspace_a"),
            content=PDF_BYTES,
            now=NOW,
        )
        second = await registry.upload(
            request=request("workspace_b"),
            content=PDF_BYTES,
            now=NOW,
        )
        assert (
            first.record.storage_key
            == second.record.storage_key
        )
        storage_path = registry._storage.resolve(
            first.record.storage_key
        )

        first_delete = await registry.delete(
            document_id=first.record.id,
            workspace_id="workspace_a",
            now=NOW,
        )
        assert first_delete.storage_deleted is False
        assert storage_path.is_file()

        second_delete = await registry.delete(
            document_id=second.record.id,
            workspace_id="workspace_b",
            now=NOW,
        )
        assert second_delete.storage_deleted is True
        assert not storage_path.exists()
    engine.dispose()


@pytest.mark.asyncio
async def test_deleted_document_can_be_restored(
    tmp_path: Path,
) -> None:
    engine = make_engine()
    with Session(engine) as session:
        add_workspace(session, "workspace_a")
        registry = service(session, tmp_path)
        uploaded = await registry.upload(
            request=request("workspace_a"),
            content=PDF_BYTES,
            now=NOW,
        )
        await registry.delete(
            document_id=uploaded.record.id,
            workspace_id="workspace_a",
            now=NOW,
        )

        restored = await registry.upload(
            request=request("workspace_a"),
            content=PDF_BYTES,
            actor_id="operator_2",
            now=NOW,
        )

        assert restored.created is False
        assert restored.restored is True
        assert restored.record.id == uploaded.record.id
        assert restored.record.status == "active"
        assert [
            item["event_type"]
            for item in registry.events(
                document_id=uploaded.record.id,
                workspace_id="workspace_a",
            )
        ] == [
            "document.uploaded",
            "document.deleted",
            "document.uploaded",
        ]
    engine.dispose()


@pytest.mark.asyncio
async def test_tampered_blob_fails_closed_and_marks_missing(
    tmp_path: Path,
) -> None:
    engine = make_engine()
    with Session(engine) as session:
        add_workspace(session, "workspace_a")
        registry = service(session, tmp_path)
        uploaded = await registry.upload(
            request=request("workspace_a"),
            content=PDF_BYTES,
            now=NOW,
        )
        path = registry._storage.resolve(
            uploaded.record.storage_key
        )
        path.write_bytes(b"tampered")

        with pytest.raises(DocumentStorageError) as captured:
            registry.read_content(
                document_id=uploaded.record.id,
                workspace_id="workspace_a",
            )

        assert captured.value.code in {
            "DOCUMENT_STORAGE_HASH_MISMATCH",
            "DOCUMENT_STORAGE_SIZE_MISMATCH",
        }
        row = session.get(
            DocumentModel,
            uploaded.record.id,
        )
        assert row is not None
        assert row.storage_state == "missing"
    engine.dispose()


@pytest.mark.asyncio
async def test_unsafe_metadata_is_rejected(
    tmp_path: Path,
) -> None:
    engine = make_engine()
    with Session(engine) as session:
        add_workspace(session, "workspace_a")
        registry = service(session, tmp_path)

        with pytest.raises(
            ValueError,
            match="Unsafe document metadata key",
        ):
            await registry.upload(
                request=request("workspace_a"),
                content=PDF_BYTES,
                metadata={"raw_content": "blocked"},
                now=NOW,
            )
    engine.dispose()


@pytest.mark.asyncio
async def test_event_chain_detects_tampering(
    tmp_path: Path,
) -> None:
    engine = make_engine()
    with Session(engine) as session:
        add_workspace(session, "workspace_a")
        registry = service(session, tmp_path)
        await registry.upload(
            request=request("workspace_a"),
            content=PDF_BYTES,
            now=NOW,
        )
        event = session.scalar(
            select(DocumentEventModel)
        )
        assert event is not None
        event.payload_json = {"tampered": True}
        session.flush()

        assert registry.verify_event_chain() is False
    engine.dispose()


def test_storage_rejects_path_traversal(
    tmp_path: Path,
) -> None:
    storage = ManagedDocumentStorage(
        tmp_path / "documents"
    )
    with pytest.raises(DocumentStorageError) as captured:
        storage.resolve("../outside")
    assert captured.value.code == (
        "DOCUMENT_STORAGE_KEY_UNSAFE"
    )


def test_storage_rejects_hash_mismatch(
    tmp_path: Path,
) -> None:
    storage = ManagedDocumentStorage(
        tmp_path / "documents"
    )
    with pytest.raises(DocumentStorageError) as captured:
        storage.put(
            content=b"actual",
            expected_sha256="0" * 64,
            extension="pdf",
        )
    assert captured.value.code == (
        "DOCUMENT_STORAGE_HASH_MISMATCH"
    )


@pytest.mark.asyncio
async def test_missing_workspace_fails_before_storage_write(
    tmp_path: Path,
) -> None:
    engine = make_engine()
    with Session(engine) as session:
        registry = service(session, tmp_path)
        with pytest.raises(DocumentWorkspaceError) as captured:
            await registry.upload(
                request=request("missing"),
                content=PDF_BYTES,
                now=NOW,
            )
        assert captured.value.code == (
            "DOCUMENT_WORKSPACE_NOT_FOUND"
        )
        assert not (tmp_path / "documents").exists()
    engine.dispose()


@pytest.mark.asyncio
async def test_deleted_records_are_opt_in_for_list(
    tmp_path: Path,
) -> None:
    engine = make_engine()
    with Session(engine) as session:
        add_workspace(session, "workspace_a")
        registry = service(session, tmp_path)
        uploaded = await registry.upload(
            request=request("workspace_a"),
            content=PDF_BYTES,
            now=NOW,
        )
        await registry.delete(
            document_id=uploaded.record.id,
            workspace_id="workspace_a",
            now=NOW,
        )

        assert registry.list(
            workspace_id="workspace_a"
        ) == []
        assert len(
            registry.list(
                workspace_id="workspace_a",
                include_deleted=True,
            )
        ) == 1
    engine.dispose()
