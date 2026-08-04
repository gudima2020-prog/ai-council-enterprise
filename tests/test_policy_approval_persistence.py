from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database.models import WorkspaceModel
from backend.policy_approvals import (
    PolicyApprovalScope,
    PolicyApprovalStateError,
    PolicyApprovalStatus,
    PolicyApprovalTokenError,
)
from backend.policy_approvals.models import (
    PolicyApprovalEvidenceModel,
    PolicyApprovalModel,
)
from backend.policy_approvals.service import (
    PolicyApprovalNotFoundError,
    PolicyApprovalService,
    PolicyApprovalWorkspaceError,
)
from backend.runtime_policy import PolicyOperation


NOW = datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc)


def make_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return engine


def add_workspace(session: Session, workspace_id: str = "workspace_alpha") -> str:
    session.add(
        WorkspaceModel(
            id=workspace_id,
            name=f"Policy {workspace_id}",
            description="",
            workspace_type="general",
            status="active",
            metadata_json={},
        )
    )
    session.flush()
    return workspace_id


def make_scope(
    workspace_id: str = "workspace_alpha",
    *,
    subject_id: str = "request_001",
    model: str = "claude-sonnet",
) -> PolicyApprovalScope:
    return PolicyApprovalScope(
        workspace_id=workspace_id,
        operation=PolicyOperation.MODEL_INFERENCE,
        policy_version="p2-011.1",
        policy_fingerprint="a" * 64,
        subject_type="gateway_route",
        subject_id=subject_id,
        subject_payload={
            "provider": "svrtr",
            "model": model,
            "request_fingerprint": "b" * 64,
        },
    )


@pytest.mark.asyncio
async def test_request_persists_record_and_evidence() -> None:
    engine = make_engine()
    with Session(engine) as session:
        add_workspace(session)
        service = PolicyApprovalService(session=session, event_bus=EventBus())

        result = await service.request(
            scope=make_scope(),
            reason_codes=("CONFIDENTIAL_EXTERNAL_APPROVAL_REQUIRED",),
            requested_by="operator",
            now=NOW,
        )

        assert result.created is True
        assert result.record.status == PolicyApprovalStatus.PENDING
        row = session.get(PolicyApprovalModel, result.record.id)
        assert row is not None
        assert row.token_hash is None
        evidence = service.evidence(
            approval_id=result.record.id,
            workspace_id="workspace_alpha",
        )
        assert [item["event_type"] for item in evidence] == ["requested"]
        assert service.verify_evidence_chain() is True
    engine.dispose()


@pytest.mark.asyncio
async def test_same_active_scope_is_idempotent() -> None:
    engine = make_engine()
    with Session(engine) as session:
        add_workspace(session)
        service = PolicyApprovalService(session=session, event_bus=EventBus())

        first = await service.request(
            scope=make_scope(),
            reason_codes=("APPROVAL_REQUIRED",),
            now=NOW,
        )
        second = await service.request(
            scope=make_scope(),
            reason_codes=("APPROVAL_REQUIRED",),
            now=NOW + timedelta(seconds=1),
        )

        assert first.created is True
        assert second.created is False
        assert second.record.id == first.record.id
        assert len(list(session.scalars(select(PolicyApprovalModel)).all())) == 1
    engine.dispose()


@pytest.mark.asyncio
async def test_approve_returns_raw_token_once_and_stores_hash_only() -> None:
    engine = make_engine()
    with Session(engine) as session:
        add_workspace(session)
        service = PolicyApprovalService(session=session, event_bus=EventBus())
        requested = await service.request(
            scope=make_scope(),
            reason_codes=("APPROVAL_REQUIRED",),
            now=NOW,
        )

        grant = await service.approve(
            approval_id=requested.record.id,
            workspace_id="workspace_alpha",
            decided_by="security_operator",
            note="Approved for exact route.",
            now=NOW + timedelta(minutes=1),
        )

        assert len(grant.token) >= 32
        row = session.get(PolicyApprovalModel, requested.record.id)
        assert row is not None
        assert row.token_hash is not None
        assert grant.token != row.token_hash
        assert grant.token not in str(
            service.evidence(
                approval_id=requested.record.id,
                workspace_id="workspace_alpha",
            )
        )
        assert "token_hash" not in service.get(
            approval_id=requested.record.id,
            workspace_id="workspace_alpha",
        ).to_public_dict()
    engine.dispose()


@pytest.mark.asyncio
async def test_consume_is_scope_bound_and_one_time() -> None:
    engine = make_engine()
    with Session(engine) as session:
        add_workspace(session)
        service = PolicyApprovalService(session=session, event_bus=EventBus())
        scope = make_scope()
        requested = await service.request(
            scope=scope,
            reason_codes=("APPROVAL_REQUIRED",),
            now=NOW,
        )
        grant = await service.approve(
            approval_id=requested.record.id,
            workspace_id="workspace_alpha",
            decided_by="security_operator",
            now=NOW + timedelta(seconds=1),
        )

        consumed = await service.consume(
            approval_id=requested.record.id,
            workspace_id="workspace_alpha",
            token=grant.token,
            scope=scope,
            now=NOW + timedelta(seconds=2),
        )
        assert consumed.status == PolicyApprovalStatus.CONSUMED

        with pytest.raises(PolicyApprovalStateError):
            await service.consume(
                approval_id=requested.record.id,
                workspace_id="workspace_alpha",
                token=grant.token,
                scope=scope,
                now=NOW + timedelta(seconds=3),
            )
    engine.dispose()


@pytest.mark.asyncio
async def test_wrong_token_preserves_approved_state() -> None:
    engine = make_engine()
    with Session(engine) as session:
        add_workspace(session)
        service = PolicyApprovalService(session=session, event_bus=EventBus())
        scope = make_scope()
        requested = await service.request(
            scope=scope,
            reason_codes=("APPROVAL_REQUIRED",),
            now=NOW,
        )
        await service.approve(
            approval_id=requested.record.id,
            workspace_id="workspace_alpha",
            decided_by="security_operator",
            now=NOW + timedelta(seconds=1),
        )

        with pytest.raises(PolicyApprovalTokenError):
            await service.consume(
                approval_id=requested.record.id,
                workspace_id="workspace_alpha",
                token="wrong-token-value" * 4,
                scope=scope,
                now=NOW + timedelta(seconds=2),
            )

        assert service.get(
            approval_id=requested.record.id,
            workspace_id="workspace_alpha",
        ).status == PolicyApprovalStatus.APPROVED
    engine.dispose()


@pytest.mark.asyncio
async def test_workspace_isolation_hides_approval() -> None:
    engine = make_engine()
    with Session(engine) as session:
        add_workspace(session, "workspace_alpha")
        add_workspace(session, "workspace_beta")
        service = PolicyApprovalService(session=session, event_bus=EventBus())
        requested = await service.request(
            scope=make_scope("workspace_alpha"),
            reason_codes=("APPROVAL_REQUIRED",),
            now=NOW,
        )

        with pytest.raises(PolicyApprovalNotFoundError):
            service.get(
                approval_id=requested.record.id,
                workspace_id="workspace_beta",
            )
    engine.dispose()


@pytest.mark.asyncio
async def test_deny_and_revoke_are_persisted() -> None:
    engine = make_engine()
    with Session(engine) as session:
        add_workspace(session)
        service = PolicyApprovalService(session=session, event_bus=EventBus())
        denied_request = await service.request(
            scope=make_scope(subject_id="request_denied"),
            reason_codes=("APPROVAL_REQUIRED",),
            now=NOW,
        )
        denied = await service.deny(
            approval_id=denied_request.record.id,
            workspace_id="workspace_alpha",
            decided_by="security_operator",
            note="Rejected.",
            now=NOW + timedelta(seconds=1),
        )
        assert denied.status == PolicyApprovalStatus.DENIED

        revoke_request = await service.request(
            scope=make_scope(subject_id="request_revoked"),
            reason_codes=("APPROVAL_REQUIRED",),
            now=NOW,
        )
        await service.approve(
            approval_id=revoke_request.record.id,
            workspace_id="workspace_alpha",
            decided_by="security_operator",
            now=NOW + timedelta(seconds=1),
        )
        revoked = await service.revoke(
            approval_id=revoke_request.record.id,
            workspace_id="workspace_alpha",
            revoked_by="security_operator",
            note="Risk context changed.",
            now=NOW + timedelta(seconds=2),
        )
        assert revoked.status == PolicyApprovalStatus.REVOKED
    engine.dispose()


@pytest.mark.asyncio
async def test_expiry_reconciliation_is_fail_closed() -> None:
    engine = make_engine()
    with Session(engine) as session:
        add_workspace(session)
        service = PolicyApprovalService(session=session, event_bus=EventBus())
        requested = await service.request(
            scope=make_scope(),
            reason_codes=("APPROVAL_REQUIRED",),
            ttl_seconds=60,
            now=NOW,
        )

        result = await service.reconcile_expired(
            workspace_id="workspace_alpha",
            now=NOW + timedelta(seconds=60),
        )

        assert result["approval_ids"] == [requested.record.id]
        assert service.get(
            approval_id=requested.record.id,
            workspace_id="workspace_alpha",
        ).status == PolicyApprovalStatus.EXPIRED
        evidence = service.evidence(
            approval_id=requested.record.id,
            workspace_id="workspace_alpha",
        )
        assert [item["event_type"] for item in evidence] == [
            "requested",
            "expired",
        ]
    engine.dispose()


@pytest.mark.asyncio
async def test_evidence_tampering_breaks_hash_chain() -> None:
    engine = make_engine()
    with Session(engine) as session:
        add_workspace(session)
        service = PolicyApprovalService(session=session, event_bus=EventBus())
        requested = await service.request(
            scope=make_scope(),
            reason_codes=("APPROVAL_REQUIRED",),
            now=NOW,
        )
        await service.deny(
            approval_id=requested.record.id,
            workspace_id="workspace_alpha",
            decided_by="security_operator",
            now=NOW + timedelta(seconds=1),
        )
        assert service.verify_evidence_chain() is True

        first = session.scalar(
            select(PolicyApprovalEvidenceModel)
            .order_by(PolicyApprovalEvidenceModel.sequence.asc())
            .limit(1)
        )
        assert first is not None
        first.payload_json = {"tampered": True}
        session.flush()

        assert service.verify_evidence_chain() is False
    engine.dispose()


@pytest.mark.asyncio
async def test_missing_workspace_is_rejected() -> None:
    engine = make_engine()
    with Session(engine) as session:
        service = PolicyApprovalService(session=session, event_bus=EventBus())
        with pytest.raises(PolicyApprovalWorkspaceError):
            await service.request(
                scope=make_scope("workspace_missing"),
                reason_codes=("APPROVAL_REQUIRED",),
                now=NOW,
            )
    engine.dispose()
