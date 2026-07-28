from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from statistics import fmean
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.autonomy.learning_schemas import (
    MissionCalibrationScope,
    MissionForecastOutcomeResolveRequest,
    MissionLearningPolicyUpsert,
    MissionLearningReconcileRequest,
    MissionLearningRunDecisionRequest,
    MissionLearningRunRequest,
)
from backend.autonomy.models import (
    MissionForecastCalibrationModel,
    MissionForecastModel,
    MissionForecastOutcomeModel,
    MissionLearningPolicyModel,
    MissionLearningRunModel,
    MissionLearningSignalModel,
    MissionResourceUsageModel,
    WorkspaceMissionModel,
)
from backend.autonomy.service import AutonomousMissionError, ensure_utc
from backend.core.events import Event, EventBus
from backend.database.session import session_scope


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


class MissionLearningNotFound(AutonomousMissionError):
    pass


class MissionLearningStateError(AutonomousMissionError):
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


class MissionLearningService:
    """Calibrates Mission forecasts from resolved outcomes.

    The service is deliberately conservative. Outcome capture is observational,
    while applying a calibration remains an explicit operation unless a Mission
    policy enables adaptive learning, disables human approval, and allows
    automatic application.
    """

    TERMINAL_MISSION_EVENTS = {
        "mission.completed": "completed",
        "mission.failed": "failed",
        "mission.cancelled": "cancelled",
    }
    TERMINAL_CYCLE_EVENTS = {
        "mission.cycle.completed": ("cycle_outcome", 100.0),
        "mission.cycle.failed": ("cycle_outcome", 0.0),
        "mission.cycle.cancelled": ("cycle_outcome", 25.0),
    }

    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._mission_locks: dict[str, asyncio.Lock] = {}
        self._outcomes_resolved = 0
        self._learning_runs = 0
        self._calibrations_applied = 0
        self._signals_captured = 0

    def status(self) -> dict[str, Any]:
        with self._session_factory() as session:
            count = lambda model: int(
                session.scalar(select(func.count()).select_from(model)) or 0
            )
            return {
                "policies": count(MissionLearningPolicyModel),
                "forecast_outcomes": count(MissionForecastOutcomeModel),
                "calibrations": count(MissionForecastCalibrationModel),
                "learning_runs": count(MissionLearningRunModel),
                "learning_signals": count(MissionLearningSignalModel),
                "outcomes_resolved": self._outcomes_resolved,
                "learning_runs_created": self._learning_runs,
                "calibrations_applied": self._calibrations_applied,
                "signals_captured": self._signals_captured,
                "method": "builtin.calibration.v1",
                "default_safety": {
                    "enabled": False,
                    "learning_mode": "manual",
                    "auto_calibration_enabled": False,
                    "auto_apply_enabled": False,
                    "require_human_approval": True,
                    "outcome_capture_is_observational": True,
                },
                "capabilities": [
                    "forecast_outcome_resolution",
                    "brier_score_and_calibration_error",
                    "mission_and_workspace_calibration",
                    "cost_cycle_and_duration_correction",
                    "manual_or_guarded_adaptive_application",
                    "idempotent_event_learning_signals",
                    "reconciliation_and_backfill",
                    "planner_learning_context",
                    "immutable_audit_and_event_transport",
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
        request: MissionLearningPolicyUpsert,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            row = self._policy_row(session, mission_id)
            created = row is None
            if row is None:
                row = MissionLearningPolicyModel(
                    workspace_id=mission.workspace_id,
                    mission_id=mission.id,
                )
                session.add(row)
            values = request.model_dump(mode="json")
            values["learning_mode"] = request.learning_mode.value
            values["metadata_json"] = values.pop("metadata")
            for key, value in values.items():
                setattr(row, key, value)
            session.flush()
            result = self._policy_to_dict(row, mission)
            workspace_id = mission.workspace_id
            correlation_id = row.id

        await self._publish(
            "mission.learning.policy.created"
            if created
            else "mission.learning.policy.updated",
            workspace_id=workspace_id,
            correlation_id=correlation_id,
            payload={"mission_id": mission_id, "policy": result},
        )
        return result

    def dashboard(self, mission_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            policy = self._policy_row(session, mission_id)
            outcomes = list(
                session.scalars(
                    select(MissionForecastOutcomeModel)
                    .where(MissionForecastOutcomeModel.mission_id == mission_id)
                    .order_by(MissionForecastOutcomeModel.resolved_at.desc())
                    .limit(100)
                ).all()
            )
            current_mission = self._current_calibration(
                session,
                scope_type="mission",
                scope_id=mission_id,
            )
            current_workspace = self._current_calibration(
                session,
                scope_type="workspace",
                scope_id=mission.workspace_id,
            )
            latest_run = session.scalar(
                select(MissionLearningRunModel)
                .where(MissionLearningRunModel.mission_id == mission_id)
                .order_by(MissionLearningRunModel.created_at.desc())
            )
            signals = int(
                session.scalar(
                    select(func.count())
                    .select_from(MissionLearningSignalModel)
                    .where(MissionLearningSignalModel.mission_id == mission_id)
                )
                or 0
            )
            aggregate = self._outcome_summary(outcomes)
            return {
                "mission_id": mission_id,
                "workspace_id": mission.workspace_id,
                "policy": self._policy_to_dict(policy, mission),
                "outcomes": aggregate,
                "current_mission_calibration": (
                    self._calibration_to_dict(current_mission)
                    if current_mission is not None
                    else None
                ),
                "current_workspace_calibration": (
                    self._calibration_to_dict(current_workspace)
                    if current_workspace is not None
                    else None
                ),
                "latest_learning_run": (
                    self._run_to_dict(latest_run) if latest_run is not None else None
                ),
                "learning_signal_count": signals,
                "advisory_only_until_applied": True,
            }

    def verify_integrity(self) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        with self._session_factory() as session:
            current_rows = list(
                session.scalars(
                    select(MissionForecastCalibrationModel).where(
                        MissionForecastCalibrationModel.status == "current"
                    )
                ).all()
            )
            by_scope: dict[tuple[str, str], list[str]] = {}
            for row in current_rows:
                by_scope.setdefault(
                    (row.scope_type, row.scope_id), []
                ).append(row.id)
            for (scope_type, scope_id), ids in by_scope.items():
                if len(ids) > 1:
                    issues.append(
                        {
                            "type": "multiple_current_calibrations",
                            "scope_type": scope_type,
                            "scope_id": scope_id,
                            "calibration_ids": ids,
                        }
                    )

            applied_runs = list(
                session.scalars(
                    select(MissionLearningRunModel).where(
                        MissionLearningRunModel.status == "applied"
                    )
                ).all()
            )
            for run in applied_runs:
                calibration = (
                    session.get(
                        MissionForecastCalibrationModel,
                        run.calibration_id,
                    )
                    if run.calibration_id
                    else None
                )
                if calibration is None:
                    issues.append(
                        {
                            "type": "applied_run_missing_calibration",
                            "learning_run_id": run.id,
                        }
                    )
                elif calibration.status not in {"current", "superseded"}:
                    issues.append(
                        {
                            "type": "applied_run_invalid_calibration_status",
                            "learning_run_id": run.id,
                            "calibration_id": calibration.id,
                            "calibration_status": calibration.status,
                        }
                    )

            orphan_outcomes = int(
                session.scalar(
                    select(func.count())
                    .select_from(MissionForecastOutcomeModel)
                    .outerjoin(
                        MissionForecastModel,
                        MissionForecastModel.id
                        == MissionForecastOutcomeModel.forecast_id,
                    )
                    .where(MissionForecastModel.id.is_(None))
                )
                or 0
            )
            if orphan_outcomes:
                issues.append(
                    {
                        "type": "orphan_forecast_outcomes",
                        "count": orphan_outcomes,
                    }
                )

        return {
            "valid": not issues,
            "issues": issues,
            "checked_at": iso(utc_now()),
        }

    def build_context(
        self,
        mission_id: str,
        *,
        cycle_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        del cycle_id
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            mission_calibration = self._current_calibration(
                session, scope_type="mission", scope_id=mission_id
            )
            workspace_calibration = self._current_calibration(
                session, scope_type="workspace", scope_id=mission.workspace_id
            )
            rows = list(
                session.scalars(
                    select(MissionLearningRunModel)
                    .where(MissionLearningRunModel.mission_id == mission_id)
                    .order_by(MissionLearningRunModel.created_at.desc())
                    .limit(max(1, min(100, limit)))
                ).all()
            )
            return {
                "mission_id": mission_id,
                "calibration": (
                    self._calibration_to_dict(mission_calibration)
                    if mission_calibration is not None
                    else (
                        self._calibration_to_dict(workspace_calibration)
                        if workspace_calibration is not None
                        else None
                    )
                ),
                "recent_learning_runs": [self._run_to_dict(row) for row in rows],
                "calibration_applied_to_forecasts": (
                    mission_calibration is not None
                    or workspace_calibration is not None
                ),
            }

    def list_outcomes(
        self,
        mission_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_mission(session, mission_id)
            rows = list(
                session.scalars(
                    select(MissionForecastOutcomeModel)
                    .where(MissionForecastOutcomeModel.mission_id == mission_id)
                    .order_by(MissionForecastOutcomeModel.resolved_at.desc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._outcome_to_dict(row) for row in rows]

    def list_calibrations(
        self,
        mission_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            statement = select(MissionForecastCalibrationModel).where(
                (
                    (MissionForecastCalibrationModel.scope_type == "mission")
                    & (MissionForecastCalibrationModel.scope_id == mission_id)
                )
                | (
                    (MissionForecastCalibrationModel.scope_type == "workspace")
                    & (
                        MissionForecastCalibrationModel.scope_id
                        == mission.workspace_id
                    )
                )
            )
            if status:
                statement = statement.where(
                    MissionForecastCalibrationModel.status == status
                )
            rows = list(
                session.scalars(
                    statement.order_by(
                        MissionForecastCalibrationModel.created_at.desc()
                    )
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._calibration_to_dict(row) for row in rows]

    def get_calibration(self, calibration_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(MissionForecastCalibrationModel, calibration_id)
            return self._calibration_to_dict(row) if row is not None else None

    def list_runs(
        self,
        mission_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_mission(session, mission_id)
            statement = select(MissionLearningRunModel).where(
                MissionLearningRunModel.mission_id == mission_id
            )
            if status:
                statement = statement.where(MissionLearningRunModel.status == status)
            rows = list(
                session.scalars(
                    statement.order_by(MissionLearningRunModel.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._run_to_dict(row) for row in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(MissionLearningRunModel, run_id)
            return self._run_to_dict(row) if row is not None else None

    async def resolve_manual_outcome(
        self,
        mission_id: str,
        request: MissionForecastOutcomeResolveRequest,
    ) -> dict[str, Any]:
        source_event_id = "manual:" + hashlib.sha256(
            f"{request.source_ref}:{mission_id}".encode("utf-8")
        ).hexdigest()[:48]
        results = await self._resolve_outcomes(
            mission_id,
            actual_status=request.actual_status,
            source_event_id=source_event_id,
            forecast_id=request.forecast_id,
            actual_cost_usd=request.actual_cost_usd,
            actual_cycle_count=request.actual_cycle_count,
            actual_completed_at=request.actual_completed_at,
            details={
                "actor_id": request.actor_id,
                "source_ref": request.source_ref,
                "metadata": request.metadata,
                "manual": True,
            },
        )
        return {
            "mission_id": mission_id,
            "resolved": len(results),
            "outcomes": results,
        }

    async def run_learning(
        self,
        mission_id: str,
        request: MissionLearningRunRequest,
    ) -> dict[str, Any]:
        lock = self._mission_locks.setdefault(mission_id, asyncio.Lock())
        async with lock:
            with self._session_factory() as session:
                mission = self._require_mission(session, mission_id)
                policy = self._policy_row(session, mission_id)
                policy_view = self._policy_to_dict(policy, mission)
                if request.automatic:
                    if (
                        policy is None
                        or not policy.enabled
                        or not policy.auto_calibration_enabled
                        or policy.learning_mode == "manual"
                    ):
                        raise MissionLearningStateError(
                            "Automatic calibration is disabled by Mission policy."
                        )

                scope_type = request.scope_type.value
                scope_id = (
                    mission.id
                    if scope_type == MissionCalibrationScope.MISSION.value
                    else mission.workspace_id
                )
                window = (
                    policy.calibration_window if policy is not None else 50
                )
                min_samples = policy.min_samples if policy is not None else 5
                outcomes = self._calibration_outcomes(
                    session,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    limit=window,
                )
                if len(outcomes) < min_samples and not request.force:
                    raise MissionLearningStateError(
                        f"Calibration requires at least {min_samples} resolved "
                        f"outcomes; available: {len(outcomes)}."
                    )
                calculated = self._calculate_calibration(
                    outcomes,
                    max_probability_adjustment=(
                        policy.max_probability_adjustment_percent
                        if policy is not None
                        else 15.0
                    ),
                    max_multiplier_adjustment=(
                        policy.max_multiplier_adjustment_percent
                        if policy is not None
                        else 30.0
                    ),
                )
                calibration = MissionForecastCalibrationModel(
                    workspace_id=mission.workspace_id,
                    mission_id=mission.id if scope_type == "mission" else None,
                    policy_id=policy.id if policy is not None else None,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    status="proposed",
                    sample_count=len(outcomes),
                    success_bias_percent=calculated["success_bias_percent"],
                    completion_bias_percent=calculated[
                        "completion_bias_percent"
                    ],
                    cost_multiplier=calculated["cost_multiplier"],
                    cycle_multiplier=calculated["cycle_multiplier"],
                    duration_multiplier=calculated["duration_multiplier"],
                    confidence_multiplier=calculated[
                        "confidence_multiplier"
                    ],
                    baseline_score=calculated["baseline_score"],
                    calibrated_score=calculated["calibrated_score"],
                    improvement_percent=calculated["improvement_percent"],
                    success_brier_score=calculated["success_brier_score"],
                    completion_brier_score=calculated[
                        "completion_brier_score"
                    ],
                    expected_calibration_error_percent=calculated[
                        "expected_calibration_error_percent"
                    ],
                    actor_id=request.actor_id,
                    reason=request.reason,
                    metrics_json={
                        **calculated["metrics"],
                        "metadata": request.metadata,
                    },
                )
                session.add(calibration)
                session.flush()
                run = MissionLearningRunModel(
                    workspace_id=mission.workspace_id,
                    mission_id=mission.id,
                    policy_id=policy.id if policy is not None else None,
                    calibration_id=calibration.id,
                    status="proposed",
                    actor_id=request.actor_id,
                    automatic=request.automatic,
                    scope_type=scope_type,
                    sample_count=len(outcomes),
                    baseline_score=calculated["baseline_score"],
                    candidate_score=calculated["calibrated_score"],
                    improvement_percent=calculated["improvement_percent"],
                    reason=request.reason,
                    source_summary_json={
                        "scope_id": scope_id,
                        "outcome_ids": [row.id for row in outcomes],
                        "policy": policy_view,
                    },
                    recommendations_json={
                        "success_probability_adjustment_percent": calculated[
                            "success_bias_percent"
                        ],
                        "completion_probability_adjustment_percent": calculated[
                            "completion_bias_percent"
                        ],
                        "cost_multiplier": calculated["cost_multiplier"],
                        "cycle_multiplier": calculated["cycle_multiplier"],
                        "duration_multiplier": calculated[
                            "duration_multiplier"
                        ],
                        "confidence_multiplier": calculated[
                            "confidence_multiplier"
                        ],
                    },
                )
                session.add(run)
                session.flush()
                run_id = run.id
                workspace_id = mission.workspace_id
                result = self._run_to_dict(run)
                self._learning_runs += 1

            await self._publish(
                "mission.learning.run.proposed",
                workspace_id=workspace_id,
                correlation_id=mission_id,
                payload={
                    "mission_id": mission_id,
                    "learning_run_id": run_id,
                    "learning_run": result,
                },
            )

            should_apply = request.apply
            if request.automatic:
                should_apply = bool(
                    policy_view["auto_apply_enabled"]
                    and not policy_view["require_human_approval"]
                )
            if should_apply:
                return await self.apply_run(
                    run_id,
                    MissionLearningRunDecisionRequest(
                        actor_id=request.actor_id,
                        reason=request.reason,
                        force=request.force,
                    ),
                )
            return result

    async def apply_run(
        self,
        run_id: str,
        request: MissionLearningRunDecisionRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            run = session.get(MissionLearningRunModel, run_id)
            if run is None:
                raise MissionLearningNotFound("Mission Learning Run not found.")
            if run.status == "applied":
                return self._run_to_dict(run)
            if run.status != "proposed":
                raise MissionLearningStateError(
                    f"Learning Run cannot be applied from status {run.status}."
                )
            calibration = session.get(
                MissionForecastCalibrationModel, run.calibration_id
            )
            if calibration is None:
                raise MissionLearningNotFound("Forecast Calibration not found.")
            policy = (
                session.get(MissionLearningPolicyModel, run.policy_id)
                if run.policy_id
                else None
            )
            min_improvement = (
                policy.min_improvement_percent if policy is not None else 2.0
            )
            if (
                calibration.improvement_percent < min_improvement
                and not request.force
            ):
                raise MissionLearningStateError(
                    "Calibration improvement is below the configured threshold."
                )
            if run.automatic and (
                policy is None
                or not policy.enabled
                or not policy.auto_apply_enabled
                or policy.require_human_approval
            ):
                raise MissionLearningStateError(
                    "Automatic calibration application is not allowed."
                )

            current_rows = list(
                session.scalars(
                    select(MissionForecastCalibrationModel).where(
                        MissionForecastCalibrationModel.scope_type
                        == calibration.scope_type,
                        MissionForecastCalibrationModel.scope_id
                        == calibration.scope_id,
                        MissionForecastCalibrationModel.status == "current",
                    )
                ).all()
            )
            for current in current_rows:
                current.status = "superseded"
            now = utc_now()
            calibration.status = "current"
            calibration.applied_at = now
            calibration.actor_id = request.actor_id
            calibration.reason = request.reason
            run.status = "applied"
            run.applied_at = now
            run.applied_json = {
                "calibration_id": calibration.id,
                "actor_id": request.actor_id,
                "reason": request.reason,
                "force": request.force,
            }
            session.flush()
            result = self._run_to_dict(run)
            workspace_id = run.workspace_id
            mission_id = run.mission_id
            calibration_result = self._calibration_to_dict(calibration)
            self._calibrations_applied += 1

        await self._publish(
            "mission.learning.calibration.applied",
            workspace_id=workspace_id,
            correlation_id=mission_id,
            payload={
                "mission_id": mission_id,
                "learning_run_id": run_id,
                "calibration": calibration_result,
            },
        )
        return result

    async def reject_run(
        self,
        run_id: str,
        request: MissionLearningRunDecisionRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            run = session.get(MissionLearningRunModel, run_id)
            if run is None:
                raise MissionLearningNotFound("Mission Learning Run not found.")
            if run.status == "rejected":
                return self._run_to_dict(run)
            if run.status != "proposed":
                raise MissionLearningStateError(
                    f"Learning Run cannot be rejected from status {run.status}."
                )
            calibration = session.get(
                MissionForecastCalibrationModel, run.calibration_id
            )
            now = utc_now()
            run.status = "rejected"
            run.rejected_at = now
            run.applied_json = {
                "actor_id": request.actor_id,
                "reason": request.reason,
            }
            if calibration is not None:
                calibration.status = "rejected"
                calibration.rejected_at = now
                calibration.actor_id = request.actor_id
                calibration.reason = request.reason
            session.flush()
            result = self._run_to_dict(run)
            workspace_id = run.workspace_id
            mission_id = run.mission_id

        await self._publish(
            "mission.learning.run.rejected",
            workspace_id=workspace_id,
            correlation_id=mission_id,
            payload={
                "mission_id": mission_id,
                "learning_run_id": run_id,
                "reason": request.reason,
            },
        )
        return result

    async def handle_event(self, event: Event) -> None:
        if event.event_type in self.TERMINAL_CYCLE_EVENTS:
            await self._capture_cycle_signal(event)
            mission_id = str(
                event.payload.get("mission_id") or event.correlation_id or ""
            )
            if mission_id:
                await self._maybe_auto_learn(mission_id, event)
            return

        actual_status = self.TERMINAL_MISSION_EVENTS.get(event.event_type)
        if actual_status is None:
            return
        mission_id = str(
            event.payload.get("mission_id") or event.correlation_id or ""
        )
        if not mission_id:
            return
        await self._capture_mission_signal(event, actual_status)
        with self._session_factory() as session:
            policy = self._policy_row(session, mission_id)
            capture_enabled = (
                policy.capture_outcomes_enabled if policy is not None else True
            )
        if capture_enabled:
            await self._resolve_outcomes(
                mission_id,
                actual_status=actual_status,
                source_event_id=event.id,
                details={"terminal_event": event.to_dict(), "manual": False},
            )
        await self._maybe_auto_learn(mission_id, event)

    async def reconcile(
        self,
        request: MissionLearningReconcileRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            statement = select(WorkspaceMissionModel).where(
                WorkspaceMissionModel.status.in_(
                    ("completed", "failed", "cancelled")
                )
            )
            if request.workspace_id:
                statement = statement.where(
                    WorkspaceMissionModel.workspace_id == request.workspace_id
                )
            missions = list(session.scalars(statement).all())

        resolved = 0
        run_ids: list[str] = []
        errors: list[dict[str, str]] = []
        for mission in missions:
            try:
                rows = await self._resolve_outcomes(
                    mission.id,
                    actual_status=mission.status,
                    source_event_id=f"reconcile:{mission.id}:{mission.version}",
                    details={"reconciled": True, "actor_id": request.actor_id},
                )
                resolved += len(rows)
                if request.run_calibration:
                    result = await self.run_learning(
                        mission.id,
                        MissionLearningRunRequest(
                            scope_type=MissionCalibrationScope.MISSION,
                            actor_id=request.actor_id,
                            reason="Mission learning reconciliation.",
                            apply=request.apply,
                            automatic=False,
                            force=request.force,
                        ),
                    )
                    run_ids.append(result["id"])
            except Exception as exc:
                errors.append({"mission_id": mission.id, "error": str(exc)})
        return {
            "terminal_missions": len(missions),
            "outcomes_resolved": resolved,
            "learning_run_ids": run_ids,
            "errors": errors,
        }

    async def _resolve_outcomes(
        self,
        mission_id: str,
        *,
        actual_status: str,
        source_event_id: str,
        forecast_id: str | None = None,
        actual_cost_usd: float | None = None,
        actual_cycle_count: int | None = None,
        actual_completed_at: datetime | None = None,
        details: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        lock = self._mission_locks.setdefault(mission_id, asyncio.Lock())
        async with lock:
            with self._session_factory() as session:
                mission = self._require_mission(session, mission_id)
                existing_ids = set(
                    session.scalars(
                        select(MissionForecastOutcomeModel.forecast_id).where(
                            MissionForecastOutcomeModel.mission_id == mission_id
                        )
                    ).all()
                )
                statement = select(MissionForecastModel).where(
                    MissionForecastModel.mission_id == mission_id
                )
                if forecast_id:
                    statement = statement.where(
                        MissionForecastModel.id == forecast_id
                    )
                forecasts = list(
                    session.scalars(
                        statement.order_by(MissionForecastModel.generated_at.asc())
                    ).all()
                )
                forecasts = [
                    row for row in forecasts if row.id not in existing_ids
                ]
                if forecast_id and not forecasts and forecast_id not in existing_ids:
                    raise MissionLearningNotFound("Mission Forecast not found.")

                cycle_count = (
                    int(actual_cycle_count)
                    if actual_cycle_count is not None
                    else int(mission.cycle_count)
                )
                completed_at = actual_completed_at or (
                    mission.completed_at
                    or mission.cancelled_at
                    or mission.updated_at
                    or utc_now()
                )
                actual_success = actual_status == "completed"
                actual_completion = actual_status == "completed"
                created: list[MissionForecastOutcomeModel] = []
                for forecast in forecasts:
                    actual_success_value = 1.0 if actual_success else 0.0
                    actual_completion_value = 1.0 if actual_completion else 0.0
                    predicted_success = float(
                        forecast.success_probability_percent
                    )
                    predicted_completion = float(
                        forecast.completion_probability_percent
                    )
                    success_brier = (
                        predicted_success / 100.0 - actual_success_value
                    ) ** 2
                    completion_brier = (
                        predicted_completion / 100.0
                        - actual_completion_value
                    ) ** 2
                    cycle_count_at_forecast = int(
                        (forecast.metrics_json or {}).get("cycle_count", 0)
                    )
                    actual_remaining_cycles = float(
                        max(0, cycle_count - cycle_count_at_forecast)
                    )
                    forecast_actual_cost = (
                        float(actual_cost_usd)
                        if actual_cost_usd is not None
                        else float(
                            session.scalar(
                                select(
                                    func.coalesce(
                                        func.sum(
                                            MissionResourceUsageModel.cost_usd
                                        ),
                                        0.0,
                                    )
                                ).where(
                                    MissionResourceUsageModel.mission_id
                                    == mission_id,
                                    MissionResourceUsageModel.occurred_at
                                    >= forecast.generated_at,
                                )
                            )
                            or 0.0
                        )
                    )
                    cost_abs = abs(
                        float(forecast.expected_cost_usd)
                        - forecast_actual_cost
                    )
                    cost_relative = (
                        100.0 * cost_abs / forecast_actual_cost
                        if forecast_actual_cost > 0
                        else None
                    )
                    duration_abs = None
                    if (
                        forecast.p50_completion_at is not None
                        and completed_at is not None
                    ):
                        duration_abs = abs(
                            (
                                ensure_utc(completed_at)
                                - ensure_utc(forecast.p50_completion_at)
                            ).total_seconds()
                        )
                    outcome = MissionForecastOutcomeModel(
                        workspace_id=mission.workspace_id,
                        mission_id=mission.id,
                        forecast_id=forecast.id,
                        source_event_id=source_event_id,
                        actual_status=actual_status,
                        actual_success=actual_success,
                        actual_completion=actual_completion,
                        predicted_success_percent=predicted_success,
                        predicted_completion_percent=predicted_completion,
                        predicted_cost_usd=float(forecast.expected_cost_usd),
                        predicted_remaining_cycles=float(
                            forecast.expected_remaining_cycles
                        ),
                        predicted_p50_completion_at=forecast.p50_completion_at,
                        actual_cost_usd=money(forecast_actual_cost),
                        actual_remaining_cycles=actual_remaining_cycles,
                        actual_completed_at=completed_at,
                        success_brier_score=round(success_brier, 8),
                        completion_brier_score=round(completion_brier, 8),
                        success_absolute_error_percent=abs(
                            predicted_success
                            - (100.0 if actual_success else 0.0)
                        ),
                        completion_absolute_error_percent=abs(
                            predicted_completion
                            - (100.0 if actual_completion else 0.0)
                        ),
                        cost_absolute_error_usd=money(cost_abs),
                        cost_relative_error_percent=(
                            round(cost_relative, 4)
                            if cost_relative is not None
                            else None
                        ),
                        cycles_absolute_error=abs(
                            float(forecast.expected_remaining_cycles)
                            - actual_remaining_cycles
                        ),
                        duration_absolute_error_seconds=duration_abs,
                        details_json={
                            **(details or {}),
                            "forecast_generated_at": iso(
                                forecast.generated_at
                            ),
                            "cycle_count_at_forecast": cycle_count_at_forecast,
                            "actual_cycle_count": cycle_count,
                        },
                    )
                    session.add(outcome)
                    created.append(outcome)
                session.flush()
                results = [self._outcome_to_dict(row) for row in created]
                workspace_id = mission.workspace_id
                self._outcomes_resolved += len(created)

            if results:
                await self._publish(
                    "mission.learning.outcomes.resolved",
                    workspace_id=workspace_id,
                    correlation_id=mission_id,
                    payload={
                        "mission_id": mission_id,
                        "source_event_id": source_event_id,
                        "resolved": len(results),
                        "outcome_ids": [row["id"] for row in results],
                    },
                )
            return results

    async def _capture_cycle_signal(self, event: Event) -> None:
        mission_id = str(
            event.payload.get("mission_id") or event.correlation_id or ""
        )
        if not mission_id:
            return
        signal_type, reward = self.TERMINAL_CYCLE_EVENTS[event.event_type]
        await self._capture_signal(
            mission_id=mission_id,
            cycle_id=str(event.payload.get("cycle_id") or "") or None,
            source_event_id=event.id,
            signal_type=signal_type,
            reward_percent=reward,
            payload={"event": event.to_dict()},
        )

    async def _capture_mission_signal(
        self,
        event: Event,
        actual_status: str,
    ) -> None:
        mission_id = str(
            event.payload.get("mission_id") or event.correlation_id or ""
        )
        reward = 100.0 if actual_status == "completed" else (
            25.0 if actual_status == "cancelled" else 0.0
        )
        await self._capture_signal(
            mission_id=mission_id,
            cycle_id=None,
            source_event_id=event.id,
            signal_type="mission_outcome",
            reward_percent=reward,
            payload={"event": event.to_dict(), "actual_status": actual_status},
        )

    async def _capture_signal(
        self,
        *,
        mission_id: str,
        cycle_id: str | None,
        source_event_id: str,
        signal_type: str,
        reward_percent: float,
        payload: dict[str, Any],
    ) -> None:
        with self._session_factory() as session:
            mission = session.get(WorkspaceMissionModel, mission_id)
            if mission is None:
                return
            existing = session.scalar(
                select(MissionLearningSignalModel).where(
                    MissionLearningSignalModel.source_event_id == source_event_id,
                    MissionLearningSignalModel.signal_type == signal_type,
                )
            )
            if existing is not None:
                return
            row = MissionLearningSignalModel(
                workspace_id=mission.workspace_id,
                mission_id=mission.id,
                cycle_id=cycle_id,
                source_event_id=source_event_id,
                signal_type=signal_type,
                reward_percent=clamp(reward_percent),
                confidence_percent=100.0,
                payload_json=payload,
            )
            session.add(row)
            session.flush()
            self._signals_captured += 1

    async def _maybe_auto_learn(self, mission_id: str, event: Event) -> None:
        with self._session_factory() as session:
            mission = session.get(WorkspaceMissionModel, mission_id)
            policy = self._policy_row(session, mission_id) if mission else None
            if (
                mission is None
                or policy is None
                or not policy.enabled
                or not policy.auto_calibration_enabled
                or policy.learning_mode == "manual"
            ):
                return
            auto_apply = bool(
                policy.auto_apply_enabled
                and not policy.require_human_approval
            )
        try:
            await self.run_learning(
                mission_id,
                MissionLearningRunRequest(
                    scope_type=MissionCalibrationScope.MISSION,
                    actor_id="system",
                    reason=f"Automatic learning after {event.event_type}.",
                    apply=auto_apply,
                    automatic=True,
                    force=False,
                    metadata={"source_event_id": event.id},
                ),
            )
        except MissionLearningStateError:
            return

    def _calibration_outcomes(
        self,
        session: Session,
        *,
        scope_type: str,
        scope_id: str,
        limit: int,
    ) -> list[MissionForecastOutcomeModel]:
        statement = select(MissionForecastOutcomeModel)
        if scope_type == "mission":
            statement = statement.where(
                MissionForecastOutcomeModel.mission_id == scope_id
            )
        else:
            statement = statement.where(
                MissionForecastOutcomeModel.workspace_id == scope_id
            )
        return list(
            session.scalars(
                statement.order_by(
                    MissionForecastOutcomeModel.resolved_at.desc()
                ).limit(limit)
            ).all()
        )

    def _calculate_calibration(
        self,
        outcomes: list[MissionForecastOutcomeModel],
        *,
        max_probability_adjustment: float,
        max_multiplier_adjustment: float,
    ) -> dict[str, Any]:
        if not outcomes:
            raise MissionLearningStateError(
                "No resolved forecast outcomes are available."
            )
        actual_success = [
            100.0 if row.actual_success else 0.0 for row in outcomes
        ]
        actual_completion = [
            100.0 if row.actual_completion else 0.0 for row in outcomes
        ]
        predicted_success = [
            float(row.predicted_success_percent) for row in outcomes
        ]
        predicted_completion = [
            float(row.predicted_completion_percent) for row in outcomes
        ]
        success_bias = self._clip(
            fmean(
                actual - predicted
                for actual, predicted in zip(
                    actual_success, predicted_success, strict=True
                )
            ),
            -max_probability_adjustment,
            max_probability_adjustment,
        )
        completion_bias = self._clip(
            fmean(
                actual - predicted
                for actual, predicted in zip(
                    actual_completion, predicted_completion, strict=True
                )
            ),
            -max_probability_adjustment,
            max_probability_adjustment,
        )
        adjusted_success = [
            clamp(value + success_bias) for value in predicted_success
        ]
        adjusted_completion = [
            clamp(value + completion_bias) for value in predicted_completion
        ]
        baseline_probability_mae = fmean(
            [
                abs(predicted - actual)
                for predicted, actual in zip(
                    predicted_success, actual_success, strict=True
                )
            ]
            + [
                abs(predicted - actual)
                for predicted, actual in zip(
                    predicted_completion, actual_completion, strict=True
                )
            ]
        )
        candidate_probability_mae = fmean(
            [
                abs(predicted - actual)
                for predicted, actual in zip(
                    adjusted_success, actual_success, strict=True
                )
            ]
            + [
                abs(predicted - actual)
                for predicted, actual in zip(
                    adjusted_completion, actual_completion, strict=True
                )
            ]
        )

        multiplier_delta = max_multiplier_adjustment / 100.0
        multiplier_min = max(0.1, 1.0 - multiplier_delta)
        multiplier_max = 1.0 + multiplier_delta

        cost_ratios = [
            row.actual_cost_usd / row.predicted_cost_usd
            for row in outcomes
            if row.predicted_cost_usd > 0 and row.actual_cost_usd >= 0
        ]
        cycle_ratios = [
            row.actual_remaining_cycles / row.predicted_remaining_cycles
            for row in outcomes
            if row.predicted_remaining_cycles > 0
        ]
        duration_ratios = []
        for row in outcomes:
            generated_at_raw = (row.details_json or {}).get(
                "forecast_generated_at"
            )
            if (
                generated_at_raw
                and row.actual_completed_at is not None
                and row.predicted_p50_completion_at is not None
            ):
                try:
                    generated_at = datetime.fromisoformat(generated_at_raw)
                    predicted_duration = (
                        ensure_utc(row.predicted_p50_completion_at)
                        - ensure_utc(generated_at)
                    ).total_seconds()
                    actual_duration = (
                        ensure_utc(row.actual_completed_at)
                        - ensure_utc(generated_at)
                    ).total_seconds()
                    if predicted_duration > 0 and actual_duration >= 0:
                        duration_ratios.append(
                            actual_duration / predicted_duration
                        )
                except (TypeError, ValueError):
                    pass

        cost_multiplier = self._clip(
            fmean(cost_ratios) if cost_ratios else 1.0,
            multiplier_min,
            multiplier_max,
        )
        cycle_multiplier = self._clip(
            fmean(cycle_ratios) if cycle_ratios else 1.0,
            multiplier_min,
            multiplier_max,
        )
        duration_multiplier = self._clip(
            fmean(duration_ratios) if duration_ratios else 1.0,
            multiplier_min,
            multiplier_max,
        )

        baseline_cost_mae = self._relative_mae(
            [
                (row.predicted_cost_usd, row.actual_cost_usd)
                for row in outcomes
                if row.actual_cost_usd > 0
            ]
        )
        candidate_cost_mae = self._relative_mae(
            [
                (
                    row.predicted_cost_usd * cost_multiplier,
                    row.actual_cost_usd,
                )
                for row in outcomes
                if row.actual_cost_usd > 0
            ]
        )
        baseline_cycle_mae = self._relative_mae(
            [
                (
                    row.predicted_remaining_cycles,
                    row.actual_remaining_cycles,
                )
                for row in outcomes
                if row.actual_remaining_cycles > 0
            ]
        )
        candidate_cycle_mae = self._relative_mae(
            [
                (
                    row.predicted_remaining_cycles * cycle_multiplier,
                    row.actual_remaining_cycles,
                )
                for row in outcomes
                if row.actual_remaining_cycles > 0
            ]
        )
        baseline_score = clamp(
            100.0
            - (
                baseline_probability_mae * 0.70
                + baseline_cost_mae * 0.20
                + baseline_cycle_mae * 0.10
            )
        )
        calibrated_score = clamp(
            100.0
            - (
                candidate_probability_mae * 0.70
                + candidate_cost_mae * 0.20
                + candidate_cycle_mae * 0.10
            )
        )
        success_brier = fmean(
            float(row.success_brier_score) for row in outcomes
        )
        completion_brier = fmean(
            float(row.completion_brier_score) for row in outcomes
        )
        ece = self._expected_calibration_error(
            predicted_success + predicted_completion,
            actual_success + actual_completion,
        )
        confidence_multiplier = self._clip(
            1.0 - ece / 200.0,
            0.5,
            1.0,
        )
        return {
            "success_bias_percent": round(success_bias, 4),
            "completion_bias_percent": round(completion_bias, 4),
            "cost_multiplier": round(cost_multiplier, 6),
            "cycle_multiplier": round(cycle_multiplier, 6),
            "duration_multiplier": round(duration_multiplier, 6),
            "confidence_multiplier": round(confidence_multiplier, 6),
            "baseline_score": baseline_score,
            "calibrated_score": calibrated_score,
            "improvement_percent": round(
                calibrated_score - baseline_score, 4
            ),
            "success_brier_score": round(success_brier, 8),
            "completion_brier_score": round(completion_brier, 8),
            "expected_calibration_error_percent": round(ece, 4),
            "metrics": {
                "sample_count": len(outcomes),
                "baseline_probability_mae_percent": round(
                    baseline_probability_mae, 4
                ),
                "candidate_probability_mae_percent": round(
                    candidate_probability_mae, 4
                ),
                "baseline_cost_relative_mae_percent": round(
                    baseline_cost_mae, 4
                ),
                "candidate_cost_relative_mae_percent": round(
                    candidate_cost_mae, 4
                ),
                "baseline_cycle_relative_mae_percent": round(
                    baseline_cycle_mae, 4
                ),
                "candidate_cycle_relative_mae_percent": round(
                    candidate_cycle_mae, 4
                ),
                "cost_ratio_samples": len(cost_ratios),
                "cycle_ratio_samples": len(cycle_ratios),
                "duration_ratio_samples": len(duration_ratios),
            },
        }

    @staticmethod
    def _relative_mae(pairs: list[tuple[float, float]]) -> float:
        if not pairs:
            return 0.0
        return min(
            100.0,
            fmean(
                abs(predicted - actual) / max(abs(actual), 1e-9) * 100.0
                for predicted, actual in pairs
            ),
        )

    @staticmethod
    def _expected_calibration_error(
        predicted: list[float],
        actual: list[float],
        *,
        buckets: int = 10,
    ) -> float:
        if not predicted:
            return 0.0
        total = len(predicted)
        error = 0.0
        for bucket in range(buckets):
            lower = bucket * 100.0 / buckets
            upper = (bucket + 1) * 100.0 / buckets
            indices = [
                index
                for index, value in enumerate(predicted)
                if lower <= value < upper
                or (bucket == buckets - 1 and value == 100.0)
            ]
            if not indices:
                continue
            confidence = fmean(predicted[index] for index in indices)
            accuracy = fmean(actual[index] for index in indices)
            error += len(indices) / total * abs(confidence - accuracy)
        return error

    def _current_calibration(
        self,
        session: Session,
        *,
        scope_type: str,
        scope_id: str,
    ) -> MissionForecastCalibrationModel | None:
        return session.scalar(
            select(MissionForecastCalibrationModel)
            .where(
                MissionForecastCalibrationModel.scope_type == scope_type,
                MissionForecastCalibrationModel.scope_id == scope_id,
                MissionForecastCalibrationModel.status == "current",
            )
            .order_by(MissionForecastCalibrationModel.applied_at.desc())
        )

    def _policy_row(
        self,
        session: Session,
        mission_id: str,
    ) -> MissionLearningPolicyModel | None:
        return session.scalar(
            select(MissionLearningPolicyModel).where(
                MissionLearningPolicyModel.mission_id == mission_id
            )
        )

    @staticmethod
    def _require_mission(
        session: Session,
        mission_id: str,
    ) -> WorkspaceMissionModel:
        row = session.get(WorkspaceMissionModel, mission_id)
        if row is None:
            raise MissionLearningNotFound("Mission not found.")
        return row

    @staticmethod
    def _clip(value: float, minimum: float, maximum: float) -> float:
        return min(maximum, max(minimum, float(value)))

    def _policy_to_dict(
        self,
        row: MissionLearningPolicyModel | None,
        mission: WorkspaceMissionModel,
    ) -> dict[str, Any]:
        if row is None:
            return {
                "id": None,
                "workspace_id": mission.workspace_id,
                "mission_id": mission.id,
                "enabled": False,
                "learning_mode": "manual",
                "capture_outcomes_enabled": True,
                "auto_calibration_enabled": False,
                "auto_apply_enabled": False,
                "require_human_approval": True,
                "min_samples": 5,
                "calibration_window": 50,
                "max_probability_adjustment_percent": 15.0,
                "max_multiplier_adjustment_percent": 30.0,
                "min_improvement_percent": 2.0,
                "metadata": {},
                "created_at": None,
                "updated_at": None,
                "is_default": True,
            }
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "enabled": row.enabled,
            "learning_mode": row.learning_mode,
            "capture_outcomes_enabled": row.capture_outcomes_enabled,
            "auto_calibration_enabled": row.auto_calibration_enabled,
            "auto_apply_enabled": row.auto_apply_enabled,
            "require_human_approval": row.require_human_approval,
            "min_samples": row.min_samples,
            "calibration_window": row.calibration_window,
            "max_probability_adjustment_percent": (
                row.max_probability_adjustment_percent
            ),
            "max_multiplier_adjustment_percent": (
                row.max_multiplier_adjustment_percent
            ),
            "min_improvement_percent": row.min_improvement_percent,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
            "is_default": False,
        }

    @staticmethod
    def _outcome_to_dict(row: MissionForecastOutcomeModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "forecast_id": row.forecast_id,
            "source_event_id": row.source_event_id,
            "actual_status": row.actual_status,
            "actual_success": row.actual_success,
            "actual_completion": row.actual_completion,
            "predicted_success_percent": row.predicted_success_percent,
            "predicted_completion_percent": row.predicted_completion_percent,
            "predicted_cost_usd": row.predicted_cost_usd,
            "predicted_remaining_cycles": row.predicted_remaining_cycles,
            "predicted_p50_completion_at": iso(
                row.predicted_p50_completion_at
            ),
            "actual_cost_usd": row.actual_cost_usd,
            "actual_remaining_cycles": row.actual_remaining_cycles,
            "actual_completed_at": iso(row.actual_completed_at),
            "success_brier_score": row.success_brier_score,
            "completion_brier_score": row.completion_brier_score,
            "success_absolute_error_percent": (
                row.success_absolute_error_percent
            ),
            "completion_absolute_error_percent": (
                row.completion_absolute_error_percent
            ),
            "cost_absolute_error_usd": row.cost_absolute_error_usd,
            "cost_relative_error_percent": row.cost_relative_error_percent,
            "cycles_absolute_error": row.cycles_absolute_error,
            "duration_absolute_error_seconds": (
                row.duration_absolute_error_seconds
            ),
            "details": dict(row.details_json or {}),
            "resolved_at": iso(row.resolved_at),
            "created_at": iso(row.created_at),
        }

    @staticmethod
    def _calibration_to_dict(
        row: MissionForecastCalibrationModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "policy_id": row.policy_id,
            "scope_type": row.scope_type,
            "scope_id": row.scope_id,
            "status": row.status,
            "method": row.method,
            "sample_count": row.sample_count,
            "success_bias_percent": row.success_bias_percent,
            "completion_bias_percent": row.completion_bias_percent,
            "cost_multiplier": row.cost_multiplier,
            "cycle_multiplier": row.cycle_multiplier,
            "duration_multiplier": row.duration_multiplier,
            "confidence_multiplier": row.confidence_multiplier,
            "baseline_score": row.baseline_score,
            "calibrated_score": row.calibrated_score,
            "improvement_percent": row.improvement_percent,
            "success_brier_score": row.success_brier_score,
            "completion_brier_score": row.completion_brier_score,
            "expected_calibration_error_percent": (
                row.expected_calibration_error_percent
            ),
            "actor_id": row.actor_id,
            "reason": row.reason,
            "metrics": dict(row.metrics_json or {}),
            "applied_at": iso(row.applied_at),
            "rejected_at": iso(row.rejected_at),
            "created_at": iso(row.created_at),
        }

    def _run_to_dict(self, row: MissionLearningRunModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "policy_id": row.policy_id,
            "calibration_id": row.calibration_id,
            "status": row.status,
            "actor_id": row.actor_id,
            "automatic": row.automatic,
            "scope_type": row.scope_type,
            "sample_count": row.sample_count,
            "baseline_score": row.baseline_score,
            "candidate_score": row.candidate_score,
            "improvement_percent": row.improvement_percent,
            "reason": row.reason,
            "source_summary": dict(row.source_summary_json or {}),
            "recommendations": dict(row.recommendations_json or {}),
            "applied": dict(row.applied_json or {}),
            "error": row.error,
            "created_at": iso(row.created_at),
            "applied_at": iso(row.applied_at),
            "rejected_at": iso(row.rejected_at),
        }

    @staticmethod
    def _outcome_summary(
        rows: list[MissionForecastOutcomeModel],
    ) -> dict[str, Any]:
        if not rows:
            return {
                "count": 0,
                "success_brier_score": None,
                "completion_brier_score": None,
                "success_mae_percent": None,
                "completion_mae_percent": None,
                "cost_mae_usd": None,
            }
        return {
            "count": len(rows),
            "success_brier_score": round(
                fmean(row.success_brier_score for row in rows), 8
            ),
            "completion_brier_score": round(
                fmean(row.completion_brier_score for row in rows), 8
            ),
            "success_mae_percent": round(
                fmean(row.success_absolute_error_percent for row in rows), 4
            ),
            "completion_mae_percent": round(
                fmean(row.completion_absolute_error_percent for row in rows), 4
            ),
            "cost_mae_usd": money(
                fmean(row.cost_absolute_error_usd for row in rows)
            ),
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
                source="mission_learning_service",
                workspace_id=workspace_id,
                correlation_id=correlation_id,
                payload=payload,
            )
        )
