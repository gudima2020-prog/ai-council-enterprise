from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.autonomy.portfolio import MissionPortfolioService
from backend.autonomy.portfolio_schemas import (
    MissionDependencyCreate,
    MissionPortfolioMode,
    MissionPortfolioPolicyUpsert,
    MissionPortfolioRankRequest,
    MissionPortfolioRebalanceRequest,
)
from backend.autonomy.schemas import (
    MissionActivateRequest,
    MissionCreate,
    MissionCycleCreateRequest,
    MissionCycleRunRequest,
    MissionDecisionRequest,
    MissionGoalCreate,
)
from backend.autonomy.service import (
    AutonomousMissionError,
    AutonomousMissionService,
    MissionStateError,
)
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
            "plan": {"id": f"plan_portfolio_{len(self.requests)}", "status": "ready"},
            "planner_run": {"id": f"planner_portfolio_{len(self.requests)}"},
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
                id="workspace_portfolio",
                name="Mission Portfolio Test",
            )
        )
    return scope


async def make_services(scope):
    bus = EventBus()
    planner = FakePlanner()
    portfolio = MissionPortfolioService(event_bus=bus, session_factory=scope)
    missions = AutonomousMissionService(
        event_bus=bus,
        planner=planner,
        session_factory=scope,
        scheduler_interval_seconds=3600,
        portfolio_context_provider=portfolio.build_context,
        cycle_portfolio_guard=portfolio.evaluate_cycle_admission,
        portfolio_tick_hook=portfolio.scheduler_tick,
    )
    return bus, planner, portfolio, missions


async def create_active_mission(missions, *, title: str, priority: int = 50):
    mission = await missions.create_mission(
        MissionCreate(
            workspace_id="workspace_portfolio",
            title=title,
            objective=f"Complete {title}.",
            priority=priority,
            goals=[
                MissionGoalCreate(
                    goal_key="deliver",
                    title="Deliver",
                    success_criteria="Deliver a verified result.",
                )
            ],
        )
    )
    await missions.activate_mission(mission["id"], MissionActivateRequest())
    return mission


@pytest.mark.asyncio
async def test_default_portfolio_policy_is_safe() -> None:
    scope = make_scope()
    _, _, portfolio, missions = await make_services(scope)
    await create_active_mission(missions, title="Safe defaults")
    policy = portfolio.get_policy("workspace_portfolio")
    assert policy["enabled"] is False
    assert policy["prioritization_mode"] == "manual"
    assert policy["require_human_approval"] is True
    assert policy["auto_rebalance_enabled"] is False
    assert policy["enforce_cycle_admission"] is False


@pytest.mark.asyncio
async def test_hard_dependency_blocks_cycle_until_upstream_completes() -> None:
    scope = make_scope()
    _, planner, portfolio, missions = await make_services(scope)
    upstream = await create_active_mission(missions, title="Upstream", priority=90)
    downstream = await create_active_mission(missions, title="Downstream", priority=80)
    await portfolio.create_dependency(
        downstream["id"],
        MissionDependencyCreate(depends_on_mission_id=upstream["id"]),
    )
    cycle = await missions.create_cycle(downstream["id"], MissionCycleCreateRequest())
    with pytest.raises(MissionStateError):
        await missions.run_cycle(cycle["id"], MissionCycleRunRequest())
    assert planner.requests == []

    await missions.complete_mission(
        upstream["id"],
        MissionDecisionRequest(actor_id="owner", reason="Upstream result accepted."),
    )
    await portfolio.refresh_dependencies(workspace_id="workspace_portfolio")
    result = await missions.run_cycle(cycle["id"], MissionCycleRunRequest())
    assert result["cycle"]["status"] == "ready"
    assert planner.requests[-1].context["mission_portfolio_admission"]["allowed"] is True


@pytest.mark.asyncio
async def test_cross_mission_dependency_cycle_is_rejected() -> None:
    scope = make_scope()
    _, _, portfolio, missions = await make_services(scope)
    first = await create_active_mission(missions, title="First")
    second = await create_active_mission(missions, title="Second")
    await portfolio.create_dependency(
        second["id"],
        MissionDependencyCreate(depends_on_mission_id=first["id"]),
    )
    with pytest.raises(AutonomousMissionError):
        await portfolio.create_dependency(
            first["id"],
            MissionDependencyCreate(depends_on_mission_id=second["id"]),
        )
    graph = portfolio.validate_graph("workspace_portfolio")
    assert graph["valid"] is True
    assert graph["dependency_count"] == 1


@pytest.mark.asyncio
async def test_portfolio_ranking_blocks_unready_mission() -> None:
    scope = make_scope()
    _, _, portfolio, missions = await make_services(scope)
    upstream = await create_active_mission(missions, title="Foundation", priority=60)
    blocked = await create_active_mission(missions, title="Blocked high priority", priority=100)
    ready = await create_active_mission(missions, title="Ready mission", priority=90)
    await portfolio.create_dependency(
        blocked["id"],
        MissionDependencyCreate(depends_on_mission_id=upstream["id"]),
    )
    ranking = await portfolio.rank_workspace(
        "workspace_portfolio",
        MissionPortfolioRankRequest(actor_id="owner"),
    )
    by_id = {item["mission_id"]: item for item in ranking["ranking"]}
    assert by_id[blocked["id"]]["blocked"] is True
    assert by_id[blocked["id"]]["decision"] == "block"
    assert by_id[ready["id"]]["blocked"] is False
    assert by_id[ready["id"]]["rank"] < by_id[blocked["id"]]["rank"]


@pytest.mark.asyncio
async def test_explicit_adaptive_policy_applies_portfolio_slots() -> None:
    scope = make_scope()
    _, _, portfolio, missions = await make_services(scope)
    high = await create_active_mission(missions, title="High", priority=95)
    low = await create_active_mission(missions, title="Low", priority=10)
    await portfolio.upsert_policy(
        "workspace_portfolio",
        MissionPortfolioPolicyUpsert(
            enabled=True,
            prioritization_mode=MissionPortfolioMode.ADAPTIVE,
            require_human_approval=False,
            auto_rebalance_enabled=True,
            enforce_cycle_admission=True,
            max_parallel_missions=1,
            min_selection_score=0,
        ),
    )
    result = await portfolio.rebalance(
        "workspace_portfolio",
        MissionPortfolioRebalanceRequest(
            actor_id="system",
            automatic=True,
            apply=True,
            rationale="Adaptive test rebalance.",
        ),
    )
    assignments = {row["mission_id"]: row for row in result["assignments"]}
    assert assignments[high["id"]]["status"] == "selected"
    assert assignments[low["id"]]["status"] == "deferred"
    assert portfolio.evaluate_mission_admission(high["id"])["allowed"] is True
    assert portfolio.evaluate_mission_admission(low["id"])["allowed"] is False


@pytest.mark.asyncio
async def test_planner_receives_portfolio_context() -> None:
    scope = make_scope()
    _, planner, portfolio, missions = await make_services(scope)
    mission = await create_active_mission(missions, title="Planner context", priority=80)
    cycle = await missions.create_cycle(mission["id"], MissionCycleCreateRequest())
    await missions.run_cycle(cycle["id"], MissionCycleRunRequest())
    context = planner.requests[-1].context
    assert context["mission_portfolio"]["mission_id"] == mission["id"]
    assert context["mission_portfolio_admission"]["allowed"] is True
