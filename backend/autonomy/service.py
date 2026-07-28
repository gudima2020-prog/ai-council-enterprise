from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from backend.autonomy.models import (
    AutonomousWorkspacePolicyModel,
    MissionCycleModel,
    MissionGoalModel,
    MissionProgressUpdateModel,
    WorkspaceMissionModel,
)
from backend.autonomy.schemas import (
    AutonomousWorkspacePolicyUpsert,
    AutonomyMode,
    MissionActivateRequest,
    MissionCreate,
    MissionCycleCreateRequest,
    MissionCycleRunRequest,
    MissionCycleStatus,
    MissionCycleTrigger,
    MissionDecisionRequest,
    MissionGoalCreate,
    MissionGoalStatus,
    MissionGoalUpdate,
    MissionPauseRequest,
    MissionProgressRequest,
    MissionStatus,
    MissionUpdate,
)
from backend.core.events import Event, EventBus
from backend.database.session import session_scope
from backend.orchestration.planner import (
    ExecutionPlannerError,
    ExecutionPlannerService,
)
from backend.orchestration.planner_schemas import (
    ExecutionPlanGenerationRequest,
    PlannerMode,
)


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


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


class AutonomousMissionError(RuntimeError):
    pass


class MissionNotFound(AutonomousMissionError):
    pass


class MissionGoalNotFound(AutonomousMissionError):
    pass


class MissionCycleNotFound(AutonomousMissionError):
    pass


class MissionStateError(AutonomousMissionError):
    pass


class AutonomousMissionService:
    """Controls long-running Workspace missions and planning cycles.

    The scheduler is conservative by default. A Workspace must have an enabled
    policy in ``autonomous`` mode before a due Mission is planned without a
    direct API request. Automatic start is additionally blocked when approval
    is required.
    """

    ACTIVE_CYCLE_STATUSES = {
        MissionCycleStatus.QUEUED.value,
        MissionCycleStatus.PLANNING.value,
        MissionCycleStatus.READY.value,
        MissionCycleStatus.RUNNING.value,
    }

    TERMINAL_MISSION_STATUSES = {
        MissionStatus.COMPLETED.value,
        MissionStatus.FAILED.value,
        MissionStatus.CANCELLED.value,
    }

    def __init__(
        self,
        *,
        event_bus: EventBus,
        planner: ExecutionPlannerService,
        session_factory: SessionContextFactory = session_scope,
        scheduler_interval_seconds: int = 15,
        memory_context_provider: Callable[..., dict[str, Any]] | None = None,
        governance_context_provider: Callable[..., dict[str, Any]] | None = None,
        strategy_context_provider: Callable[..., dict[str, Any]] | None = None,
        cycle_strategy_assigner: Callable[..., Any] | None = None,
        resource_context_provider: Callable[..., dict[str, Any]] | None = None,
        cycle_resource_allocator: Callable[..., Any] | None = None,
        cycle_admission_guard: Callable[..., dict[str, Any]] | None = None,
        schedule_context_provider: Callable[..., dict[str, Any]] | None = None,
        cycle_schedule_guard: Callable[..., dict[str, Any]] | None = None,
        schedule_next_resolver: Callable[..., datetime] | None = None,
        schedule_tick_hook: Callable[..., Any] | None = None,
        portfolio_context_provider: Callable[..., dict[str, Any]] | None = None,
        cycle_portfolio_guard: Callable[..., dict[str, Any]] | None = None,
        portfolio_tick_hook: Callable[..., Any] | None = None,
        workspace_resource_context_provider: Callable[..., dict[str, Any]] | None = None,
        cycle_workspace_resource_allocator: Callable[..., Any] | None = None,
        workspace_resource_tick_hook: Callable[..., Any] | None = None,
        forecast_context_provider: Callable[..., dict[str, Any]] | None = None,
        learning_context_provider: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._planner = planner
        self._session_factory = session_factory
        self._scheduler_interval_seconds = max(5, scheduler_interval_seconds)
        self._memory_context_provider = memory_context_provider
        self._governance_context_provider = governance_context_provider
        self._strategy_context_provider = strategy_context_provider
        self._cycle_strategy_assigner = cycle_strategy_assigner
        self._resource_context_provider = resource_context_provider
        self._cycle_resource_allocator = cycle_resource_allocator
        self._cycle_admission_guard = cycle_admission_guard
        self._schedule_context_provider = schedule_context_provider
        self._cycle_schedule_guard = cycle_schedule_guard
        self._schedule_next_resolver = schedule_next_resolver
        self._schedule_tick_hook = schedule_tick_hook
        self._portfolio_context_provider = portfolio_context_provider
        self._cycle_portfolio_guard = cycle_portfolio_guard
        self._portfolio_tick_hook = portfolio_tick_hook
        self._workspace_resource_context_provider = workspace_resource_context_provider
        self._cycle_workspace_resource_allocator = cycle_workspace_resource_allocator
        self._workspace_resource_tick_hook = workspace_resource_tick_hook
        self._forecast_context_provider = forecast_context_provider
        self._learning_context_provider = learning_context_provider
        self._scheduler_task: asyncio.Task[None] | None = None
        self._cycle_tasks: set[asyncio.Task[Any]] = set()
        self._cycle_locks: dict[str, asyncio.Lock] = {}
        self._tick_lock = asyncio.Lock()
        self._running = False
        self._ticks = 0
        self._cycles_started = 0
        self._cycles_completed = 0
        self._cycles_failed = 0
        self._last_tick_at: datetime | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._scheduler_task = asyncio.create_task(
            self._scheduler_loop(),
            name="autonomous-mission-scheduler",
        )

    async def shutdown(self) -> None:
        self._running = False
        scheduler = self._scheduler_task
        self._scheduler_task = None
        if scheduler is not None:
            scheduler.cancel()
            await asyncio.gather(scheduler, return_exceptions=True)

        tasks = list(self._cycle_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._cycle_tasks.clear()
        self._cycle_locks.clear()

    def status(self) -> dict[str, Any]:
        with self._session_factory() as session:
            policies = int(
                session.scalar(
                    select(func.count()).select_from(
                        AutonomousWorkspacePolicyModel
                    )
                )
                or 0
            )
            missions = list(
                session.scalars(select(WorkspaceMissionModel)).all()
            )
            cycles = list(session.scalars(select(MissionCycleModel)).all())

        return {
            "running": self._running,
            "scheduler_interval_seconds": self._scheduler_interval_seconds,
            "ticks": self._ticks,
            "last_tick_at": iso(self._last_tick_at),
            "cycle_tasks": sum(
                1 for task in self._cycle_tasks if not task.done()
            ),
            "cycles_started": self._cycles_started,
            "cycles_completed": self._cycles_completed,
            "cycles_failed": self._cycles_failed,
            "policies": policies,
            "missions": len(missions),
            "active_missions": sum(
                1 for row in missions if row.status == MissionStatus.ACTIVE.value
            ),
            "active_cycles": sum(
                1 for row in cycles if row.status in self.ACTIVE_CYCLE_STATUSES
            ),
            "capabilities": [
                "workspace_autonomy_policy",
                "long_running_missions",
                "goal_dependency_graph",
                "weighted_progress",
                "scheduled_planning_cycles",
                "execution_plan_linkage",
                "conservative_autonomous_mode",
                "mission_progress_evidence",
            ],
        }

    def get_policy(self, workspace_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.scalar(
                select(AutonomousWorkspacePolicyModel).where(
                    AutonomousWorkspacePolicyModel.workspace_id == workspace_id
                )
            )
            return self._policy_to_dict(row, workspace_id=workspace_id)

    async def upsert_policy(
        self,
        workspace_id: str,
        request: AutonomousWorkspacePolicyUpsert,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.scalar(
                select(AutonomousWorkspacePolicyModel).where(
                    AutonomousWorkspacePolicyModel.workspace_id == workspace_id
                )
            )
            created = row is None
            if row is None:
                row = AutonomousWorkspacePolicyModel(workspace_id=workspace_id)
                session.add(row)

            values = request.model_dump(mode="json")
            values["autonomy_mode"] = request.autonomy_mode.value
            values["metadata_json"] = values.pop("metadata")
            for key, value in values.items():
                setattr(row, key, value)
            session.flush()
            result = self._policy_to_dict(row, workspace_id=workspace_id)

        await self._event_bus.publish(
            Event(
                event_type=(
                    "autonomy.policy.created" if created
                    else "autonomy.policy.updated"
                ),
                source="autonomous_mission_service",
                workspace_id=workspace_id,
                correlation_id=row.id,
                payload={
                    "policy_id": row.id,
                    "workspace_id": workspace_id,
                    "enabled": row.enabled,
                    "autonomy_mode": row.autonomy_mode,
                },
            )
        )
        return result

    def dashboard(self, workspace_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            missions = list(
                session.scalars(
                    select(WorkspaceMissionModel)
                    .where(WorkspaceMissionModel.workspace_id == workspace_id)
                    .order_by(
                        WorkspaceMissionModel.priority.desc(),
                        WorkspaceMissionModel.created_at.desc(),
                    )
                ).all()
            )
            mission_ids = [row.id for row in missions]
            cycles: list[MissionCycleModel] = []
            if mission_ids:
                cycles = list(
                    session.scalars(
                        select(MissionCycleModel).where(
                            MissionCycleModel.mission_id.in_(mission_ids)
                        )
                    ).all()
                )

        return {
            "workspace_id": workspace_id,
            "policy": self.get_policy(workspace_id),
            "mission_counts": {
                status.value: sum(
                    1 for row in missions if row.status == status.value
                )
                for status in MissionStatus
            },
            "cycle_counts": {
                status.value: sum(
                    1 for row in cycles if row.status == status.value
                )
                for status in MissionCycleStatus
            },
            "average_progress_percent": (
                round(
                    sum(float(row.progress_percent) for row in missions)
                    / len(missions),
                    2,
                )
                if missions else 0.0
            ),
            "overdue_missions": [
                row.id
                for row in missions
                if row.deadline_at is not None
                and ensure_utc(row.deadline_at) < utc_now()
                and row.status not in self.TERMINAL_MISSION_STATUSES
            ],
            "missions": [self._mission_summary(row) for row in missions[:50]],
        }

    async def create_mission(self, request: MissionCreate) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = WorkspaceMissionModel(
                workspace_id=request.workspace_id,
                title=request.title,
                objective=request.objective,
                success_criteria=request.success_criteria,
                strategy_hint=request.strategy_hint,
                priority=request.priority,
                planner_ref=request.planner_ref,
                planning_mode=request.planning_mode,
                auto_start_plans=request.auto_start_plans,
                max_cycles=request.max_cycles,
                deadline_at=request.deadline_at,
                metadata_json=request.metadata,
            )
            session.add(mission)
            session.flush()

            for goal_request in request.goals:
                self._add_goal_row(session, mission.id, goal_request)

            validation = self._validate_goal_graph(session, mission.id)
            if not validation["valid"]:
                raise AutonomousMissionError(
                    "Некорректный граф целей: "
                    + "; ".join(validation["errors"])
                )
            result = self._load_mission_dict(session, mission.id)

        await self._publish_mission_event(
            "mission.created",
            result,
            {"goal_count": len(result["goals"])},
        )
        return result

    def list_missions(
        self,
        *,
        workspace_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(WorkspaceMissionModel)
            if workspace_id is not None:
                statement = statement.where(
                    WorkspaceMissionModel.workspace_id == workspace_id
                )
            if status is not None:
                statement = statement.where(
                    WorkspaceMissionModel.status == status
                )
            rows = list(
                session.scalars(
                    statement.order_by(
                        WorkspaceMissionModel.priority.desc(),
                        WorkspaceMissionModel.created_at.desc(),
                    ).offset(offset).limit(limit)
                ).all()
            )
            return [self._mission_summary(row) for row in rows]

    def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            return self._load_mission_dict(session, mission_id)

    async def update_mission(
        self,
        mission_id: str,
        request: MissionUpdate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            if mission.status in self.TERMINAL_MISSION_STATUSES:
                raise MissionStateError(
                    "Завершённую Mission нельзя редактировать."
                )
            values = request.model_dump(exclude_unset=True)
            if "metadata" in values:
                values["metadata_json"] = values.pop("metadata")
            for key, value in values.items():
                setattr(mission, key, value)
            mission.version += 1
            mission.last_activity_at = utc_now()
            session.flush()
            result = self._load_mission_dict(session, mission_id)

        await self._publish_mission_event("mission.updated", result, {})
        return result

    async def delete_mission(self, mission_id: str) -> bool:
        with self._session_factory() as session:
            mission = session.get(WorkspaceMissionModel, mission_id)
            if mission is None:
                return False
            if mission.status != MissionStatus.DRAFT.value:
                raise MissionStateError(
                    "Удалять разрешено только Mission в статусе draft."
                )
            workspace_id = mission.workspace_id
            session.delete(mission)

        await self._event_bus.publish(
            Event(
                event_type="mission.deleted",
                source="autonomous_mission_service",
                workspace_id=workspace_id,
                correlation_id=mission_id,
                payload={"mission_id": mission_id},
            )
        )
        return True

    async def add_goal(
        self,
        mission_id: str,
        request: MissionGoalCreate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mutable_mission(session, mission_id)
            goal = self._add_goal_row(session, mission_id, request)
            validation = self._validate_goal_graph(session, mission_id)
            if not validation["valid"]:
                raise AutonomousMissionError(
                    "Некорректный граф целей: "
                    + "; ".join(validation["errors"])
                )
            mission.version += 1
            mission.last_activity_at = utc_now()
            result = self._goal_to_dict(goal)
            workspace_id = mission.workspace_id

        await self._event_bus.publish(
            Event(
                event_type="mission.goal.created",
                source="autonomous_mission_service",
                workspace_id=workspace_id,
                correlation_id=mission_id,
                payload={"mission_id": mission_id, "goal": result},
            )
        )
        return result

    async def update_goal(
        self,
        mission_id: str,
        goal_id: str,
        request: MissionGoalUpdate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mutable_mission(session, mission_id)
            goal = self._require_goal(session, mission_id, goal_id)
            values = request.model_dump(exclude_unset=True, mode="json")
            if "depends_on" in values:
                dependencies = self._clean_dependencies(
                    values.pop("depends_on"),
                    own_key=goal.goal_key,
                )
                goal.depends_on_json = dependencies
            if "metadata" in values:
                values["metadata_json"] = values.pop("metadata")
            if "status" in values:
                status = values.pop("status")
                goal.status = str(status)
                if goal.status == MissionGoalStatus.ACHIEVED.value:
                    goal.progress_percent = 100.0
                    goal.completed_at = utc_now()
            for key, value in values.items():
                setattr(goal, key, value)

            validation = self._validate_goal_graph(session, mission_id)
            if not validation["valid"]:
                raise AutonomousMissionError(
                    "Некорректный граф целей: "
                    + "; ".join(validation["errors"])
                )
            mission.version += 1
            mission.last_activity_at = utc_now()
            self._recalculate_mission_progress(session, mission)
            result = self._goal_to_dict(goal)
            workspace_id = mission.workspace_id

        await self._event_bus.publish(
            Event(
                event_type="mission.goal.updated",
                source="autonomous_mission_service",
                workspace_id=workspace_id,
                correlation_id=mission_id,
                payload={"mission_id": mission_id, "goal": result},
            )
        )
        return result

    async def delete_goal(self, mission_id: str, goal_id: str) -> bool:
        with self._session_factory() as session:
            mission = self._require_mutable_mission(session, mission_id)
            goal = self._require_goal(session, mission_id, goal_id)
            for other in session.scalars(
                select(MissionGoalModel).where(
                    MissionGoalModel.mission_id == mission_id
                )
            ).all():
                if goal.goal_key in (other.depends_on_json or []):
                    raise AutonomousMissionError(
                        f"Goal {goal.goal_key} используется зависимостью "
                        f"Goal {other.goal_key}."
                    )
            session.delete(goal)
            mission.version += 1
            mission.last_activity_at = utc_now()
            self._recalculate_mission_progress(session, mission)
            workspace_id = mission.workspace_id

        await self._event_bus.publish(
            Event(
                event_type="mission.goal.deleted",
                source="autonomous_mission_service",
                workspace_id=workspace_id,
                correlation_id=mission_id,
                payload={"mission_id": mission_id, "goal_id": goal_id},
            )
        )
        return True

    def validate_mission(self, mission_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            graph = self._validate_goal_graph(session, mission_id)
            errors = list(graph["errors"])
            if not mission.objective.strip():
                errors.append("Mission objective пуст.")
            if not graph["goal_count"]:
                errors.append("Mission должна содержать минимум одну Goal.")
            if mission.deadline_at is not None and ensure_utc(
                mission.deadline_at
            ) <= utc_now():
                errors.append("Mission deadline уже наступил.")
            return {
                "mission_id": mission_id,
                "valid": not errors,
                "errors": errors,
                "goal_count": graph["goal_count"],
                "topological_order": graph["topological_order"],
                "ready_goal_keys": graph["ready_goal_keys"],
            }

    async def activate_mission(
        self,
        mission_id: str,
        request: MissionActivateRequest,
    ) -> dict[str, Any]:
        validation = self.validate_mission(mission_id)
        if not validation["valid"]:
            raise MissionStateError(
                "Mission не прошла проверку: "
                + "; ".join(validation["errors"])
            )

        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            if mission.status not in {
                MissionStatus.DRAFT.value,
                MissionStatus.PAUSED.value,
            }:
                raise MissionStateError(
                    "Активировать можно Mission в статусе draft или paused."
                )
            policy = self._policy_row(session, mission.workspace_id)
            limit = policy.max_active_missions if policy else 3
            active_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(WorkspaceMissionModel)
                    .where(
                        WorkspaceMissionModel.workspace_id == mission.workspace_id,
                        WorkspaceMissionModel.status == MissionStatus.ACTIVE.value,
                        WorkspaceMissionModel.id != mission.id,
                    )
                )
                or 0
            )
            if active_count >= limit:
                raise MissionStateError(
                    f"Достигнут лимит активных Mission: {limit}."
                )

            now = utc_now()
            mission.status = MissionStatus.ACTIVE.value
            mission.started_at = mission.started_at or now
            mission.pause_reason = None
            mission.failure_reason = None
            mission.next_cycle_at = ensure_utc(request.first_cycle_at) or now
            mission.last_activity_at = now
            mission.version += 1
            for goal in session.scalars(
                select(MissionGoalModel).where(
                    MissionGoalModel.mission_id == mission.id,
                    MissionGoalModel.status == MissionGoalStatus.PENDING.value,
                )
            ).all():
                if not goal.depends_on_json:
                    goal.status = MissionGoalStatus.ACTIVE.value
                    goal.started_at = goal.started_at or now
            result = self._load_mission_dict(session, mission.id)

        await self._publish_mission_event(
            "mission.activated",
            result,
            {"actor_id": request.actor_id, "reason": request.reason},
        )
        return result

    async def pause_mission(
        self,
        mission_id: str,
        request: MissionPauseRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            if mission.status != MissionStatus.ACTIVE.value:
                raise MissionStateError("Поставить на паузу можно только active Mission.")
            mission.status = MissionStatus.PAUSED.value
            mission.pause_reason = request.reason
            mission.next_cycle_at = None
            mission.last_activity_at = utc_now()
            mission.version += 1
            result = self._load_mission_dict(session, mission.id)

        await self._publish_mission_event(
            "mission.paused",
            result,
            {"actor_id": request.actor_id, "reason": request.reason},
        )
        return result

    async def resume_mission(
        self,
        mission_id: str,
        request: MissionActivateRequest,
    ) -> dict[str, Any]:
        return await self.activate_mission(mission_id, request)

    async def complete_mission(
        self,
        mission_id: str,
        request: MissionDecisionRequest,
    ) -> dict[str, Any]:
        return await self._finish_mission(
            mission_id,
            status=MissionStatus.COMPLETED,
            actor_id=request.actor_id,
            reason=request.reason,
        )

    async def fail_mission(
        self,
        mission_id: str,
        request: MissionDecisionRequest,
    ) -> dict[str, Any]:
        return await self._finish_mission(
            mission_id,
            status=MissionStatus.FAILED,
            actor_id=request.actor_id,
            reason=request.reason,
        )

    async def cancel_mission(
        self,
        mission_id: str,
        request: MissionDecisionRequest,
    ) -> dict[str, Any]:
        return await self._finish_mission(
            mission_id,
            status=MissionStatus.CANCELLED,
            actor_id=request.actor_id,
            reason=request.reason,
        )

    async def record_progress(
        self,
        mission_id: str,
        request: MissionProgressRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            if mission.status in self.TERMINAL_MISSION_STATUSES:
                raise MissionStateError(
                    "Нельзя менять прогресс завершённой Mission."
                )
            goal: MissionGoalModel | None = None
            previous = float(mission.progress_percent)
            if request.goal_id is not None:
                goal = self._require_goal(session, mission_id, request.goal_id)
                previous = float(goal.progress_percent)
                new_value = self._progress_value(previous, request)
                goal.progress_percent = new_value
                if request.status is not None:
                    goal.status = request.status.value
                elif new_value >= 100:
                    goal.status = MissionGoalStatus.ACHIEVED.value
                elif goal.status == MissionGoalStatus.PENDING.value:
                    goal.status = MissionGoalStatus.ACTIVE.value
                if goal.status == MissionGoalStatus.ACHIEVED.value:
                    goal.progress_percent = 100.0
                    goal.completed_at = utc_now()
                if goal.status == MissionGoalStatus.ACTIVE.value:
                    goal.started_at = goal.started_at or utc_now()
                new_value = float(goal.progress_percent)
            else:
                new_value = self._progress_value(previous, request)
                mission.progress_percent = new_value

            update = MissionProgressUpdateModel(
                mission_id=mission.id,
                goal_id=goal.id if goal else None,
                actor_id=request.actor_id,
                previous_progress_percent=previous,
                new_progress_percent=new_value,
                message=request.message,
                evidence_json=request.evidence,
                metadata_json=request.metadata,
            )
            session.add(update)
            mission.last_activity_at = utc_now()
            mission.version += 1
            self._recalculate_mission_progress(session, mission)
            self._activate_unblocked_goals(session, mission.id)
            all_achieved = self._all_goals_achieved(session, mission.id)
            if all_achieved:
                mission.status = MissionStatus.COMPLETED.value
                mission.progress_percent = 100.0
                mission.completed_at = utc_now()
                mission.next_cycle_at = None
            session.flush()
            result = self._load_mission_dict(session, mission.id)
            update_result = self._progress_to_dict(update)

        await self._publish_mission_event(
            "mission.progress.recorded",
            result,
            {"progress_update": update_result},
        )
        if result["status"] == MissionStatus.COMPLETED.value:
            await self._publish_mission_event(
                "mission.completed",
                result,
                {"reason": "All mission goals achieved."},
            )
        return {"mission": result, "progress_update": update_result}

    def list_progress(
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
                    select(MissionProgressUpdateModel)
                    .where(MissionProgressUpdateModel.mission_id == mission_id)
                    .order_by(MissionProgressUpdateModel.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._progress_to_dict(row) for row in rows]

    async def create_cycle(
        self,
        mission_id: str,
        request: MissionCycleCreateRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            if mission.status != MissionStatus.ACTIVE.value:
                raise MissionStateError(
                    "Cycle можно создать только для active Mission."
                )
            if mission.cycle_count >= mission.max_cycles:
                raise MissionStateError("Mission исчерпала max_cycles.")
            if request.idempotency_key:
                existing = session.scalar(
                    select(MissionCycleModel).where(
                        MissionCycleModel.idempotency_key
                        == request.idempotency_key
                    )
                )
                if existing is not None:
                    if existing.mission_id != mission_id:
                        raise AutonomousMissionError(
                            "idempotency_key уже используется другой Mission."
                        )
                    return self._cycle_to_dict(existing)

            active_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(MissionCycleModel)
                    .where(
                        MissionCycleModel.mission_id == mission_id,
                        MissionCycleModel.status.in_(self.ACTIVE_CYCLE_STATUSES),
                    )
                )
                or 0
            )
            policy = self._policy_row(session, mission.workspace_id)
            max_parallel = policy.max_parallel_cycles if policy else 1
            if active_count >= max_parallel:
                raise MissionStateError(
                    f"Достигнут лимит активных Cycle: {max_parallel}."
                )

            goals = self._resolve_cycle_goals(
                session,
                mission_id,
                request.goal_ids,
            )
            cycle_number = int(
                session.scalar(
                    select(func.max(MissionCycleModel.cycle_number)).where(
                        MissionCycleModel.mission_id == mission_id
                    )
                )
                or 0
            ) + 1
            row = MissionCycleModel(
                mission_id=mission_id,
                cycle_number=cycle_number,
                status=MissionCycleStatus.QUEUED.value,
                trigger=request.trigger.value,
                goal_ids_json=[goal.id for goal in goals],
                idempotency_key=request.idempotency_key,
                scheduled_for=request.scheduled_for,
                request_json={"context": request.context},
            )
            session.add(row)
            session.flush()
            mission.current_cycle_id = row.id
            mission.last_activity_at = utc_now()
            result = self._cycle_to_dict(row)
            workspace_id = mission.workspace_id

        await self._event_bus.publish(
            Event(
                event_type="mission.cycle.created",
                source="autonomous_mission_service",
                workspace_id=workspace_id,
                correlation_id=mission_id,
                payload={"mission_id": mission_id, "cycle": result},
            )
        )
        return result

    def get_cycle(self, cycle_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(MissionCycleModel, cycle_id)
            return None if row is None else self._cycle_to_dict(row)

    def list_cycles(
        self,
        mission_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_mission(session, mission_id)
            statement = select(MissionCycleModel).where(
                MissionCycleModel.mission_id == mission_id
            )
            if status is not None:
                statement = statement.where(MissionCycleModel.status == status)
            rows = list(
                session.scalars(
                    statement.order_by(MissionCycleModel.cycle_number.desc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._cycle_to_dict(row) for row in rows]

    async def run_cycle(
        self,
        cycle_id: str,
        request: MissionCycleRunRequest,
    ) -> dict[str, Any]:
        lock = self._cycle_locks.setdefault(cycle_id, asyncio.Lock())
        async with lock:
            portfolio_admission: dict[str, Any] = {
                "allowed": True,
                "governed": False,
                "forced": request.force,
                "reasons": [],
            }
            if self._cycle_portfolio_guard is not None:
                portfolio_admission = dict(
                    self._cycle_portfolio_guard(cycle_id, force=request.force) or {}
                )
                if not portfolio_admission.get("allowed", False):
                    mission_id = str(portfolio_admission.get("mission_id") or "")
                    workspace_id = self._cycle_workspace_id(cycle_id)
                    await self._event_bus.publish(
                        Event(
                            event_type="mission.cycle.blocked_by_portfolio",
                            source="autonomous_mission_service",
                            workspace_id=workspace_id,
                            correlation_id=mission_id or None,
                            payload={
                                "mission_id": mission_id,
                                "cycle_id": cycle_id,
                                "portfolio_admission": portfolio_admission,
                            },
                        )
                    )
                    raise MissionStateError(
                        "Mission Cycle is blocked by Mission dependency or portfolio policy."
                    )

            schedule_admission: dict[str, Any] = {
                "allowed": True,
                "governed": False,
                "forced": request.force,
                "reasons": [],
            }
            if self._cycle_schedule_guard is not None:
                schedule_admission = dict(
                    self._cycle_schedule_guard(cycle_id, force=request.force) or {}
                )
                if not schedule_admission.get("allowed", False):
                    mission_id = str(schedule_admission.get("mission_id") or "")
                    workspace_id = self._cycle_workspace_id(cycle_id)
                    await self._event_bus.publish(
                        Event(
                            event_type="mission.cycle.blocked_by_schedule",
                            source="autonomous_mission_service",
                            workspace_id=workspace_id,
                            correlation_id=mission_id or None,
                            payload={
                                "mission_id": mission_id,
                                "cycle_id": cycle_id,
                                "schedule_admission": schedule_admission,
                            },
                        )
                    )
                    raise MissionStateError(
                        "Mission Cycle is blocked by schedule policy or deadline."
                    )

            admission: dict[str, Any] = {
                "allowed": True,
                "forced": request.force,
                "blocking_checkpoints": [],
            }
            if self._cycle_admission_guard is not None:
                admission = self._cycle_admission_guard(
                    cycle_id,
                    force=request.force,
                )
                if not admission.get("allowed", False):
                    mission_id = str(admission.get("mission_id") or "")
                    workspace_id = self._cycle_workspace_id(cycle_id)
                    await self._event_bus.publish(
                        Event(
                            event_type="mission.cycle.blocked_by_checkpoint",
                            source="autonomous_mission_service",
                            workspace_id=workspace_id,
                            correlation_id=mission_id or None,
                            payload={
                                "mission_id": mission_id,
                                "cycle_id": cycle_id,
                                "admission": admission,
                            },
                        )
                    )
                    raise MissionStateError(
                        "Mission Cycle заблокирован нерешённым Decision Checkpoint."
                    )
            mission_strategy_assignment: dict[str, Any] = {}
            if self._cycle_strategy_assigner is not None:
                try:
                    assigned = await self._cycle_strategy_assigner(
                        cycle_id,
                        actor_id="system",
                    )
                    mission_strategy_assignment = dict(assigned or {})
                except Exception:
                    mission_strategy_assignment = {}
            resource_admission: dict[str, Any] = {
                "allowed": True,
                "governed": False,
                "reasons": [],
                "allocation": None,
            }
            if self._cycle_resource_allocator is not None:
                resource_admission = dict(
                    await self._cycle_resource_allocator(
                        cycle_id,
                        actor_id="system",
                        force=request.force,
                    )
                    or {}
                )
                if not resource_admission.get("allowed", False):
                    mission_id = str(resource_admission.get("mission_id") or "")
                    workspace_id = self._cycle_workspace_id(cycle_id)
                    await self._event_bus.publish(
                        Event(
                            event_type="mission.cycle.blocked_by_resources",
                            source="autonomous_mission_service",
                            workspace_id=workspace_id,
                            correlation_id=mission_id or None,
                            payload={
                                "mission_id": mission_id,
                                "cycle_id": cycle_id,
                                "resource_admission": resource_admission,
                            },
                        )
                    )
                    raise MissionStateError(
                        "Mission Cycle заблокирован бюджетом или доступной мощностью."
                    )
            workspace_resource_admission: dict[str, Any] = {
                "allowed": True,
                "governed": False,
                "reasons": [],
                "reservation": None,
            }
            if self._cycle_workspace_resource_allocator is not None:
                workspace_resource_admission = dict(
                    await self._cycle_workspace_resource_allocator(
                        cycle_id,
                        actor_id="system",
                        force=request.force,
                    )
                    or {}
                )
                if not workspace_resource_admission.get("allowed", False):
                    mission_id = str(
                        workspace_resource_admission.get("mission_id") or ""
                    )
                    workspace_id = self._cycle_workspace_id(cycle_id)
                    await self._event_bus.publish(
                        Event(
                            event_type=(
                                "mission.cycle.blocked_by_workspace_resources"
                            ),
                            source="autonomous_mission_service",
                            workspace_id=workspace_id,
                            correlation_id=mission_id or None,
                            payload={
                                "mission_id": mission_id,
                                "cycle_id": cycle_id,
                                "workspace_resource_admission": (
                                    workspace_resource_admission
                                ),
                            },
                        )
                    )
                    raise MissionStateError(
                        "Mission Cycle заблокирован общим бюджетом или "
                        "мощностью Workspace."
                    )
            snapshot = self._prepare_cycle_for_planning(cycle_id, request)
            mission_memory: dict[str, Any] = {}
            if self._memory_context_provider is not None:
                try:
                    memory_snapshot = self._memory_context_provider(
                        snapshot["mission_id"],
                        limit=100,
                    )
                    mission_memory = dict(memory_snapshot.get("context") or {})
                except Exception:
                    mission_memory = {}
            mission_governance: dict[str, Any] = {}
            if self._governance_context_provider is not None:
                try:
                    governance_snapshot = self._governance_context_provider(
                        snapshot["mission_id"],
                        limit=50,
                    )
                    mission_governance = dict(governance_snapshot)
                except Exception:
                    mission_governance = {}
            mission_resources: dict[str, Any] = {}
            if self._resource_context_provider is not None:
                try:
                    resource_snapshot = self._resource_context_provider(
                        snapshot["mission_id"],
                        cycle_id=cycle_id,
                        limit=20,
                    )
                    mission_resources = dict(resource_snapshot or {})
                except Exception:
                    mission_resources = {}
            workspace_resources: dict[str, Any] = {}
            if self._workspace_resource_context_provider is not None:
                try:
                    workspace_resource_snapshot = (
                        self._workspace_resource_context_provider(
                            snapshot["mission_id"],
                            cycle_id=cycle_id,
                            limit=20,
                        )
                    )
                    workspace_resources = dict(
                        workspace_resource_snapshot or {}
                    )
                except Exception:
                    workspace_resources = {}
            mission_schedule: dict[str, Any] = {}
            if self._schedule_context_provider is not None:
                try:
                    schedule_snapshot = self._schedule_context_provider(
                        snapshot["mission_id"],
                        cycle_id=cycle_id,
                        limit=20,
                    )
                    mission_schedule = dict(schedule_snapshot or {})
                except Exception:
                    mission_schedule = {}
            mission_portfolio: dict[str, Any] = {}
            if self._portfolio_context_provider is not None:
                try:
                    portfolio_snapshot = self._portfolio_context_provider(
                        snapshot["mission_id"],
                        cycle_id=cycle_id,
                        limit=20,
                    )
                    mission_portfolio = dict(portfolio_snapshot or {})
                except Exception:
                    mission_portfolio = {}
            mission_forecast: dict[str, Any] = {}
            if self._forecast_context_provider is not None:
                try:
                    forecast_snapshot = self._forecast_context_provider(
                        snapshot["mission_id"],
                        cycle_id=cycle_id,
                        limit=20,
                    )
                    mission_forecast = dict(forecast_snapshot or {})
                except Exception:
                    mission_forecast = {}
            mission_learning: dict[str, Any] = {}
            if self._learning_context_provider is not None:
                try:
                    learning_snapshot = self._learning_context_provider(
                        snapshot["mission_id"],
                        cycle_id=cycle_id,
                        limit=20,
                    )
                    mission_learning = dict(learning_snapshot or {})
                except Exception:
                    mission_learning = {}
            mission_strategy: dict[str, Any] = {}
            if self._strategy_context_provider is not None:
                try:
                    strategy_snapshot = self._strategy_context_provider(
                        snapshot["mission_id"],
                        cycle_id=cycle_id,
                        limit=20,
                    )
                    mission_strategy = dict(strategy_snapshot or {})
                except Exception:
                    mission_strategy = {}
            selected_strategy = dict(
                mission_strategy.get("selected_strategy") or {}
            )
            selected_strategy_hint = str(
                selected_strategy.get("strategy_hint") or ""
            ).strip()
            effective_strategy_hint = snapshot["strategy_hint"]
            if selected_strategy_hint:
                effective_strategy_hint = "\n\n".join(
                    item
                    for item in (
                        snapshot["strategy_hint"].strip(),
                        "Selected Mission Strategy:\n" + selected_strategy_hint,
                    )
                    if item
                )
            try:
                planner_request = ExecutionPlanGenerationRequest(
                    workspace_id=snapshot["workspace_id"],
                    title=(
                        f"Mission: {snapshot['mission_title']} — "
                        f"cycle {snapshot['cycle_number']}"
                    ),
                    objective=snapshot["objective"],
                    strategy_hint=effective_strategy_hint,
                    planner_ref=snapshot["planner_ref"],
                    mode=PlannerMode(snapshot["planning_mode"]),
                    auto_validate=snapshot["auto_validate"],
                    auto_assign=snapshot["auto_assign"],
                    strict_assignment=snapshot["strict_assignment"],
                    auto_start=snapshot["auto_start"],
                    wait=request.wait,
                    wait_timeout_seconds=request.wait_timeout_seconds,
                    auto_replan=True,
                    max_replans=2,
                    auto_review=snapshot["auto_review"],
                    review_threshold=snapshot["review_threshold"],
                    auto_fix_review=snapshot["auto_fix_review"],
                    max_review_rounds=snapshot["max_review_rounds"],
                    require_review_pass=snapshot["require_review_pass"],
                    context={
                        **request.context,
                        "mission_id": snapshot["mission_id"],
                        "mission_cycle_id": cycle_id,
                        "goal_ids": snapshot["goal_ids"],
                        "mission_memory": mission_memory,
                        "mission_governance": mission_governance,
                        "mission_strategy": mission_strategy,
                        "mission_strategy_assignment": mission_strategy_assignment,
                        "mission_resources": mission_resources,
                        "mission_resource_admission": resource_admission,
                        "workspace_resources": workspace_resources,
                        "workspace_resource_admission": (
                            workspace_resource_admission
                        ),
                        "mission_schedule": mission_schedule,
                        "mission_schedule_admission": schedule_admission,
                        "mission_portfolio": mission_portfolio,
                        "mission_portfolio_admission": portfolio_admission,
                        "mission_forecast": mission_forecast,
                        "mission_learning": mission_learning,
                        "mission_cycle_admission": admission,
                    },
                )
                result = await self._planner.generate(planner_request)
            except Exception as exc:
                self._cycles_failed += 1
                failed = self._finish_cycle_failure(cycle_id, str(exc))
                await self._event_bus.publish(
                    Event(
                        event_type="mission.cycle.failed",
                        source="autonomous_mission_service",
                        workspace_id=snapshot["workspace_id"],
                        correlation_id=snapshot["mission_id"],
                        payload={
                            "mission_id": snapshot["mission_id"],
                            "cycle_id": cycle_id,
                            "error": str(exc),
                        },
                    )
                )
                if isinstance(exc, AutonomousMissionError):
                    raise
                if isinstance(exc, ExecutionPlannerError):
                    raise AutonomousMissionError(str(exc)) from exc
                raise AutonomousMissionError(str(exc)) from exc

            completed = self._finish_cycle_planning(cycle_id, result, snapshot)
            self._cycles_started += 1
            await self._event_bus.publish(
                Event(
                    event_type="mission.cycle.plan_created",
                    source="autonomous_mission_service",
                    workspace_id=snapshot["workspace_id"],
                    correlation_id=snapshot["mission_id"],
                    payload={
                        "mission_id": snapshot["mission_id"],
                        "cycle_id": cycle_id,
                        "execution_plan_id": completed["execution_plan_id"],
                        "status": completed["status"],
                    },
                )
            )
            return {
                "cycle": completed,
                "planner": result,
            }

    async def tick_once(
        self,
        *,
        workspace_id: str | None = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        async with self._tick_lock:
            self._ticks += 1
            self._last_tick_at = utc_now()
            portfolio_maintenance: dict[str, Any] = {}
            if self._portfolio_tick_hook is not None:
                try:
                    portfolio_result = self._portfolio_tick_hook(
                        workspace_id=workspace_id,
                        limit=max(limit, 100),
                    )
                    if asyncio.iscoroutine(portfolio_result):
                        portfolio_result = await portfolio_result
                    portfolio_maintenance = dict(portfolio_result or {})
                except Exception:
                    portfolio_maintenance = {}
            workspace_resource_maintenance: dict[str, Any] = {}
            if self._workspace_resource_tick_hook is not None:
                try:
                    workspace_resource_result = (
                        self._workspace_resource_tick_hook()
                    )
                    if asyncio.iscoroutine(workspace_resource_result):
                        workspace_resource_result = await workspace_resource_result
                    workspace_resource_maintenance = dict(
                        workspace_resource_result or {}
                    )
                except Exception:
                    workspace_resource_maintenance = {}
            schedule_maintenance: dict[str, Any] = {}
            if self._schedule_tick_hook is not None:
                try:
                    hook_result = self._schedule_tick_hook(
                        workspace_id=workspace_id,
                        limit=max(limit, 100),
                    )
                    if asyncio.iscoroutine(hook_result):
                        hook_result = await hook_result
                    schedule_maintenance = dict(hook_result or {})
                except Exception:
                    schedule_maintenance = {}
            due = self._due_missions(workspace_id=workspace_id, limit=limit)
            created: list[str] = []
            launched: list[str] = []
            skipped: list[dict[str, str]] = []

            for item in due:
                try:
                    cycle = await self.create_cycle(
                        item["mission_id"],
                        MissionCycleCreateRequest(
                            trigger=MissionCycleTrigger.SCHEDULED,
                            idempotency_key=item["idempotency_key"],
                            scheduled_for=item["scheduled_for"],
                            context={"scheduler": True},
                        ),
                    )
                    created.append(cycle["id"])
                    self._advance_schedule(item["mission_id"], item["interval"])
                    if item["autonomy_mode"] == AutonomyMode.AUTONOMOUS.value:
                        task = asyncio.create_task(
                            self.run_cycle(
                                cycle["id"],
                                MissionCycleRunRequest(
                                    auto_start=item["auto_start"],
                                    wait=False,
                                    context={"scheduler": True},
                                ),
                            ),
                            name=f"mission-cycle-{cycle['id']}",
                        )
                        self._cycle_tasks.add(task)
                        task.add_done_callback(self._cycle_tasks.discard)
                        launched.append(cycle["id"])
                except (AutonomousMissionError, IntegrityError) as exc:
                    skipped.append(
                        {"mission_id": item["mission_id"], "reason": str(exc)}
                    )

            return {
                "due": len(due),
                "portfolio_maintenance": portfolio_maintenance,
                "workspace_resource_maintenance": (
                    workspace_resource_maintenance
                ),
                "schedule_maintenance": schedule_maintenance,
                "created_cycle_ids": created,
                "launched_cycle_ids": launched,
                "skipped": skipped,
            }

    async def handle_execution_event(self, event: Event) -> None:
        if event.event_type not in {
            "execution_plan.runtime.completed",
            "execution_plan.runtime.failed",
            "execution_plan.runtime.cancelled",
        }:
            return
        plan_id = str(event.payload.get("plan_id") or event.correlation_id or "")
        if not plan_id:
            return

        with self._session_factory() as session:
            cycle = session.scalar(
                select(MissionCycleModel).where(
                    MissionCycleModel.execution_plan_id == plan_id
                )
            )
            if cycle is None:
                return
            mission = self._require_mission(session, cycle.mission_id)
            if event.event_type == "execution_plan.runtime.completed":
                cycle.status = MissionCycleStatus.COMPLETED.value
                cycle.error = None
                self._cycles_completed += 1
                event_type = "mission.cycle.completed"
            elif event.event_type == "execution_plan.runtime.cancelled":
                cycle.status = MissionCycleStatus.CANCELLED.value
                cycle.error = str(event.payload.get("reason") or "Plan cancelled.")
                event_type = "mission.cycle.cancelled"
            else:
                cycle.status = MissionCycleStatus.FAILED.value
                cycle.error = str(
                    event.payload.get("error")
                    or event.payload.get("message")
                    or "Execution Plan failed."
                )
                self._cycles_failed += 1
                event_type = "mission.cycle.failed"
            cycle.response_json = {
                **(cycle.response_json or {}),
                "terminal_event": event.to_dict(),
            }
            cycle.finished_at = utc_now()
            mission.current_cycle_id = None
            mission.last_activity_at = utc_now()
            if (
                mission.status == MissionStatus.ACTIVE.value
                and mission.next_cycle_at is None
            ):
                policy = self._policy_row(session, mission.workspace_id)
                interval = policy.cycle_interval_seconds if policy else 3600
                mission.next_cycle_at = utc_now() + timedelta(seconds=interval)
            payload = {
                "mission_id": mission.id,
                "cycle_id": cycle.id,
                "execution_plan_id": plan_id,
                "cycle_status": cycle.status,
                "error": cycle.error,
            }
            workspace_id = mission.workspace_id

        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="autonomous_mission_service",
                workspace_id=workspace_id,
                correlation_id=payload["mission_id"],
                causation_id=event.id,
                payload=payload,
            )
        )

    async def _scheduler_loop(self) -> None:
        while self._running:
            try:
                await self.tick_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # The next interval retries. Errors are visible through the
                # scheduler status and EventBus handlers without terminating
                # application lifespan.
                pass
            await asyncio.sleep(self._scheduler_interval_seconds)

    async def _finish_mission(
        self,
        mission_id: str,
        *,
        status: MissionStatus,
        actor_id: str,
        reason: str,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            if mission.status in self.TERMINAL_MISSION_STATUSES:
                if mission.status == status.value:
                    return self._load_mission_dict(session, mission.id)
                raise MissionStateError("Mission уже завершена.")
            now = utc_now()
            mission.status = status.value
            mission.next_cycle_at = None
            mission.current_cycle_id = None
            mission.last_activity_at = now
            mission.version += 1
            if status == MissionStatus.COMPLETED:
                mission.completed_at = now
                mission.progress_percent = max(
                    float(mission.progress_percent),
                    100.0 if self._all_goals_achieved(session, mission.id) else float(mission.progress_percent),
                )
            elif status == MissionStatus.CANCELLED:
                mission.cancelled_at = now
                mission.failure_reason = reason
            elif status == MissionStatus.FAILED:
                mission.failure_reason = reason
            for cycle in session.scalars(
                select(MissionCycleModel).where(
                    MissionCycleModel.mission_id == mission.id,
                    MissionCycleModel.status.in_(self.ACTIVE_CYCLE_STATUSES),
                )
            ).all():
                cycle.status = MissionCycleStatus.CANCELLED.value
                cycle.error = f"Mission {status.value}: {reason}"
                cycle.finished_at = now
            result = self._load_mission_dict(session, mission.id)

        await self._publish_mission_event(
            f"mission.{status.value}",
            result,
            {"actor_id": actor_id, "reason": reason},
        )
        return result

    def _cycle_workspace_id(self, cycle_id: str) -> str | None:
        with self._session_factory() as session:
            cycle = session.get(MissionCycleModel, cycle_id)
            if cycle is None:
                return None
            mission = session.get(WorkspaceMissionModel, cycle.mission_id)
            return None if mission is None else mission.workspace_id

    def _prepare_cycle_for_planning(
        self,
        cycle_id: str,
        request: MissionCycleRunRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            cycle = self._require_cycle(session, cycle_id)
            mission = self._require_mission(session, cycle.mission_id)
            if mission.status != MissionStatus.ACTIVE.value:
                raise MissionStateError("Mission должна быть active.")
            if cycle.status not in {
                MissionCycleStatus.QUEUED.value,
                MissionCycleStatus.FAILED.value,
            }:
                if request.force and cycle.status == MissionCycleStatus.READY.value:
                    pass
                else:
                    raise MissionStateError(
                        f"Cycle нельзя запустить из статуса {cycle.status}."
                    )
            if mission.cycle_count >= mission.max_cycles and cycle.status != MissionCycleStatus.FAILED.value:
                raise MissionStateError("Mission исчерпала max_cycles.")

            policy_row = self._policy_row(session, mission.workspace_id)
            policy = self._policy_to_dict(
                policy_row,
                workspace_id=mission.workspace_id,
            )
            goals = self._cycle_goal_rows(session, cycle)
            if not goals:
                raise MissionStateError("Для Cycle нет доступных Goals.")

            cycle.status = MissionCycleStatus.PLANNING.value
            cycle.started_at = cycle.started_at or utc_now()
            cycle.error = None
            mission.current_cycle_id = cycle.id
            mission.last_activity_at = utc_now()

            requested_auto_start = request.auto_start
            if requested_auto_start is None:
                requested_auto_start = (
                    mission.auto_start_plans
                    if mission.auto_start_plans is not None
                    else bool(policy["allow_auto_start"])
                )
            safe_auto_start = bool(
                requested_auto_start
                and policy["enabled"]
                and policy["autonomy_mode"] == AutonomyMode.AUTONOMOUS.value
                and policy["allow_auto_start"]
                and not policy["require_user_approval"]
            )

            objective = self._build_cycle_objective(mission, goals)
            strategy_hint = mission.strategy_hint.strip()
            planner_ref = mission.planner_ref or str(policy["planner_ref"])
            planning_mode = mission.planning_mode or str(policy["planning_mode"])
            request_json = {
                "auto_start_requested": requested_auto_start,
                "auto_start_effective": safe_auto_start,
                "wait": request.wait,
                "wait_timeout_seconds": request.wait_timeout_seconds,
                "force": request.force,
                "context": request.context,
                "objective": objective,
                "goal_ids": [goal.id for goal in goals],
            }
            cycle.request_json = request_json
            return {
                "mission_id": mission.id,
                "workspace_id": mission.workspace_id,
                "mission_title": mission.title,
                "cycle_number": cycle.cycle_number,
                "goal_ids": [goal.id for goal in goals],
                "objective": objective,
                "strategy_hint": strategy_hint,
                "planner_ref": planner_ref,
                "planning_mode": planning_mode,
                "auto_start": safe_auto_start,
                "auto_validate": bool(policy["auto_validate"]),
                "auto_assign": bool(policy["auto_assign"]),
                "strict_assignment": bool(policy["strict_assignment"]),
                "auto_review": bool(policy["auto_review"]),
                "review_threshold": int(policy["review_threshold"]),
                "auto_fix_review": bool(policy["auto_fix_review"]),
                "max_review_rounds": int(policy["max_review_rounds"]),
                "require_review_pass": bool(policy["require_review_pass"]),
            }

    def _finish_cycle_planning(
        self,
        cycle_id: str,
        planner_result: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        plan = dict(planner_result.get("plan") or {})
        planner_run = dict(planner_result.get("planner_run") or {})
        if not plan.get("id"):
            raise AutonomousMissionError(
                "Planner не вернул Execution Plan id."
            )
        with self._session_factory() as session:
            cycle = self._require_cycle(session, cycle_id)
            mission = self._require_mission(session, cycle.mission_id)
            cycle.execution_plan_id = str(plan["id"])
            cycle.planner_run_id = (
                str(planner_run.get("id")) if planner_run.get("id") else None
            )
            cycle.status = (
                MissionCycleStatus.RUNNING.value
                if snapshot["auto_start"]
                else MissionCycleStatus.READY.value
            )
            cycle.response_json = {
                "planner_run_id": cycle.planner_run_id,
                "execution_plan_id": cycle.execution_plan_id,
                "plan_status": plan.get("status"),
                "runtime": planner_result.get("runtime"),
            }
            cycle.error = None
            mission.current_cycle_id = cycle.id
            mission.cycle_count += 1
            mission.last_activity_at = utc_now()
            result = self._cycle_to_dict(cycle)
        return result

    def _finish_cycle_failure(self, cycle_id: str, error: str) -> dict[str, Any]:
        with self._session_factory() as session:
            cycle = self._require_cycle(session, cycle_id)
            mission = self._require_mission(session, cycle.mission_id)
            cycle.status = MissionCycleStatus.FAILED.value
            cycle.error = error
            cycle.finished_at = utc_now()
            mission.current_cycle_id = None
            mission.last_activity_at = utc_now()
            return self._cycle_to_dict(cycle)

    def _due_missions(
        self,
        *,
        workspace_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        now = utc_now()
        with self._session_factory() as session:
            statement = (
                select(WorkspaceMissionModel)
                .where(
                    WorkspaceMissionModel.status == MissionStatus.ACTIVE.value,
                    WorkspaceMissionModel.next_cycle_at.is_not(None),
                    WorkspaceMissionModel.next_cycle_at <= now,
                )
                .order_by(
                    WorkspaceMissionModel.priority.desc(),
                    WorkspaceMissionModel.next_cycle_at.asc(),
                )
                .limit(limit)
            )
            if workspace_id is not None:
                statement = statement.where(
                    WorkspaceMissionModel.workspace_id == workspace_id
                )
            rows = list(session.scalars(statement).all())
            result: list[dict[str, Any]] = []
            for mission in rows:
                policy = self._policy_row(session, mission.workspace_id)
                if policy is None or not policy.enabled:
                    continue
                if policy.autonomy_mode == AutonomyMode.OBSERVE.value:
                    continue
                if mission.cycle_count >= mission.max_cycles:
                    mission.status = MissionStatus.PAUSED.value
                    mission.pause_reason = "max_cycles reached"
                    mission.next_cycle_at = None
                    continue
                active_count = int(
                    session.scalar(
                        select(func.count())
                        .select_from(MissionCycleModel)
                        .where(
                            MissionCycleModel.mission_id == mission.id,
                            MissionCycleModel.status.in_(self.ACTIVE_CYCLE_STATUSES),
                        )
                    )
                    or 0
                )
                if active_count >= policy.max_parallel_cycles:
                    continue
                scheduled = ensure_utc(mission.next_cycle_at) or now
                key = f"scheduled:{mission.id}:{int(scheduled.timestamp())}"
                result.append(
                    {
                        "mission_id": mission.id,
                        "scheduled_for": scheduled,
                        "idempotency_key": key,
                        "interval": policy.cycle_interval_seconds,
                        "autonomy_mode": policy.autonomy_mode,
                        "auto_start": bool(
                            policy.allow_auto_start
                            and not policy.require_user_approval
                        ),
                    }
                )
            return result

    def _advance_schedule(self, mission_id: str, interval: int) -> None:
        resolved: datetime | None = None
        if self._schedule_next_resolver is not None:
            try:
                resolved = self._schedule_next_resolver(
                    mission_id,
                    fallback_interval_seconds=interval,
                    from_time=utc_now(),
                )
            except Exception:
                resolved = None
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            mission.next_cycle_at = ensure_utc(resolved) or (
                utc_now() + timedelta(seconds=interval)
            )
            mission.last_activity_at = utc_now()

    def _resolve_cycle_goals(
        self,
        session: Session,
        mission_id: str,
        goal_ids: list[str],
    ) -> list[MissionGoalModel]:
        all_goals = list(
            session.scalars(
                select(MissionGoalModel)
                .where(MissionGoalModel.mission_id == mission_id)
                .order_by(MissionGoalModel.sequence, MissionGoalModel.goal_key)
            ).all()
        )
        by_id = {goal.id: goal for goal in all_goals}
        by_key = {goal.goal_key: goal for goal in all_goals}
        selected: list[MissionGoalModel]
        if goal_ids:
            selected = []
            for goal_id in goal_ids:
                goal = by_id.get(goal_id)
                if goal is None:
                    raise MissionGoalNotFound(
                        f"Goal не найдена в Mission: {goal_id}."
                    )
                selected.append(goal)
        else:
            selected = [
                goal
                for goal in all_goals
                if goal.status in {
                    MissionGoalStatus.PENDING.value,
                    MissionGoalStatus.ACTIVE.value,
                    MissionGoalStatus.FAILED.value,
                }
                and all(
                    by_key.get(dep) is not None
                    and by_key[dep].status == MissionGoalStatus.ACHIEVED.value
                    for dep in (goal.depends_on_json or [])
                )
            ]
        if not selected:
            raise MissionStateError("Нет доступных Goal для нового Cycle.")
        return selected

    def _cycle_goal_rows(
        self,
        session: Session,
        cycle: MissionCycleModel,
    ) -> list[MissionGoalModel]:
        ids = list(cycle.goal_ids_json or [])
        if not ids:
            return self._resolve_cycle_goals(session, cycle.mission_id, [])
        rows = list(
            session.scalars(
                select(MissionGoalModel)
                .where(
                    MissionGoalModel.mission_id == cycle.mission_id,
                    MissionGoalModel.id.in_(ids),
                )
                .order_by(MissionGoalModel.sequence, MissionGoalModel.goal_key)
            ).all()
        )
        return rows

    @staticmethod
    def _build_cycle_objective(
        mission: WorkspaceMissionModel,
        goals: list[MissionGoalModel],
    ) -> str:
        parts = [
            f"Long-running Mission: {mission.title}",
            f"Mission objective: {mission.objective}",
        ]
        if mission.success_criteria.strip():
            parts.append(f"Mission success criteria: {mission.success_criteria}")
        parts.append("Goals for this planning cycle:")
        for index, goal in enumerate(goals, start=1):
            line = f"{index}. [{goal.goal_key}] {goal.title}"
            if goal.description.strip():
                line += f" — {goal.description}"
            if goal.success_criteria.strip():
                line += f"; success criteria: {goal.success_criteria}"
            parts.append(line)
        parts.append(
            "Return verifiable outputs and evidence that can be used to update "
            "Mission progress. Do not mark a Goal achieved without evidence."
        )
        return "\n".join(parts)

    def _validate_goal_graph(
        self,
        session: Session,
        mission_id: str,
    ) -> dict[str, Any]:
        goals = list(
            session.scalars(
                select(MissionGoalModel)
                .where(MissionGoalModel.mission_id == mission_id)
                .order_by(MissionGoalModel.sequence, MissionGoalModel.goal_key)
            ).all()
        )
        by_key = {goal.goal_key: goal for goal in goals}
        errors: list[str] = []
        adjacency: dict[str, list[str]] = {key: [] for key in by_key}
        indegree: dict[str, int] = {key: 0 for key in by_key}
        for goal in goals:
            seen: set[str] = set()
            for dep in goal.depends_on_json or []:
                if dep in seen:
                    continue
                seen.add(dep)
                if dep == goal.goal_key:
                    errors.append(f"Goal {goal.goal_key} зависит от себя.")
                    continue
                if dep not in by_key:
                    errors.append(
                        f"Goal {goal.goal_key} зависит от неизвестной Goal {dep}."
                    )
                    continue
                adjacency[dep].append(goal.goal_key)
                indegree[goal.goal_key] += 1

        queue = sorted(key for key, degree in indegree.items() if degree == 0)
        order: list[str] = []
        while queue:
            key = queue.pop(0)
            order.append(key)
            for child in sorted(adjacency[key]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
                    queue.sort()
        if len(order) != len(goals):
            errors.append("Граф Goals содержит цикл.")

        ready = [
            goal.goal_key
            for goal in goals
            if goal.status in {
                MissionGoalStatus.PENDING.value,
                MissionGoalStatus.ACTIVE.value,
                MissionGoalStatus.FAILED.value,
            }
            and all(
                by_key.get(dep) is not None
                and by_key[dep].status == MissionGoalStatus.ACHIEVED.value
                for dep in (goal.depends_on_json or [])
            )
        ]
        return {
            "valid": not errors,
            "errors": errors,
            "goal_count": len(goals),
            "topological_order": order,
            "ready_goal_keys": ready,
        }

    def _activate_unblocked_goals(self, session: Session, mission_id: str) -> None:
        goals = list(
            session.scalars(
                select(MissionGoalModel).where(
                    MissionGoalModel.mission_id == mission_id
                )
            ).all()
        )
        by_key = {goal.goal_key: goal for goal in goals}
        now = utc_now()
        for goal in goals:
            if goal.status != MissionGoalStatus.PENDING.value:
                continue
            if all(
                by_key.get(dep) is not None
                and by_key[dep].status == MissionGoalStatus.ACHIEVED.value
                for dep in (goal.depends_on_json or [])
            ):
                goal.status = MissionGoalStatus.ACTIVE.value
                goal.started_at = goal.started_at or now

    def _recalculate_mission_progress(
        self,
        session: Session,
        mission: WorkspaceMissionModel,
    ) -> float:
        goals = list(
            session.scalars(
                select(MissionGoalModel).where(
                    MissionGoalModel.mission_id == mission.id,
                    MissionGoalModel.status != MissionGoalStatus.CANCELLED.value,
                )
            ).all()
        )
        if not goals:
            return float(mission.progress_percent)
        total_weight = sum(float(goal.weight) for goal in goals)
        if total_weight <= 0:
            return float(mission.progress_percent)
        value = sum(
            float(goal.weight) * float(goal.progress_percent)
            for goal in goals
        ) / total_weight
        mission.progress_percent = round(max(0.0, min(100.0, value)), 4)
        return float(mission.progress_percent)

    def _all_goals_achieved(self, session: Session, mission_id: str) -> bool:
        goals = list(
            session.scalars(
                select(MissionGoalModel).where(
                    MissionGoalModel.mission_id == mission_id,
                    MissionGoalModel.status != MissionGoalStatus.CANCELLED.value,
                )
            ).all()
        )
        return bool(goals) and all(
            goal.status == MissionGoalStatus.ACHIEVED.value for goal in goals
        )

    @staticmethod
    def _progress_value(
        previous: float,
        request: MissionProgressRequest,
    ) -> float:
        if request.progress_percent is not None:
            value = request.progress_percent
        elif request.progress_delta is not None:
            value = previous + request.progress_delta
        else:
            value = previous
        return round(max(0.0, min(100.0, float(value))), 4)

    def _add_goal_row(
        self,
        session: Session,
        mission_id: str,
        request: MissionGoalCreate,
    ) -> MissionGoalModel:
        goal = MissionGoalModel(
            mission_id=mission_id,
            goal_key=request.goal_key,
            title=request.title,
            description=request.description,
            success_criteria=request.success_criteria,
            sequence=request.sequence,
            weight=request.weight,
            depends_on_json=request.depends_on,
            metadata_json=request.metadata,
        )
        session.add(goal)
        session.flush()
        return goal

    @staticmethod
    def _clean_dependencies(
        values: list[str],
        *,
        own_key: str,
    ) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            if not item or item in seen:
                continue
            if item == own_key:
                raise AutonomousMissionError(
                    "Goal не может зависеть от самой себя."
                )
            seen.add(item)
            result.append(item)
        return result

    def _require_mission(
        self,
        session: Session,
        mission_id: str,
    ) -> WorkspaceMissionModel:
        mission = session.get(WorkspaceMissionModel, mission_id)
        if mission is None:
            raise MissionNotFound("Mission не найдена.")
        return mission

    def _require_mutable_mission(
        self,
        session: Session,
        mission_id: str,
    ) -> WorkspaceMissionModel:
        mission = self._require_mission(session, mission_id)
        if mission.status in self.TERMINAL_MISSION_STATUSES:
            raise MissionStateError("Завершённую Mission нельзя изменять.")
        return mission

    def _require_goal(
        self,
        session: Session,
        mission_id: str,
        goal_id: str,
    ) -> MissionGoalModel:
        goal = session.scalar(
            select(MissionGoalModel).where(
                MissionGoalModel.id == goal_id,
                MissionGoalModel.mission_id == mission_id,
            )
        )
        if goal is None:
            raise MissionGoalNotFound("Mission Goal не найдена.")
        return goal

    def _require_cycle(
        self,
        session: Session,
        cycle_id: str,
    ) -> MissionCycleModel:
        cycle = session.get(MissionCycleModel, cycle_id)
        if cycle is None:
            raise MissionCycleNotFound("Mission Cycle не найден.")
        return cycle

    def _policy_row(
        self,
        session: Session,
        workspace_id: str,
    ) -> AutonomousWorkspacePolicyModel | None:
        return session.scalar(
            select(AutonomousWorkspacePolicyModel).where(
                AutonomousWorkspacePolicyModel.workspace_id == workspace_id
            )
        )

    @staticmethod
    def _policy_to_dict(
        row: AutonomousWorkspacePolicyModel | None,
        *,
        workspace_id: str,
    ) -> dict[str, Any]:
        if row is None:
            return {
                "id": None,
                "workspace_id": workspace_id,
                "enabled": False,
                "autonomy_mode": AutonomyMode.SUPERVISED.value,
                "max_active_missions": 3,
                "max_parallel_cycles": 1,
                "cycle_interval_seconds": 3600,
                "require_user_approval": True,
                "allow_auto_start": False,
                "planner_ref": "builtin.planner",
                "planning_mode": "balanced",
                "auto_validate": True,
                "auto_assign": True,
                "strict_assignment": False,
                "auto_review": True,
                "review_threshold": 80,
                "auto_fix_review": True,
                "max_review_rounds": 2,
                "require_review_pass": True,
                "metadata": {},
                "created_at": None,
                "updated_at": None,
                "persisted": False,
            }
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "enabled": row.enabled,
            "autonomy_mode": row.autonomy_mode,
            "max_active_missions": row.max_active_missions,
            "max_parallel_cycles": row.max_parallel_cycles,
            "cycle_interval_seconds": row.cycle_interval_seconds,
            "require_user_approval": row.require_user_approval,
            "allow_auto_start": row.allow_auto_start,
            "planner_ref": row.planner_ref,
            "planning_mode": row.planning_mode,
            "auto_validate": row.auto_validate,
            "auto_assign": row.auto_assign,
            "strict_assignment": row.strict_assignment,
            "auto_review": row.auto_review,
            "review_threshold": row.review_threshold,
            "auto_fix_review": row.auto_fix_review,
            "max_review_rounds": row.max_review_rounds,
            "require_review_pass": row.require_review_pass,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
            "persisted": True,
        }

    def _load_mission_dict(
        self,
        session: Session,
        mission_id: str,
    ) -> dict[str, Any] | None:
        mission = session.scalar(
            select(WorkspaceMissionModel)
            .options(
                selectinload(WorkspaceMissionModel.goals),
                selectinload(WorkspaceMissionModel.cycles),
            )
            .where(WorkspaceMissionModel.id == mission_id)
            .execution_options(populate_existing=True)
        )
        if mission is None:
            return None
        result = self._mission_summary(mission)
        result["goals"] = [self._goal_to_dict(goal) for goal in mission.goals]
        result["cycles"] = [self._cycle_to_dict(cycle) for cycle in mission.cycles]
        result["validation"] = self._validate_goal_graph(session, mission.id)
        return result

    @staticmethod
    def _mission_summary(row: WorkspaceMissionModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "title": row.title,
            "objective": row.objective,
            "success_criteria": row.success_criteria,
            "strategy_hint": row.strategy_hint,
            "status": row.status,
            "priority": row.priority,
            "planner_ref": row.planner_ref,
            "planning_mode": row.planning_mode,
            "auto_start_plans": row.auto_start_plans,
            "max_cycles": row.max_cycles,
            "cycle_count": row.cycle_count,
            "progress_percent": float(row.progress_percent),
            "current_cycle_id": row.current_cycle_id,
            "next_cycle_at": iso(row.next_cycle_at),
            "deadline_at": iso(row.deadline_at),
            "last_activity_at": iso(row.last_activity_at),
            "pause_reason": row.pause_reason,
            "failure_reason": row.failure_reason,
            "version": row.version,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
            "started_at": iso(row.started_at),
            "completed_at": iso(row.completed_at),
            "cancelled_at": iso(row.cancelled_at),
        }

    @staticmethod
    def _goal_to_dict(row: MissionGoalModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "mission_id": row.mission_id,
            "goal_key": row.goal_key,
            "title": row.title,
            "description": row.description,
            "success_criteria": row.success_criteria,
            "sequence": row.sequence,
            "weight": float(row.weight),
            "status": row.status,
            "progress_percent": float(row.progress_percent),
            "depends_on": list(row.depends_on_json or []),
            "result": dict(row.result_json or {}),
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
            "started_at": iso(row.started_at),
            "completed_at": iso(row.completed_at),
        }

    @staticmethod
    def _cycle_to_dict(row: MissionCycleModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "mission_id": row.mission_id,
            "cycle_number": row.cycle_number,
            "status": row.status,
            "trigger": row.trigger,
            "goal_ids": list(row.goal_ids_json or []),
            "idempotency_key": row.idempotency_key,
            "planner_run_id": row.planner_run_id,
            "execution_plan_id": row.execution_plan_id,
            "request": dict(row.request_json or {}),
            "response": dict(row.response_json or {}),
            "error": row.error,
            "scheduled_for": iso(row.scheduled_for),
            "started_at": iso(row.started_at),
            "finished_at": iso(row.finished_at),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _progress_to_dict(row: MissionProgressUpdateModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "mission_id": row.mission_id,
            "goal_id": row.goal_id,
            "cycle_id": row.cycle_id,
            "actor_id": row.actor_id,
            "previous_progress_percent": float(row.previous_progress_percent),
            "new_progress_percent": float(row.new_progress_percent),
            "message": row.message,
            "evidence": dict(row.evidence_json or {}),
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
        }

    async def _publish_mission_event(
        self,
        event_type: str,
        mission: dict[str, Any],
        extra: dict[str, Any],
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="autonomous_mission_service",
                workspace_id=mission["workspace_id"],
                correlation_id=mission["id"],
                payload={
                    "mission_id": mission["id"],
                    "workspace_id": mission["workspace_id"],
                    "status": mission["status"],
                    "progress_percent": mission["progress_percent"],
                    **extra,
                },
            )
        )
