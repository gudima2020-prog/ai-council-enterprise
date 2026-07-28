from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.autonomy.forecast import MissionForecastService
from backend.autonomy.forecast_schemas import (
    MissionForecastGenerateRequest,
    MissionForecastMode,
    MissionForecastPolicyUpsert,
    MissionScenarioCreate,
    MissionScenarioSelectionRequest,
    MissionScenarioType,
    WorkspacePortfolioSimulationRequest,
)
from backend.autonomy.schemas import (
    MissionActivateRequest,
    MissionCreate,
    MissionCycleCreateRequest,
    MissionCycleRunRequest,
    MissionGoalCreate,
)
from backend.autonomy.service import AutonomousMissionError, AutonomousMissionService
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
            "plan": {"id": f"plan_forecast_{len(self.requests)}", "status": "ready"},
            "planner_run": {"id": f"planner_forecast_{len(self.requests)}"},
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
                id="workspace_forecast",
                name="Mission Forecast Test",
            )
        )
    return scope


async def make_services(scope):
    bus = EventBus()
    planner = FakePlanner()
    forecasts = MissionForecastService(event_bus=bus, session_factory=scope)
    missions = AutonomousMissionService(
        event_bus=bus,
        planner=planner,
        session_factory=scope,
        scheduler_interval_seconds=3600,
        forecast_context_provider=forecasts.build_context,
    )
    return planner, forecasts, missions


async def create_mission(missions, title: str, priority: int = 70):
    mission = await missions.create_mission(
        MissionCreate(
            workspace_id="workspace_forecast",
            title=title,
            objective=f"Complete {title}.",
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
async def test_default_forecast_policy_is_conservative() -> None:
    scope = make_scope()
    _, forecasts, missions = await make_services(scope)
    mission = await create_mission(missions, "Safe forecast defaults")
    policy = forecasts.get_policy(mission["id"])
    assert policy["enabled"] is False
    assert policy["forecast_mode"] == "manual"
    assert policy["auto_refresh_enabled"] is False
    assert policy["require_human_approval"] is True
    assert policy["allow_auto_scenario_selection"] is False


@pytest.mark.asyncio
async def test_forecast_is_persisted_with_transparent_drivers() -> None:
    scope = make_scope()
    _, forecasts, missions = await make_services(scope)
    mission = await create_mission(missions, "Forecast persistence", priority=85)
    result = await forecasts.generate_forecast(
        mission["id"],
        MissionForecastGenerateRequest(actor_id="owner", reason="baseline"),
    )
    assert result["status"] == "current"
    assert 0 <= result["success_probability_percent"] <= 100
    assert 0 <= result["completion_probability_percent"] <= 100
    assert result["drivers"]
    assert result["assumptions"]["forecast_is_advisory"] is True
    assert forecasts.get_forecast(result["id"])["id"] == result["id"]


@pytest.mark.asyncio
async def test_scenario_must_be_explicitly_selected() -> None:
    scope = make_scope()
    _, forecasts, missions = await make_services(scope)
    mission = await create_mission(missions, "Scenario selection")
    await forecasts.generate_forecast(
        mission["id"], MissionForecastGenerateRequest(actor_id="owner")
    )
    scenario = await forecasts.create_scenario(
        mission["id"],
        MissionScenarioCreate(
            scenario_key="optimistic",
            title="Optimistic",
            scenario_type=MissionScenarioType.OPTIMISTIC,
        ),
    )
    assert scenario["status"] == "evaluated"
    selected = await forecasts.select_scenario(
        scenario["id"],
        MissionScenarioSelectionRequest(
            actor_id="owner",
            reason="Scenario reviewed by user.",
        ),
    )
    assert selected["status"] == "selected"
    assert forecasts.build_context(mission["id"])["selected_scenario"]["id"] == scenario["id"]


@pytest.mark.asyncio
async def test_automatic_scenario_selection_requires_opt_in() -> None:
    scope = make_scope()
    _, forecasts, missions = await make_services(scope)
    mission = await create_mission(missions, "Automatic selection guard")
    scenario = await forecasts.create_scenario(
        mission["id"],
        MissionScenarioCreate(
            scenario_key="auto-denied",
            title="Automatic denied",
            scenario_type=MissionScenarioType.BASELINE,
        ),
    )
    with pytest.raises(AutonomousMissionError):
        await forecasts.select_scenario(
            scenario["id"],
            MissionScenarioSelectionRequest(
                actor_id="system",
                reason="Attempt automatic selection.",
                automatic=True,
            ),
        )
    await forecasts.upsert_policy(
        mission["id"],
        MissionForecastPolicyUpsert(
            enabled=True,
            forecast_mode=MissionForecastMode.ADAPTIVE,
            require_human_approval=False,
            allow_auto_scenario_selection=True,
        ),
    )
    selected = await forecasts.select_scenario(
        scenario["id"],
        MissionScenarioSelectionRequest(
            actor_id="system",
            reason="Policy explicitly permits selection.",
            automatic=True,
        ),
    )
    assert selected["status"] == "selected"


@pytest.mark.asyncio
async def test_workspace_simulation_respects_budget_constraint() -> None:
    scope = make_scope()
    _, forecasts, missions = await make_services(scope)
    first = await create_mission(missions, "First simulation mission", priority=90)
    second = await create_mission(missions, "Second simulation mission", priority=80)
    for index, mission in enumerate((first, second), start=1):
        scenario = await forecasts.create_scenario(
            mission["id"],
            MissionScenarioCreate(
                scenario_key=f"cost-{index}",
                title=f"Cost scenario {index}",
                overrides={"additional_budget_usd": 80},
            ),
        )
        await forecasts.select_scenario(
            scenario["id"],
            MissionScenarioSelectionRequest(
                actor_id="owner",
                reason="Use scenario in simulation.",
            ),
        )
    simulation = await forecasts.simulate_workspace(
        "workspace_forecast",
        WorkspacePortfolioSimulationRequest(
            name="Budget constrained",
            actor_id="owner",
            total_budget_usd=100,
            max_parallel_missions=2,
            agent_slots=10,
            tool_slots=10,
            compute_units=10,
        ),
    )
    assert simulation["selected_count"] == 1
    assert len(simulation["result"]["deferred"]) == 1
    assert "budget" in simulation["result"]["deferred"][0]["reasons"]


@pytest.mark.asyncio
async def test_planner_receives_forecast_context() -> None:
    scope = make_scope()
    planner, forecasts, missions = await make_services(scope)
    mission = await create_mission(missions, "Planner forecast context")
    forecast = await forecasts.generate_forecast(
        mission["id"], MissionForecastGenerateRequest(actor_id="owner")
    )
    cycle = await missions.create_cycle(mission["id"], MissionCycleCreateRequest())
    await missions.run_cycle(cycle["id"], MissionCycleRunRequest())
    context = planner.requests[-1].context["mission_forecast"]
    assert context["mission_id"] == mission["id"]
    assert context["current_forecast"]["id"] == forecast["id"]
    assert context["advisory_only"] is True
