from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.autonomy.resources import MissionResourceService
from backend.autonomy.resources_schemas import (
    MissionCapacityPlanRequest,
    MissionResourceAllocationApprove,
    MissionResourceAllocationCreate,
    MissionResourceAllocationMode,
    MissionResourcePolicyUpsert,
    MissionResourceUsageCategory,
    MissionResourceUsageCreate,
)
from backend.autonomy.schemas import (
    MissionActivateRequest,
    MissionCreate,
    MissionCycleCreateRequest,
    MissionCycleRunRequest,
    MissionGoalCreate,
)
from backend.autonomy.service import AutonomousMissionService, MissionStateError
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
            "plan": {"id": f"plan_resource_{len(self.requests)}", "status": "ready"},
            "planner_run": {"id": f"planner_resource_{len(self.requests)}"},
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
                id="workspace_resources",
                name="Mission Resource Test",
            )
        )
    return scope


async def make_services(scope):
    bus = EventBus()
    planner = FakePlanner()
    resources = MissionResourceService(event_bus=bus, session_factory=scope)
    missions = AutonomousMissionService(
        event_bus=bus,
        planner=planner,
        session_factory=scope,
        scheduler_interval_seconds=3600,
        resource_context_provider=resources.build_context,
        cycle_resource_allocator=resources.prepare_cycle,
    )
    mission = await missions.create_mission(
        MissionCreate(
            workspace_id="workspace_resources",
            title="Resource governed mission",
            objective="Execute within approved budget and capacity.",
            goals=[
                MissionGoalCreate(
                    goal_key="deliver",
                    title="Deliver",
                    success_criteria="A result is delivered within budget.",
                )
            ],
        )
    )
    await missions.activate_mission(mission["id"], MissionActivateRequest())
    return bus, planner, resources, missions, mission


@pytest.mark.asyncio
async def test_default_resource_policy_is_disabled_and_manual() -> None:
    scope = make_scope()
    _, _, resources, _, mission = await make_services(scope)
    policy = resources.get_policy(mission["id"])
    assert policy["enabled"] is False
    assert policy["allocation_mode"] == "manual"
    assert policy["require_human_approval"] is True
    assert policy["auto_allocation_enabled"] is False


@pytest.mark.asyncio
async def test_manual_policy_blocks_cycle_without_approved_allocation() -> None:
    scope = make_scope()
    _, planner, resources, missions, mission = await make_services(scope)
    await resources.upsert_policy(
        mission["id"],
        MissionResourcePolicyUpsert(
            enabled=True,
            total_budget_usd=100,
            default_cycle_budget_usd=10,
            max_cycle_budget_usd=25,
            reserve_percent=10,
        ),
    )
    cycle = await missions.create_cycle(mission["id"], MissionCycleCreateRequest())
    with pytest.raises(MissionStateError):
        await missions.run_cycle(cycle["id"], MissionCycleRunRequest())
    assert planner.requests == []


@pytest.mark.asyncio
async def test_approved_allocation_is_reserved_and_injected_into_planner() -> None:
    scope = make_scope()
    _, planner, resources, missions, mission = await make_services(scope)
    await resources.upsert_policy(
        mission["id"],
        MissionResourcePolicyUpsert(
            enabled=True,
            total_budget_usd=100,
            default_cycle_budget_usd=10,
            max_cycle_budget_usd=25,
            reserve_percent=10,
            agent_slots=2,
            tool_slots=2,
            compute_units=2,
        ),
    )
    cycle = await missions.create_cycle(mission["id"], MissionCycleCreateRequest())
    allocation = await resources.create_allocation(
        cycle["id"],
        MissionResourceAllocationCreate(
            budget_usd=12,
            agent_slots=1,
            tool_slots=1,
            compute_units=1,
            rationale="Approved test allocation.",
        ),
    )
    approved = await resources.approve_allocation(
        allocation["id"],
        MissionResourceAllocationApprove(
            actor_id="owner",
            rationale="Budget reviewed and approved.",
        ),
    )
    assert approved["status"] == "reserved"
    result = await missions.run_cycle(cycle["id"], MissionCycleRunRequest())
    assert result["cycle"]["status"] == "ready"
    request = planner.requests[-1]
    assert request.context["mission_resource_admission"]["allowed"] is True
    assert request.context["mission_resources"]["cycle_allocation"]["id"] == allocation["id"]
    assert request.context["mission_resources"]["cycle_allocation"]["budget_usd"] == 12.0


@pytest.mark.asyncio
async def test_budget_limit_rejects_allocation_approval() -> None:
    scope = make_scope()
    _, _, resources, missions, mission = await make_services(scope)
    await resources.upsert_policy(
        mission["id"],
        MissionResourcePolicyUpsert(
            enabled=True,
            total_budget_usd=20,
            default_cycle_budget_usd=10,
            max_cycle_budget_usd=15,
            reserve_percent=50,
        ),
    )
    cycle = await missions.create_cycle(mission["id"], MissionCycleCreateRequest())
    allocation = await resources.create_allocation(
        cycle["id"],
        MissionResourceAllocationCreate(budget_usd=12),
    )
    with pytest.raises(MissionStateError):
        await resources.approve_allocation(
            allocation["id"],
            MissionResourceAllocationApprove(
                actor_id="owner",
                rationale="Attempt to exceed allocatable budget.",
            ),
        )


@pytest.mark.asyncio
async def test_usage_is_idempotent_and_marks_overrun() -> None:
    scope = make_scope()
    _, _, resources, missions, mission = await make_services(scope)
    await resources.upsert_policy(
        mission["id"],
        MissionResourcePolicyUpsert(
            enabled=True,
            total_budget_usd=100,
            default_cycle_budget_usd=10,
            max_cycle_budget_usd=20,
            reserve_percent=0,
            overrun_tolerance_percent=5,
        ),
    )
    cycle = await missions.create_cycle(mission["id"], MissionCycleCreateRequest())
    allocation = await resources.create_allocation(
        cycle["id"],
        MissionResourceAllocationCreate(budget_usd=10),
    )
    await resources.approve_allocation(
        allocation["id"],
        MissionResourceAllocationApprove(
            actor_id="owner",
            rationale="Approve cost test.",
        ),
    )
    usage_request = MissionResourceUsageCreate(
        category=MissionResourceUsageCategory.LLM,
        quantity=1000,
        unit="tokens",
        cost_usd=11,
        idempotency_key="usage-001",
    )
    first = await resources.record_usage(allocation["id"], usage_request)
    second = await resources.record_usage(allocation["id"], usage_request)
    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    assert second["allocation"]["actual_cost_usd"] == 11.0
    assert second["allocation"]["status"] == "exceeded"
    assert len(resources.list_usage(mission["id"])) == 1


@pytest.mark.asyncio
async def test_capacity_plan_uses_historical_actual_cost() -> None:
    scope = make_scope()
    bus, _, resources, missions, mission = await make_services(scope)
    await resources.upsert_policy(
        mission["id"],
        MissionResourcePolicyUpsert(
            enabled=True,
            allocation_mode=MissionResourceAllocationMode.ADAPTIVE,
            require_human_approval=False,
            auto_allocation_enabled=True,
            auto_rebalance_enabled=True,
            total_budget_usd=500,
            default_cycle_budget_usd=10,
            max_cycle_budget_usd=100,
            reserve_percent=0,
            min_rebalance_improvement_percent=0,
        ),
    )
    cycle = await missions.create_cycle(mission["id"], MissionCycleCreateRequest())
    admission = await resources.prepare_cycle(cycle["id"])
    allocation = admission["allocation"]
    await resources.record_usage(
        allocation["id"],
        MissionResourceUsageCreate(
            category=MissionResourceUsageCategory.TOOL,
            cost_usd=20,
            idempotency_key="capacity-usage-001",
        ),
    )
    await resources.handle_event(
        Event(
            event_type="mission.cycle.completed",
            source="test",
            workspace_id=mission["workspace_id"],
            correlation_id=mission["id"],
            payload={"mission_id": mission["id"], "cycle_id": cycle["id"]},
        )
    )
    result = await resources.create_capacity_plan(
        mission["id"],
        MissionCapacityPlanRequest(apply=True, force=True),
    )
    assert result["applied"] is True
    assert result["capacity_plan"]["recommended_cycle_budget_usd"] >= 20.0
    assert result["policy"]["default_cycle_budget_usd"] >= 20.0
