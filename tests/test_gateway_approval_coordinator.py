from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database.models import WorkspaceModel
from backend.gateway.approvals import GatewayApprovalCoordinator
from backend.gateway.schemas import (
    GatewayMessage,
    GatewayRequest,
)
from backend.policy_approvals import (
    PolicyApprovalStateError,
    PolicyApprovalStatus,
)
from backend.policy_approvals.models import (
    PolicyApprovalModel,
)
from backend.policy_approvals.service import PolicyApprovalService
from backend.runtime_policy import (
    DataClassification,
    PolicyOperation,
    ProviderTrust,
    RuntimePolicyContext,
    RuntimePolicyEngine,
)


@pytest.fixture
def environment():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )

    with session_factory() as session:
        session.add(
            WorkspaceModel(
                id="workspace_alpha",
                name="Workspace Alpha",
                description="",
                workspace_type="general",
                status="active",
                metadata_json={},
            )
        )
        session.commit()

    @contextmanager
    def session_scope_factory():
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    event_bus = EventBus()
    coordinator = GatewayApprovalCoordinator(
        session_scope_factory=session_scope_factory,
        event_bus=event_bus,
    )

    yield {
        "engine": engine,
        "session_factory": session_factory,
        "session_scope": session_scope_factory,
        "event_bus": event_bus,
        "coordinator": coordinator,
    }

    engine.dispose()


def make_request(
    *,
    request_id: str = "gateway_request_001",
    provider: str = "trusted",
    model: str = "model-a",
    content: str = "confidential request",
) -> GatewayRequest:
    return GatewayRequest(
        messages=[
            GatewayMessage(
                role="system",
                content="Follow the policy.",
            ),
            GatewayMessage(
                role="user",
                content=content,
            ),
        ],
        model=model,
        provider=provider,
        workspace_id="workspace_alpha",
        source="gateway_test",
        request_id=request_id,
    )


def decision_for(request: GatewayRequest):
    return RuntimePolicyEngine().evaluate(
        RuntimePolicyContext(
            operation=PolicyOperation.MODEL_INFERENCE,
            data_classification=(
                DataClassification.CONFIDENTIAL
            ),
            workspace_id=request.workspace_id,
            provider_trust=(
                ProviderTrust.TRUSTED_EXTERNAL
            ),
        )
    )


async def approve(
    environment,
    *,
    approval_id: str,
) -> str:
    with environment["session_scope"]() as session:
        grant = await PolicyApprovalService(
            session=session,
            event_bus=environment["event_bus"],
        ).approve(
            approval_id=approval_id,
            workspace_id="workspace_alpha",
            decided_by="operator_alpha",
            note="Approved for exact gateway scope.",
        )
        return grant.token


@pytest.mark.asyncio
async def test_coordinator_requests_and_consumes_exact_scope(
    environment,
) -> None:
    request = make_request()
    decision = decision_for(request)

    pending = await environment["coordinator"].request(
        request=request,
        decision=decision,
        requested_by="operator_alpha",
    )

    assert pending.authorized is False
    assert pending.error_code == "POLICY_APPROVAL_REQUIRED"
    assert pending.metadata["status"] == "pending"
    approval_id = pending.metadata["approval_id"]
    token = await approve(
        environment,
        approval_id=approval_id,
    )

    consumed = await environment["coordinator"].consume(
        request=request,
        decision=decision,
        approval_id=approval_id,
        token=token,
    )

    assert consumed.authorized is True
    assert consumed.error_code is None
    assert consumed.metadata["status"] == "consumed"
    assert token not in str(consumed.metadata)

    with environment["session_scope"]() as session:
        record = PolicyApprovalService(
            session=session,
            event_bus=environment["event_bus"],
        ).get(
            approval_id=approval_id,
            workspace_id="workspace_alpha",
        )
        assert record.status == PolicyApprovalStatus.CONSUMED


@pytest.mark.asyncio
async def test_changed_prompt_hash_fails_closed(
    environment,
) -> None:
    request = make_request(
        request_id="gateway_request_changed",
    )
    decision = decision_for(request)
    pending = await environment["coordinator"].request(
        request=request,
        decision=decision,
    )
    approval_id = pending.metadata["approval_id"]
    token = await approve(
        environment,
        approval_id=approval_id,
    )

    changed = replace(
        request,
        messages=[
            *request.messages[:-1],
            GatewayMessage(
                role="user",
                content="changed confidential request",
            ),
        ],
    )
    outcome = await environment["coordinator"].consume(
        request=changed,
        decision=decision_for(changed),
        approval_id=approval_id,
        token=token,
    )

    assert outcome.authorized is False
    assert (
        outcome.error_code
        == "POLICY_APPROVAL_SCOPE_MISMATCH"
    )
    assert token not in str(outcome.metadata)

    with environment["session_scope"]() as session:
        record = PolicyApprovalService(
            session=session,
            event_bus=environment["event_bus"],
        ).get(
            approval_id=approval_id,
            workspace_id="workspace_alpha",
        )
        assert record.status == PolicyApprovalStatus.APPROVED


@pytest.mark.asyncio
async def test_changed_provider_route_fails_closed(
    environment,
) -> None:
    request = make_request(
        request_id="gateway_request_provider",
    )
    decision = decision_for(request)
    pending = await environment["coordinator"].request(
        request=request,
        decision=decision,
    )
    approval_id = pending.metadata["approval_id"]
    token = await approve(
        environment,
        approval_id=approval_id,
    )

    changed = replace(
        request,
        provider="another-trusted-provider",
    )
    outcome = await environment["coordinator"].consume(
        request=changed,
        decision=decision_for(changed),
        approval_id=approval_id,
        token=token,
    )

    assert outcome.authorized is False
    assert (
        outcome.error_code
        == "POLICY_APPROVAL_SCOPE_MISMATCH"
    )


@pytest.mark.asyncio
async def test_atomic_compare_and_set_rejects_stale_session(
    environment,
) -> None:
    request = make_request(
        request_id="gateway_request_atomic",
    )
    decision = decision_for(request)
    pending = await environment["coordinator"].request(
        request=request,
        decision=decision,
    )
    approval_id = pending.metadata["approval_id"]
    token = await approve(
        environment,
        approval_id=approval_id,
    )

    session_factory = environment["session_factory"]
    session_one = session_factory()
    session_two = session_factory()
    try:
        service_one = PolicyApprovalService(
            session=session_one,
            event_bus=environment["event_bus"],
        )
        service_two = PolicyApprovalService(
            session=session_two,
            event_bus=environment["event_bus"],
        )

        # Hold the ORM instance strongly so session_two retains
        # the intentionally stale approved state during the race.
        stale_row_two = session_two.get(
            PolicyApprovalModel,
            approval_id,
        )
        assert stale_row_two is not None
        assert (
            stale_row_two.status
            == PolicyApprovalStatus.APPROVED.value
        )

        record_one = service_one.get(
            approval_id=approval_id,
            workspace_id="workspace_alpha",
        )
        record_two = service_two.get(
            approval_id=approval_id,
            workspace_id="workspace_alpha",
        )
        assert record_one.status == PolicyApprovalStatus.APPROVED
        assert record_two.status == PolicyApprovalStatus.APPROVED

        await service_one.consume(
            approval_id=approval_id,
            workspace_id="workspace_alpha",
            token=token,
            scope=record_one.scope,
        )
        session_one.commit()

        with pytest.raises(
            PolicyApprovalStateError,
            match="concurrently",
        ):
            await service_two.consume(
                approval_id=approval_id,
                workspace_id="workspace_alpha",
                token=token,
                scope=record_two.scope,
            )
        session_two.rollback()
    finally:
        session_one.close()
        session_two.close()


@pytest.mark.asyncio
async def test_missing_workspace_is_rejected(
    environment,
) -> None:
    request = replace(
        make_request(),
        workspace_id=None,
    )
    decision = decision_for(request)

    outcome = await environment["coordinator"].request(
        request=request,
        decision=decision,
    )

    assert outcome.authorized is False
    assert (
        outcome.error_code
        == "POLICY_APPROVAL_WORKSPACE_INVALID"
    )
