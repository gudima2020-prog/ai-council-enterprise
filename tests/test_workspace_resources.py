from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.autonomy.schemas import (
    MissionActivateRequest,
    MissionCreate,
    MissionCycleCreateRequest,
    MissionCycleRunRequest,
    MissionGoalCreate,
)
from backend.autonomy.service import AutonomousMissionService, MissionStateError
from backend.autonomy.workspace_resources import WorkspaceResourceCoordinator
from backend.autonomy.workspace_resources_schemas import (
    WorkspaceResourceAllocationMode,
    WorkspaceResourceConflictAction,
    WorkspaceResourceConflictResolution,
    WorkspaceResourcePolicyUpsert,
)
from backend.core.events import Event, EventBus
from backend.database import models as database_models
from backend.database.base import Base
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401


class FakePlanner:
    def __init__(self) -> None:
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return {
            "plan": {
                "id": f"plan_workspace_resource_{len(self.requests)}",
                "status": "ready",
            },
            "planner_run": {"id": f"planner_workspace_resource_{len(self.requests)}"},
            "runtime": None,
        }


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

    with scope() as session:
        session.add(
            database_models.WorkspaceModel(
                id="workspace_shared_resources",
                name="Shared Mission Resource Pool",
            )
        )
    return scope


async def make_services(scope):
    bus = EventBus()
    planner = FakePlanner()
    coordinator = WorkspaceResourceCoordinator(
        event_bus=bus,
        session_factory=scope,
    )
    missions = AutonomousMissionService(
        event_bus=bus,
        planner=planner,
        session_factory=scope,
        scheduler_interval_seconds=3600,
        workspace_resource_context_provider=coordinator.build_context,
        cycle_workspace_resource_allocator=coordinator.prepare_cycle,
        workspace_resource_tick_hook=coordinator.scheduler_tick,
    )
    return bus, planner, coordinator, missions


async def create_active_mission(missions, *, title: str, priority: int):
    mission = await missions.create_mission(
        MissionCreate(
            workspace_id="workspace_shared_resources",
            title=title,
            objective=f"Complete {title} within shared Workspace capacity.",
            priority=priority,
            goals=[
                MissionGoalCreate(
                    goal_key="deliver",
                    title="Deliver",
                    success_criteria="A verified result is delivered.",
                )
            ],
        )
    )
    await missions.activate_mission(mission["id"], MissionActivateRequest())
    return mission


@pytest.mark.asyncio
async def test_default_workspace_resource_policy_is_disabled_and_safe() -> None:
    scope = make_scope()
    _, _, coordinator, missions = await make_services(scope)
    await create_active_mission(missions, title="Safe defaults", priority=50)
    policy = coordinator.get_policy("workspace_shared_resources")
    assert policy["enabled"] is False
    assert policy["allocation_mode"] == "manual"
    assert policy["require_human_approval"] is True
    assert policy["auto_rebalance_enabled"] is False
    assert policy["enforce_cycle_admission"] is False


@pytest.mark.asyncio
async def test_shared_budget_blocks_second_mission_cycle() -> None:
    scope = make_scope()
    _, planner, coordinator, missions = await make_services(scope)
    first = await create_active_mission(missions, title="First", priority=80)
    second = await create_active_mission(missions, title="Second", priority=70)
    await coordinator.upsert_policy(
        "workspace_shared_resources",
        WorkspaceResourcePolicyUpsert(
            enabled=True,
            allocation_mode=WorkspaceResourceAllocationMode.WEIGHTED,
            require_human_approval=False,
            enforce_cycle_admission=True,
            total_budget_usd=15,
            default_cycle_budget_usd=10,
            max_cycle_budget_usd=10,
            reserve_percent=0,
            max_parallel_cycles=2,
            agent_slots=4,
            tool_slots=4,
            compute_units=4,
        ),
    )
    first_cycle = await missions.create_cycle(first["id"], MissionCycleCreateRequest())
    await missions.run_cycle(first_cycle["id"], MissionCycleRunRequest())
    second_cycle = await missions.create_cycle(second["id"], MissionCycleCreateRequest())
    with pytest.raises(MissionStateError):
        await missions.run_cycle(second_cycle["id"], MissionCycleRunRequest())
    assert len(planner.requests) == 1
    conflicts = coordinator.list_conflicts(
        "workspace_shared_resources",
        status="open",
    )
    assert len(conflicts) == 1
    assert "workspace_budget_exceeded" in conflicts[0]["metadata"]["reasons"]


@pytest.mark.asyncio
async def test_workspace_reservation_is_injected_into_planner_context() -> None:
    scope = make_scope()
    _, planner, coordinator, missions = await make_services(scope)
    mission = await create_active_mission(missions, title="Planner context", priority=90)
    await coordinator.upsert_policy(
        "workspace_shared_resources",
        WorkspaceResourcePolicyUpsert(
            enabled=True,
            allocation_mode=WorkspaceResourceAllocationMode.WEIGHTED,
            require_human_approval=False,
            enforce_cycle_admission=True,
            total_budget_usd=100,
            default_cycle_budget_usd=12,
            max_cycle_budget_usd=20,
            reserve_percent=0,
        ),
    )
    cycle = await missions.create_cycle(mission["id"], MissionCycleCreateRequest())
    result = await missions.run_cycle(cycle["id"], MissionCycleRunRequest())
    assert result["cycle"]["status"] == "ready"
    context = planner.requests[-1].context
    admission = context["workspace_resource_admission"]
    assert admission["allowed"] is True
    assert admission["reservation"]["budget_usd"] == 12.0
    assert context["workspace_resources"]["cycle_reservation"]["cycle_id"] == cycle["id"]


@pytest.mark.asyncio
async def test_high_priority_conflict_can_preempt_safe_reserved_capacity() -> None:
    scope = make_scope()
    _, _, coordinator, missions = await make_services(scope)
    low = await create_active_mission(missions, title="Low priority", priority=10)
    high = await create_active_mission(missions, title="High priority", priority=95)
    await coordinator.upsert_policy(
        "workspace_shared_resources",
        WorkspaceResourcePolicyUpsert(
            enabled=True,
            allocation_mode=WorkspaceResourceAllocationMode.ADAPTIVE,
            require_human_approval=False,
            auto_rebalance_enabled=True,
            enforce_cycle_admission=True,
            total_budget_usd=10,
            default_cycle_budget_usd=10,
            max_cycle_budget_usd=10,
            reserve_percent=0,
            max_parallel_cycles=1,
            agent_slots=1,
            tool_slots=1,
            compute_units=1,
        ),
    )
    low_cycle = await missions.create_cycle(low["id"], MissionCycleCreateRequest())
    low_admission = await coordinator.prepare_cycle(low_cycle["id"])
    assert low_admission["allowed"] is True

    high_cycle = await missions.create_cycle(high["id"], MissionCycleCreateRequest())
    high_admission = await coordinator.prepare_cycle(high_cycle["id"])
    assert high_admission["allowed"] is False
    conflict = high_admission["conflict"]
    resolved = await coordinator.resolve_conflict(
        conflict["id"],
        WorkspaceResourceConflictResolution(
            action=WorkspaceResourceConflictAction.PREEMPT,
            actor_id="owner",
            reason="Higher-priority Mission receives the shared slot.",
        ),
    )
    assert resolved["status"] == "resolved"
    reservations = coordinator.list_reservations("workspace_shared_resources")
    by_mission = {row["mission_id"]: row for row in reservations}
    assert by_mission[low["id"]]["status"] == "released"
    assert by_mission[high["id"]]["status"] == "reserved"


@pytest.mark.asyncio
async def test_terminal_cycle_event_settles_workspace_reservation() -> None:
    scope = make_scope()
    bus, _, coordinator, missions = await make_services(scope)
    mission = await create_active_mission(missions, title="Settlement", priority=50)
    await coordinator.upsert_policy(
        "workspace_shared_resources",
        WorkspaceResourcePolicyUpsert(
            enabled=True,
            allocation_mode=WorkspaceResourceAllocationMode.WEIGHTED,
            require_human_approval=False,
            enforce_cycle_admission=True,
            total_budget_usd=100,
            default_cycle_budget_usd=10,
            reserve_percent=0,
        ),
    )
    cycle = await missions.create_cycle(mission["id"], MissionCycleCreateRequest())
    admission = await coordinator.prepare_cycle(cycle["id"])
    reservation_id = admission["reservation"]["id"]
    await coordinator.handle_event(
        Event(
            event_type="mission.cycle.completed",
            source="test",
            workspace_id=mission["workspace_id"],
            correlation_id=mission["id"],
            payload={"mission_id": mission["id"], "cycle_id": cycle["id"]},
        )
    )
    reservation = coordinator.get_reservation(reservation_id)
    assert reservation is not None
    assert reservation["status"] == "consumed"
    assert reservation["released_at"] is not None
