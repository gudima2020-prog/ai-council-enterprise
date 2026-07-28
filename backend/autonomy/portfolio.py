from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.autonomy.models import (
    MissionCycleModel,
    MissionDependencyModel,
    MissionPortfolioAssignmentModel,
    MissionPortfolioEvaluationModel,
    MissionPortfolioPolicyModel,
    MissionRiskModel,
    MissionStrategyModel,
    WorkspaceMissionModel,
)
from backend.autonomy.portfolio_schemas import (
    MissionDependencyCreate,
    MissionDependencyStatus,
    MissionDependencyType,
    MissionDependencyUpdate,
    MissionDependencyWaiveRequest,
    MissionPortfolioAssignmentOverrideRequest,
    MissionPortfolioAssignmentStatus,
    MissionPortfolioMode,
    MissionPortfolioPolicyUpsert,
    MissionPortfolioRankRequest,
    MissionPortfolioRebalanceRequest,
)
from backend.autonomy.service import (
    AutonomousMissionError,
    MissionCycleNotFound,
    MissionNotFound,
    MissionStateError,
    ensure_utc,
    iso,
)
from backend.core.events import Event, EventBus
from backend.database.session import session_scope


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


class MissionDependencyNotFound(AutonomousMissionError):
    pass


class MissionPortfolioService:
    """Coordinates Mission dependencies and Workspace portfolio capacity.

    Dependency admission is always evaluated. Portfolio slot enforcement is
    opt-in and remains disabled until a Workspace policy explicitly enables it.
    Automatic rebalance additionally requires adaptive/weighted mode, disabled
    human approval, and ``auto_rebalance_enabled``.
    """

    TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
    ACTIVE_STATUSES = {"active"}

    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._lock = asyncio.Lock()
        self._rank_runs = 0
        self._rebalances = 0
        self._last_tick_at: datetime | None = None

    def status(self) -> dict[str, Any]:
        with self._session_factory() as session:
            dependencies = int(
                session.scalar(select(func.count()).select_from(MissionDependencyModel))
                or 0
            )
            policies = int(
                session.scalar(select(func.count()).select_from(MissionPortfolioPolicyModel))
                or 0
            )
            assignments = list(session.scalars(select(MissionPortfolioAssignmentModel)).all())
        return {
            "dependencies": dependencies,
            "policies": policies,
            "assignments": len(assignments),
            "selected_assignments": sum(
                1 for row in assignments if row.status == MissionPortfolioAssignmentStatus.SELECTED.value
            ),
            "rank_runs": self._rank_runs,
            "rebalances": self._rebalances,
            "last_tick_at": iso(self._last_tick_at),
            "capabilities": [
                "cross_mission_dependency_graph",
                "hard_and_soft_dependency_admission",
                "workspace_portfolio_policy",
                "weighted_and_adaptive_prioritization",
                "portfolio_slot_assignment",
                "planner_portfolio_context",
                "audit_and_event_transport",
            ],
        }

    def get_policy(self, workspace_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.scalar(
                select(MissionPortfolioPolicyModel).where(
                    MissionPortfolioPolicyModel.workspace_id == workspace_id
                )
            )
            return self._policy_to_dict(row, workspace_id=workspace_id)

    async def upsert_policy(
        self,
        workspace_id: str,
        request: MissionPortfolioPolicyUpsert,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            self._require_workspace_mission_presence(session, workspace_id)
            row = session.scalar(
                select(MissionPortfolioPolicyModel).where(
                    MissionPortfolioPolicyModel.workspace_id == workspace_id
                )
            )
            created = row is None
            if row is None:
                row = MissionPortfolioPolicyModel(workspace_id=workspace_id)
                session.add(row)
            values = request.model_dump(mode="json")
            values["prioritization_mode"] = request.prioritization_mode.value
            values["metadata_json"] = values.pop("metadata")
            for key, value in values.items():
                setattr(row, key, value)
            session.flush()
            result = self._policy_to_dict(row, workspace_id=workspace_id)

        await self._publish(
            "mission.portfolio.policy.created" if created else "mission.portfolio.policy.updated",
            workspace_id=workspace_id,
            correlation_id=row.id,
            payload={"policy": result},
        )
        return result

    async def create_dependency(
        self,
        mission_id: str,
        request: MissionDependencyCreate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            upstream = self._require_mission(session, request.depends_on_mission_id)
            if mission.id == upstream.id:
                raise AutonomousMissionError("Mission cannot depend on itself.")
            if mission.workspace_id != upstream.workspace_id:
                raise AutonomousMissionError(
                    "Cross-Workspace Mission dependencies are not allowed."
                )
            existing = session.scalar(
                select(MissionDependencyModel).where(
                    MissionDependencyModel.mission_id == mission.id,
                    MissionDependencyModel.depends_on_mission_id == upstream.id,
                )
            )
            if existing is not None:
                raise AutonomousMissionError("Mission dependency already exists.")
            row = MissionDependencyModel(
                workspace_id=mission.workspace_id,
                mission_id=mission.id,
                depends_on_mission_id=upstream.id,
                dependency_type=request.dependency_type.value,
                required_statuses_json=list(request.required_statuses),
                allow_failed=request.allow_failed,
                priority=request.priority,
                rationale=request.rationale,
                metadata_json=dict(request.metadata),
            )
            self._refresh_dependency_row(row, upstream)
            session.add(row)
            session.flush()
            graph = self._validate_graph_session(session, mission.workspace_id)
            if not graph["valid"]:
                raise AutonomousMissionError(
                    "Mission dependency graph contains a cycle: "
                    + " -> ".join(graph.get("cycle") or [])
                )
            result = self._dependency_to_dict(row, upstream=upstream)

        await self._publish(
            "mission.dependency.created",
            workspace_id=result["workspace_id"],
            correlation_id=mission_id,
            payload={"dependency": result},
        )
        return result

    async def update_dependency(
        self,
        dependency_id: str,
        request: MissionDependencyUpdate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_dependency(session, dependency_id)
            values = request.model_dump(exclude_unset=True, mode="json")
            if "dependency_type" in values:
                values["dependency_type"] = request.dependency_type.value
            if "required_statuses" in values:
                values["required_statuses_json"] = values.pop("required_statuses")
            if "metadata" in values:
                values["metadata_json"] = values.pop("metadata")
            for key, value in values.items():
                setattr(row, key, value)
            upstream = self._require_mission(session, row.depends_on_mission_id)
            if row.status != MissionDependencyStatus.WAIVED.value:
                self._refresh_dependency_row(row, upstream)
            session.flush()
            graph = self._validate_graph_session(session, row.workspace_id)
            if not graph["valid"]:
                raise AutonomousMissionError(
                    "Mission dependency graph contains a cycle: "
                    + " -> ".join(graph.get("cycle") or [])
                )
            result = self._dependency_to_dict(row, upstream=upstream)

        await self._publish(
            "mission.dependency.updated",
            workspace_id=result["workspace_id"],
            correlation_id=result["mission_id"],
            payload={"dependency": result},
        )
        return result

    async def waive_dependency(
        self,
        dependency_id: str,
        request: MissionDependencyWaiveRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_dependency(session, dependency_id)
            row.status = MissionDependencyStatus.WAIVED.value
            row.waived_at = utc_now()
            row.waived_by = request.actor_id
            row.waiver_reason = request.reason
            row.satisfied_at = None
            result = self._dependency_to_dict(
                row,
                upstream=self._require_mission(session, row.depends_on_mission_id),
            )
        await self._publish(
            "mission.dependency.waived",
            workspace_id=result["workspace_id"],
            correlation_id=result["mission_id"],
            payload={"dependency": result, "actor_id": request.actor_id},
        )
        return result

    async def delete_dependency(self, dependency_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_dependency(session, dependency_id)
            payload = self._dependency_to_dict(
                row,
                upstream=self._require_mission(session, row.depends_on_mission_id),
            )
            session.delete(row)
        await self._publish(
            "mission.dependency.deleted",
            workspace_id=payload["workspace_id"],
            correlation_id=payload["mission_id"],
            payload={"dependency_id": dependency_id},
        )
        return {"deleted": True, "dependency_id": dependency_id}

    def list_dependencies(
        self,
        mission_id: str,
        *,
        incoming: bool = False,
        include_waived: bool = True,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_mission(session, mission_id)
            if incoming:
                statement = select(MissionDependencyModel).where(
                    MissionDependencyModel.depends_on_mission_id == mission_id
                )
            else:
                statement = select(MissionDependencyModel).where(
                    MissionDependencyModel.mission_id == mission_id
                )
            if not include_waived:
                statement = statement.where(
                    MissionDependencyModel.status != MissionDependencyStatus.WAIVED.value
                )
            rows = list(
                session.scalars(
                    statement.order_by(
                        MissionDependencyModel.priority.desc(),
                        MissionDependencyModel.created_at.asc(),
                    )
                ).all()
            )
            upstream_ids = {row.depends_on_mission_id for row in rows}
            upstream_map = {
                row.id: row
                for row in session.scalars(
                    select(WorkspaceMissionModel).where(
                        WorkspaceMissionModel.id.in_(upstream_ids)
                    )
                ).all()
            } if upstream_ids else {}
            return [
                self._dependency_to_dict(row, upstream=upstream_map.get(row.depends_on_mission_id))
                for row in rows
            ]

    def validate_graph(self, workspace_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            return self._validate_graph_session(session, workspace_id)

    async def refresh_dependencies(
        self,
        *,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        changes: list[dict[str, Any]] = []
        with self._session_factory() as session:
            statement = select(MissionDependencyModel)
            if workspace_id is not None:
                statement = statement.where(
                    MissionDependencyModel.workspace_id == workspace_id
                )
            rows = list(session.scalars(statement).all())
            upstream_ids = {row.depends_on_mission_id for row in rows}
            upstream_map = {
                row.id: row
                for row in session.scalars(
                    select(WorkspaceMissionModel).where(
                        WorkspaceMissionModel.id.in_(upstream_ids)
                    )
                ).all()
            } if upstream_ids else {}
            for row in rows:
                if row.status == MissionDependencyStatus.WAIVED.value:
                    continue
                previous = row.status
                upstream = upstream_map.get(row.depends_on_mission_id)
                if upstream is None:
                    row.status = MissionDependencyStatus.FAILED.value
                else:
                    self._refresh_dependency_row(row, upstream)
                if previous != row.status:
                    changes.append(
                        {
                            "id": row.id,
                            "workspace_id": row.workspace_id,
                            "mission_id": row.mission_id,
                            "previous_status": previous,
                            "new_status": row.status,
                        }
                    )
        for change in changes:
            await self._publish(
                "mission.dependency.status_changed",
                workspace_id=change["workspace_id"],
                correlation_id=change["mission_id"],
                payload=change,
            )
        return {"updated": len(changes), "changes": changes}

    def evaluate_mission_admission(
        self,
        mission_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            return self._mission_admission_session(session, mission, force=force)

    def evaluate_cycle_admission(
        self,
        cycle_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            cycle = session.get(MissionCycleModel, cycle_id)
            if cycle is None:
                raise MissionCycleNotFound(f"Mission Cycle not found: {cycle_id}")
            mission = self._require_mission(session, cycle.mission_id)
            result = self._mission_admission_session(session, mission, force=force)
            result["cycle_id"] = cycle_id
            return result

    async def rank_workspace(
        self,
        workspace_id: str,
        request: MissionPortfolioRankRequest,
    ) -> dict[str, Any]:
        async with self._lock:
            await self.refresh_dependencies(workspace_id=workspace_id)
            with self._session_factory() as session:
                policy = session.scalar(
                    select(MissionPortfolioPolicyModel).where(
                        MissionPortfolioPolicyModel.workspace_id == workspace_id
                    )
                )
                statuses = ["active"]
                if request.include_paused:
                    statuses.append("paused")
                missions = list(
                    session.scalars(
                        select(WorkspaceMissionModel)
                        .where(
                            WorkspaceMissionModel.workspace_id == workspace_id,
                            WorkspaceMissionModel.status.in_(statuses),
                        )
                        .order_by(
                            WorkspaceMissionModel.priority.desc(),
                            WorkspaceMissionModel.created_at.asc(),
                        )
                    ).all()
                )
                effective = self._policy_to_dict(policy, workspace_id=workspace_id)
                ranking = [
                    self._score_mission(session, mission, effective)
                    for mission in missions
                ]
                ranking.sort(
                    key=lambda item: (
                        bool(item["blocked"]),
                        -float(item["score"]),
                        -int(item["priority"]),
                        item["mission_id"],
                    )
                )
                for index, item in enumerate(ranking, start=1):
                    item["rank"] = index
                    if item["blocked"]:
                        item["decision"] = "block"
                    elif index <= int(effective["max_parallel_missions"]) and item["score"] >= float(effective["min_selection_score"]):
                        item["decision"] = "select"
                    else:
                        item["decision"] = "defer"
                    if request.persist:
                        session.add(
                            MissionPortfolioEvaluationModel(
                                workspace_id=workspace_id,
                                mission_id=item["mission_id"],
                                policy_id=None if policy is None else policy.id,
                                actor_id=request.actor_id,
                                automatic=request.automatic,
                                score=item["score"],
                                rank=item["rank"],
                                decision=item["decision"],
                                components_json=dict(item["components"]),
                                reason=item["reason"],
                                context_json=dict(request.context),
                            )
                        )
                self._rank_runs += 1
                result = {
                    "workspace_id": workspace_id,
                    "policy": effective,
                    "ranking": ranking,
                    "persisted": request.persist,
                    "evaluated_at": iso(utc_now()),
                }

        await self._publish(
            "mission.portfolio.ranked",
            workspace_id=workspace_id,
            correlation_id=workspace_id,
            payload={
                "workspace_id": workspace_id,
                "count": len(result["ranking"]),
                "automatic": request.automatic,
                "persisted": request.persist,
            },
        )
        return result

    async def rebalance(
        self,
        workspace_id: str,
        request: MissionPortfolioRebalanceRequest,
    ) -> dict[str, Any]:
        policy = self.get_policy(workspace_id)
        if request.automatic:
            if not policy["enabled"]:
                raise MissionStateError("Mission Portfolio Policy is disabled.")
            if policy["require_human_approval"] and not request.force:
                raise MissionStateError("Automatic portfolio rebalance requires human approval.")
            if not policy["auto_rebalance_enabled"] and not request.force:
                raise MissionStateError("Automatic portfolio rebalance is disabled.")
            if policy["prioritization_mode"] == MissionPortfolioMode.MANUAL.value and not request.force:
                raise MissionStateError("Manual portfolio mode cannot rebalance automatically.")

        ranked = await self.rank_workspace(
            workspace_id,
            MissionPortfolioRankRequest(
                actor_id=request.actor_id,
                automatic=request.automatic,
                persist=True,
                include_paused=request.include_paused,
                context=request.context,
            ),
        )
        applied: list[dict[str, Any]] = []
        if request.apply:
            with self._session_factory() as session:
                policy_row = session.scalar(
                    select(MissionPortfolioPolicyModel).where(
                        MissionPortfolioPolicyModel.workspace_id == workspace_id
                    )
                )
                for item in ranked["ranking"]:
                    status = {
                        "select": MissionPortfolioAssignmentStatus.SELECTED.value,
                        "block": MissionPortfolioAssignmentStatus.BLOCKED.value,
                    }.get(item["decision"], MissionPortfolioAssignmentStatus.DEFERRED.value)
                    row = session.scalar(
                        select(MissionPortfolioAssignmentModel).where(
                            MissionPortfolioAssignmentModel.mission_id == item["mission_id"]
                        )
                    )
                    if row is not None and row.manual_override and not request.force:
                        applied.append(self._assignment_to_dict(row))
                        continue
                    if row is None:
                        row = MissionPortfolioAssignmentModel(
                            workspace_id=workspace_id,
                            mission_id=item["mission_id"],
                        )
                        session.add(row)
                    row.policy_id = None if policy_row is None else policy_row.id
                    row.status = status
                    row.score = item["score"]
                    row.rank = item["rank"]
                    row.automatic = request.automatic
                    row.manual_override = False
                    row.actor_id = request.actor_id
                    row.reason = request.rationale or item["reason"]
                    row.metadata_json = {
                        "components": item["components"],
                        "decision": item["decision"],
                        **dict(request.context),
                    }
                    row.assigned_at = utc_now()
                    row.released_at = (
                        utc_now()
                        if status == MissionPortfolioAssignmentStatus.RELEASED.value
                        else None
                    )
                    session.flush()
                    applied.append(self._assignment_to_dict(row))
                if policy_row is not None:
                    policy_row.last_rebalanced_at = utc_now()
            self._rebalances += 1

        result = {
            **ranked,
            "applied": request.apply,
            "assignments": applied,
            "rationale": request.rationale,
        }
        await self._publish(
            "mission.portfolio.rebalanced",
            workspace_id=workspace_id,
            correlation_id=workspace_id,
            payload={
                "workspace_id": workspace_id,
                "automatic": request.automatic,
                "applied": request.apply,
                "assignment_count": len(applied),
            },
        )
        return result

    async def override_assignment(
        self,
        mission_id: str,
        request: MissionPortfolioAssignmentOverrideRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            policy = session.scalar(
                select(MissionPortfolioPolicyModel).where(
                    MissionPortfolioPolicyModel.workspace_id == mission.workspace_id
                )
            )
            if request.status == MissionPortfolioAssignmentStatus.SELECTED and not request.force:
                selected_count = int(
                    session.scalar(
                        select(func.count())
                        .select_from(MissionPortfolioAssignmentModel)
                        .where(
                            MissionPortfolioAssignmentModel.workspace_id == mission.workspace_id,
                            MissionPortfolioAssignmentModel.status == MissionPortfolioAssignmentStatus.SELECTED.value,
                            MissionPortfolioAssignmentModel.mission_id != mission.id,
                        )
                    )
                    or 0
                )
                limit = policy.max_parallel_missions if policy is not None else 3
                if selected_count >= limit:
                    raise MissionStateError(
                        f"Portfolio selected Mission limit reached: {limit}."
                    )
            row = session.scalar(
                select(MissionPortfolioAssignmentModel).where(
                    MissionPortfolioAssignmentModel.mission_id == mission.id
                )
            )
            if row is None:
                row = MissionPortfolioAssignmentModel(
                    workspace_id=mission.workspace_id,
                    mission_id=mission.id,
                )
                session.add(row)
            row.policy_id = None if policy is None else policy.id
            row.status = request.status.value
            row.automatic = False
            row.manual_override = True
            row.actor_id = request.actor_id
            row.reason = request.reason
            row.metadata_json = dict(request.metadata)
            row.assigned_at = utc_now()
            row.released_at = (
                utc_now()
                if request.status == MissionPortfolioAssignmentStatus.RELEASED
                else None
            )
            session.flush()
            result = self._assignment_to_dict(row)

        await self._publish(
            "mission.portfolio.assignment.overridden",
            workspace_id=result["workspace_id"],
            correlation_id=mission_id,
            payload={"assignment": result},
        )
        return result

    def list_assignments(
        self,
        workspace_id: str,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(MissionPortfolioAssignmentModel).where(
                MissionPortfolioAssignmentModel.workspace_id == workspace_id
            )
            if status is not None:
                statement = statement.where(MissionPortfolioAssignmentModel.status == status)
            rows = list(
                session.scalars(
                    statement.order_by(
                        MissionPortfolioAssignmentModel.rank.asc(),
                        MissionPortfolioAssignmentModel.score.desc(),
                    )
                ).all()
            )
            return [self._assignment_to_dict(row) for row in rows]

    def list_evaluations(
        self,
        workspace_id: str,
        *,
        mission_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(MissionPortfolioEvaluationModel).where(
                MissionPortfolioEvaluationModel.workspace_id == workspace_id
            )
            if mission_id is not None:
                statement = statement.where(
                    MissionPortfolioEvaluationModel.mission_id == mission_id
                )
            rows = list(
                session.scalars(
                    statement.order_by(MissionPortfolioEvaluationModel.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._evaluation_to_dict(row) for row in rows]

    def dashboard(self, workspace_id: str) -> dict[str, Any]:
        policy = self.get_policy(workspace_id)
        assignments = self.list_assignments(workspace_id)
        with self._session_factory() as session:
            missions = list(
                session.scalars(
                    select(WorkspaceMissionModel).where(
                        WorkspaceMissionModel.workspace_id == workspace_id
                    )
                ).all()
            )
            graph = self._validate_graph_session(session, workspace_id)
        return {
            "workspace_id": workspace_id,
            "policy": policy,
            "missions": {
                "total": len(missions),
                "active": sum(1 for row in missions if row.status == "active"),
                "paused": sum(1 for row in missions if row.status == "paused"),
                "terminal": sum(1 for row in missions if row.status in self.TERMINAL_STATUSES),
            },
            "assignments": assignments,
            "dependency_graph": graph,
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
            policy = session.scalar(
                select(MissionPortfolioPolicyModel).where(
                    MissionPortfolioPolicyModel.workspace_id == mission.workspace_id
                )
            )
            assignment = session.scalar(
                select(MissionPortfolioAssignmentModel).where(
                    MissionPortfolioAssignmentModel.mission_id == mission.id
                )
            )
            admission = self._mission_admission_session(session, mission, force=False)
            dependencies = self.list_dependencies(mission_id)[:limit]
            siblings = list(
                session.scalars(
                    select(MissionPortfolioAssignmentModel)
                    .where(
                        MissionPortfolioAssignmentModel.workspace_id == mission.workspace_id,
                        MissionPortfolioAssignmentModel.mission_id != mission.id,
                    )
                    .order_by(
                        MissionPortfolioAssignmentModel.rank.asc(),
                        MissionPortfolioAssignmentModel.score.desc(),
                    )
                    .limit(limit)
                ).all()
            )
            return {
                "mission_id": mission.id,
                "workspace_id": mission.workspace_id,
                "cycle_id": cycle_id,
                "policy": self._policy_to_dict(policy, workspace_id=mission.workspace_id),
                "assignment": None if assignment is None else self._assignment_to_dict(assignment),
                "admission": admission,
                "dependencies": dependencies,
                "portfolio_peers": [self._assignment_to_dict(row) for row in siblings],
            }

    async def scheduler_tick(
        self,
        *,
        workspace_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        self._last_tick_at = utc_now()
        refreshed = await self.refresh_dependencies(workspace_id=workspace_id)
        rebalanced: list[str] = []
        skipped: list[dict[str, str]] = []
        with self._session_factory() as session:
            statement = select(MissionPortfolioPolicyModel).where(
                MissionPortfolioPolicyModel.enabled.is_(True),
                MissionPortfolioPolicyModel.auto_rebalance_enabled.is_(True),
                MissionPortfolioPolicyModel.require_human_approval.is_(False),
                MissionPortfolioPolicyModel.prioritization_mode.in_(["weighted", "adaptive"]),
            )
            if workspace_id is not None:
                statement = statement.where(
                    MissionPortfolioPolicyModel.workspace_id == workspace_id
                )
            policies = list(session.scalars(statement.limit(limit)).all())
            due_workspaces = []
            now = utc_now()
            for row in policies:
                last = ensure_utc(row.last_rebalanced_at)
                if last is None or last + timedelta(seconds=row.rebalance_interval_seconds) <= now:
                    due_workspaces.append(row.workspace_id)
        for target_workspace in due_workspaces:
            try:
                await self.rebalance(
                    target_workspace,
                    MissionPortfolioRebalanceRequest(
                        actor_id="system",
                        automatic=True,
                        apply=True,
                        rationale="Scheduled adaptive Mission portfolio rebalance.",
                    ),
                )
                rebalanced.append(target_workspace)
            except AutonomousMissionError as exc:
                skipped.append({"workspace_id": target_workspace, "reason": str(exc)})
        return {
            "dependency_refresh": refreshed,
            "rebalanced_workspace_ids": rebalanced,
            "skipped": skipped,
        }

    async def handle_event(self, event: Event) -> None:
        if event.event_type not in {
            "mission.activated",
            "mission.completed",
            "mission.failed",
            "mission.cancelled",
            "mission.paused",
            "mission.cycle.completed",
            "mission.cycle.failed",
            "mission.cycle.cancelled",
        }:
            return
        mission_id = str(event.payload.get("mission_id") or event.correlation_id or "")
        if not mission_id:
            return
        with self._session_factory() as session:
            mission = session.get(WorkspaceMissionModel, mission_id)
            workspace_id = event.workspace_id or (mission.workspace_id if mission is not None else None)
        await self.refresh_dependencies(workspace_id=workspace_id)
        if workspace_id:
            policy = self.get_policy(workspace_id)
            if (
                policy["enabled"]
                and policy["auto_rebalance_enabled"]
                and not policy["require_human_approval"]
                and policy["prioritization_mode"] in {"weighted", "adaptive"}
            ):
                try:
                    await self.rebalance(
                        workspace_id,
                        MissionPortfolioRebalanceRequest(
                            actor_id="system",
                            automatic=True,
                            apply=True,
                            rationale=f"Portfolio rebalance after {event.event_type}.",
                            context={"causation_event_id": event.id},
                        ),
                    )
                except AutonomousMissionError:
                    return

    def _mission_admission_session(
        self,
        session: Session,
        mission: WorkspaceMissionModel,
        *,
        force: bool,
    ) -> dict[str, Any]:
        dependencies = list(
            session.scalars(
                select(MissionDependencyModel).where(
                    MissionDependencyModel.mission_id == mission.id
                )
            ).all()
        )
        upstream_ids = {row.depends_on_mission_id for row in dependencies}
        upstream_map = {
            row.id: row
            for row in session.scalars(
                select(WorkspaceMissionModel).where(
                    WorkspaceMissionModel.id.in_(upstream_ids)
                )
            ).all()
        } if upstream_ids else {}
        hard_blocks: list[dict[str, Any]] = []
        soft_warnings: list[dict[str, Any]] = []
        dependency_rows: list[dict[str, Any]] = []
        for row in dependencies:
            upstream = upstream_map.get(row.depends_on_mission_id)
            if row.status != MissionDependencyStatus.WAIVED.value and upstream is not None:
                self._refresh_dependency_row(row, upstream)
            item = self._dependency_to_dict(row, upstream=upstream)
            dependency_rows.append(item)
            satisfied = row.status in {
                MissionDependencyStatus.SATISFIED.value,
                MissionDependencyStatus.WAIVED.value,
            }
            if satisfied or row.dependency_type == MissionDependencyType.INFORMATIONAL.value:
                continue
            if row.dependency_type == MissionDependencyType.HARD.value:
                hard_blocks.append(item)
            else:
                soft_warnings.append(item)

        policy = session.scalar(
            select(MissionPortfolioPolicyModel).where(
                MissionPortfolioPolicyModel.workspace_id == mission.workspace_id
            )
        )
        assignment = session.scalar(
            select(MissionPortfolioAssignmentModel).where(
                MissionPortfolioAssignmentModel.mission_id == mission.id
            )
        )
        portfolio_block = False
        portfolio_reason = None
        if policy is not None and policy.enabled and policy.enforce_cycle_admission:
            if assignment is None:
                portfolio_block = True
                portfolio_reason = "Mission has no portfolio slot assignment."
            elif assignment.status != MissionPortfolioAssignmentStatus.SELECTED.value:
                portfolio_block = True
                portfolio_reason = f"Mission portfolio assignment is {assignment.status}."

        allowed = not hard_blocks and not portfolio_block
        if force:
            allowed = True
        reasons = []
        if hard_blocks:
            reasons.append(f"{len(hard_blocks)} hard Mission dependencies are unsatisfied.")
        if portfolio_reason:
            reasons.append(portfolio_reason)
        return {
            "allowed": allowed,
            "forced": force,
            "governed": bool(dependencies) or bool(policy and policy.enabled),
            "mission_id": mission.id,
            "workspace_id": mission.workspace_id,
            "hard_blocks": hard_blocks,
            "soft_warnings": soft_warnings,
            "dependencies": dependency_rows,
            "portfolio_policy": self._policy_to_dict(policy, workspace_id=mission.workspace_id),
            "portfolio_assignment": None if assignment is None else self._assignment_to_dict(assignment),
            "reasons": reasons,
        }

    def _score_mission(
        self,
        session: Session,
        mission: WorkspaceMissionModel,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        admission = self._mission_admission_session(session, mission, force=False)
        priority = float(mission.priority)
        progress = float(mission.progress_percent)
        deadline = self._deadline_urgency(mission.deadline_at)
        dependency = 0.0 if admission["hard_blocks"] else (70.0 if admission["soft_warnings"] else 100.0)
        selected_strategy = session.scalar(
            select(MissionStrategyModel).where(
                MissionStrategyModel.mission_id == mission.id,
                MissionStrategyModel.status == "selected",
            )
        )
        strategy = float(selected_strategy.adaptive_score) if selected_strategy is not None else 50.0
        open_risk = float(
            session.scalar(
                select(func.max(MissionRiskModel.exposure_score)).where(
                    MissionRiskModel.mission_id == mission.id,
                    MissionRiskModel.status.notin_(["closed", "mitigated"]),
                )
            )
            or 0.0
        )
        weights = {
            "priority": float(policy["priority_weight"]),
            "progress": float(policy["progress_weight"]),
            "deadline": float(policy["deadline_weight"]),
            "dependency": float(policy["dependency_weight"]),
            "strategy": float(policy["strategy_weight"]),
        }
        positive_weight = max(1.0, sum(weights.values()))
        weighted = (
            priority * weights["priority"]
            + progress * weights["progress"]
            + deadline * weights["deadline"]
            + dependency * weights["dependency"]
            + strategy * weights["strategy"]
        ) / positive_weight
        risk_penalty = open_risk * float(policy["risk_penalty_weight"]) / 100.0
        score = max(0.0, min(100.0, round(weighted - risk_penalty, 4)))
        blocked = bool(admission["hard_blocks"])
        reason = (
            "Blocked by hard Mission dependency."
            if blocked
            else "Weighted portfolio score calculated from priority, progress, deadline, dependencies, strategy and risk."
        )
        return {
            "mission_id": mission.id,
            "title": mission.title,
            "status": mission.status,
            "priority": mission.priority,
            "progress_percent": float(mission.progress_percent),
            "deadline_at": iso(mission.deadline_at),
            "score": score,
            "blocked": blocked,
            "reason": reason,
            "components": {
                "priority": priority,
                "progress": progress,
                "deadline_urgency": deadline,
                "dependency_readiness": dependency,
                "strategy_quality": strategy,
                "risk_exposure": open_risk,
                "risk_penalty": round(risk_penalty, 4),
                "weights": weights,
            },
            "dependency_admission": admission,
        }

    @staticmethod
    def _deadline_urgency(deadline_at: datetime | None) -> float:
        deadline = ensure_utc(deadline_at)
        if deadline is None:
            return 25.0
        seconds = (deadline - utc_now()).total_seconds()
        if seconds <= 0:
            return 100.0
        days = seconds / 86400.0
        if days <= 1:
            return 95.0
        if days <= 7:
            return max(60.0, 95.0 - ((days - 1.0) / 6.0) * 35.0)
        if days <= 30:
            return max(30.0, 60.0 - ((days - 7.0) / 23.0) * 30.0)
        return 20.0

    @staticmethod
    def _refresh_dependency_row(
        row: MissionDependencyModel,
        upstream: WorkspaceMissionModel,
    ) -> None:
        if row.status == MissionDependencyStatus.WAIVED.value:
            return
        required = set(row.required_statuses_json or ["completed"])
        if upstream.status in required:
            row.status = MissionDependencyStatus.SATISFIED.value
            row.satisfied_at = row.satisfied_at or utc_now()
            return
        if upstream.status in {"failed", "cancelled"}:
            if row.allow_failed:
                row.status = MissionDependencyStatus.SATISFIED.value
                row.satisfied_at = row.satisfied_at or utc_now()
            else:
                row.status = MissionDependencyStatus.FAILED.value
                row.satisfied_at = None
            return
        row.status = MissionDependencyStatus.PENDING.value
        row.satisfied_at = None

    def _validate_graph_session(self, session: Session, workspace_id: str) -> dict[str, Any]:
        missions = list(
            session.scalars(
                select(WorkspaceMissionModel).where(
                    WorkspaceMissionModel.workspace_id == workspace_id
                )
            ).all()
        )
        mission_ids = {row.id for row in missions}
        dependencies = list(
            session.scalars(
                select(MissionDependencyModel).where(
                    MissionDependencyModel.workspace_id == workspace_id,
                    MissionDependencyModel.status != MissionDependencyStatus.WAIVED.value,
                )
            ).all()
        )
        graph = {mission_id: [] for mission_id in mission_ids}
        errors: list[str] = []
        for row in dependencies:
            if row.mission_id not in mission_ids or row.depends_on_mission_id not in mission_ids:
                errors.append(f"Unknown Mission in dependency {row.id}.")
                continue
            graph[row.mission_id].append(row.depends_on_mission_id)

        visiting: set[str] = set()
        visited: set[str] = set()
        stack: list[str] = []
        cycle: list[str] = []

        def visit(node: str) -> bool:
            nonlocal cycle
            if node in visited:
                return False
            if node in visiting:
                index = stack.index(node) if node in stack else 0
                cycle = stack[index:] + [node]
                return True
            visiting.add(node)
            stack.append(node)
            for target in graph.get(node, []):
                if visit(target):
                    return True
            stack.pop()
            visiting.remove(node)
            visited.add(node)
            return False

        for node in sorted(graph):
            if visit(node):
                errors.append("Mission dependency cycle detected.")
                break

        indegree = {node: 0 for node in graph}
        reverse: dict[str, list[str]] = {node: [] for node in graph}
        for node, upstreams in graph.items():
            indegree[node] = len(upstreams)
            for upstream in upstreams:
                reverse.setdefault(upstream, []).append(node)
        ready = sorted(node for node, degree in indegree.items() if degree == 0)
        order: list[str] = []
        while ready:
            node = ready.pop(0)
            order.append(node)
            for dependent in sorted(reverse.get(node, [])):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    ready.append(dependent)
                    ready.sort()
        return {
            "workspace_id": workspace_id,
            "valid": not errors and len(order) == len(graph),
            "errors": errors,
            "cycle": cycle,
            "mission_count": len(missions),
            "dependency_count": len(dependencies),
            "topological_order": order,
        }

    @staticmethod
    def _policy_to_dict(
        row: MissionPortfolioPolicyModel | None,
        *,
        workspace_id: str,
    ) -> dict[str, Any]:
        if row is None:
            return {
                "id": None,
                "workspace_id": workspace_id,
                "enabled": False,
                "prioritization_mode": "manual",
                "require_human_approval": True,
                "auto_rebalance_enabled": False,
                "enforce_cycle_admission": False,
                "max_parallel_missions": 3,
                "min_selection_score": 50.0,
                "rebalance_interval_seconds": 300,
                "priority_weight": 35.0,
                "progress_weight": 15.0,
                "deadline_weight": 20.0,
                "dependency_weight": 20.0,
                "strategy_weight": 10.0,
                "risk_penalty_weight": 15.0,
                "last_rebalanced_at": None,
                "metadata": {},
                "created_at": None,
                "updated_at": None,
                "persisted": False,
            }
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "enabled": row.enabled,
            "prioritization_mode": row.prioritization_mode,
            "require_human_approval": row.require_human_approval,
            "auto_rebalance_enabled": row.auto_rebalance_enabled,
            "enforce_cycle_admission": row.enforce_cycle_admission,
            "max_parallel_missions": row.max_parallel_missions,
            "min_selection_score": float(row.min_selection_score),
            "rebalance_interval_seconds": row.rebalance_interval_seconds,
            "priority_weight": float(row.priority_weight),
            "progress_weight": float(row.progress_weight),
            "deadline_weight": float(row.deadline_weight),
            "dependency_weight": float(row.dependency_weight),
            "strategy_weight": float(row.strategy_weight),
            "risk_penalty_weight": float(row.risk_penalty_weight),
            "last_rebalanced_at": iso(row.last_rebalanced_at),
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
            "persisted": True,
        }

    @staticmethod
    def _dependency_to_dict(
        row: MissionDependencyModel,
        *,
        upstream: WorkspaceMissionModel | None,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "depends_on_mission_id": row.depends_on_mission_id,
            "depends_on_title": None if upstream is None else upstream.title,
            "depends_on_status": None if upstream is None else upstream.status,
            "dependency_type": row.dependency_type,
            "status": row.status,
            "required_statuses": list(row.required_statuses_json or []),
            "allow_failed": row.allow_failed,
            "priority": row.priority,
            "rationale": row.rationale,
            "metadata": dict(row.metadata_json or {}),
            "satisfied_at": iso(row.satisfied_at),
            "waived_at": iso(row.waived_at),
            "waived_by": row.waived_by,
            "waiver_reason": row.waiver_reason,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _assignment_to_dict(row: MissionPortfolioAssignmentModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "policy_id": row.policy_id,
            "status": row.status,
            "score": float(row.score),
            "rank": row.rank,
            "automatic": row.automatic,
            "manual_override": row.manual_override,
            "actor_id": row.actor_id,
            "reason": row.reason,
            "metadata": dict(row.metadata_json or {}),
            "assigned_at": iso(row.assigned_at),
            "released_at": iso(row.released_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _evaluation_to_dict(row: MissionPortfolioEvaluationModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "policy_id": row.policy_id,
            "actor_id": row.actor_id,
            "automatic": row.automatic,
            "score": float(row.score),
            "rank": row.rank,
            "decision": row.decision,
            "components": dict(row.components_json or {}),
            "reason": row.reason,
            "context": dict(row.context_json or {}),
            "created_at": iso(row.created_at),
        }

    @staticmethod
    def _require_mission(session: Session, mission_id: str) -> WorkspaceMissionModel:
        row = session.get(WorkspaceMissionModel, mission_id)
        if row is None:
            raise MissionNotFound(f"Mission not found: {mission_id}")
        return row

    @staticmethod
    def _require_dependency(session: Session, dependency_id: str) -> MissionDependencyModel:
        row = session.get(MissionDependencyModel, dependency_id)
        if row is None:
            raise MissionDependencyNotFound(f"Mission dependency not found: {dependency_id}")
        return row

    @staticmethod
    def _require_workspace_mission_presence(session: Session, workspace_id: str) -> None:
        exists = session.scalar(
            select(WorkspaceMissionModel.id).where(
                WorkspaceMissionModel.workspace_id == workspace_id
            ).limit(1)
        )
        if exists is None:
            raise MissionNotFound(f"No Mission found for Workspace: {workspace_id}")

    async def _publish(
        self,
        event_type: str,
        *,
        workspace_id: str,
        correlation_id: str,
        payload: dict[str, Any],
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="mission_portfolio_service",
                workspace_id=workspace_id,
                correlation_id=correlation_id,
                payload=payload,
            )
        )
