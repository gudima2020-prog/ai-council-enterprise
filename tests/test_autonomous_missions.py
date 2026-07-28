from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.autonomy import models as autonomy_models  # noqa: F401
from backend.autonomy.schemas import (
    AutonomousWorkspacePolicyUpsert,
    AutonomyMode,
    MissionActivateRequest,
    MissionCreate,
    MissionCycleCreateRequest,
    MissionCycleRunRequest,
    MissionGoalCreate,
    MissionProgressRequest,
)
from backend.autonomy.service import AutonomousMissionService
from backend.core.events import Event, EventBus
from backend.database import models as database_models
from backend.database.base import Base
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.orchestration.planner import ExecutionPlannerService
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

    with scope() as session:
        session.add(
            database_models.WorkspaceModel(
                id="workspace_test",
                name="Autonomous Workspace Test",
            )
        )
    return scope


def make_service(scope):
    bus = EventBus()
    planner = ExecutionPlannerService(
        event_bus=bus,
        session_factory=scope,
    )
    return AutonomousMissionService(
        event_bus=bus,
        planner=planner,
        session_factory=scope,
        scheduler_interval_seconds=3600,
    ), bus


@pytest.mark.asyncio
async def test_policy_keeps_autonomous_start_conservative() -> None:
    scope = make_scope()
    service, _ = make_service(scope)

    policy = await service.upsert_policy(
        "workspace_test",
        AutonomousWorkspacePolicyUpsert(
            enabled=True,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            require_user_approval=True,
            allow_auto_start=True,
        ),
    )

    assert policy["autonomy_mode"] == "autonomous"
    assert policy["require_user_approval"] is True
    assert policy["allow_auto_start"] is False


@pytest.mark.asyncio
async def test_goal_graph_and_weighted_progress() -> None:
    scope = make_scope()
    service, _ = make_service(scope)

    mission = await service.create_mission(
        MissionCreate(
            workspace_id="workspace_test",
            title="Build autonomous release",
            objective="Prepare and validate a release.",
            goals=[
                MissionGoalCreate(
                    goal_key="design",
                    title="Design",
                    weight=1,
                ),
                MissionGoalCreate(
                    goal_key="release",
                    title="Release",
                    weight=3,
                    depends_on=["design"],
                ),
            ],
        )
    )
    validation = service.validate_mission(mission["id"])
    assert validation["valid"] is True
    assert validation["topological_order"] == ["design", "release"]

    activated = await service.activate_mission(
        mission["id"],
        MissionActivateRequest(),
    )
    by_key = {goal["goal_key"]: goal for goal in activated["goals"]}
    assert by_key["design"]["status"] == "active"
    assert by_key["release"]["status"] == "pending"

    progress = await service.record_progress(
        mission["id"],
        MissionProgressRequest(
            goal_id=by_key["design"]["id"],
            progress_percent=100,
            message="Design evidence accepted.",
            evidence={"artifact": "design.md"},
        ),
    )
    assert progress["mission"]["progress_percent"] == 25.0
    release = next(
        goal
        for goal in progress["mission"]["goals"]
        if goal["goal_key"] == "release"
    )
    assert release["status"] == "active"


@pytest.mark.asyncio
async def test_cycle_creates_execution_plan_and_tracks_terminal_event() -> None:
    scope = make_scope()
    service, bus = make_service(scope)
    await service.upsert_policy(
        "workspace_test",
        AutonomousWorkspacePolicyUpsert(
            enabled=True,
            autonomy_mode=AutonomyMode.SUPERVISED,
            require_user_approval=True,
        ),
    )
    mission = await service.create_mission(
        MissionCreate(
            workspace_id="workspace_test",
            title="Mission cycle",
            objective="Create a verifiable result.",
            goals=[
                MissionGoalCreate(
                    goal_key="result",
                    title="Produce result",
                )
            ],
        )
    )
    await service.activate_mission(mission["id"], MissionActivateRequest())
    cycle = await service.create_cycle(
        mission["id"],
        MissionCycleCreateRequest(idempotency_key="cycle-test-1"),
    )
    run = await service.run_cycle(
        cycle["id"],
        MissionCycleRunRequest(auto_start=False),
    )
    assert run["cycle"]["status"] == "ready"
    plan_id = run["cycle"]["execution_plan_id"]
    assert plan_id

    await service.handle_execution_event(
        Event(
            event_type="execution_plan.runtime.completed",
            source="test",
            workspace_id="workspace_test",
            correlation_id=plan_id,
            payload={"plan_id": plan_id},
        )
    )
    completed = service.get_cycle(cycle["id"])
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["finished_at"] is not None


@pytest.mark.asyncio
async def test_supervised_tick_creates_cycle_without_launching_plan() -> None:
    scope = make_scope()
    service, _ = make_service(scope)
    await service.upsert_policy(
        "workspace_test",
        AutonomousWorkspacePolicyUpsert(
            enabled=True,
            autonomy_mode=AutonomyMode.SUPERVISED,
            cycle_interval_seconds=60,
        ),
    )
    mission = await service.create_mission(
        MissionCreate(
            workspace_id="workspace_test",
            title="Supervised mission",
            objective="Wait for explicit planning approval.",
            goals=[MissionGoalCreate(goal_key="goal", title="Goal")],
        )
    )
    await service.activate_mission(
        mission["id"],
        MissionActivateRequest(first_cycle_at=datetime.now(timezone.utc)),
    )
    result = await service.tick_once(workspace_id="workspace_test")
    assert len(result["created_cycle_ids"]) == 1
    assert result["launched_cycle_ids"] == []
    cycle = service.get_cycle(result["created_cycle_ids"][0])
    assert cycle is not None
    assert cycle["status"] == "queued"
    assert cycle["execution_plan_id"] is None
