from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.control_center import models as control_models  # noqa: F401
from backend.control_center.compliance import (
    HumanControlComplianceImmutabilityError,
    HumanControlComplianceService,
)
from backend.control_center.compliance_schemas import (
    HumanControlAccessFindingDecision,
    HumanControlAccessFindingDecisionRequest,
    HumanControlAccessReviewCompleteRequest,
    HumanControlAccessReviewCreate,
    HumanControlComplianceReportCreate,
    HumanControlComplianceReportType,
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
                id="workspace_compliance",
                name="Compliance Test",
            )
        )
    return scope


@pytest.mark.asyncio
async def test_operator_audit_chain_is_idempotent_redacted_and_immutable() -> None:
    scope = make_scope()
    service = HumanControlComplianceService(
        event_bus=EventBus(),
        session_factory=scope,
    )
    event = Event(
        id="event_compliance_001",
        event_type="human_control.auth.login.succeeded",
        source="test",
        workspace_id="workspace_compliance",
        payload={
            "actor_id": "owner",
            "identity_id": "identity_owner",
            "access_token": "must-not-be-stored",
            "session": {"id": "session_001", "auth_method": "password"},
        },
    )
    first = await service.record_event(event)
    second = await service.record_event(event)

    assert first["sequence"] == 1
    assert second["id"] == first["id"]
    assert first["payload"]["access_token"] == "[REDACTED]"
    assert service.verify_chain()["valid"] is True
    assert service.status()["runtime_duplicates"] == 1

    with pytest.raises(HumanControlComplianceImmutabilityError):
        with scope() as session:
            row = session.get(
                control_models.HumanControlOperatorAuditEventModel,
                first["id"],
            )
            row.actor_id = "tampered"


@pytest.mark.asyncio
async def test_compliance_report_has_evidence_hash_and_activity_summary() -> None:
    scope = make_scope()
    service = HumanControlComplianceService(
        event_bus=EventBus(),
        session_factory=scope,
    )
    await service.record_event(
        Event(
            id="event_report_001",
            event_type="human_control.item.approved",
            source="test",
            workspace_id="workspace_compliance",
            payload={
                "actor_id": "owner",
                "item_id": "item_001",
                "risk_level": "high",
            },
        )
    )
    report = await service.generate_report(
        HumanControlComplianceReportCreate(
            workspace_id="workspace_compliance",
            report_type=HumanControlComplianceReportType.FULL,
            generated_by="owner",
        )
    )

    assert len(report["evidence_hash"]) == 64
    assert report["summary"]["audit_entries"] == 1
    assert report["report"]["chain_verification"]["valid"] is True
    assert "activity" in report["report"]["sections"]


@pytest.mark.asyncio
async def test_privileged_access_review_detects_and_revokes_unbounded_token() -> None:
    scope = make_scope()
    with scope() as session:
        identity = control_models.HumanControlIdentityModel(
            actor_id="owner",
            username="owner",
            username_normalized="owner",
            display_name="Owner",
            identity_type="human",
            status="active",
            password_hash="hash",
            password_salt="salt",
            password_iterations=310000,
            created_by="bootstrap",
        )
        role = control_models.HumanControlRoleModel(
            scope_key="workspace:workspace_compliance:owner",
            workspace_id="workspace_compliance",
            role_key="owner",
            name="Owner",
            permissions_json=["*"],
            created_by="bootstrap",
        )
        session.add_all([identity, role])
        session.flush()
        binding = control_models.HumanControlRoleBindingModel(
            binding_key="workspace:workspace_compliance:owner:owner",
            workspace_id="workspace_compliance",
            actor_id="owner",
            role_id=role.id,
            granted_by="bootstrap",
            reason="test",
        )
        token = control_models.HumanControlApiTokenModel(
            identity_id=identity.id,
            workspace_id="workspace_compliance",
            name="Unbounded admin token",
            token_hash="a" * 64,
            token_prefix="hc_api_test",
            scopes_json=["*"],
            status="active",
            expires_at=None,
            created_by="owner",
        )
        session.add_all([binding, token])
        session.flush()
        token_id = token.id

    service = HumanControlComplianceService(
        event_bus=EventBus(),
        session_factory=scope,
    )
    review = await service.start_access_review(
        HumanControlAccessReviewCreate(
            workspace_id="workspace_compliance",
            initiated_by="owner",
            idempotency_key="access-review-001",
        )
    )
    findings = service.list_findings(review_id=review["id"])
    token_finding = next(
        row for row in findings if row["resource_type"] == "api_token"
    )

    resolved = await service.decide_finding(
        token_finding["id"],
        HumanControlAccessFindingDecisionRequest(
            decision=HumanControlAccessFindingDecision.REMEDIATE,
            actor_id="owner",
            reason="Токен больше не нужен.",
            apply_change=True,
        ),
    )
    assert resolved["status"] == "remediated"
    with scope() as session:
        token = session.get(control_models.HumanControlApiTokenModel, token_id)
        assert token.status == "revoked"
        assert token.revoked_by == "owner"

    completed = await service.complete_access_review(
        review["id"],
        HumanControlAccessReviewCompleteRequest(
            actor_id="owner",
            reason="Проверка завершена.",
            force=True,
        ),
    )
    assert completed["status"] == "completed"
    assert len(completed["evidence_hash"]) == 64
