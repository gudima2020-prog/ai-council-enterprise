from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, time, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.autonomy.models import (
    MissionCycleModel,
    MissionDeadlineEventModel,
    MissionProgressUpdateModel,
    MissionScheduleEvaluationModel,
    MissionSchedulePolicyModel,
    MissionScheduleWindowModel,
    WorkspaceMissionModel,
)
from backend.autonomy.schedule_schemas import (
    MissionDeadlineResolveRequest,
    MissionOverdueAction,
    MissionScheduleDecision,
    MissionScheduleEvaluationRequest,
    MissionScheduleMode,
    MissionSchedulePolicyUpsert,
    MissionScheduleWindowCreate,
    MissionScheduleWindowUpdate,
)
from backend.autonomy.service import (
    AutonomousMissionError,
    MissionCycleNotFound,
    MissionNotFound,
    MissionStateError,
)
from backend.core.events import Event, EventBus
from backend.database.session import session_scope


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


class MissionScheduleWindowNotFound(AutonomousMissionError):
    pass


class MissionDeadlineEventNotFound(AutonomousMissionError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    normalized = ensure_utc(value)
    return None if normalized is None else normalized.isoformat()


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ensure_utc(parsed)


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", maxsplit=1)
    return time(int(hour), int(minute))


class MissionScheduleService:
    """Mission schedules, deadline surveillance and adaptive cadence.

    Schedule governance is disabled by default. Manual Mission cycles keep their
    previous behaviour unless ``enforce_manual_cycles`` is explicitly enabled.
    Automatic cadence changes require an adaptive policy and explicit opt-in.
    """

    TERMINAL_MISSION_STATUSES = {"completed", "failed", "cancelled"}
    TERMINAL_CYCLE_STATUSES = {"completed", "failed", "cancelled"}

    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._evaluations = 0
        self._automatic_applies = 0
        self._deadline_events_created = 0
        self._deadline_pauses = 0

    def status(self) -> dict[str, Any]:
        with self._session_factory() as session:
            policies = int(
                session.scalar(select(func.count()).select_from(MissionSchedulePolicyModel))
                or 0
            )
            windows = int(
                session.scalar(select(func.count()).select_from(MissionScheduleWindowModel))
                or 0
            )
            evaluations = int(
                session.scalar(select(func.count()).select_from(MissionScheduleEvaluationModel))
                or 0
            )
            open_deadlines = int(
                session.scalar(
                    select(func.count())
                    .select_from(MissionDeadlineEventModel)
                    .where(MissionDeadlineEventModel.resolved_at.is_(None))
                )
                or 0
            )
        return {
            "policies": policies,
            "windows": windows,
            "evaluations": evaluations,
            "open_deadline_events": open_deadlines,
            "runtime": {
                "evaluations_created": self._evaluations,
                "automatic_applies": self._automatic_applies,
                "deadline_events_created": self._deadline_events_created,
                "missions_paused_by_deadline": self._deadline_pauses,
            },
            "default_safety": {
                "enabled": False,
                "schedule_mode": "manual",
                "adaptive_enabled": False,
                "require_human_approval": True,
                "auto_apply_enabled": False,
                "enforce_manual_cycles": False,
                "overdue_action": "observe",
            },
            "capabilities": [
                "mission_schedule_portfolio",
                "timezone_aware_windows",
                "quiet_hours",
                "deadline_surveillance",
                "cycle_schedule_admission",
                "adaptive_cadence_recommendations",
                "safe_schedule_auto_apply",
                "planner_schedule_context",
            ],
        }

    def get_policy(self, mission_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            policy = self._policy_row(session, mission_id)
            return self._policy_to_dict(policy, mission)

    async def upsert_policy(
        self,
        mission_id: str,
        request: MissionSchedulePolicyUpsert,
    ) -> dict[str, Any]:
        self._zone(request.timezone, strict=True)

        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            row = self._policy_row(session, mission_id)
            created = row is None
            if row is None:
                row = MissionSchedulePolicyModel(
                    workspace_id=mission.workspace_id,
                    mission_id=mission.id,
                )
                session.add(row)
            values = request.model_dump()
            values["schedule_mode"] = request.schedule_mode.value
            values["overdue_action"] = request.overdue_action.value
            values["timezone_name"] = values.pop("timezone")
            values["allowed_weekdays_json"] = values.pop("allowed_weekdays")
            values["metadata_json"] = values.pop("metadata")
            for field, value in values.items():
                setattr(row, field, value)
            session.flush()
            result = self._policy_to_dict(row, mission)

        await self._publish(
            "mission.schedule.policy.created" if created else "mission.schedule.policy.updated",
            workspace_id=result["workspace_id"],
            mission_id=mission_id,
            payload={"policy": result},
        )
        return result

    def dashboard(self, mission_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            policy = self._policy_row(session, mission_id)
            windows = list(
                session.scalars(
                    select(MissionScheduleWindowModel)
                    .where(MissionScheduleWindowModel.mission_id == mission_id)
                    .order_by(
                        MissionScheduleWindowModel.priority.desc(),
                        MissionScheduleWindowModel.created_at,
                    )
                ).all()
            )
            evaluations = list(
                session.scalars(
                    select(MissionScheduleEvaluationModel)
                    .where(MissionScheduleEvaluationModel.mission_id == mission_id)
                    .order_by(MissionScheduleEvaluationModel.created_at.desc())
                    .limit(20)
                ).all()
            )
            deadline_events = list(
                session.scalars(
                    select(MissionDeadlineEventModel)
                    .where(MissionDeadlineEventModel.mission_id == mission_id)
                    .order_by(MissionDeadlineEventModel.detected_at.desc())
                    .limit(20)
                ).all()
            )
            next_allowed = self._next_allowed_at(
                session,
                mission,
                policy,
                utc_now(),
            )
            admission = self._evaluate_time_admission(
                session,
                mission,
                policy,
                now=utc_now(),
                manual=False,
                force=False,
            )
            return {
                "mission_id": mission.id,
                "workspace_id": mission.workspace_id,
                "status": mission.status,
                "deadline_at": iso(mission.deadline_at),
                "next_cycle_at": iso(mission.next_cycle_at),
                "next_allowed_at": iso(next_allowed),
                "policy": self._policy_to_dict(policy, mission),
                "current_admission": admission,
                "windows": [self._window_to_dict(row) for row in windows],
                "recent_evaluations": [
                    self._evaluation_to_dict(row) for row in evaluations
                ],
                "deadline_events": [
                    self._deadline_event_to_dict(row) for row in deadline_events
                ],
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
            windows = list(
                session.scalars(
                    select(MissionScheduleWindowModel)
                    .where(
                        MissionScheduleWindowModel.mission_id == mission_id,
                        MissionScheduleWindowModel.enabled.is_(True),
                    )
                    .order_by(MissionScheduleWindowModel.priority.desc())
                    .limit(max(1, min(limit, 100)))
                ).all()
            )
            evaluations = list(
                session.scalars(
                    select(MissionScheduleEvaluationModel)
                    .where(MissionScheduleEvaluationModel.mission_id == mission_id)
                    .order_by(MissionScheduleEvaluationModel.created_at.desc())
                    .limit(max(1, min(limit, 100)))
                ).all()
            )
            now = utc_now()
            deadline_seconds = None
            overdue = False
            if mission.deadline_at is not None:
                deadline_seconds = int(
                    (ensure_utc(mission.deadline_at) - now).total_seconds()
                )
                overdue = deadline_seconds < 0
            return {
                "mission_id": mission.id,
                "cycle_id": cycle_id,
                "policy": self._policy_to_dict(policy, mission),
                "deadline": {
                    "deadline_at": iso(mission.deadline_at),
                    "seconds_remaining": deadline_seconds,
                    "overdue": overdue,
                },
                "next_cycle_at": iso(mission.next_cycle_at),
                "next_allowed_at": iso(
                    self._next_allowed_at(session, mission, policy, now)
                ),
                "windows": [self._window_to_dict(row) for row in windows],
                "recent_evaluations": [
                    self._evaluation_to_dict(row) for row in evaluations
                ],
            }

    async def create_window(
        self,
        mission_id: str,
        request: MissionScheduleWindowCreate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            row = MissionScheduleWindowModel(
                workspace_id=mission.workspace_id,
                mission_id=mission.id,
                name=request.name,
                enabled=request.enabled,
                weekdays_json=request.weekdays,
                start_time=request.start_time,
                end_time=request.end_time,
                starts_at=parse_datetime(request.starts_at),
                ends_at=parse_datetime(request.ends_at),
                priority=request.priority,
                max_cycles=request.max_cycles,
                metadata_json=request.metadata,
            )
            if row.starts_at and row.ends_at and row.starts_at >= row.ends_at:
                raise AutonomousMissionError("starts_at must be earlier than ends_at.")
            session.add(row)
            session.flush()
            result = self._window_to_dict(row)

        await self._publish(
            "mission.schedule.window.created",
            workspace_id=result["workspace_id"],
            mission_id=mission_id,
            payload={"window": result},
        )
        return result

    def list_windows(self, mission_id: str) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_mission(session, mission_id)
            rows = list(
                session.scalars(
                    select(MissionScheduleWindowModel)
                    .where(MissionScheduleWindowModel.mission_id == mission_id)
                    .order_by(
                        MissionScheduleWindowModel.priority.desc(),
                        MissionScheduleWindowModel.created_at,
                    )
                ).all()
            )
            return [self._window_to_dict(row) for row in rows]

    async def update_window(
        self,
        window_id: str,
        request: MissionScheduleWindowUpdate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(MissionScheduleWindowModel, window_id)
            if row is None:
                raise MissionScheduleWindowNotFound("Mission Schedule Window not found.")
            values = request.model_dump(exclude_unset=True)
            if "weekdays" in values:
                values["weekdays_json"] = values.pop("weekdays")
            if "metadata" in values:
                values["metadata_json"] = values.pop("metadata")
            if "starts_at" in values:
                values["starts_at"] = parse_datetime(values["starts_at"])
            if "ends_at" in values:
                values["ends_at"] = parse_datetime(values["ends_at"])
            for field, value in values.items():
                setattr(row, field, value)
            if row.starts_at and row.ends_at and ensure_utc(row.starts_at) >= ensure_utc(row.ends_at):
                raise AutonomousMissionError("starts_at must be earlier than ends_at.")
            result = self._window_to_dict(row)

        await self._publish(
            "mission.schedule.window.updated",
            workspace_id=result["workspace_id"],
            mission_id=result["mission_id"],
            payload={"window": result},
        )
        return result

    async def delete_window(self, window_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(MissionScheduleWindowModel, window_id)
            if row is None:
                raise MissionScheduleWindowNotFound("Mission Schedule Window not found.")
            result = self._window_to_dict(row)
            session.delete(row)

        await self._publish(
            "mission.schedule.window.deleted",
            workspace_id=result["workspace_id"],
            mission_id=result["mission_id"],
            payload={"window_id": window_id},
        )
        return result

    async def evaluate(
        self,
        mission_id: str,
        request: MissionScheduleEvaluationRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            policy = self._policy_row(session, mission_id)
            if policy is None:
                policy = MissionSchedulePolicyModel(
                    workspace_id=mission.workspace_id,
                    mission_id=mission.id,
                )
                session.add(policy)
                session.flush()

            sample_size = max(1, int(policy.adaptation_sample_cycles))
            cycles = list(
                session.scalars(
                    select(MissionCycleModel)
                    .where(MissionCycleModel.mission_id == mission_id)
                    .order_by(MissionCycleModel.created_at.desc())
                    .limit(sample_size)
                ).all()
            )
            terminal = [row for row in cycles if row.status in self.TERMINAL_CYCLE_STATUSES]
            failed = [row for row in terminal if row.status == "failed"]
            failure_rate = (len(failed) / len(terminal) * 100.0) if terminal else 0.0

            updates = list(
                session.scalars(
                    select(MissionProgressUpdateModel)
                    .where(MissionProgressUpdateModel.mission_id == mission_id)
                    .order_by(MissionProgressUpdateModel.created_at.desc())
                    .limit(2)
                ).all()
            )
            progress_velocity = 0.0
            if len(updates) >= 2:
                progress_velocity = max(
                    0.0,
                    min(
                        100.0,
                        float(updates[0].new_progress_percent)
                        - float(updates[1].new_progress_percent),
                    ),
                )

            now = utc_now()
            urgency = self._deadline_urgency(mission, now)
            previous = int(policy.base_interval_seconds)
            recommended = previous
            reasons: list[str] = []

            adaptive = bool(
                policy.enabled
                and policy.schedule_mode == MissionScheduleMode.ADAPTIVE.value
                and policy.adaptive_enabled
            )
            if adaptive:
                if urgency >= 80:
                    recommended = int(recommended * 0.45)
                    reasons.append("deadline urgency is critical")
                elif urgency >= 55:
                    recommended = int(recommended * 0.70)
                    reasons.append("deadline urgency is elevated")
                elif float(mission.progress_percent) >= 90:
                    recommended = int(recommended * 1.35)
                    reasons.append("mission is close to completion")

                if failure_rate >= 50:
                    recommended = int(recommended * 1.50)
                    reasons.append("recent failure rate is high")
                elif failure_rate >= 25:
                    recommended = int(recommended * 1.20)
                    reasons.append("recent failure rate is elevated")

                if progress_velocity >= 20 and urgency < 55:
                    recommended = int(recommended * 1.15)
                    reasons.append("recent progress velocity is healthy")
                elif progress_velocity <= 1 and urgency >= 40:
                    recommended = int(recommended * 0.85)
                    reasons.append("progress is slow relative to deadline")

            recommended = max(
                int(policy.min_interval_seconds),
                min(int(policy.max_interval_seconds), max(30, recommended)),
            )
            if recommended < previous:
                decision = MissionScheduleDecision.ACCELERATE.value
            elif recommended > previous:
                decision = MissionScheduleDecision.SLOW_DOWN.value
            else:
                decision = MissionScheduleDecision.KEEP.value

            automatic_allowed = bool(
                request.automatic
                and policy.enabled
                and policy.schedule_mode == MissionScheduleMode.ADAPTIVE.value
                and policy.adaptive_enabled
                and policy.auto_apply_enabled
                and not policy.require_human_approval
            )
            apply_allowed = bool(
                request.apply
                and (recommended != previous or request.force)
                and (
                    not request.automatic
                    or automatic_allowed
                    or request.force
                )
            )
            applied: int | None = None
            if apply_allowed:
                policy.base_interval_seconds = recommended
                mission.next_cycle_at = self._next_allowed_at(
                    session,
                    mission,
                    policy,
                    now + timedelta(seconds=recommended),
                )
                mission.last_activity_at = now
                applied = recommended
                if request.automatic:
                    self._automatic_applies += 1

            row = MissionScheduleEvaluationModel(
                workspace_id=mission.workspace_id,
                mission_id=mission.id,
                cycle_id=mission.current_cycle_id,
                policy_id=policy.id,
                actor_id=request.actor_id,
                automatic=request.automatic,
                decision=decision,
                previous_interval_seconds=previous,
                recommended_interval_seconds=recommended,
                applied_interval_seconds=applied,
                urgency_score=round(urgency, 2),
                failure_rate_percent=round(failure_rate, 2),
                progress_velocity_percent=round(progress_velocity, 2),
                reason="; ".join(reasons) or request.rationale,
                metrics_json={
                    "sample_cycles": len(cycles),
                    "terminal_cycles": len(terminal),
                    "failed_cycles": len(failed),
                    "mission_progress_percent": float(mission.progress_percent),
                    "deadline_at": iso(mission.deadline_at),
                    "request_context": request.context,
                    "automatic_allowed": automatic_allowed,
                },
            )
            session.add(row)
            session.flush()
            result = self._evaluation_to_dict(row)
            result["next_cycle_at"] = iso(mission.next_cycle_at)
            result["applied"] = applied is not None
            self._evaluations += 1

        await self._publish(
            "mission.schedule.evaluated",
            workspace_id=result["workspace_id"],
            mission_id=mission_id,
            payload={"evaluation": result},
        )
        if result["applied"]:
            await self._publish(
                "mission.schedule.applied",
                workspace_id=result["workspace_id"],
                mission_id=mission_id,
                payload={"evaluation": result},
            )
        return result

    def evaluate_cycle_admission(
        self,
        cycle_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            cycle = session.get(MissionCycleModel, cycle_id)
            if cycle is None:
                raise MissionCycleNotFound(f"Mission Cycle not found: {cycle_id}.")
            mission = self._require_mission(session, cycle.mission_id)
            policy = self._policy_row(session, mission.id)
            manual = cycle.trigger == "manual"
            result = self._evaluate_time_admission(
                session,
                mission,
                policy,
                now=utc_now(),
                manual=manual,
                force=force,
            )
            result.update(
                {
                    "cycle_id": cycle.id,
                    "mission_id": mission.id,
                    "workspace_id": mission.workspace_id,
                    "trigger": cycle.trigger,
                }
            )
            return result

    def next_cycle_at(
        self,
        mission_id: str,
        *,
        fallback_interval_seconds: int = 3600,
        from_time: datetime | None = None,
    ) -> datetime:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            policy = self._policy_row(session, mission_id)
            interval = (
                int(policy.base_interval_seconds)
                if policy is not None and policy.enabled
                else max(30, int(fallback_interval_seconds))
            )
            candidate = (ensure_utc(from_time) or utc_now()) + timedelta(seconds=interval)
            return self._next_allowed_at(session, mission, policy, candidate)

    async def scheduler_tick(
        self,
        *,
        workspace_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        deadline_result = await self.scan_deadlines(
            workspace_id=workspace_id,
            limit=limit,
        )
        with self._session_factory() as session:
            statement = (
                select(WorkspaceMissionModel)
                .join(
                    MissionSchedulePolicyModel,
                    MissionSchedulePolicyModel.mission_id == WorkspaceMissionModel.id,
                )
                .where(
                    WorkspaceMissionModel.status == "active",
                    MissionSchedulePolicyModel.enabled.is_(True),
                    MissionSchedulePolicyModel.schedule_mode == MissionScheduleMode.ADAPTIVE.value,
                    MissionSchedulePolicyModel.adaptive_enabled.is_(True),
                    MissionSchedulePolicyModel.auto_apply_enabled.is_(True),
                    MissionSchedulePolicyModel.require_human_approval.is_(False),
                )
                .order_by(WorkspaceMissionModel.priority.desc())
                .limit(max(1, min(limit, 1000)))
            )
            if workspace_id is not None:
                statement = statement.where(
                    WorkspaceMissionModel.workspace_id == workspace_id
                )
            candidates: list[str] = []
            for mission in session.scalars(statement).all():
                policy = self._policy_row(session, mission.id)
                if policy is None:
                    continue
                last_evaluation = session.scalar(
                    select(MissionScheduleEvaluationModel)
                    .where(MissionScheduleEvaluationModel.mission_id == mission.id)
                    .order_by(MissionScheduleEvaluationModel.created_at.desc())
                    .limit(1)
                )
                cooldown_seconds = max(
                    300,
                    min(int(policy.base_interval_seconds), 3600),
                )
                if (
                    last_evaluation is not None
                    and ensure_utc(last_evaluation.created_at)
                    > utc_now() - timedelta(seconds=cooldown_seconds)
                ):
                    continue
                candidates.append(mission.id)

        adaptive_results: list[str] = []
        for mission_id in candidates:
            try:
                result = await self.evaluate(
                    mission_id,
                    MissionScheduleEvaluationRequest(
                        actor_id="mission-scheduler",
                        apply=True,
                        automatic=True,
                        rationale="Automatic adaptive cadence evaluation.",
                    ),
                )
                adaptive_results.append(result["id"])
            except AutonomousMissionError:
                continue
        return {
            "deadline_scan": deadline_result,
            "adaptive_evaluations": adaptive_results,
        }

    async def scan_deadlines(
        self,
        *,
        workspace_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        now = utc_now()
        created: list[dict[str, Any]] = []
        paused: list[str] = []
        with self._session_factory() as session:
            statement = (
                select(WorkspaceMissionModel)
                .where(
                    WorkspaceMissionModel.deadline_at.is_not(None),
                    WorkspaceMissionModel.status.not_in(self.TERMINAL_MISSION_STATUSES),
                )
                .order_by(WorkspaceMissionModel.deadline_at.asc())
                .limit(max(1, min(limit, 1000)))
            )
            if workspace_id is not None:
                statement = statement.where(
                    WorkspaceMissionModel.workspace_id == workspace_id
                )
            missions = list(session.scalars(statement).all())
            for mission in missions:
                policy = self._policy_row(session, mission.id)
                warning_seconds = (
                    int(policy.deadline_warning_seconds)
                    if policy is not None
                    else 86_400
                )
                deadline = ensure_utc(mission.deadline_at)
                seconds_remaining = int((deadline - now).total_seconds())
                event_type: str | None = None
                severity = "info"
                if seconds_remaining < 0:
                    event_type = "overdue"
                    severity = "critical"
                elif seconds_remaining <= warning_seconds:
                    event_type = "approaching"
                    severity = "warning"
                if event_type is None:
                    continue
                existing = session.scalar(
                    select(MissionDeadlineEventModel).where(
                        MissionDeadlineEventModel.mission_id == mission.id,
                        MissionDeadlineEventModel.event_type == event_type,
                        MissionDeadlineEventModel.resolved_at.is_(None),
                    )
                )
                if existing is None:
                    row = MissionDeadlineEventModel(
                        workspace_id=mission.workspace_id,
                        mission_id=mission.id,
                        event_type=event_type,
                        severity=severity,
                        deadline_at=deadline,
                        details_json={
                            "seconds_remaining": seconds_remaining,
                            "mission_status": mission.status,
                            "mission_progress_percent": float(mission.progress_percent),
                        },
                    )
                    session.add(row)
                    session.flush()
                    created.append(self._deadline_event_to_dict(row))
                    self._deadline_events_created += 1
                if (
                    event_type == "overdue"
                    and policy is not None
                    and policy.enabled
                    and policy.overdue_action == MissionOverdueAction.PAUSE.value
                    and mission.status == "active"
                ):
                    mission.status = "paused"
                    mission.pause_reason = "Mission deadline exceeded."
                    mission.next_cycle_at = None
                    mission.last_activity_at = now
                    paused.append(mission.id)
                    self._deadline_pauses += 1

        for event in created:
            await self._publish(
                f"mission.deadline.{event['event_type']}",
                workspace_id=event["workspace_id"],
                mission_id=event["mission_id"],
                payload={"deadline_event": event},
            )
        for mission_id in paused:
            workspace = next(
                (
                    item["workspace_id"]
                    for item in created
                    if item["mission_id"] == mission_id
                ),
                workspace_id,
            )
            await self._publish(
                "mission.schedule.paused_overdue",
                workspace_id=workspace,
                mission_id=mission_id,
                payload={"mission_id": mission_id},
            )
        return {
            "scanned": len(missions),
            "created": created,
            "paused_mission_ids": paused,
        }

    def list_evaluations(
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
                    select(MissionScheduleEvaluationModel)
                    .where(MissionScheduleEvaluationModel.mission_id == mission_id)
                    .order_by(MissionScheduleEvaluationModel.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._evaluation_to_dict(row) for row in rows]

    def list_deadline_events(
        self,
        mission_id: str,
        *,
        open_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_mission(session, mission_id)
            statement = select(MissionDeadlineEventModel).where(
                MissionDeadlineEventModel.mission_id == mission_id
            )
            if open_only:
                statement = statement.where(
                    MissionDeadlineEventModel.resolved_at.is_(None)
                )
            rows = list(
                session.scalars(
                    statement.order_by(MissionDeadlineEventModel.detected_at.desc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._deadline_event_to_dict(row) for row in rows]

    async def resolve_deadline_event(
        self,
        event_id: str,
        request: MissionDeadlineResolveRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(MissionDeadlineEventModel, event_id)
            if row is None:
                raise MissionDeadlineEventNotFound("Mission Deadline Event not found.")
            if row.resolved_at is None:
                row.resolved_at = utc_now()
                row.resolved_by = request.actor_id
                row.resolution_reason = request.reason
            result = self._deadline_event_to_dict(row)

        await self._publish(
            "mission.deadline.resolved",
            workspace_id=result["workspace_id"],
            mission_id=result["mission_id"],
            payload={"deadline_event": result},
        )
        return result

    def _evaluate_time_admission(
        self,
        session: Session,
        mission: WorkspaceMissionModel,
        policy: MissionSchedulePolicyModel | None,
        *,
        now: datetime,
        manual: bool,
        force: bool,
    ) -> dict[str, Any]:
        if force:
            return {"allowed": True, "forced": True, "reasons": []}
        if policy is None or not policy.enabled:
            return {"allowed": True, "governed": False, "reasons": []}
        if manual and not policy.enforce_manual_cycles:
            return {
                "allowed": True,
                "governed": True,
                "manual_bypass": True,
                "reasons": [],
            }

        reasons: list[str] = []
        deadline = ensure_utc(mission.deadline_at)
        if (
            deadline is not None
            and deadline < now
            and policy.overdue_action == MissionOverdueAction.PAUSE.value
        ):
            reasons.append("mission deadline exceeded")

        local_now = now.astimezone(self._zone(policy.timezone_name))
        weekdays = list(policy.allowed_weekdays_json or list(range(7)))
        if weekdays and local_now.weekday() not in weekdays:
            reasons.append("weekday is not allowed")
        if self._is_quiet_time(
            local_now.time(),
            policy.quiet_hours_start,
            policy.quiet_hours_end,
        ):
            reasons.append("quiet hours are active")

        windows = list(
            session.scalars(
                select(MissionScheduleWindowModel).where(
                    MissionScheduleWindowModel.mission_id == mission.id,
                    MissionScheduleWindowModel.enabled.is_(True),
                )
            ).all()
        )
        if windows and not any(
            self._window_matches(session, window, local_now, now)
            for window in windows
        ):
            reasons.append("outside all enabled schedule windows")

        return {
            "allowed": not reasons,
            "governed": True,
            "forced": False,
            "manual_bypass": False,
            "local_time": local_now.isoformat(),
            "timezone": policy.timezone_name,
            "reasons": reasons,
        }

    def _next_allowed_at(
        self,
        session: Session,
        mission: WorkspaceMissionModel,
        policy: MissionSchedulePolicyModel | None,
        candidate: datetime,
    ) -> datetime:
        candidate = ensure_utc(candidate) or utc_now()
        if policy is None or not policy.enabled:
            return candidate
        for _ in range(14 * 24 * 4 + 1):
            result = self._evaluate_time_admission(
                session,
                mission,
                policy,
                now=candidate,
                manual=False,
                force=False,
            )
            if result["allowed"]:
                return candidate
            candidate += timedelta(minutes=15)
        return candidate

    def _window_matches(
        self,
        session: Session,
        window: MissionScheduleWindowModel,
        local_now: datetime,
        utc_value: datetime,
    ) -> bool:
        starts_at = ensure_utc(window.starts_at)
        ends_at = ensure_utc(window.ends_at)
        if starts_at is not None and utc_value < starts_at:
            return False
        if ends_at is not None and utc_value >= ends_at:
            return False
        weekdays = list(window.weekdays_json or list(range(7)))
        if weekdays and local_now.weekday() not in weekdays:
            return False
        if not self._time_in_range(local_now.time(), window.start_time, window.end_time):
            return False
        if window.max_cycles > 0:
            count = int(
                session.scalar(
                    select(func.count())
                    .select_from(MissionCycleModel)
                    .where(
                        MissionCycleModel.mission_id == window.mission_id,
                        MissionCycleModel.scheduled_for.is_not(None),
                        MissionCycleModel.scheduled_for >= starts_at
                        if starts_at is not None
                        else MissionCycleModel.created_at >= window.created_at,
                    )
                )
                or 0
            )
            if count >= window.max_cycles:
                return False
        return True

    @staticmethod
    def _time_in_range(current: time, start: str, end: str) -> bool:
        start_time = parse_hhmm(start)
        end_time = parse_hhmm(end)
        if start_time <= end_time:
            return start_time <= current <= end_time
        return current >= start_time or current <= end_time

    def _is_quiet_time(
        self,
        current: time,
        start: str | None,
        end: str | None,
    ) -> bool:
        if start is None or end is None:
            return False
        return self._time_in_range(current, start, end)

    @staticmethod
    def _zone(name: str, *, strict: bool = False) -> tzinfo:
        normalized = (name or "UTC").strip()
        upper_name = normalized.upper()
        if upper_name in {"UTC", "GMT", "Z"} or normalized in {
            "Etc/UTC",
            "Etc/GMT",
        }:
            # Windows Python installations may not include an IANA database.
            # UTC must remain available even when the optional tzdata package
            # has not been installed.
            return timezone.utc

        try:
            return ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            if strict:
                raise AutonomousMissionError(
                    "Unknown IANA timezone or timezone database is unavailable: "
                    f"{normalized}. Install the 'tzdata' package on Windows."
                ) from exc
            return timezone.utc

    @staticmethod
    def _deadline_urgency(mission: WorkspaceMissionModel, now: datetime) -> float:
        deadline = ensure_utc(mission.deadline_at)
        if deadline is None:
            return 0.0
        seconds = (deadline - now).total_seconds()
        if seconds <= 0:
            return 100.0
        remaining_progress = max(1.0, 100.0 - float(mission.progress_percent))
        days = seconds / 86400.0
        pressure = remaining_progress / max(0.25, days)
        return max(0.0, min(100.0, pressure))

    def _policy_row(
        self,
        session: Session,
        mission_id: str,
    ) -> MissionSchedulePolicyModel | None:
        return session.scalar(
            select(MissionSchedulePolicyModel).where(
                MissionSchedulePolicyModel.mission_id == mission_id
            )
        )

    @staticmethod
    def _require_mission(session: Session, mission_id: str) -> WorkspaceMissionModel:
        mission = session.get(WorkspaceMissionModel, mission_id)
        if mission is None:
            raise MissionNotFound(f"Mission not found: {mission_id}.")
        return mission

    def _policy_to_dict(
        self,
        row: MissionSchedulePolicyModel | None,
        mission: WorkspaceMissionModel,
    ) -> dict[str, Any]:
        if row is None:
            return {
                "id": None,
                "workspace_id": mission.workspace_id,
                "mission_id": mission.id,
                "enabled": False,
                "schedule_mode": "manual",
                "timezone": "UTC",
                "base_interval_seconds": 3600,
                "min_interval_seconds": 300,
                "max_interval_seconds": 86400,
                "adaptive_enabled": False,
                "require_human_approval": True,
                "auto_apply_enabled": False,
                "enforce_manual_cycles": False,
                "allowed_weekdays": list(range(7)),
                "quiet_hours_start": None,
                "quiet_hours_end": None,
                "deadline_warning_seconds": 86400,
                "overdue_action": "observe",
                "target_cycles_per_day": 1.0,
                "adaptation_sample_cycles": 10,
                "metadata": {},
                "created_at": None,
                "updated_at": None,
            }
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "enabled": row.enabled,
            "schedule_mode": row.schedule_mode,
            "timezone": row.timezone_name,
            "base_interval_seconds": row.base_interval_seconds,
            "min_interval_seconds": row.min_interval_seconds,
            "max_interval_seconds": row.max_interval_seconds,
            "adaptive_enabled": row.adaptive_enabled,
            "require_human_approval": row.require_human_approval,
            "auto_apply_enabled": row.auto_apply_enabled,
            "enforce_manual_cycles": row.enforce_manual_cycles,
            "allowed_weekdays": list(row.allowed_weekdays_json or []),
            "quiet_hours_start": row.quiet_hours_start,
            "quiet_hours_end": row.quiet_hours_end,
            "deadline_warning_seconds": row.deadline_warning_seconds,
            "overdue_action": row.overdue_action,
            "target_cycles_per_day": row.target_cycles_per_day,
            "adaptation_sample_cycles": row.adaptation_sample_cycles,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _window_to_dict(row: MissionScheduleWindowModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "name": row.name,
            "enabled": row.enabled,
            "weekdays": list(row.weekdays_json or []),
            "start_time": row.start_time,
            "end_time": row.end_time,
            "starts_at": iso(row.starts_at),
            "ends_at": iso(row.ends_at),
            "priority": row.priority,
            "max_cycles": row.max_cycles,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _evaluation_to_dict(row: MissionScheduleEvaluationModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "cycle_id": row.cycle_id,
            "policy_id": row.policy_id,
            "actor_id": row.actor_id,
            "automatic": row.automatic,
            "decision": row.decision,
            "previous_interval_seconds": row.previous_interval_seconds,
            "recommended_interval_seconds": row.recommended_interval_seconds,
            "applied_interval_seconds": row.applied_interval_seconds,
            "urgency_score": row.urgency_score,
            "failure_rate_percent": row.failure_rate_percent,
            "progress_velocity_percent": row.progress_velocity_percent,
            "reason": row.reason,
            "metrics": dict(row.metrics_json or {}),
            "created_at": iso(row.created_at),
        }

    @staticmethod
    def _deadline_event_to_dict(row: MissionDeadlineEventModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "event_type": row.event_type,
            "severity": row.severity,
            "deadline_at": iso(row.deadline_at),
            "detected_at": iso(row.detected_at),
            "resolved_at": iso(row.resolved_at),
            "resolved_by": row.resolved_by,
            "resolution_reason": row.resolution_reason,
            "details": dict(row.details_json or {}),
        }

    async def _publish(
        self,
        event_type: str,
        *,
        workspace_id: str | None,
        mission_id: str,
        payload: dict[str, Any],
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="mission_schedule_service",
                workspace_id=workspace_id,
                correlation_id=mission_id,
                payload={"mission_id": mission_id, **payload},
            )
        )
