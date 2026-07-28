from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.autonomy.forecast_schemas import (
    MissionForecastGenerateRequest,
    MissionForecastMode,
    MissionForecastPolicyUpsert,
    MissionForecastStatus,
    MissionScenarioArchiveRequest,
    MissionScenarioCreate,
    MissionScenarioEvaluateRequest,
    MissionScenarioSelectionRequest,
    MissionScenarioStatus,
    MissionScenarioType,
    MissionScenarioUpdate,
    PortfolioSimulationStatus,
    WorkspacePortfolioSimulationRequest,
)
from backend.autonomy.models import (
    MissionCycleModel,
    MissionDependencyModel,
    MissionForecastCalibrationModel,
    MissionForecastModel,
    MissionForecastPolicyModel,
    MissionProgressUpdateModel,
    MissionResourcePolicyModel,
    MissionResourceUsageModel,
    MissionRiskModel,
    MissionScenarioModel,
    MissionSchedulePolicyModel,
    MissionStrategyModel,
    WorkspaceMissionModel,
    WorkspacePortfolioSimulationModel,
    WorkspaceResourcePolicyModel,
)
from backend.autonomy.service import AutonomousMissionError, MissionNotFound, ensure_utc
from backend.core.events import Event, EventBus
from backend.database.models import WorkspaceModel
from backend.database.session import session_scope


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


class MissionForecastNotFound(AutonomousMissionError):
    pass


class MissionScenarioNotFound(AutonomousMissionError):
    pass


class WorkspacePortfolioSimulationNotFound(AutonomousMissionError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return ensure_utc(value).isoformat()


def clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return round(min(maximum, max(minimum, float(value))), 4)


def money(value: float) -> float:
    return round(max(0.0, float(value)), 6)


class MissionForecastService:
    """Heuristic outcome forecasting and explicit what-if simulation.

    Forecasts are advisory. They never change Mission state, allocate resources,
    select a scenario or start work without a separate, explicit action. The
    built-in method is deterministic and stores its assumptions and drivers so
    operators can inspect how a result was produced.
    """

    TERMINAL_CYCLE_STATUSES = {"completed", "failed", "cancelled"}
    OPEN_RISK_STATUSES = {"identified", "monitoring", "materialized"}

    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._mission_locks: dict[str, asyncio.Lock] = {}
        self._forecasts_generated = 0
        self._scenarios_evaluated = 0
        self._simulations_completed = 0
        self._automatic_refreshes = 0

    def status(self) -> dict[str, Any]:
        with self._session_factory() as session:
            policies = int(
                session.scalar(
                    select(func.count()).select_from(MissionForecastPolicyModel)
                )
                or 0
            )
            forecasts = int(
                session.scalar(select(func.count()).select_from(MissionForecastModel))
                or 0
            )
            scenarios = int(
                session.scalar(select(func.count()).select_from(MissionScenarioModel))
                or 0
            )
            simulations = int(
                session.scalar(
                    select(func.count()).select_from(
                        WorkspacePortfolioSimulationModel
                    )
                )
                or 0
            )
        return {
            "policies": policies,
            "forecasts": forecasts,
            "scenarios": scenarios,
            "simulations": simulations,
            "forecasts_generated": self._forecasts_generated,
            "scenarios_evaluated": self._scenarios_evaluated,
            "simulations_completed": self._simulations_completed,
            "automatic_refreshes": self._automatic_refreshes,
            "method": "builtin.heuristic.v1",
            "default_safety": {
                "enabled": False,
                "forecast_mode": "manual",
                "auto_refresh_enabled": False,
                "require_human_approval": True,
                "allow_auto_scenario_selection": False,
            },
            "capabilities": [
                "mission_outcome_forecast",
                "transparent_forecast_drivers",
                "forecast_history_and_invalidation",
                "mission_scenario_portfolio",
                "selected_scenario_planner_context",
                "workspace_what_if_simulation",
                "budget_and_capacity_constrained_simulation",
                "event_driven_forecast_refresh",
                "audit_and_event_transport",
            ],
        }

    def get_policy(self, mission_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            row = self._policy_row(session, mission_id)
            return self._policy_to_dict(row, mission)

    async def upsert_policy(
        self,
        mission_id: str,
        request: MissionForecastPolicyUpsert,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            row = self._policy_row(session, mission_id)
            created = row is None
            if row is None:
                row = MissionForecastPolicyModel(
                    workspace_id=mission.workspace_id,
                    mission_id=mission.id,
                )
                session.add(row)
            values = request.model_dump(mode="json")
            values["forecast_mode"] = request.forecast_mode.value
            values["metadata_json"] = values.pop("metadata")
            for key, value in values.items():
                setattr(row, key, value)
            session.flush()
            result = self._policy_to_dict(row, mission)
            correlation_id = row.id
            workspace_id = mission.workspace_id

        await self._publish(
            "mission.forecast.policy.created"
            if created
            else "mission.forecast.policy.updated",
            workspace_id=workspace_id,
            correlation_id=correlation_id,
            payload={"mission_id": mission_id, "policy": result},
        )
        return result

    async def generate_forecast(
        self,
        mission_id: str,
        request: MissionForecastGenerateRequest,
    ) -> dict[str, Any]:
        lock = self._mission_locks.setdefault(mission_id, asyncio.Lock())
        async with lock:
            with self._session_factory() as session:
                mission = self._require_mission(session, mission_id)
                policy = self._policy_row(session, mission_id)
                horizon = int(
                    request.horizon_cycles
                    or (policy.horizon_cycles if policy is not None else 10)
                )
                calculated = self._calculate_forecast(
                    session,
                    mission,
                    horizon_cycles=horizon,
                    assumptions=request.assumptions,
                )
                current_rows = list(
                    session.scalars(
                        select(MissionForecastModel).where(
                            MissionForecastModel.mission_id == mission_id,
                            MissionForecastModel.status
                            == MissionForecastStatus.CURRENT.value,
                        )
                    ).all()
                )
                now = utc_now()
                for current in current_rows:
                    current.status = MissionForecastStatus.SUPERSEDED.value
                row = MissionForecastModel(
                    workspace_id=mission.workspace_id,
                    mission_id=mission.id,
                    policy_id=policy.id if policy is not None else None,
                    status=MissionForecastStatus.CURRENT.value,
                    method=request.method,
                    horizon_cycles=horizon,
                    sample_count=calculated["sample_count"],
                    success_probability_percent=calculated[
                        "success_probability_percent"
                    ],
                    completion_probability_percent=calculated[
                        "completion_probability_percent"
                    ],
                    expected_progress_percent=calculated[
                        "expected_progress_percent"
                    ],
                    confidence_percent=calculated["confidence_percent"],
                    risk_score=calculated["risk_score"],
                    expected_remaining_cycles=calculated[
                        "expected_remaining_cycles"
                    ],
                    expected_cost_usd=calculated["expected_cost_usd"],
                    p50_completion_at=calculated["p50_completion_at"],
                    p90_completion_at=calculated["p90_completion_at"],
                    actor_id=request.actor_id,
                    reason=request.reason,
                    assumptions_json=calculated["assumptions"],
                    drivers_json=calculated["drivers"],
                    metrics_json=calculated["metrics"],
                    generated_at=now,
                )
                session.add(row)
                session.flush()
                result = self._forecast_to_dict(row)
                workspace_id = mission.workspace_id
                forecast_id = row.id

            self._forecasts_generated += 1
            await self._publish(
                "mission.forecast.generated",
                workspace_id=workspace_id,
                correlation_id=forecast_id,
                payload={"mission_id": mission_id, "forecast": result},
            )
            return result

    def get_forecast(self, forecast_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(MissionForecastModel, forecast_id)
            return self._forecast_to_dict(row) if row is not None else None

    def list_forecasts(
        self,
        mission_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_mission(session, mission_id)
            stmt = select(MissionForecastModel).where(
                MissionForecastModel.mission_id == mission_id
            )
            if status:
                stmt = stmt.where(MissionForecastModel.status == status)
            rows = list(
                session.scalars(
                    stmt.order_by(MissionForecastModel.generated_at.desc())
                    .limit(limit)
                    .offset(offset)
                ).all()
            )
            return [self._forecast_to_dict(row) for row in rows]

    async def invalidate_forecast(
        self,
        forecast_id: str,
        *,
        actor_id: str,
        reason: str,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_forecast(session, forecast_id)
            if row.status == MissionForecastStatus.INVALIDATED.value:
                return self._forecast_to_dict(row)
            row.status = MissionForecastStatus.INVALIDATED.value
            row.invalidated_at = utc_now()
            metrics = dict(row.metrics_json or {})
            metrics["invalidation"] = {"actor_id": actor_id, "reason": reason}
            row.metrics_json = metrics
            result = self._forecast_to_dict(row)
            workspace_id = row.workspace_id
            mission_id = row.mission_id
        await self._publish(
            "mission.forecast.invalidated",
            workspace_id=workspace_id,
            correlation_id=forecast_id,
            payload={
                "mission_id": mission_id,
                "forecast_id": forecast_id,
                "actor_id": actor_id,
                "reason": reason,
            },
        )
        return result

    def dashboard(self, mission_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            policy = self._policy_row(session, mission_id)
            current = self._current_forecast_row(session, mission_id)
            selected = self._selected_scenario_row(session, mission_id)
            forecasts = list(
                session.scalars(
                    select(MissionForecastModel)
                    .where(MissionForecastModel.mission_id == mission_id)
                    .order_by(MissionForecastModel.generated_at.desc())
                    .limit(20)
                ).all()
            )
            scenarios = list(
                session.scalars(
                    select(MissionScenarioModel)
                    .where(MissionScenarioModel.mission_id == mission_id)
                    .order_by(MissionScenarioModel.updated_at.desc())
                    .limit(50)
                ).all()
            )
            stale = self._is_stale(current, policy)
            return {
                "mission": {
                    "id": mission.id,
                    "workspace_id": mission.workspace_id,
                    "title": mission.title,
                    "status": mission.status,
                    "priority": mission.priority,
                    "progress_percent": mission.progress_percent,
                    "deadline_at": iso(mission.deadline_at),
                },
                "policy": self._policy_to_dict(policy, mission),
                "current_forecast": (
                    self._forecast_to_dict(current) if current is not None else None
                ),
                "forecast_stale": stale,
                "selected_scenario": (
                    self._scenario_to_dict(selected) if selected is not None else None
                ),
                "recent_forecasts": [
                    self._forecast_to_dict(row) for row in forecasts
                ],
                "scenarios": [self._scenario_to_dict(row) for row in scenarios],
            }

    def build_context(
        self,
        mission_id: str,
        *,
        cycle_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            policy = self._policy_row(session, mission_id)
            forecast = self._current_forecast_row(session, mission_id)
            scenario = self._selected_scenario_row(session, mission_id)
            recent = list(
                session.scalars(
                    select(MissionForecastModel)
                    .where(MissionForecastModel.mission_id == mission_id)
                    .order_by(MissionForecastModel.generated_at.desc())
                    .limit(limit)
                ).all()
            )
            return {
                "mission_id": mission_id,
                "workspace_id": mission.workspace_id,
                "cycle_id": cycle_id,
                "policy": self._policy_to_dict(policy, mission),
                "current_forecast": (
                    self._forecast_to_dict(forecast) if forecast is not None else None
                ),
                "forecast_stale": self._is_stale(forecast, policy),
                "selected_scenario": (
                    self._scenario_to_dict(scenario) if scenario is not None else None
                ),
                "recent_forecasts": [
                    self._forecast_to_dict(row) for row in recent
                ],
                "advisory_only": True,
            }

    async def create_scenario(
        self,
        mission_id: str,
        request: MissionScenarioCreate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            policy = self._policy_row(session, mission_id)
            count = int(
                session.scalar(
                    select(func.count())
                    .select_from(MissionScenarioModel)
                    .where(
                        MissionScenarioModel.mission_id == mission_id,
                        MissionScenarioModel.status
                        != MissionScenarioStatus.ARCHIVED.value,
                    )
                )
                or 0
            )
            maximum = policy.max_scenarios if policy is not None else 20
            if count >= maximum:
                raise AutonomousMissionError(
                    f"Mission scenario limit reached: {maximum}."
                )
            row = MissionScenarioModel(
                workspace_id=mission.workspace_id,
                mission_id=mission.id,
                scenario_key=request.scenario_key,
                title=request.title,
                description=request.description,
                scenario_type=request.scenario_type.value,
                status=MissionScenarioStatus.DRAFT.value,
                overrides_json=dict(request.overrides),
                assumptions_json=dict(request.assumptions),
                metadata_json=dict(request.metadata),
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError as exc:
                raise AutonomousMissionError(
                    f"Scenario key already exists: {request.scenario_key}."
                ) from exc
            result = self._scenario_to_dict(row)
            workspace_id = mission.workspace_id
            scenario_id = row.id

        await self._publish(
            "mission.scenario.created",
            workspace_id=workspace_id,
            correlation_id=scenario_id,
            payload={"mission_id": mission_id, "scenario": result},
        )
        if request.evaluate:
            return await self.evaluate_scenario(
                scenario_id,
                MissionScenarioEvaluateRequest(
                    actor_id="system",
                    reason="evaluate on scenario creation",
                ),
            )
        return result

    def get_scenario(self, scenario_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(MissionScenarioModel, scenario_id)
            return self._scenario_to_dict(row) if row is not None else None

    def list_scenarios(
        self,
        mission_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_mission(session, mission_id)
            stmt = select(MissionScenarioModel).where(
                MissionScenarioModel.mission_id == mission_id
            )
            if status:
                stmt = stmt.where(MissionScenarioModel.status == status)
            rows = list(
                session.scalars(
                    stmt.order_by(MissionScenarioModel.updated_at.desc())
                    .limit(limit)
                    .offset(offset)
                ).all()
            )
            return [self._scenario_to_dict(row) for row in rows]

    async def update_scenario(
        self,
        scenario_id: str,
        request: MissionScenarioUpdate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_scenario(session, scenario_id)
            if row.status == MissionScenarioStatus.ARCHIVED.value:
                raise AutonomousMissionError("Archived scenario cannot be changed.")
            if request.expected_version is not None and row.version != request.expected_version:
                raise AutonomousMissionError(
                    f"Scenario version conflict: expected {request.expected_version}, current {row.version}."
                )
            values = request.model_dump(exclude_unset=True, mode="json")
            values.pop("expected_version", None)
            if "scenario_type" in values:
                values["scenario_type"] = request.scenario_type.value
            if "overrides" in values:
                values["overrides_json"] = values.pop("overrides")
            if "assumptions" in values:
                values["assumptions_json"] = values.pop("assumptions")
            if "metadata" in values:
                values["metadata_json"] = values.pop("metadata")
            for key, value in values.items():
                setattr(row, key, value)
            row.status = MissionScenarioStatus.DRAFT.value
            row.result_json = {}
            row.score = 0.0
            row.evaluated_at = None
            row.version += 1
            result = self._scenario_to_dict(row)
            workspace_id = row.workspace_id
            mission_id = row.mission_id

        await self._publish(
            "mission.scenario.updated",
            workspace_id=workspace_id,
            correlation_id=scenario_id,
            payload={"mission_id": mission_id, "scenario": result},
        )
        return result

    async def evaluate_scenario(
        self,
        scenario_id: str,
        request: MissionScenarioEvaluateRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_scenario(session, scenario_id)
            if row.status == MissionScenarioStatus.ARCHIVED.value:
                raise AutonomousMissionError("Archived scenario cannot be evaluated.")
            mission = self._require_mission(session, row.mission_id)
            policy = self._policy_row(session, mission.id)
            baseline = None if request.refresh_baseline else self._current_forecast_row(
                session, mission.id
            )
            if baseline is None:
                horizon = policy.horizon_cycles if policy is not None else 10
                calculated = self._calculate_forecast(
                    session,
                    mission,
                    horizon_cycles=horizon,
                    assumptions={},
                )
                baseline_result = self._calculated_forecast_to_dict(
                    mission,
                    calculated,
                    horizon_cycles=horizon,
                )
                baseline_id = None
            else:
                baseline_result = self._forecast_to_dict(baseline)
                baseline_id = baseline.id
            result_json = self._apply_scenario(
                baseline_result,
                scenario_type=row.scenario_type,
                overrides=dict(row.overrides_json or {}),
            )
            row.baseline_forecast_id = baseline_id
            row.result_json = result_json
            row.score = result_json["scenario_score"]
            if row.status != MissionScenarioStatus.SELECTED.value:
                row.status = MissionScenarioStatus.EVALUATED.value
            row.evaluated_at = utc_now()
            row.version += 1
            result = self._scenario_to_dict(row)
            workspace_id = mission.workspace_id
            mission_id = mission.id

        self._scenarios_evaluated += 1
        await self._publish(
            "mission.scenario.evaluated",
            workspace_id=workspace_id,
            correlation_id=scenario_id,
            payload={
                "mission_id": mission_id,
                "scenario": result,
                "actor_id": request.actor_id,
                "reason": request.reason,
            },
        )
        return result

    async def select_scenario(
        self,
        scenario_id: str,
        request: MissionScenarioSelectionRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_scenario(session, scenario_id)
            mission = self._require_mission(session, row.mission_id)
            policy = self._policy_row(session, mission.id)
            if row.status == MissionScenarioStatus.ARCHIVED.value:
                raise AutonomousMissionError("Archived scenario cannot be selected.")
            if not row.result_json:
                raise AutonomousMissionError("Scenario must be evaluated before selection.")
            if request.automatic:
                allowed = bool(
                    policy is not None
                    and policy.enabled
                    and policy.forecast_mode
                    in {
                        MissionForecastMode.HEURISTIC.value,
                        MissionForecastMode.ADAPTIVE.value,
                    }
                    and policy.allow_auto_scenario_selection
                    and not policy.require_human_approval
                )
                if not allowed and not request.force:
                    raise AutonomousMissionError(
                        "Automatic scenario selection is disabled by policy."
                    )
            previous = list(
                session.scalars(
                    select(MissionScenarioModel).where(
                        MissionScenarioModel.mission_id == mission.id,
                        MissionScenarioModel.status
                        == MissionScenarioStatus.SELECTED.value,
                        MissionScenarioModel.id != row.id,
                    )
                ).all()
            )
            for old in previous:
                old.status = MissionScenarioStatus.EVALUATED.value
                old.selected_at = None
                old.selected_by = None
                old.selection_reason = None
                old.version += 1
            row.status = MissionScenarioStatus.SELECTED.value
            row.selected_at = utc_now()
            row.selected_by = request.actor_id
            row.selection_reason = request.reason
            row.version += 1
            result = self._scenario_to_dict(row)
            workspace_id = mission.workspace_id
            mission_id = mission.id

        await self._publish(
            "mission.scenario.selected",
            workspace_id=workspace_id,
            correlation_id=scenario_id,
            payload={
                "mission_id": mission_id,
                "scenario": result,
                "automatic": request.automatic,
                "forced": request.force,
            },
        )
        return result

    async def archive_scenario(
        self,
        scenario_id: str,
        request: MissionScenarioArchiveRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_scenario(session, scenario_id)
            row.status = MissionScenarioStatus.ARCHIVED.value
            row.archived_at = utc_now()
            row.selected_at = None
            row.selected_by = None
            row.selection_reason = None
            metadata = dict(row.metadata_json or {})
            metadata["archive"] = {
                "actor_id": request.actor_id,
                "reason": request.reason,
            }
            row.metadata_json = metadata
            row.version += 1
            result = self._scenario_to_dict(row)
            workspace_id = row.workspace_id
            mission_id = row.mission_id
        await self._publish(
            "mission.scenario.archived",
            workspace_id=workspace_id,
            correlation_id=scenario_id,
            payload={"mission_id": mission_id, "scenario": result},
        )
        return result

    async def simulate_workspace(
        self,
        workspace_id: str,
        request: WorkspacePortfolioSimulationRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            self._require_workspace(session, workspace_id)
            missions = list(
                session.scalars(
                    select(WorkspaceMissionModel)
                    .where(
                        WorkspaceMissionModel.workspace_id == workspace_id,
                        WorkspaceMissionModel.status.in_(request.include_statuses),
                    )
                    .order_by(
                        WorkspaceMissionModel.priority.desc(),
                        WorkspaceMissionModel.created_at.asc(),
                    )
                ).all()
            )
            pool = session.scalar(
                select(WorkspaceResourcePolicyModel).where(
                    WorkspaceResourcePolicyModel.workspace_id == workspace_id
                )
            )
            capacity = self._simulation_capacity(pool, request)
            candidates: list[dict[str, Any]] = []
            for mission in missions:
                candidate = self._simulation_candidate(
                    session,
                    mission,
                    scenario_id=request.mission_scenario_ids.get(mission.id),
                    overrides=request.mission_overrides.get(mission.id, {}),
                )
                candidates.append(candidate)
            candidates.sort(
                key=lambda item: (
                    item["portfolio_score"],
                    item["mission_priority"],
                    item["success_probability_percent"],
                ),
                reverse=True,
            )
            selected: list[dict[str, Any]] = []
            deferred: list[dict[str, Any]] = []
            remaining = dict(capacity)
            for rank, candidate in enumerate(candidates, start=1):
                candidate["rank"] = rank
                reasons: list[str] = []
                if len(selected) >= int(capacity["max_parallel_missions"]):
                    reasons.append("parallel_mission_limit")
                if candidate["expected_cost_usd"] > remaining["budget_usd"]:
                    reasons.append("budget")
                if candidate["agent_slots"] > remaining["agent_slots"]:
                    reasons.append("agent_slots")
                if candidate["tool_slots"] > remaining["tool_slots"]:
                    reasons.append("tool_slots")
                if candidate["compute_units"] > remaining["compute_units"]:
                    reasons.append("compute_units")
                if reasons:
                    candidate["decision"] = "defer"
                    candidate["reasons"] = reasons
                    deferred.append(candidate)
                    continue
                candidate["decision"] = "select"
                candidate["reasons"] = []
                selected.append(candidate)
                remaining["budget_usd"] = money(
                    remaining["budget_usd"] - candidate["expected_cost_usd"]
                )
                remaining["agent_slots"] -= candidate["agent_slots"]
                remaining["tool_slots"] -= candidate["tool_slots"]
                remaining["compute_units"] = round(
                    remaining["compute_units"] - candidate["compute_units"], 4
                )
            expected_cost = money(
                sum(item["expected_cost_usd"] for item in selected)
            )
            expected_successes = round(
                sum(item["success_probability_percent"] / 100 for item in selected),
                4,
            )
            portfolio_score = clamp(
                sum(item["portfolio_score"] for item in selected)
                / max(1, len(selected))
            )
            now = utc_now()
            result_json = {
                "capacity": capacity,
                "remaining_capacity": remaining,
                "selected": selected,
                "deferred": deferred,
                "summary": {
                    "mission_count": len(candidates),
                    "selected_count": len(selected),
                    "deferred_count": len(deferred),
                    "expected_cost_usd": expected_cost,
                    "expected_successful_missions": expected_successes,
                    "portfolio_score": portfolio_score,
                },
                "advisory_only": True,
            }
            row = WorkspacePortfolioSimulationModel(
                workspace_id=workspace_id,
                name=request.name,
                status=PortfolioSimulationStatus.COMPLETED.value,
                actor_id=request.actor_id,
                mission_count=len(candidates),
                selected_count=len(selected),
                portfolio_score=portfolio_score,
                expected_cost_usd=expected_cost,
                expected_successful_missions=expected_successes,
                input_json=request.model_dump(mode="json"),
                result_json=result_json,
                assumptions_json=dict(request.assumptions),
                metadata_json=dict(request.metadata),
                completed_at=now,
            )
            session.add(row)
            session.flush()
            result = self._simulation_to_dict(row)
            simulation_id = row.id

        self._simulations_completed += 1
        await self._publish(
            "mission.portfolio_simulation.completed",
            workspace_id=workspace_id,
            correlation_id=simulation_id,
            payload={"simulation": result},
        )
        return result

    def get_simulation(self, simulation_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(WorkspacePortfolioSimulationModel, simulation_id)
            return self._simulation_to_dict(row) if row is not None else None

    def list_simulations(
        self,
        workspace_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_workspace(session, workspace_id)
            rows = list(
                session.scalars(
                    select(WorkspacePortfolioSimulationModel)
                    .where(
                        WorkspacePortfolioSimulationModel.workspace_id
                        == workspace_id
                    )
                    .order_by(
                        WorkspacePortfolioSimulationModel.created_at.desc()
                    )
                    .limit(limit)
                    .offset(offset)
                ).all()
            )
            return [self._simulation_to_dict(row) for row in rows]

    def compare_simulations(
        self,
        workspace_id: str,
        simulation_ids: list[str],
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            self._require_workspace(session, workspace_id)
            rows = list(
                session.scalars(
                    select(WorkspacePortfolioSimulationModel).where(
                        WorkspacePortfolioSimulationModel.workspace_id
                        == workspace_id,
                        WorkspacePortfolioSimulationModel.id.in_(simulation_ids),
                    )
                ).all()
            )
            found = {row.id for row in rows}
            missing = [item for item in simulation_ids if item not in found]
            if missing:
                raise WorkspacePortfolioSimulationNotFound(
                    f"Portfolio simulations not found: {', '.join(missing)}."
                )
            ordered = sorted(
                rows,
                key=lambda row: (
                    row.portfolio_score,
                    row.expected_successful_missions,
                    -row.expected_cost_usd,
                ),
                reverse=True,
            )
            best = ordered[0]
            return {
                "workspace_id": workspace_id,
                "best_simulation_id": best.id,
                "comparison": [
                    {
                        "rank": index,
                        "simulation_id": row.id,
                        "name": row.name,
                        "portfolio_score": row.portfolio_score,
                        "mission_count": row.mission_count,
                        "selected_count": row.selected_count,
                        "expected_cost_usd": row.expected_cost_usd,
                        "expected_successful_missions": (
                            row.expected_successful_missions
                        ),
                        "delta_score_from_best": round(
                            row.portfolio_score - best.portfolio_score, 4
                        ),
                        "delta_cost_from_best_usd": money(
                            row.expected_cost_usd - best.expected_cost_usd
                        )
                        if row.expected_cost_usd >= best.expected_cost_usd
                        else -money(best.expected_cost_usd - row.expected_cost_usd),
                    }
                    for index, row in enumerate(ordered, start=1)
                ],
            }

    async def handle_event(self, event: Event) -> None:
        mission_id = str(event.payload.get("mission_id") or event.correlation_id or "")
        if not mission_id:
            return
        with self._session_factory() as session:
            mission = session.get(WorkspaceMissionModel, mission_id)
            if mission is None:
                return
            policy = self._policy_row(session, mission_id)
            if (
                policy is None
                or not policy.enabled
                or not policy.auto_refresh_enabled
                or policy.forecast_mode == MissionForecastMode.MANUAL.value
            ):
                return
        await self.generate_forecast(
            mission_id,
            MissionForecastGenerateRequest(
                actor_id="system",
                reason=f"automatic refresh after {event.event_type}",
            ),
        )
        self._automatic_refreshes += 1

    def _calculate_forecast(
        self,
        session: Session,
        mission: WorkspaceMissionModel,
        *,
        horizon_cycles: int,
        assumptions: dict[str, Any],
    ) -> dict[str, Any]:
        cycles = list(
            session.scalars(
                select(MissionCycleModel)
                .where(MissionCycleModel.mission_id == mission.id)
                .order_by(MissionCycleModel.cycle_number.asc())
            ).all()
        )
        terminal = [row for row in cycles if row.status in self.TERMINAL_CYCLE_STATUSES]
        completed = [row for row in terminal if row.status == "completed"]
        failed = [row for row in terminal if row.status == "failed"]
        cancelled = [row for row in terminal if row.status == "cancelled"]
        sample_count = len(terminal)
        historical_success = (
            100.0 * len(completed) / sample_count if sample_count else 50.0
        )

        progress_updates = list(
            session.scalars(
                select(MissionProgressUpdateModel)
                .where(MissionProgressUpdateModel.mission_id == mission.id)
                .order_by(MissionProgressUpdateModel.created_at.asc())
            ).all()
        )
        positive_progress = sum(
            max(0.0, row.new_progress_percent - row.previous_progress_percent)
            for row in progress_updates
        )
        progress_per_completed_cycle = (
            positive_progress / len(completed) if completed else 0.0
        )
        if progress_per_completed_cycle <= 0:
            progress_per_completed_cycle = max(
                2.0,
                mission.progress_percent / max(1, mission.cycle_count),
            )

        risks = list(
            session.scalars(
                select(MissionRiskModel).where(
                    MissionRiskModel.mission_id == mission.id,
                    MissionRiskModel.status.in_(self.OPEN_RISK_STATUSES),
                )
            ).all()
        )
        risk_score = (
            sum(row.exposure_score for row in risks) / len(risks) if risks else 0.0
        )
        critical_risks = sum(1 for row in risks if row.severity == "critical")

        dependencies = list(
            session.scalars(
                select(MissionDependencyModel).where(
                    MissionDependencyModel.mission_id == mission.id
                )
            ).all()
        )
        hard_blockers = sum(
            1
            for row in dependencies
            if row.dependency_type == "hard"
            and row.status not in {"satisfied", "waived"}
        )

        strategy = session.scalar(
            select(MissionStrategyModel)
            .where(
                MissionStrategyModel.mission_id == mission.id,
                MissionStrategyModel.status == "selected",
            )
            .order_by(MissionStrategyModel.selected_at.desc())
        )
        strategy_score = (
            float(strategy.adaptive_score) if strategy is not None else 50.0
        )

        total_cost = float(
            session.scalar(
                select(func.coalesce(func.sum(MissionResourceUsageModel.cost_usd), 0.0))
                .where(MissionResourceUsageModel.mission_id == mission.id)
            )
            or 0.0
        )
        resource_policy = session.scalar(
            select(MissionResourcePolicyModel).where(
                MissionResourcePolicyModel.mission_id == mission.id
            )
        )
        avg_cost_per_cycle = (
            total_cost / max(1, sample_count)
            if total_cost > 0
            else (
                float(resource_policy.default_cycle_budget_usd)
                if resource_policy is not None
                else 0.0
            )
        )

        durations = []
        for row in terminal:
            if row.started_at is not None and row.finished_at is not None:
                seconds = (
                    ensure_utc(row.finished_at) - ensure_utc(row.started_at)
                ).total_seconds()
                if seconds >= 0:
                    durations.append(seconds)
        schedule = session.scalar(
            select(MissionSchedulePolicyModel).where(
                MissionSchedulePolicyModel.mission_id == mission.id
            )
        )
        cycle_seconds = (
            sum(durations) / len(durations)
            if durations
            else float(schedule.base_interval_seconds if schedule is not None else 3600)
        )

        remaining_progress = max(0.0, 100.0 - mission.progress_percent)
        remaining_cycles = (
            remaining_progress / max(0.1, progress_per_completed_cycle)
            if remaining_progress > 0
            else 0.0
        )
        remaining_cycles = min(float(horizon_cycles), max(0.0, remaining_cycles))
        available_cycles = max(0, mission.max_cycles - mission.cycle_count)

        priority_effect = (mission.priority - 50.0) * 0.12
        progress_effect = mission.progress_percent * 0.16
        history_effect = (historical_success - 50.0) * 0.34
        strategy_effect = (strategy_score - 50.0) * 0.15
        risk_penalty = risk_score * 0.24 + critical_risks * 6.0
        dependency_penalty = hard_blockers * 12.0
        failure_penalty = len(failed) * 1.5 + len(cancelled) * 0.5
        success_probability = clamp(
            48.0
            + priority_effect
            + progress_effect
            + history_effect
            + strategy_effect
            - risk_penalty
            - dependency_penalty
            - failure_penalty
        )

        capacity_factor = 100.0
        if remaining_cycles > available_cycles and remaining_progress > 0:
            capacity_factor = clamp(100.0 * available_cycles / max(1.0, remaining_cycles))
        deadline_factor = 100.0
        now = utc_now()
        predicted_seconds = remaining_cycles * max(60.0, cycle_seconds)
        if mission.deadline_at is not None:
            seconds_left = max(
                0.0,
                (ensure_utc(mission.deadline_at) - now).total_seconds(),
            )
            deadline_factor = clamp(
                100.0 if predicted_seconds <= 0 else 100.0 * seconds_left / predicted_seconds
            )
        completion_probability = clamp(
            success_probability * 0.65
            + capacity_factor * 0.20
            + deadline_factor * 0.15
        )
        expected_progress = clamp(
            mission.progress_percent
            + min(
                remaining_progress,
                remaining_cycles
                * progress_per_completed_cycle
                * success_probability
                / 100.0,
            )
        )
        data_coverage = min(1.0, sample_count / 8.0)
        progress_coverage = min(1.0, len(progress_updates) / 5.0)
        confidence = clamp(
            15.0
            + data_coverage * 50.0
            + progress_coverage * 15.0
            + (10.0 if strategy is not None else 0.0)
            + (10.0 if risks else 0.0)
        )
        expected_cost = money(avg_cost_per_cycle * remaining_cycles)
        p50 = now + timedelta(seconds=predicted_seconds) if remaining_cycles > 0 else now
        uncertainty_multiplier = 1.15 + (100.0 - confidence) / 100.0
        p90 = (
            now + timedelta(seconds=predicted_seconds * uncertainty_multiplier)
            if remaining_cycles > 0
            else now
        )
        drivers = [
            {
                "name": "historical_cycle_success",
                "value": round(historical_success, 4),
                "effect": round(history_effect, 4),
            },
            {
                "name": "mission_progress",
                "value": round(mission.progress_percent, 4),
                "effect": round(progress_effect, 4),
            },
            {
                "name": "open_risk_exposure",
                "value": round(risk_score, 4),
                "effect": round(-risk_penalty, 4),
            },
            {
                "name": "hard_dependency_blockers",
                "value": hard_blockers,
                "effect": round(-dependency_penalty, 4),
            },
            {
                "name": "selected_strategy_score",
                "value": round(strategy_score, 4),
                "effect": round(strategy_effect, 4),
            },
        ]
        default_assumptions = {
            "method": "builtin.heuristic.v1",
            "forecast_is_advisory": True,
            "future_cycle_cost_matches_observed_average": True,
            "future_progress_velocity_matches_observed_velocity": True,
            "no_unmodeled_external_shock": True,
        }
        default_assumptions.update(dict(assumptions))

        calibration = session.scalar(
            select(MissionForecastCalibrationModel)
            .where(
                MissionForecastCalibrationModel.scope_type == "mission",
                MissionForecastCalibrationModel.scope_id == mission.id,
                MissionForecastCalibrationModel.status == "current",
            )
            .order_by(MissionForecastCalibrationModel.applied_at.desc())
        )
        if calibration is None:
            calibration = session.scalar(
                select(MissionForecastCalibrationModel)
                .where(
                    MissionForecastCalibrationModel.scope_type == "workspace",
                    MissionForecastCalibrationModel.scope_id
                    == mission.workspace_id,
                    MissionForecastCalibrationModel.status == "current",
                )
                .order_by(MissionForecastCalibrationModel.applied_at.desc())
            )
        if calibration is not None:
            success_probability = clamp(
                success_probability + calibration.success_bias_percent
            )
            completion_probability = clamp(
                completion_probability + calibration.completion_bias_percent
            )
            expected_cost = money(
                expected_cost * calibration.cost_multiplier
            )
            remaining_cycles = max(
                0.0,
                remaining_cycles * calibration.cycle_multiplier,
            )
            predicted_seconds = max(
                0.0,
                predicted_seconds * calibration.duration_multiplier,
            )
            confidence = clamp(
                confidence * calibration.confidence_multiplier
            )
            p50 = (
                now + timedelta(seconds=predicted_seconds)
                if remaining_cycles > 0
                else now
            )
            p90 = (
                now
                + timedelta(
                    seconds=predicted_seconds * uncertainty_multiplier
                )
                if remaining_cycles > 0
                else now
            )
            drivers.append(
                {
                    "name": "forecast_calibration",
                    "value": calibration.id,
                    "effect": {
                        "success_bias_percent": (
                            calibration.success_bias_percent
                        ),
                        "completion_bias_percent": (
                            calibration.completion_bias_percent
                        ),
                        "cost_multiplier": calibration.cost_multiplier,
                        "cycle_multiplier": calibration.cycle_multiplier,
                        "duration_multiplier": (
                            calibration.duration_multiplier
                        ),
                        "confidence_multiplier": (
                            calibration.confidence_multiplier
                        ),
                    },
                }
            )
            default_assumptions.update(
                {
                    "calibration_id": calibration.id,
                    "calibration_scope_type": calibration.scope_type,
                    "calibration_scope_id": calibration.scope_id,
                    "calibration_method": calibration.method,
                }
            )
        return {
            "sample_count": sample_count,
            "success_probability_percent": success_probability,
            "completion_probability_percent": completion_probability,
            "expected_progress_percent": expected_progress,
            "confidence_percent": confidence,
            "risk_score": clamp(risk_score),
            "expected_remaining_cycles": round(remaining_cycles, 4),
            "expected_cost_usd": expected_cost,
            "p50_completion_at": p50,
            "p90_completion_at": p90,
            "assumptions": default_assumptions,
            "drivers": drivers,
            "metrics": {
                "cycle_count": len(cycles),
                "terminal_cycle_count": sample_count,
                "completed_cycles": len(completed),
                "failed_cycles": len(failed),
                "cancelled_cycles": len(cancelled),
                "historical_success_percent": round(historical_success, 4),
                "progress_updates": len(progress_updates),
                "progress_per_completed_cycle": round(
                    progress_per_completed_cycle, 4
                ),
                "open_risks": len(risks),
                "critical_risks": critical_risks,
                "hard_dependency_blockers": hard_blockers,
                "selected_strategy_id": strategy.id if strategy is not None else None,
                "average_cycle_duration_seconds": round(cycle_seconds, 4),
                "average_cycle_cost_usd": money(avg_cost_per_cycle),
                "available_cycles": available_cycles,
                "deadline_factor_percent": deadline_factor,
                "capacity_factor_percent": capacity_factor,
                "calibration_id": (
                    calibration.id if calibration is not None else None
                ),
                "calibration_scope": (
                    calibration.scope_type if calibration is not None else None
                ),
            },
        }

    def _apply_scenario(
        self,
        baseline: dict[str, Any],
        *,
        scenario_type: str,
        overrides: dict[str, Any],
    ) -> dict[str, Any]:
        defaults: dict[str, float] = {}
        if scenario_type == MissionScenarioType.OPTIMISTIC.value:
            defaults = {
                "success_probability_delta": 10.0,
                "completion_probability_delta": 10.0,
                "risk_delta_percent": -15.0,
                "cost_multiplier": 0.9,
                "duration_multiplier": 0.85,
            }
        elif scenario_type == MissionScenarioType.PESSIMISTIC.value:
            defaults = {
                "success_probability_delta": -15.0,
                "completion_probability_delta": -20.0,
                "risk_delta_percent": 25.0,
                "cost_multiplier": 1.25,
                "duration_multiplier": 1.4,
            }
        elif scenario_type == MissionScenarioType.BASELINE.value:
            defaults = {
                "cost_multiplier": 1.0,
                "duration_multiplier": 1.0,
            }
        effective = {**defaults, **overrides}
        success = clamp(
            float(baseline.get("success_probability_percent", 50.0))
            + float(effective.get("success_probability_delta", 0.0))
        )
        completion = clamp(
            float(baseline.get("completion_probability_percent", 50.0))
            + float(effective.get("completion_probability_delta", 0.0))
        )
        risk = clamp(
            float(baseline.get("risk_score", 0.0))
            + float(effective.get("risk_delta_percent", 0.0))
        )
        expected_progress = clamp(
            float(baseline.get("expected_progress_percent", 0.0))
            + float(effective.get("progress_delta_percent", 0.0))
        )
        cost_multiplier = max(0.0, float(effective.get("cost_multiplier", 1.0)))
        duration_multiplier = max(
            0.01, float(effective.get("duration_multiplier", 1.0))
        )
        expected_cost = money(
            float(baseline.get("expected_cost_usd", 0.0)) * cost_multiplier
            + float(effective.get("additional_budget_usd", 0.0))
        )
        remaining_cycles = max(
            0.0,
            float(baseline.get("expected_remaining_cycles", 0.0))
            * duration_multiplier,
        )
        base_p50 = self._parse_datetime(baseline.get("p50_completion_at"))
        base_p90 = self._parse_datetime(baseline.get("p90_completion_at"))
        now = utc_now()
        p50 = (
            now + (base_p50 - now) * duration_multiplier
            if base_p50 is not None and base_p50 > now
            else base_p50
        )
        p90 = (
            now + (base_p90 - now) * duration_multiplier
            if base_p90 is not None and base_p90 > now
            else base_p90
        )
        cost_efficiency = clamp(100.0 / max(1.0, cost_multiplier))
        duration_efficiency = clamp(100.0 / max(1.0, duration_multiplier))
        score = clamp(
            success * 0.38
            + completion * 0.28
            + (100.0 - risk) * 0.14
            + cost_efficiency * 0.10
            + duration_efficiency * 0.10
        )
        return {
            "scenario_type": scenario_type,
            "effective_overrides": effective,
            "success_probability_percent": success,
            "completion_probability_percent": completion,
            "expected_progress_percent": expected_progress,
            "risk_score": risk,
            "expected_remaining_cycles": round(remaining_cycles, 4),
            "expected_cost_usd": expected_cost,
            "p50_completion_at": iso(p50),
            "p90_completion_at": iso(p90),
            "confidence_percent": float(baseline.get("confidence_percent", 0.0)),
            "scenario_score": score,
            "baseline_forecast_id": baseline.get("id"),
            "advisory_only": True,
        }

    def _simulation_candidate(
        self,
        session: Session,
        mission: WorkspaceMissionModel,
        *,
        scenario_id: str | None,
        overrides: dict[str, Any],
    ) -> dict[str, Any]:
        scenario = None
        if scenario_id:
            scenario = session.get(MissionScenarioModel, scenario_id)
            if scenario is None or scenario.mission_id != mission.id:
                raise MissionScenarioNotFound(
                    f"Scenario {scenario_id} does not belong to Mission {mission.id}."
                )
            if not scenario.result_json:
                raise AutonomousMissionError(
                    f"Scenario {scenario_id} has not been evaluated."
                )
            base = dict(scenario.result_json)
        else:
            scenario = self._selected_scenario_row(session, mission.id)
            if scenario is not None and scenario.result_json:
                base = dict(scenario.result_json)
            else:
                forecast = self._current_forecast_row(session, mission.id)
                if forecast is not None:
                    base = self._forecast_to_dict(forecast)
                else:
                    policy = self._policy_row(session, mission.id)
                    horizon = policy.horizon_cycles if policy is not None else 10
                    calculated = self._calculate_forecast(
                        session,
                        mission,
                        horizon_cycles=horizon,
                        assumptions={},
                    )
                    base = self._calculated_forecast_to_dict(
                        mission,
                        calculated,
                        horizon_cycles=horizon,
                    )
        if overrides:
            base = self._apply_scenario(
                base,
                scenario_type=MissionScenarioType.CUSTOM.value,
                overrides=overrides,
            )
        resource_policy = session.scalar(
            select(MissionResourcePolicyModel).where(
                MissionResourcePolicyModel.mission_id == mission.id
            )
        )
        agent_slots = int(
            overrides.get(
                "agent_slots",
                resource_policy.agent_slots if resource_policy is not None else 1,
            )
        )
        tool_slots = int(
            overrides.get(
                "tool_slots",
                resource_policy.tool_slots if resource_policy is not None else 1,
            )
        )
        compute_units = float(
            overrides.get(
                "compute_units",
                resource_policy.compute_units if resource_policy is not None else 1.0,
            )
        )
        success = float(base.get("success_probability_percent", 50.0))
        completion = float(base.get("completion_probability_percent", 50.0))
        risk = float(base.get("risk_score", 0.0))
        score = clamp(
            success * 0.38
            + completion * 0.25
            + mission.priority * 0.17
            + mission.progress_percent * 0.10
            + (100.0 - risk) * 0.10
        )
        return {
            "mission_id": mission.id,
            "title": mission.title,
            "status": mission.status,
            "mission_priority": mission.priority,
            "progress_percent": mission.progress_percent,
            "scenario_id": scenario.id if scenario is not None else None,
            "success_probability_percent": success,
            "completion_probability_percent": completion,
            "risk_score": risk,
            "confidence_percent": float(base.get("confidence_percent", 0.0)),
            "expected_cost_usd": money(base.get("expected_cost_usd", 0.0)),
            "expected_remaining_cycles": float(
                base.get("expected_remaining_cycles", 0.0)
            ),
            "p50_completion_at": base.get("p50_completion_at"),
            "agent_slots": max(0, agent_slots),
            "tool_slots": max(0, tool_slots),
            "compute_units": max(0.0, compute_units),
            "portfolio_score": score,
        }

    def _simulation_capacity(
        self,
        policy: WorkspaceResourcePolicyModel | None,
        request: WorkspacePortfolioSimulationRequest,
    ) -> dict[str, Any]:
        total_budget = (
            request.total_budget_usd
            if request.total_budget_usd is not None
            else (policy.total_budget_usd if policy is not None else 0.0)
        )
        if total_budget <= 0:
            total_budget = 1_000_000_000.0
        reserve_percent = policy.reserve_percent if policy is not None else 0.0
        allocatable_budget = money(total_budget * (1.0 - reserve_percent / 100.0))
        return {
            "budget_usd": allocatable_budget,
            "total_budget_usd": money(total_budget),
            "reserve_percent": round(reserve_percent, 4),
            "max_parallel_missions": int(
                request.max_parallel_missions
                if request.max_parallel_missions is not None
                else (policy.max_parallel_cycles if policy is not None else 1000)
            ),
            "agent_slots": int(
                request.agent_slots
                if request.agent_slots is not None
                else (policy.agent_slots if policy is not None else 100000)
            ),
            "tool_slots": int(
                request.tool_slots
                if request.tool_slots is not None
                else (policy.tool_slots if policy is not None else 100000)
            ),
            "compute_units": float(
                request.compute_units
                if request.compute_units is not None
                else (policy.compute_units if policy is not None else 100000.0)
            ),
            "source": "request_override"
            if any(
                value is not None
                for value in (
                    request.total_budget_usd,
                    request.max_parallel_missions,
                    request.agent_slots,
                    request.tool_slots,
                    request.compute_units,
                )
            )
            else ("workspace_resource_policy" if policy is not None else "unbounded"),
        }

    def _calculated_forecast_to_dict(
        self,
        mission: WorkspaceMissionModel,
        calculated: dict[str, Any],
        *,
        horizon_cycles: int,
    ) -> dict[str, Any]:
        return {
            "id": None,
            "workspace_id": mission.workspace_id,
            "mission_id": mission.id,
            "status": MissionForecastStatus.CURRENT.value,
            "method": "builtin.heuristic.v1",
            "horizon_cycles": horizon_cycles,
            "sample_count": calculated["sample_count"],
            "success_probability_percent": calculated[
                "success_probability_percent"
            ],
            "completion_probability_percent": calculated[
                "completion_probability_percent"
            ],
            "expected_progress_percent": calculated["expected_progress_percent"],
            "confidence_percent": calculated["confidence_percent"],
            "risk_score": calculated["risk_score"],
            "expected_remaining_cycles": calculated["expected_remaining_cycles"],
            "expected_cost_usd": calculated["expected_cost_usd"],
            "p50_completion_at": iso(calculated["p50_completion_at"]),
            "p90_completion_at": iso(calculated["p90_completion_at"]),
            "assumptions": calculated["assumptions"],
            "drivers": calculated["drivers"],
            "metrics": calculated["metrics"],
            "generated_at": iso(utc_now()),
            "advisory_only": True,
        }

    def _is_stale(
        self,
        forecast: MissionForecastModel | None,
        policy: MissionForecastPolicyModel | None,
    ) -> bool:
        if forecast is None:
            return True
        stale_after = policy.stale_after_seconds if policy is not None else 3600
        return ensure_utc(forecast.generated_at) + timedelta(seconds=stale_after) < utc_now()

    def _policy_row(
        self,
        session: Session,
        mission_id: str,
    ) -> MissionForecastPolicyModel | None:
        return session.scalar(
            select(MissionForecastPolicyModel).where(
                MissionForecastPolicyModel.mission_id == mission_id
            )
        )

    def _current_forecast_row(
        self,
        session: Session,
        mission_id: str,
    ) -> MissionForecastModel | None:
        return session.scalar(
            select(MissionForecastModel)
            .where(
                MissionForecastModel.mission_id == mission_id,
                MissionForecastModel.status == MissionForecastStatus.CURRENT.value,
            )
            .order_by(MissionForecastModel.generated_at.desc())
        )

    def _selected_scenario_row(
        self,
        session: Session,
        mission_id: str,
    ) -> MissionScenarioModel | None:
        return session.scalar(
            select(MissionScenarioModel)
            .where(
                MissionScenarioModel.mission_id == mission_id,
                MissionScenarioModel.status == MissionScenarioStatus.SELECTED.value,
            )
            .order_by(MissionScenarioModel.selected_at.desc())
        )

    @staticmethod
    def _require_workspace(session: Session, workspace_id: str) -> WorkspaceModel:
        row = session.get(WorkspaceModel, workspace_id)
        if row is None:
            raise AutonomousMissionError(f"Workspace not found: {workspace_id}.")
        return row

    @staticmethod
    def _require_mission(session: Session, mission_id: str) -> WorkspaceMissionModel:
        row = session.get(WorkspaceMissionModel, mission_id)
        if row is None:
            raise MissionNotFound(f"Mission not found: {mission_id}.")
        return row

    @staticmethod
    def _require_forecast(session: Session, forecast_id: str) -> MissionForecastModel:
        row = session.get(MissionForecastModel, forecast_id)
        if row is None:
            raise MissionForecastNotFound(f"Forecast not found: {forecast_id}.")
        return row

    @staticmethod
    def _require_scenario(session: Session, scenario_id: str) -> MissionScenarioModel:
        row = session.get(MissionScenarioModel, scenario_id)
        if row is None:
            raise MissionScenarioNotFound(f"Scenario not found: {scenario_id}.")
        return row

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return ensure_utc(value)
        try:
            return ensure_utc(datetime.fromisoformat(str(value)))
        except (TypeError, ValueError):
            return None

    def _policy_to_dict(
        self,
        row: MissionForecastPolicyModel | None,
        mission: WorkspaceMissionModel,
    ) -> dict[str, Any]:
        if row is None:
            return {
                "id": None,
                "workspace_id": mission.workspace_id,
                "mission_id": mission.id,
                "enabled": False,
                "forecast_mode": MissionForecastMode.MANUAL.value,
                "horizon_cycles": 10,
                "min_samples": 3,
                "stale_after_seconds": 3600,
                "auto_refresh_enabled": False,
                "require_human_approval": True,
                "allow_auto_scenario_selection": False,
                "confidence_threshold_percent": 70.0,
                "max_scenarios": 20,
                "metadata": {},
                "created_at": None,
                "updated_at": None,
            }
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "enabled": row.enabled,
            "forecast_mode": row.forecast_mode,
            "horizon_cycles": row.horizon_cycles,
            "min_samples": row.min_samples,
            "stale_after_seconds": row.stale_after_seconds,
            "auto_refresh_enabled": row.auto_refresh_enabled,
            "require_human_approval": row.require_human_approval,
            "allow_auto_scenario_selection": row.allow_auto_scenario_selection,
            "confidence_threshold_percent": row.confidence_threshold_percent,
            "max_scenarios": row.max_scenarios,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    def _forecast_to_dict(self, row: MissionForecastModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "policy_id": row.policy_id,
            "status": row.status,
            "method": row.method,
            "horizon_cycles": row.horizon_cycles,
            "sample_count": row.sample_count,
            "success_probability_percent": row.success_probability_percent,
            "completion_probability_percent": row.completion_probability_percent,
            "expected_progress_percent": row.expected_progress_percent,
            "confidence_percent": row.confidence_percent,
            "risk_score": row.risk_score,
            "expected_remaining_cycles": row.expected_remaining_cycles,
            "expected_cost_usd": row.expected_cost_usd,
            "p50_completion_at": iso(row.p50_completion_at),
            "p90_completion_at": iso(row.p90_completion_at),
            "actor_id": row.actor_id,
            "reason": row.reason,
            "assumptions": dict(row.assumptions_json or {}),
            "drivers": list(row.drivers_json or []),
            "metrics": dict(row.metrics_json or {}),
            "generated_at": iso(row.generated_at),
            "invalidated_at": iso(row.invalidated_at),
            "created_at": iso(row.created_at),
            "advisory_only": True,
        }

    def _scenario_to_dict(self, row: MissionScenarioModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "baseline_forecast_id": row.baseline_forecast_id,
            "scenario_key": row.scenario_key,
            "title": row.title,
            "description": row.description,
            "scenario_type": row.scenario_type,
            "status": row.status,
            "overrides": dict(row.overrides_json or {}),
            "assumptions": dict(row.assumptions_json or {}),
            "result": dict(row.result_json or {}),
            "score": row.score,
            "selected_by": row.selected_by,
            "selection_reason": row.selection_reason,
            "version": row.version,
            "metadata": dict(row.metadata_json or {}),
            "evaluated_at": iso(row.evaluated_at),
            "selected_at": iso(row.selected_at),
            "archived_at": iso(row.archived_at),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    def _simulation_to_dict(
        self,
        row: WorkspacePortfolioSimulationModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "name": row.name,
            "status": row.status,
            "actor_id": row.actor_id,
            "mission_count": row.mission_count,
            "selected_count": row.selected_count,
            "portfolio_score": row.portfolio_score,
            "expected_cost_usd": row.expected_cost_usd,
            "expected_successful_missions": row.expected_successful_missions,
            "input": dict(row.input_json or {}),
            "result": dict(row.result_json or {}),
            "assumptions": dict(row.assumptions_json or {}),
            "metadata": dict(row.metadata_json or {}),
            "error": row.error,
            "created_at": iso(row.created_at),
            "completed_at": iso(row.completed_at),
            "advisory_only": True,
        }

    async def _publish(
        self,
        event_type: str,
        *,
        workspace_id: str,
        correlation_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="mission_forecast_service",
                workspace_id=workspace_id,
                correlation_id=correlation_id,
                payload=payload,
            )
        )
