from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.autonomy.forecast import MissionForecastService
from backend.autonomy.forecast_schemas import MissionForecastGenerateRequest
from backend.autonomy.learning import (
    MissionLearningService,
    MissionLearningStateError,
)
from backend.autonomy.learning_schemas import (
    MissionCalibrationScope,
    MissionForecastOutcomeResolveRequest,
    MissionLearningMode,
    MissionLearningPolicyUpsert,
    MissionLearningRunRequest,
)
from backend.autonomy.models import (
    MissionForecastCalibrationModel,
    MissionForecastModel,
)
from backend.autonomy.schemas import (
    MissionActivateRequest,
    MissionCreate,
    MissionGoalCreate,
)
from backend.autonomy.service import AutonomousMissionService
from backend.core.events import EventBus
from backend.database import models as database_models
from backend.database.base import Base
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401


class FakePlanner:
    async def generate(self, request):
        return {
            "plan": {"id": "plan_learning", "status": "ready"},
            "planner_run": {"id": "planner_learning"},
            "runtime": None,
        }


def make_scope():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    Base.metadata.create_all(engine)

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
                id="workspace_learning",
                name="Mission Learning Test",
            )
        )
    return scope


async def make_services(scope):
    bus = EventBus()
    learning = MissionLearningService(
        event_bus=bus,
        session_factory=scope,
    )
    forecasting = MissionForecastService(
        event_bus=bus,
        session_factory=scope,
    )
    missions = AutonomousMissionService(
        event_bus=bus,
        planner=FakePlanner(),
        session_factory=scope,
        scheduler_interval_seconds=3600,
        forecast_context_provider=forecasting.build_context,
        learning_context_provider=learning.build_context,
    )
    mission = await missions.create_mission(
        MissionCreate(
            workspace_id="workspace_learning",
            title="Calibrate forecasts",
            objective="Produce enough outcomes to calibrate the forecast.",
            priority=80,
            goals=[
                MissionGoalCreate(
                    goal_key="deliver",
                    title="Deliver",
                )
            ],
        )
    )
    await missions.activate_mission(
        mission["id"],
        MissionActivateRequest(),
    )
    return learning, forecasting, missions, mission


@pytest.mark.asyncio
async def test_learning_defaults_are_safe() -> None:
    scope = make_scope()
    learning, _, _, mission = await make_services(scope)
    policy = learning.get_policy(mission["id"])
    assert policy["enabled"] is False
    assert policy["learning_mode"] == "manual"
    assert policy["capture_outcomes_enabled"] is True
    assert policy["auto_calibration_enabled"] is False
    assert policy["auto_apply_enabled"] is False
    assert policy["require_human_approval"] is True


@pytest.mark.asyncio
async def test_forecast_outcomes_are_resolved_idempotently() -> None:
    scope = make_scope()
    learning, forecasting, _, mission = await make_services(scope)
    forecast = await forecasting.generate_forecast(
        mission["id"],
        MissionForecastGenerateRequest(actor_id="owner"),
    )
    request = MissionForecastOutcomeResolveRequest(
        forecast_id=forecast["id"],
        actual_status="completed",
        actual_cost_usd=4.0,
        actual_cycle_count=3,
        actual_completed_at=datetime.now(timezone.utc),
        actor_id="owner",
        source_ref="acceptance-001",
    )
    first = await learning.resolve_manual_outcome(mission["id"], request)
    second = await learning.resolve_manual_outcome(mission["id"], request)
    assert first["resolved"] == 1
    assert second["resolved"] == 0
    outcome = learning.list_outcomes(mission["id"])[0]
    assert outcome["actual_success"] is True
    assert 0 <= outcome["success_brier_score"] <= 1


@pytest.mark.asyncio
async def test_learning_run_requires_configured_sample_count() -> None:
    scope = make_scope()
    learning, forecasting, _, mission = await make_services(scope)
    await forecasting.generate_forecast(
        mission["id"],
        MissionForecastGenerateRequest(actor_id="owner"),
    )
    await learning.resolve_manual_outcome(
        mission["id"],
        MissionForecastOutcomeResolveRequest(
            actual_status="failed",
            actor_id="owner",
            source_ref="single-sample",
        ),
    )
    with pytest.raises(MissionLearningStateError):
        await learning.run_learning(
            mission["id"],
            MissionLearningRunRequest(
                scope_type=MissionCalibrationScope.MISSION,
                actor_id="owner",
            ),
        )


@pytest.mark.asyncio
async def test_explicit_learning_run_applies_calibration() -> None:
    scope = make_scope()
    learning, forecasting, _, mission = await make_services(scope)
    await learning.upsert_policy(
        mission["id"],
        MissionLearningPolicyUpsert(
            enabled=True,
            learning_mode=MissionLearningMode.OBSERVE,
            min_samples=3,
            min_improvement_percent=0,
            max_probability_adjustment_percent=50,
        ),
    )
    forecast_ids = []
    for index in range(3):
        forecast = await forecasting.generate_forecast(
            mission["id"],
            MissionForecastGenerateRequest(
                actor_id="owner",
                reason=f"sample-{index}",
            ),
        )
        forecast_ids.append(forecast["id"])

    with scope() as session:
        rows = list(
            session.scalars(
                select(MissionForecastModel).where(
                    MissionForecastModel.id.in_(forecast_ids)
                )
            ).all()
        )
        for row in rows:
            row.success_probability_percent = 10.0
            row.completion_probability_percent = 10.0
            row.expected_cost_usd = 10.0
            row.expected_remaining_cycles = 5.0

    resolved = await learning.resolve_manual_outcome(
        mission["id"],
        MissionForecastOutcomeResolveRequest(
            actual_status="completed",
            actual_cost_usd=20.0,
            actual_cycle_count=8,
            actor_id="owner",
            source_ref="three-samples",
        ),
    )
    assert resolved["resolved"] == 3

    run = await learning.run_learning(
        mission["id"],
        MissionLearningRunRequest(
            scope_type=MissionCalibrationScope.MISSION,
            actor_id="owner",
            reason="Apply reviewed calibration.",
            apply=True,
        ),
    )
    assert run["status"] == "applied"
    calibration = learning.get_calibration(run["calibration_id"])
    assert calibration["status"] == "current"
    assert calibration["sample_count"] == 3
    assert calibration["success_bias_percent"] > 0

    next_forecast = await forecasting.generate_forecast(
        mission["id"],
        MissionForecastGenerateRequest(actor_id="owner"),
    )
    assert (
        next_forecast["metrics"]["calibration_id"]
        == calibration["id"]
    )
    assert next_forecast["assumptions"]["calibration_id"] == calibration["id"]


@pytest.mark.asyncio
async def test_learning_integrity_verification_is_clean() -> None:
    scope = make_scope()
    learning, _, _, _ = await make_services(scope)
    result = learning.verify_integrity()
    assert result["valid"] is True
    assert result["issues"] == []
