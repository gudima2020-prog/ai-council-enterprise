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
from backend.autonomy.strategy import MissionStrategyService
from backend.autonomy.strategy_schemas import (
    MissionStrategyAutoSelectRequest,
    MissionStrategyCreate,
    MissionStrategyPolicyUpsert,
    MissionStrategyRankRequest,
    MissionStrategySelectRequest,
    MissionStrategySelectionMode,
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
            "plan": {"id": f"plan_strategy_{len(self.requests)}", "status": "ready"},
            "planner_run": {"id": f"planner_run_{len(self.requests)}"},
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
                id="workspace_strategy",
                name="Mission Strategy Test",
            )
        )
    return scope


async def make_services(scope):
    bus = EventBus()
    planner = FakePlanner()
    strategies = MissionStrategyService(event_bus=bus, session_factory=scope)
    missions = AutonomousMissionService(
        event_bus=bus,
        planner=planner,
        session_factory=scope,
        scheduler_interval_seconds=3600,
        strategy_context_provider=strategies.build_context,
        cycle_strategy_assigner=strategies.assign_cycle,
    )
    mission = await missions.create_mission(
        MissionCreate(
            workspace_id="workspace_strategy",
            title="Strategy portfolio mission",
            objective="Find and execute the strongest available approach.",
            goals=[
                MissionGoalCreate(
                    goal_key="deliver",
                    title="Deliver result",
                    success_criteria="A validated result is produced.",
                )
            ],
        )
    )
    await missions.activate_mission(mission["id"], MissionActivateRequest())
    return bus, planner, strategies, missions, mission


@pytest.mark.asyncio
async def test_default_policy_is_safe_and_manual() -> None:
    scope = make_scope()
    _, _, strategies, _, mission = await make_services(scope)
    policy = strategies.get_policy(mission["id"])
    assert policy["selection_mode"] == "manual"
    assert policy["require_human_selection"] is True
    assert policy["auto_selection_enabled"] is False


@pytest.mark.asyncio
async def test_weighted_ranking_prefers_stronger_strategy() -> None:
    scope = make_scope()
    _, _, strategies, _, mission = await make_services(scope)
    weak = await strategies.create_strategy(
        mission["id"],
        MissionStrategyCreate(
            strategy_key="weak",
            title="Weak approach",
            expected_value_percent=30,
            success_probability_percent=30,
            strategic_fit_percent=40,
            feasibility_percent=40,
            evidence_confidence_percent=30,
            risk_percent=80,
            cost_percent=80,
            duration_percent=80,
        ),
    )
    strong = await strategies.create_strategy(
        mission["id"],
        MissionStrategyCreate(
            strategy_key="strong",
            title="Strong approach",
            expected_value_percent=95,
            success_probability_percent=90,
            strategic_fit_percent=90,
            feasibility_percent=85,
            evidence_confidence_percent=90,
            risk_percent=10,
            cost_percent=20,
            duration_percent=20,
        ),
    )
    ranking = await strategies.rank_portfolio(
        mission["id"],
        MissionStrategyRankRequest(adaptive=False),
    )
    assert ranking["ranking"][0]["id"] == strong["id"]
    assert ranking["ranking"][1]["id"] == weak["id"]


@pytest.mark.asyncio
async def test_manual_selection_keeps_single_selected_strategy() -> None:
    scope = make_scope()
    _, _, strategies, _, mission = await make_services(scope)
    first = await strategies.create_strategy(
        mission["id"],
        MissionStrategyCreate(strategy_key="first", title="First"),
    )
    second = await strategies.create_strategy(
        mission["id"],
        MissionStrategyCreate(strategy_key="second", title="Second"),
    )
    await strategies.select_strategy(
        first["id"],
        MissionStrategySelectRequest(
            actor_id="owner",
            rationale="Initial manual choice.",
        ),
    )
    await strategies.select_strategy(
        second["id"],
        MissionStrategySelectRequest(
            actor_id="owner",
            rationale="Switch after review.",
        ),
    )
    rows = strategies.list_strategies(mission["id"])
    selected = [row for row in rows if row["status"] == "selected"]
    assert [row["id"] for row in selected] == [second["id"]]
    assert strategies.get_strategy(first["id"])["status"] == "candidate"
    assert len(strategies.list_selections(mission["id"])) == 2


@pytest.mark.asyncio
async def test_automatic_selection_is_blocked_by_default() -> None:
    scope = make_scope()
    _, _, strategies, _, mission = await make_services(scope)
    await strategies.create_strategy(
        mission["id"],
        MissionStrategyCreate(
            strategy_key="best",
            title="Best",
            expected_value_percent=100,
            success_probability_percent=100,
            strategic_fit_percent=100,
            feasibility_percent=100,
            evidence_confidence_percent=100,
            risk_percent=0,
            cost_percent=0,
            duration_percent=0,
        ),
    )
    with pytest.raises(MissionStateError):
        await strategies.auto_select(
            mission["id"],
            MissionStrategyAutoSelectRequest(),
        )


@pytest.mark.asyncio
async def test_explicit_adaptive_policy_allows_auto_selection() -> None:
    scope = make_scope()
    _, _, strategies, _, mission = await make_services(scope)
    await strategies.upsert_policy(
        mission["id"],
        MissionStrategyPolicyUpsert(
            selection_mode=MissionStrategySelectionMode.ADAPTIVE,
            require_human_selection=False,
            auto_selection_enabled=True,
            min_selection_score=50,
            min_improvement_percent=0,
            cooldown_cycles=0,
        ),
    )
    candidate = await strategies.create_strategy(
        mission["id"],
        MissionStrategyCreate(
            strategy_key="adaptive-best",
            title="Adaptive best",
            expected_value_percent=95,
            success_probability_percent=95,
            strategic_fit_percent=95,
            feasibility_percent=95,
            evidence_confidence_percent=95,
            risk_percent=5,
            cost_percent=10,
            duration_percent=10,
        ),
    )
    result = await strategies.auto_select(
        mission["id"],
        MissionStrategyAutoSelectRequest(),
    )
    assert result["changed"] is True
    assert result["strategy"]["id"] == candidate["id"]
    assert result["strategy"]["status"] == "selected"
    assert result["selection"]["automatic"] is True


@pytest.mark.asyncio
async def test_selected_strategy_is_injected_into_planner_context() -> None:
    scope = make_scope()
    _, planner, strategies, missions, mission = await make_services(scope)
    strategy = await strategies.create_strategy(
        mission["id"],
        MissionStrategyCreate(
            strategy_key="research-first",
            title="Research first",
            strategy_hint="Collect independent evidence before execution.",
        ),
    )
    await strategies.select_strategy(
        strategy["id"],
        MissionStrategySelectRequest(
            rationale="Use research-first strategy.",
        ),
    )
    cycle = await missions.create_cycle(
        mission["id"], MissionCycleCreateRequest()
    )
    result = await missions.run_cycle(cycle["id"], MissionCycleRunRequest())
    request = planner.requests[-1]
    assert result["cycle"]["status"] == "ready"
    assert request.context["mission_strategy"]["selected_strategy"]["id"] == strategy["id"]
    assert request.context["mission_strategy_assignment"]["assigned"] is True
    assert "Collect independent evidence" in request.strategy_hint


@pytest.mark.asyncio
async def test_cycle_feedback_updates_adaptive_performance() -> None:
    scope = make_scope()
    _, _, strategies, missions, mission = await make_services(scope)
    strategy = await strategies.create_strategy(
        mission["id"],
        MissionStrategyCreate(strategy_key="feedback", title="Feedback"),
    )
    await strategies.select_strategy(
        strategy["id"],
        MissionStrategySelectRequest(rationale="Track this strategy."),
    )
    cycle = await missions.create_cycle(
        mission["id"], MissionCycleCreateRequest()
    )
    await strategies.assign_cycle(cycle["id"])
    await strategies.handle_event(
        Event(
            event_type="mission.cycle.completed",
            source="test",
            workspace_id=mission["workspace_id"],
            correlation_id=mission["id"],
            payload={"mission_id": mission["id"], "cycle_id": cycle["id"]},
        )
    )
    updated = strategies.get_strategy(strategy["id"])
    assert updated["trial_count"] == 1
    assert updated["success_count"] == 1
    assert updated["average_reward_percent"] == 100.0
    assignments = strategies.list_assignments(mission["id"])
    assert assignments[0]["status"] == "succeeded"
    assert assignments[0]["reward_percent"] == 100.0
