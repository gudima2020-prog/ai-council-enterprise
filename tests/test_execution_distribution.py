from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.database import models as database_models  # noqa: F401
from backend.database.base import Base
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.orchestration.distributed import (
    ExecutionDistributionCoordinator,
    LeaseLost,
    WorkerConflict,
)
from backend.orchestration.distributed_schemas import (
    LeaseCompleteRequest,
    LeaseRenewRequest,
    WorkItemDispatchRequest,
    WorkerClaimRequest,
    WorkerRegisterRequest,
)
from backend.orchestration.models import (
    ExecutionLeaseModel,
    ExecutionPlanModel,
    ExecutionWorkItemModel,
)
from backend.orchestration.runtime import ExecutionPlanRuntime
from backend.orchestration.repository import ExecutionPlanRepository
from backend.orchestration.schemas import ExecutionPlanCreate
from backend.orchestration.service import ExecutionPlanService
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


async def add_plan(scope, bus: EventBus) -> str:
    with scope() as session:
        result = await ExecutionPlanService(
            ExecutionPlanRepository(session),
            bus,
        ).create_plan(
            ExecutionPlanCreate(
                title="Distributed plan",
                objective="Test persistent distributed execution.",
                steps=[],
            )
        )
        return result["id"]


@pytest.mark.asyncio
async def test_worker_registration_requires_explicit_takeover() -> None:
    scope = make_scope()
    coordinator = ExecutionDistributionCoordinator(
        event_bus=EventBus(),
        session_factory=scope,
    )
    first = await coordinator.register_worker(
        WorkerRegisterRequest(
            worker_key="worker-a",
            instance_id="instance-1",
        )
    )
    assert first["status"] == "active"

    with pytest.raises(WorkerConflict):
        await coordinator.register_worker(
            WorkerRegisterRequest(
                worker_key="worker-a",
                instance_id="instance-2",
            )
        )

    takeover = await coordinator.register_worker(
        WorkerRegisterRequest(
            worker_key="worker-a",
            instance_id="instance-2",
            force_takeover=True,
        )
    )
    assert takeover["id"] == first["id"]
    assert takeover["instance_id"] == "instance-2"


@pytest.mark.asyncio
async def test_dispatch_is_idempotent_and_capability_aware() -> None:
    scope = make_scope()
    bus = EventBus()
    coordinator = ExecutionDistributionCoordinator(
        event_bus=bus,
        session_factory=scope,
    )
    plan_id = await add_plan(scope, bus)
    request = WorkItemDispatchRequest(
        plan_id=plan_id,
        required_capabilities=["gpu"],
        idempotency_key="dispatch-001",
    )
    first = await coordinator.dispatch_plan(request)
    second = await coordinator.dispatch_plan(request)
    assert second["id"] == first["id"]

    cpu = await coordinator.register_worker(
        WorkerRegisterRequest(
            worker_key="cpu-worker",
            instance_id="cpu-1",
            capabilities=["cpu"],
        )
    )
    no_claim = await coordinator.claim_next(
        cpu["id"],
        WorkerClaimRequest(instance_id="cpu-1"),
    )
    assert no_claim is None

    gpu = await coordinator.register_worker(
        WorkerRegisterRequest(
            worker_key="gpu-worker",
            instance_id="gpu-1",
            capabilities=["cpu", "gpu"],
        )
    )
    claim = await coordinator.claim_next(
        gpu["id"],
        WorkerClaimRequest(instance_id="gpu-1"),
    )
    assert claim is not None
    assert claim["work_item"]["id"] == first["id"]
    assert claim["lease"]["fencing_token"] == 1


@pytest.mark.asyncio
async def test_lease_renewal_and_completion_use_fencing_token() -> None:
    scope = make_scope()
    bus = EventBus()
    coordinator = ExecutionDistributionCoordinator(
        event_bus=bus,
        session_factory=scope,
    )
    plan_id = await add_plan(scope, bus)
    await coordinator.dispatch_plan(
        WorkItemDispatchRequest(plan_id=plan_id)
    )
    worker = await coordinator.register_worker(
        WorkerRegisterRequest(
            worker_key="worker-a",
            instance_id="instance-a",
            capabilities=["execution_plan"],
        )
    )
    claim = await coordinator.claim_next(
        worker["id"],
        WorkerClaimRequest(instance_id="instance-a"),
    )
    assert claim is not None
    lease = claim["lease"]

    renewed = await coordinator.renew_lease(
        lease["lease_token"],
        LeaseRenewRequest(
            worker_id=worker["id"],
            instance_id="instance-a",
            fencing_token=lease["fencing_token"],
            lease_seconds=120,
        ),
    )
    assert renewed["status"] == "renewed"

    with pytest.raises(LeaseLost):
        await coordinator.complete_lease(
            lease["lease_token"],
            LeaseCompleteRequest(
                worker_id=worker["id"],
                instance_id="instance-a",
                fencing_token=lease["fencing_token"] + 1,
                result={},
            ),
        )

    completed = await coordinator.complete_lease(
        lease["lease_token"],
        LeaseCompleteRequest(
            worker_id=worker["id"],
            instance_id="instance-a",
            fencing_token=lease["fencing_token"],
            result={"ok": True},
        ),
    )
    assert completed["status"] == "completed"
    assert completed["result"] == {"ok": True}


@pytest.mark.asyncio
async def test_expired_lease_is_requeued_with_higher_fencing_token() -> None:
    scope = make_scope()
    bus = EventBus()
    coordinator = ExecutionDistributionCoordinator(
        event_bus=bus,
        session_factory=scope,
    )
    plan_id = await add_plan(scope, bus)
    item = await coordinator.dispatch_plan(
        WorkItemDispatchRequest(plan_id=plan_id, max_attempts=3)
    )
    first_worker = await coordinator.register_worker(
        WorkerRegisterRequest(
            worker_key="worker-1",
            instance_id="instance-1",
            capabilities=["execution_plan"],
        )
    )
    first_claim = await coordinator.claim_next(
        first_worker["id"],
        WorkerClaimRequest(instance_id="instance-1", lease_seconds=5),
    )
    assert first_claim is not None

    with scope() as session:
        lease = session.scalar(
            select(ExecutionLeaseModel).where(
                ExecutionLeaseModel.id == first_claim["lease"]["id"]
            )
        )
        work = session.get(ExecutionWorkItemModel, item["id"])
        assert lease is not None and work is not None
        lease.expires_at = lease.expires_at - timedelta(hours=1)
        work.lease_expires_at = lease.expires_at

    reconciled = await coordinator.reconcile()
    assert reconciled["expired_leases"] == 1
    assert reconciled["requeued"] == 1

    with scope() as session:
        work = session.get(ExecutionWorkItemModel, item["id"])
        assert work is not None
        work.available_at = work.available_at - timedelta(hours=1)

    second_worker = await coordinator.register_worker(
        WorkerRegisterRequest(
            worker_key="worker-2",
            instance_id="instance-2",
            capabilities=["execution_plan"],
        )
    )
    second_claim = await coordinator.claim_next(
        second_worker["id"],
        WorkerClaimRequest(instance_id="instance-2"),
    )
    assert second_claim is not None
    assert second_claim["lease"]["fencing_token"] == 2

    with pytest.raises(LeaseLost):
        await coordinator.complete_lease(
            first_claim["lease"]["lease_token"],
            LeaseCompleteRequest(
                worker_id=first_worker["id"],
                instance_id="instance-1",
                fencing_token=1,
                result={"stale": True},
            ),
        )


@pytest.mark.asyncio
async def test_worker_concurrency_limit_blocks_second_claim() -> None:
    scope = make_scope()
    bus = EventBus()
    coordinator = ExecutionDistributionCoordinator(
        event_bus=bus,
        session_factory=scope,
    )
    first_plan = await add_plan(scope, bus)
    second_plan = await add_plan(scope, bus)
    await coordinator.dispatch_plan(
        WorkItemDispatchRequest(plan_id=first_plan, priority=10)
    )
    await coordinator.dispatch_plan(
        WorkItemDispatchRequest(plan_id=second_plan, priority=0)
    )
    worker = await coordinator.register_worker(
        WorkerRegisterRequest(
            worker_key="single-worker",
            instance_id="single-1",
            capabilities=["execution_plan"],
            max_concurrency=1,
        )
    )
    first = await coordinator.claim_next(
        worker["id"],
        WorkerClaimRequest(instance_id="single-1"),
    )
    assert first is not None
    second = await coordinator.claim_next(
        worker["id"],
        WorkerClaimRequest(instance_id="single-1"),
    )
    assert second is None


@pytest.mark.asyncio
async def test_force_takeover_cannot_complete_previous_instance_lease() -> None:
    scope = make_scope()
    bus = EventBus()
    coordinator = ExecutionDistributionCoordinator(
        event_bus=bus,
        session_factory=scope,
    )
    plan_id = await add_plan(scope, bus)
    await coordinator.dispatch_plan(
        WorkItemDispatchRequest(plan_id=plan_id)
    )
    worker = await coordinator.register_worker(
        WorkerRegisterRequest(
            worker_key="takeover-worker",
            instance_id="old-instance",
            capabilities=["execution_plan"],
        )
    )
    claim = await coordinator.claim_next(
        worker["id"],
        WorkerClaimRequest(instance_id="old-instance"),
    )
    assert claim is not None

    takeover = await coordinator.register_worker(
        WorkerRegisterRequest(
            worker_key="takeover-worker",
            instance_id="new-instance",
            capabilities=["execution_plan"],
            force_takeover=True,
        )
    )
    with pytest.raises(LeaseLost):
        await coordinator.complete_lease(
            claim["lease"]["lease_token"],
            LeaseCompleteRequest(
                worker_id=takeover["id"],
                instance_id="new-instance",
                fencing_token=claim["lease"]["fencing_token"],
                result={"invalid": True},
            ),
        )


@pytest.mark.asyncio
async def test_runtime_recovery_preserves_plan_with_active_distributed_lease() -> None:
    scope = make_scope()
    bus = EventBus()
    coordinator = ExecutionDistributionCoordinator(
        event_bus=bus,
        session_factory=scope,
    )
    plan_id = await add_plan(scope, bus)
    await coordinator.dispatch_plan(
        WorkItemDispatchRequest(plan_id=plan_id)
    )
    worker = await coordinator.register_worker(
        WorkerRegisterRequest(
            worker_key="protected-worker",
            instance_id="protected-1",
            capabilities=["execution_plan"],
        )
    )
    claim = await coordinator.claim_next(
        worker["id"],
        WorkerClaimRequest(instance_id="protected-1"),
    )
    assert claim is not None

    with scope() as session:
        plan = session.get(ExecutionPlanModel, plan_id)
        assert plan is not None
        plan.status = "running"

    runtime = ExecutionPlanRuntime(
        event_bus=bus,
        session_factory=scope,
    )
    protected = coordinator.active_plan_ids()
    result = await runtime.recover_interrupted(
        exclude_plan_ids=protected
    )
    assert result["interrupted_failed"] == 0

    with scope() as session:
        plan = session.get(ExecutionPlanModel, plan_id)
        assert plan is not None
        assert plan.status == "running"
