from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.autonomy.schedule import MissionScheduleService
from backend.autonomy.schedule_schemas import (
    MissionOverdueAction,
    MissionScheduleEvaluationRequest,
    MissionScheduleMode,
    MissionSchedulePolicyUpsert,
)
from backend.autonomy.schemas import (
    MissionActivateRequest,
    MissionCreate,
    MissionCycleCreateRequest,
    MissionCycleRunRequest,
    MissionCycleTrigger,
    MissionGoalCreate,
)
from backend.autonomy.service import AutonomousMissionService, MissionStateError
from backend.core.events import EventBus
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
            "plan": {"id": f"plan_schedule_{len(self.requests)}", "status": "ready"},
            "planner_run": {"id": f"planner_schedule_{len(self.requests)}"},
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
                id="workspace_schedules",
                name="Mission Schedule Test",
            )
        )
    return scope


async def make_services(scope, *, deadline_at=None):
    bus = EventBus()
    planner = FakePlanner()
    schedules = MissionScheduleService(event_bus=bus, session_factory=scope)
    missions = AutonomousMissionService(
        event_bus=bus,
        planner=planner,
        session_factory=scope,
        scheduler_interval_seconds=3600,
        schedule_context_provider=schedules.build_context,
        cycle_schedule_guard=schedules.evaluate_cycle_admission,
        schedule_next_resolver=schedules.next_cycle_at,
        schedule_tick_hook=schedules.scheduler_tick,
    )
    mission = await missions.create_mission(
        MissionCreate(
            workspace_id="workspace_schedules",
            title="Schedule governed mission",
            objective="Deliver before deadline using a controlled cadence.",
            deadline_at=deadline_at,
            goals=[
                MissionGoalCreate(
                    goal_key="deliver",
                    title="Deliver",
                    success_criteria="Result delivered before deadline.",
                )
            ],
        )
    )
    await missions.activate_mission(mission["id"], MissionActivateRequest())
    return bus, planner, schedules, missions, mission


@pytest.mark.asyncio
async def test_default_schedule_policy_is_safe_and_disabled() -> None:
    scope = make_scope()
    _, _, schedules, _, mission = await make_services(scope)
    policy = schedules.get_policy(mission["id"])
    assert policy["enabled"] is False
    assert policy["schedule_mode"] == "manual"
    assert policy["require_human_approval"] is True
    assert policy["auto_apply_enabled"] is False
    assert policy["enforce_manual_cycles"] is False


@pytest.mark.asyncio
async def test_manual_cycle_bypasses_schedule_by_default() -> None:
    scope = make_scope()
    _, planner, schedules, missions, mission = await make_services(scope)
    other_day = (datetime.now(timezone.utc).weekday() + 1) % 7
    await schedules.upsert_policy(
        mission["id"],
        MissionSchedulePolicyUpsert(
            enabled=True,
            schedule_mode=MissionScheduleMode.FIXED,
            allowed_weekdays=[other_day],
            enforce_manual_cycles=False,
        ),
    )
    cycle = await missions.create_cycle(mission["id"], MissionCycleCreateRequest())
    result = await missions.run_cycle(cycle["id"], MissionCycleRunRequest())
    assert result["cycle"]["status"] == "ready"
    assert planner.requests[-1].context["mission_schedule_admission"]["manual_bypass"] is True


@pytest.mark.asyncio
async def test_scheduled_cycle_is_blocked_outside_allowed_weekday() -> None:
    scope = make_scope()
    _, planner, schedules, missions, mission = await make_services(scope)
    other_day = (datetime.now(timezone.utc).weekday() + 1) % 7
    await schedules.upsert_policy(
        mission["id"],
        MissionSchedulePolicyUpsert(
            enabled=True,
            schedule_mode=MissionScheduleMode.FIXED,
            allowed_weekdays=[other_day],
        ),
    )
    cycle = await missions.create_cycle(
        mission["id"],
        MissionCycleCreateRequest(trigger=MissionCycleTrigger.SCHEDULED),
    )
    with pytest.raises(MissionStateError):
        await missions.run_cycle(cycle["id"], MissionCycleRunRequest())
    assert planner.requests == []


@pytest.mark.asyncio
async def test_adaptive_evaluation_accelerates_near_deadline() -> None:
    scope = make_scope()
    deadline = datetime.now(timezone.utc) + timedelta(hours=2)
    _, _, schedules, _, mission = await make_services(scope, deadline_at=deadline)
    await schedules.upsert_policy(
        mission["id"],
        MissionSchedulePolicyUpsert(
            enabled=True,
            schedule_mode=MissionScheduleMode.ADAPTIVE,
            base_interval_seconds=3600,
            min_interval_seconds=300,
            max_interval_seconds=86400,
            adaptive_enabled=True,
            require_human_approval=True,
            auto_apply_enabled=False,
        ),
    )
    result = await schedules.evaluate(
        mission["id"],
        MissionScheduleEvaluationRequest(actor_id="owner", apply=True),
    )
    assert result["decision"] == "accelerate"
    assert result["recommended_interval_seconds"] < 3600
    assert result["applied"] is True
    assert result["next_cycle_at"] is not None


@pytest.mark.asyncio
async def test_overdue_pause_policy_pauses_active_mission() -> None:
    scope = make_scope()
    deadline = datetime.now(timezone.utc) + timedelta(hours=1)
    _, _, schedules, missions, mission = await make_services(scope, deadline_at=deadline)
    from backend.autonomy.models import WorkspaceMissionModel
    with scope() as session:
        row = session.get(WorkspaceMissionModel, mission["id"])
        row.deadline_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await schedules.upsert_policy(
        mission["id"],
        MissionSchedulePolicyUpsert(
            enabled=True,
            schedule_mode=MissionScheduleMode.FIXED,
            overdue_action=MissionOverdueAction.PAUSE,
        ),
    )
    result = await schedules.scan_deadlines(limit=10)
    assert mission["id"] in result["paused_mission_ids"]
    refreshed = missions.get_mission(mission["id"])
    assert refreshed is not None
    assert refreshed["status"] == "paused"
    events = schedules.list_deadline_events(mission["id"], open_only=True)
    assert events[0]["event_type"] == "overdue"
