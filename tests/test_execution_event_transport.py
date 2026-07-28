from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.events import Event, EventBus
from backend.database import models as database_models  # noqa: F401
from backend.database.base import Base
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.orchestration.distributed import ExecutionDistributionCoordinator
from backend.orchestration.distributed_schemas import WorkerRegisterRequest
from backend.orchestration.models import ExecutionEventEnvelopeModel
from backend.orchestration.transport import (
    ExecutionEventTransport,
    RemoteProtocolUnsupported,
    RemoteSessionUnauthorized,
)
from backend.orchestration.transport_schemas import (
    RemoteSessionOpenRequest,
    TransportEventAckRequest,
    TransportEventClaimRequest,
    TransportEventNackRequest,
    TransportEventPublishRequest,
    TransportEventReplayRequest,
)
from backend.task_engine import models as task_models  # noqa: F401


def make_scope():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

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

    return scope


async def make_remote(scope):
    bus = EventBus()
    coordinator = ExecutionDistributionCoordinator(
        event_bus=bus,
        session_factory=scope,
    )
    worker = await coordinator.register_worker(
        WorkerRegisterRequest(
            worker_key="remote-worker",
            instance_id="remote-instance",
            capabilities=["execution_plan", "remote-events"],
        )
    )
    transport = ExecutionEventTransport(
        event_bus=bus,
        session_factory=scope,
        node_id="test-node",
    )
    opened = await transport.open_session(
        RemoteSessionOpenRequest(
            worker_id=worker["id"],
            instance_id="remote-instance",
            features=["durable-events", "unknown-feature"],
        )
    )
    return bus, worker, transport, opened


@pytest.mark.asyncio
async def test_protocol_negotiation_and_token_validation() -> None:
    scope = make_scope()
    _, worker, transport, opened = await make_remote(scope)
    assert opened["protocol_version"] == "1.0"
    assert opened["features"] == ["durable-events"]
    assert opened["session_token"]

    with pytest.raises(RemoteSessionUnauthorized):
        await transport.claim(
            TransportEventClaimRequest(
                session_id=opened["id"],
                session_token="invalid-token-value",
            )
        )

    with pytest.raises(RemoteProtocolUnsupported):
        await transport.open_session(
            RemoteSessionOpenRequest(
                worker_id=worker["id"],
                instance_id="remote-instance",
                protocol_version="9.9",
            )
        )


@pytest.mark.asyncio
async def test_publish_claim_ack_is_idempotent() -> None:
    scope = make_scope()
    _, worker, transport, opened = await make_remote(scope)
    request = TransportEventPublishRequest(
        session_id=opened["id"],
        session_token=opened["session_token"],
        topic="commands",
        event_type="remote_executor.command.accepted",
        payload={"command": "run"},
        target_worker_id=worker["id"],
        idempotency_key="remote-command-001",
    )
    first = await transport.publish(request)
    second = await transport.publish(request)
    assert first["id"] == second["id"]

    claims = await transport.claim(
        TransportEventClaimRequest(
            session_id=opened["id"],
            session_token=opened["session_token"],
            topics=["commands"],
        )
    )
    assert len(claims) == 1
    claim = claims[0]
    ack_request = TransportEventAckRequest(
        session_id=opened["id"],
        session_token=opened["session_token"],
        lease_token=claim["lease_token"],
        consumer_key="executor-a",
        result={"accepted": True},
    )
    acknowledged = await transport.acknowledge(first["id"], ack_request)
    assert acknowledged["event"]["status"] == "acknowledged"
    assert acknowledged["idempotent"] is False

    duplicate = await transport.acknowledge(first["id"], ack_request)
    assert duplicate["idempotent"] is True
    assert transport.verify()["ok"] is True


@pytest.mark.asyncio
async def test_nack_retry_dead_letter_and_replay() -> None:
    scope = make_scope()
    _, worker, transport, opened = await make_remote(scope)
    event = await transport.publish(
        TransportEventPublishRequest(
            session_id=opened["id"],
            session_token=opened["session_token"],
            topic="commands",
            event_type="remote_executor.command.failed",
            target_worker_id=worker["id"],
            max_attempts=2,
        )
    )

    for expected_dead in (False, True):
        claim = (
            await transport.claim(
                TransportEventClaimRequest(
                    session_id=opened["id"],
                    session_token=opened["session_token"],
                    topics=["commands"],
                )
            )
        )[0]
        result = await transport.reject(
            event["id"],
            TransportEventNackRequest(
                session_id=opened["id"],
                session_token=opened["session_token"],
                lease_token=claim["lease_token"],
                consumer_key="executor-a",
                error="temporary failure",
                retry_delay_seconds=0,
            ),
        )
        assert result["dead_lettered"] is expected_dead

    replayed = await transport.replay_dead_letter(
        event["id"],
        TransportEventReplayRequest(
            reason="Failure fixed.",
            reset_attempts=True,
        ),
    )
    assert replayed["status"] == "pending"
    assert replayed["attempt_count"] == 0


@pytest.mark.asyncio
async def test_expired_event_lease_is_reconciled() -> None:
    scope = make_scope()
    _, worker, transport, opened = await make_remote(scope)
    event = await transport.publish(
        TransportEventPublishRequest(
            session_id=opened["id"],
            session_token=opened["session_token"],
            topic="commands",
            event_type="remote_executor.command.started",
            target_worker_id=worker["id"],
            max_attempts=3,
        )
    )
    await transport.claim(
        TransportEventClaimRequest(
            session_id=opened["id"],
            session_token=opened["session_token"],
            topics=["commands"],
            lease_seconds=5,
        )
    )
    with scope() as session:
        envelope = session.get(ExecutionEventEnvelopeModel, event["id"])
        assert envelope is not None
        envelope.lease_expires_at = envelope.lease_expires_at - timedelta(hours=1)

    result = await transport.reconcile()
    assert result["released_event_leases"] == 1
    current = transport.get_event(event["id"])
    assert current is not None
    assert current["status"] == "pending"


@pytest.mark.asyncio
async def test_event_bus_mirror_is_durable_and_idempotent() -> None:
    scope = make_scope()
    bus, _, transport, _ = await make_remote(scope)
    event = Event(
        event_type="execution_plan.runtime.completed",
        source="runtime",
        payload={"plan_id": "plan_1"},
    )
    first = await transport.mirror_event(event)
    second = await transport.mirror_event(event)
    assert first is not None and second is not None
    assert first["id"] == second["id"]
    rows = transport.list_events(topic="execution_plan")
    assert len(rows) == 1
