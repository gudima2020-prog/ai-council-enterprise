from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.control_center import models as control_models  # noqa: F401
from backend.control_center.compliance import HumanControlComplianceService
from backend.control_center.retention import (
    HumanControlEvidenceImmutabilityError,
    HumanControlRetentionService,
)
from backend.control_center.retention_schemas import (
    HumanControlEvidenceArchiveCreate,
    HumanControlExternalAuditPackageCreate,
    HumanControlLegalHoldCreate,
    HumanControlRetentionPolicyUpsert,
    HumanControlRetentionRunRequest,
)
from backend.core.events import Event, EventBus
from backend.database import models as database_models
from backend.database.base import Base
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401


def make_scope():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    @contextmanager
    def scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    with scope() as session:
        session.add(
            database_models.WorkspaceModel(
                id="workspace_retention",
                name="Retention Test",
            )
        )
    return scope


@pytest.mark.asyncio
async def test_legal_hold_prevents_retention_deletion() -> None:
    scope = make_scope()
    bus = EventBus()
    service = HumanControlRetentionService(event_bus=bus, session_factory=scope)
    await service.upsert_policy(
        HumanControlRetentionPolicyUpsert(
            workspace_id="workspace_retention",
            enabled=True,
            enforcement_mode="purge",
            security_event_retention_days=1,
            notification_retention_days=1,
            compliance_report_retention_days=1,
            operator_audit_retention_days=1,
            archive_before_purge=True,
            require_human_approval=False,
            actor_id="owner",
        )
    )
    with scope() as session:
        row = control_models.HumanControlSecurityEventModel(
            workspace_id="workspace_retention",
            actor_id="owner",
            event_type="old.security.event",
            success=True,
            details_json={"value": 1},
        )
        row.created_at = control_models.utc_now() - timedelta(days=30)
        session.add(row)
        session.flush()
        event_id = row.id

    await service.create_legal_hold(
        HumanControlLegalHoldCreate(
            workspace_id="workspace_retention",
            hold_key="case-001",
            title="Case 001",
            reason="Материалы нужны для проверки.",
            target_types=["security_event"],
            created_by="owner",
        )
    )
    result = await service.run_retention(
        HumanControlRetentionRunRequest(
            workspace_id="workspace_retention",
            apply=True,
            actor_id="owner",
            reason="Retention test",
            idempotency_key="retention-run-001",
        )
    )
    assert result["held"]["security_event"] == 1
    assert result["deleted"]["security_event"] == 0
    with scope() as session:
        assert session.get(control_models.HumanControlSecurityEventModel, event_id) is not None


@pytest.mark.asyncio
async def test_hash_sealed_archive_verifies_and_is_immutable() -> None:
    scope = make_scope()
    bus = EventBus()
    compliance = HumanControlComplianceService(event_bus=bus, session_factory=scope)
    await compliance.record_event(
        Event(
            id="retention-audit-event-001",
            event_type="human_control.item.approved",
            source="test",
            workspace_id="workspace_retention",
            payload={"actor_id": "owner", "item_id": "item-001"},
        )
    )
    service = HumanControlRetentionService(event_bus=bus, session_factory=scope)
    archive = await service.create_archive(
        HumanControlEvidenceArchiveCreate(
            workspace_id="workspace_retention",
            archive_key="monthly-2026-07",
            archive_type="audit",
            title="Monthly archive",
            source_types=["operator_audit"],
            created_by="owner",
        )
    )
    assert archive["status"] == "sealed"
    assert archive["item_count"] == 1
    assert service.verify_archive(archive["id"])["valid"] is True

    with pytest.raises(HumanControlEvidenceImmutabilityError):
        with scope() as session:
            row = session.get(control_models.HumanControlEvidenceArchiveModel, archive["id"])
            row.title = "tampered"


@pytest.mark.asyncio
async def test_external_auditor_package_is_hash_sealed() -> None:
    scope = make_scope()
    bus = EventBus()
    compliance = HumanControlComplianceService(event_bus=bus, session_factory=scope)
    await compliance.record_event(
        Event(
            id="retention-audit-event-002",
            event_type="human_control.auth.login.succeeded",
            source="test",
            workspace_id="workspace_retention",
            payload={"actor_id": "owner"},
        )
    )
    service = HumanControlRetentionService(event_bus=bus, session_factory=scope)
    archive = await service.create_archive(
        HumanControlEvidenceArchiveCreate(
            workspace_id="workspace_retention",
            archive_key="external-audit-source",
            archive_type="external_audit",
            title="External audit evidence",
            source_types=["operator_audit"],
            created_by="owner",
        )
    )
    package = await service.create_external_package(
        HumanControlExternalAuditPackageCreate(
            workspace_id="workspace_retention",
            package_key="auditor-2026-07",
            title="Auditor package",
            auditor_name="External Auditor",
            archive_ids=[archive["id"]],
            generated_by="owner",
        )
    )
    exported = service.export_external_package(
        package["id"],
        include_archive_items=True,
    )
    assert package["status"] == "sealed"
    assert len(package["package_hash"]) == 64
    assert exported["package"]["package_hash"] == package["package_hash"]
    assert len(exported["export_hash"]) == 64
    assert exported["archives"][0]["items"][0]["source_type"] == "operator_audit"


def test_retention_default_is_non_destructive() -> None:
    scope = make_scope()
    service = HumanControlRetentionService(event_bus=EventBus(), session_factory=scope)
    policy = service.get_policy("workspace_retention")
    assert policy["enabled"] is False
    assert policy["enforcement_mode"] == "observe"
    assert policy["archive_before_purge"] is True
