from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.autonomy.models import (
    MissionCycleModel,
    MissionPortfolioAssignmentModel,
    MissionResourceAllocationModel,
    MissionResourceUsageModel,
    WorkspaceMissionModel,
    WorkspaceResourceConflictModel,
    WorkspaceResourcePolicyModel,
    WorkspaceResourceRebalanceModel,
    WorkspaceResourceReservationModel,
)
from backend.autonomy.service import (
    AutonomousMissionError,
    MissionCycleNotFound,
    MissionNotFound,
    MissionStateError,
)
from backend.autonomy.workspace_resources_schemas import (
    WorkspaceResourceAllocationMode,
    WorkspaceResourceConflictAction,
    WorkspaceResourceConflictResolution,
    WorkspaceResourceConflictStatus,
    WorkspaceResourcePolicyUpsert,
    WorkspaceResourceRebalanceDecision,
    WorkspaceResourceRebalanceRequest,
    WorkspaceResourceRebalanceStatus,
    WorkspaceResourceReservationCreate,
    WorkspaceResourceReservationDecision,
    WorkspaceResourceReservationStatus,
)
from backend.core.events import Event, EventBus
from backend.database.session import session_scope


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


class WorkspaceResourceReservationNotFound(AutonomousMissionError):
    pass


class WorkspaceResourceConflictNotFound(AutonomousMissionError):
    pass


class WorkspaceResourceRebalanceNotFound(AutonomousMissionError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def money(value: float) -> float:
    return round(max(0.0, float(value)), 6)


class WorkspaceResourceCoordinator:
    """Coordinates a shared Workspace budget and execution capacity.

    Mission-level allocations remain the source of requested resources. This
    coordinator applies a second admission gate across all Missions in the same
    Workspace. Shared governance is disabled by default and therefore does not
    change existing Mission behaviour until explicitly enabled.
    """

    ACTIVE_STATUSES = {
        WorkspaceResourceReservationStatus.RESERVED.value,
        WorkspaceResourceReservationStatus.ACTIVE.value,
    }
    TERMINAL_STATUSES = {
        WorkspaceResourceReservationStatus.RELEASED.value,
        WorkspaceResourceReservationStatus.CONSUMED.value,
        WorkspaceResourceReservationStatus.EXCEEDED.value,
        WorkspaceResourceReservationStatus.CANCELLED.value,
    }

    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._reservations_created = 0
        self._reservations_approved = 0
        self._conflicts_created = 0
        self._conflicts_resolved = 0
        self._rebalances_created = 0
        self._rebalances_applied = 0

    def status(self) -> dict[str, Any]:
        with self._session_factory() as session:
            policies = int(
                session.scalar(
                    select(func.count()).select_from(WorkspaceResourcePolicyModel)
                )
                or 0
            )
            reservations = list(
                session.scalars(select(WorkspaceResourceReservationModel)).all()
            )
            conflicts = list(
                session.scalars(select(WorkspaceResourceConflictModel)).all()
            )
            rebalances = int(
                session.scalar(
                    select(func.count()).select_from(WorkspaceResourceRebalanceModel)
                )
                or 0
            )
        return {
            "policies": policies,
            "reservations": len(reservations),
            "active_reservations": sum(
                1 for row in reservations if row.status in self.ACTIVE_STATUSES
            ),
            "open_conflicts": sum(
                1
                for row in conflicts
                if row.status == WorkspaceResourceConflictStatus.OPEN.value
            ),
            "rebalances": rebalances,
            "reservations_created": self._reservations_created,
            "reservations_approved": self._reservations_approved,
            "conflicts_created": self._conflicts_created,
            "conflicts_resolved": self._conflicts_resolved,
            "rebalances_created": self._rebalances_created,
            "rebalances_applied": self._rebalances_applied,
            "default_safety": {
                "enabled": False,
                "allocation_mode": "manual",
                "require_human_approval": True,
                "auto_rebalance_enabled": False,
                "enforce_cycle_admission": False,
            },
            "capabilities": [
                "workspace_shared_budget",
                "cross_mission_capacity_pool",
                "cycle_reservation_admission",
                "resource_conflict_detection",
                "safe_priority_preemption",
                "adaptive_workspace_rebalance",
                "planner_workspace_resource_context",
                "audit_and_event_transport",
            ],
        }

    def get_policy(self, workspace_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            self._require_workspace_presence(session, workspace_id)
            row = self._policy_row(session, workspace_id)
            return self._policy_to_dict(row, workspace_id)

    async def upsert_policy(
        self,
        workspace_id: str,
        request: WorkspaceResourcePolicyUpsert,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            self._require_workspace_presence(session, workspace_id)
            row = self._policy_row(session, workspace_id)
            created = row is None
            if row is None:
                row = WorkspaceResourcePolicyModel(workspace_id=workspace_id)
                session.add(row)
            values = request.model_dump(mode="json")
            values["allocation_mode"] = request.allocation_mode.value
            values["metadata_json"] = values.pop("metadata")
            for key, value in values.items():
                setattr(row, key, value)
            session.flush()
            result = self._policy_to_dict(row, workspace_id)

        await self._publish(
            "mission.workspace_resource.policy.created"
            if created
            else "mission.workspace_resource.policy.updated",
            workspace_id=workspace_id,
            correlation_id=row.id,
            payload={"policy": result},
        )
        return result

    def dashboard(self, workspace_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            self._require_workspace_presence(session, workspace_id)
            policy = self._policy_row(session, workspace_id)
            reservations = list(
                session.scalars(
                    select(WorkspaceResourceReservationModel)
                    .where(
                        WorkspaceResourceReservationModel.workspace_id
                        == workspace_id
                    )
                    .order_by(
                        WorkspaceResourceReservationModel.created_at.desc()
                    )
                ).all()
            )
            conflicts = list(
                session.scalars(
                    select(WorkspaceResourceConflictModel)
                    .where(
                        WorkspaceResourceConflictModel.workspace_id == workspace_id
                    )
                    .order_by(WorkspaceResourceConflictModel.created_at.desc())
                    .limit(50)
                ).all()
            )
            rebalances = list(
                session.scalars(
                    select(WorkspaceResourceRebalanceModel)
                    .where(
                        WorkspaceResourceRebalanceModel.workspace_id == workspace_id
                    )
                    .order_by(WorkspaceResourceRebalanceModel.created_at.desc())
                    .limit(20)
                ).all()
            )
            summary = self._summary(session, workspace_id, policy, reservations)
            return {
                "workspace_id": workspace_id,
                "policy": self._policy_to_dict(policy, workspace_id),
                "summary": summary,
                "reservations": [
                    self._reservation_to_dict(row) for row in reservations[:100]
                ],
                "open_conflicts": [
                    self._conflict_to_dict(row)
                    for row in conflicts
                    if row.status == WorkspaceResourceConflictStatus.OPEN.value
                ],
                "recent_rebalances": [
                    self._rebalance_to_dict(row) for row in rebalances
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
            policy = self._policy_row(session, mission.workspace_id)
            reservations = list(
                session.scalars(
                    select(WorkspaceResourceReservationModel)
                    .where(
                        WorkspaceResourceReservationModel.workspace_id
                        == mission.workspace_id
                    )
                    .order_by(
                        WorkspaceResourceReservationModel.created_at.desc()
                    )
                    .limit(limit)
                ).all()
            )
            own = None
            if cycle_id:
                own = session.scalar(
                    select(WorkspaceResourceReservationModel).where(
                        WorkspaceResourceReservationModel.cycle_id == cycle_id
                    )
                )
            conflicts = list(
                session.scalars(
                    select(WorkspaceResourceConflictModel)
                    .where(
                        WorkspaceResourceConflictModel.workspace_id
                        == mission.workspace_id,
                        WorkspaceResourceConflictModel.status
                        == WorkspaceResourceConflictStatus.OPEN.value,
                    )
                    .order_by(WorkspaceResourceConflictModel.created_at.desc())
                    .limit(limit)
                ).all()
            )
            return {
                "workspace_id": mission.workspace_id,
                "mission_id": mission.id,
                "cycle_id": cycle_id,
                "policy": self._policy_to_dict(policy, mission.workspace_id),
                "summary": self._summary(
                    session,
                    mission.workspace_id,
                    policy,
                    reservations,
                ),
                "cycle_reservation": (
                    self._reservation_to_dict(own) if own is not None else None
                ),
                "recent_reservations": [
                    self._reservation_to_dict(row) for row in reservations
                ],
                "open_conflicts": [
                    self._conflict_to_dict(row) for row in conflicts
                ],
            }

    async def create_reservation(
        self,
        cycle_id: str,
        request: WorkspaceResourceReservationCreate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            cycle = self._require_cycle(session, cycle_id)
            mission = self._require_mission(session, cycle.mission_id)
            existing = self._reservation_by_cycle(session, cycle_id)
            if existing is not None:
                raise MissionStateError(
                    "Для Mission Cycle уже существует Workspace Resource Reservation."
                )
            policy = self._policy_row(session, mission.workspace_id)
            allocation = self._allocation_by_cycle(session, cycle_id)
            values = self._requested_values(policy, allocation, request)
            require_approval = request.request_approval or bool(
                policy is not None and policy.require_human_approval
            )
            row = WorkspaceResourceReservationModel(
                workspace_id=mission.workspace_id,
                mission_id=mission.id,
                cycle_id=cycle.id,
                allocation_id=allocation.id if allocation else None,
                policy_id=policy.id if policy else None,
                status=(
                    WorkspaceResourceReservationStatus.PENDING_APPROVAL.value
                    if require_approval and not request.force
                    else WorkspaceResourceReservationStatus.RESERVED.value
                ),
                automatic=request.automatic,
                actor_id=request.actor_id,
                approved_by=(request.actor_id if not require_approval else None),
                budget_usd=values["budget_usd"],
                agent_slots=values["agent_slots"],
                tool_slots=values["tool_slots"],
                compute_units=values["compute_units"],
                priority_score=self._priority_score(session, mission.id),
                forced=request.force,
                reason=request.reason,
                metadata_json=dict(request.metadata),
                approved_at=(utc_now() if not require_approval else None),
                reserved_at=(
                    utc_now()
                    if not require_approval or request.force
                    else None
                ),
            )
            session.add(row)
            session.flush()
            admission = self._evaluate_reservation(
                session,
                policy,
                row,
                exclude_id=row.id,
            )
            row.admission_json = {**admission, "forced": request.force}
            conflict = None
            if not admission["allowed"] and not request.force:
                row.status = WorkspaceResourceReservationStatus.PENDING_APPROVAL.value
                row.reserved_at = None
                conflict = self._ensure_conflict(session, row, admission)
            result = self._reservation_to_dict(row)
            conflict_result = (
                self._conflict_to_dict(conflict) if conflict is not None else None
            )

        self._reservations_created += 1
        await self._publish(
            "mission.workspace_resource.reservation.created",
            workspace_id=result["workspace_id"],
            correlation_id=result["mission_id"],
            payload={"reservation": result, "conflict": conflict_result},
        )
        return {"reservation": result, "conflict": conflict_result}

    async def prepare_cycle(
        self,
        cycle_id: str,
        *,
        actor_id: str = "system",
        force: bool = False,
    ) -> dict[str, Any]:
        created = False
        conflict_result = None
        with self._session_factory() as session:
            cycle = self._require_cycle(session, cycle_id)
            mission = self._require_mission(session, cycle.mission_id)
            policy = self._policy_row(session, mission.workspace_id)
            if policy is None or not policy.enabled:
                return {
                    "allowed": True,
                    "governed": False,
                    "workspace_id": mission.workspace_id,
                    "mission_id": mission.id,
                    "cycle_id": cycle.id,
                    "reasons": [],
                    "reservation": None,
                }
            row = self._reservation_by_cycle(session, cycle_id)
            if row is None:
                allocation = self._allocation_by_cycle(session, cycle_id)
                values = self._requested_values(policy, allocation, None)
                row = WorkspaceResourceReservationModel(
                    workspace_id=mission.workspace_id,
                    mission_id=mission.id,
                    cycle_id=cycle.id,
                    allocation_id=allocation.id if allocation else None,
                    policy_id=policy.id,
                    status=(
                        WorkspaceResourceReservationStatus.PENDING_APPROVAL.value
                        if policy.require_human_approval and not force
                        else WorkspaceResourceReservationStatus.RESERVED.value
                    ),
                    automatic=True,
                    actor_id=actor_id,
                    approved_by=(actor_id if force or not policy.require_human_approval else None),
                    budget_usd=values["budget_usd"],
                    agent_slots=values["agent_slots"],
                    tool_slots=values["tool_slots"],
                    compute_units=values["compute_units"],
                    priority_score=self._priority_score(session, mission.id),
                    forced=force,
                    reason="Automatic Workspace resource reservation.",
                    approved_at=(
                        utc_now()
                        if force or not policy.require_human_approval
                        else None
                    ),
                    reserved_at=(
                        utc_now()
                        if force or not policy.require_human_approval
                        else None
                    ),
                    metadata_json={"automatic_prepare": True},
                )
                session.add(row)
                session.flush()
                created = True
                self._reservations_created += 1
            if (
                row.status
                == WorkspaceResourceReservationStatus.PENDING_APPROVAL.value
                and not force
            ):
                result = self._reservation_to_dict(row)
                return {
                    "allowed": not policy.enforce_cycle_admission,
                    "governed": True,
                    "workspace_id": mission.workspace_id,
                    "mission_id": mission.id,
                    "cycle_id": cycle.id,
                    "reasons": ["workspace_resource_approval_required"],
                    "reservation": result,
                    "enforced": policy.enforce_cycle_admission,
                }
            admission = self._evaluate_reservation(
                session,
                policy,
                row,
                exclude_id=row.id,
            )
            row.admission_json = {**admission, "forced": force}
            if not admission["allowed"] and not force:
                conflict = self._ensure_conflict(session, row, admission)
                conflict_result = self._conflict_to_dict(conflict)
                result = self._reservation_to_dict(row)
                return {
                    **admission,
                    "allowed": not policy.enforce_cycle_admission,
                    "governed": True,
                    "workspace_id": mission.workspace_id,
                    "mission_id": mission.id,
                    "cycle_id": cycle.id,
                    "reservation": result,
                    "conflict": conflict_result,
                    "enforced": policy.enforce_cycle_admission,
                }
            if row.status == WorkspaceResourceReservationStatus.PENDING_APPROVAL.value:
                row.status = WorkspaceResourceReservationStatus.RESERVED.value
                row.approved_by = actor_id
                row.approved_at = utc_now()
                row.reserved_at = utc_now()
            row.forced = row.forced or force
            result = self._reservation_to_dict(row)

        if created:
            await self._publish(
                "mission.workspace_resource.reservation.created",
                workspace_id=result["workspace_id"],
                correlation_id=result["mission_id"],
                payload={"reservation": result},
            )
        await self._publish(
            "mission.workspace_resource.reservation.reserved",
            workspace_id=result["workspace_id"],
            correlation_id=result["mission_id"],
            payload={"reservation": result, "admission": admission},
        )
        return {
            "allowed": True,
            "governed": True,
            "workspace_id": result["workspace_id"],
            "mission_id": result["mission_id"],
            "cycle_id": result["cycle_id"],
            "reasons": admission["reasons"],
            "forced": force,
            "reservation": result,
            "conflict": conflict_result,
            "enforced": policy.enforce_cycle_admission,
        }

    async def approve_reservation(
        self,
        reservation_id: str,
        request: WorkspaceResourceReservationDecision,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_reservation(session, reservation_id)
            if row.status in self.TERMINAL_STATUSES:
                raise MissionStateError("Нельзя подтвердить завершённый Reservation.")
            policy = self._policy_row(session, row.workspace_id)
            admission = self._evaluate_reservation(
                session,
                policy,
                row,
                exclude_id=row.id,
            )
            if not admission["allowed"] and not request.force:
                conflict = self._ensure_conflict(session, row, admission)
                raise MissionStateError(
                    "Workspace Resource Reservation конфликтует с общим бюджетом "
                    "или мощностью: " + ", ".join(admission["reasons"])
                    + f". conflict_id={conflict.id}"
                )
            row.status = WorkspaceResourceReservationStatus.RESERVED.value
            row.approved_by = request.actor_id
            row.approved_at = utc_now()
            row.reserved_at = row.reserved_at or utc_now()
            row.forced = row.forced or request.force
            row.reason = request.reason
            row.admission_json = {**admission, "forced": request.force}
            metadata = dict(row.metadata_json or {})
            metadata.update(request.metadata)
            row.metadata_json = metadata
            row.version += 1
            result = self._reservation_to_dict(row)

        self._reservations_approved += 1
        await self._publish(
            "mission.workspace_resource.reservation.approved",
            workspace_id=result["workspace_id"],
            correlation_id=result["mission_id"],
            payload={"reservation": result},
        )
        return result

    async def release_reservation(
        self,
        reservation_id: str,
        request: WorkspaceResourceReservationDecision,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_reservation(session, reservation_id)
            if row.status in self.TERMINAL_STATUSES:
                return self._reservation_to_dict(row)
            row.status = WorkspaceResourceReservationStatus.RELEASED.value
            row.finished_at = utc_now()
            row.released_at = row.finished_at
            row.reason = request.reason
            metadata = dict(row.metadata_json or {})
            metadata.update(request.metadata)
            metadata["released_by"] = request.actor_id
            row.metadata_json = metadata
            row.version += 1
            result = self._reservation_to_dict(row)
        await self._publish(
            "mission.workspace_resource.reservation.released",
            workspace_id=result["workspace_id"],
            correlation_id=result["mission_id"],
            payload={"reservation": result},
        )
        return result

    def get_reservation(self, reservation_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(WorkspaceResourceReservationModel, reservation_id)
            return None if row is None else self._reservation_to_dict(row)

    def list_reservations(
        self,
        workspace_id: str,
        *,
        mission_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_workspace_presence(session, workspace_id)
            statement = select(WorkspaceResourceReservationModel).where(
                WorkspaceResourceReservationModel.workspace_id == workspace_id
            )
            if mission_id:
                statement = statement.where(
                    WorkspaceResourceReservationModel.mission_id == mission_id
                )
            if status:
                statement = statement.where(
                    WorkspaceResourceReservationModel.status == status
                )
            rows = list(
                session.scalars(
                    statement.order_by(
                        WorkspaceResourceReservationModel.created_at.desc()
                    )
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._reservation_to_dict(row) for row in rows]

    def list_conflicts(
        self,
        workspace_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_workspace_presence(session, workspace_id)
            statement = select(WorkspaceResourceConflictModel).where(
                WorkspaceResourceConflictModel.workspace_id == workspace_id
            )
            if status:
                statement = statement.where(
                    WorkspaceResourceConflictModel.status == status
                )
            rows = list(
                session.scalars(
                    statement.order_by(WorkspaceResourceConflictModel.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._conflict_to_dict(row) for row in rows]

    def get_conflict(self, conflict_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(WorkspaceResourceConflictModel, conflict_id)
            return None if row is None else self._conflict_to_dict(row)

    async def resolve_conflict(
        self,
        conflict_id: str,
        request: WorkspaceResourceConflictResolution,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            conflict = self._require_conflict(session, conflict_id)
            if conflict.status != WorkspaceResourceConflictStatus.OPEN.value:
                return self._conflict_to_dict(conflict)
            reservation = (
                session.get(
                    WorkspaceResourceReservationModel,
                    conflict.reservation_id,
                )
                if conflict.reservation_id
                else None
            )
            policy = self._policy_row(session, conflict.workspace_id)
            action = request.action
            if action == WorkspaceResourceConflictAction.INCREASE_CAPACITY:
                if policy is None:
                    policy = WorkspaceResourcePolicyModel(
                        workspace_id=conflict.workspace_id,
                        enabled=True,
                    )
                    session.add(policy)
                    session.flush()
                for field in (
                    "total_budget_usd",
                    "max_parallel_cycles",
                    "agent_slots",
                    "tool_slots",
                    "compute_units",
                ):
                    value = getattr(request, field)
                    if value is not None:
                        setattr(policy, field, value)
                if reservation is not None:
                    admission = self._evaluate_reservation(
                        session,
                        policy,
                        reservation,
                        exclude_id=reservation.id,
                    )
                    if admission["allowed"] or request.force:
                        self._reserve_row(
                            reservation,
                            actor_id=request.actor_id,
                            reason=request.reason,
                            forced=request.force,
                            admission=admission,
                        )
            elif action == WorkspaceResourceConflictAction.PREEMPT:
                if reservation is None:
                    raise MissionStateError("Conflict has no Reservation to admit.")
                released = self._preempt_lower_priority(
                    session,
                    reservation,
                    force=request.force,
                )
                admission = self._evaluate_reservation(
                    session,
                    policy,
                    reservation,
                    exclude_id=reservation.id,
                )
                if not admission["allowed"] and not request.force:
                    raise MissionStateError(
                        "Safe preemption did not free enough Workspace capacity."
                    )
                self._reserve_row(
                    reservation,
                    actor_id=request.actor_id,
                    reason=request.reason,
                    forced=request.force,
                    admission=admission,
                )
                metadata = dict(conflict.metadata_json or {})
                metadata["preempted_reservation_ids"] = released
                conflict.metadata_json = metadata
            elif action == WorkspaceResourceConflictAction.FORCE:
                if reservation is None:
                    raise MissionStateError("Conflict has no Reservation to force.")
                admission = self._evaluate_reservation(
                    session,
                    policy,
                    reservation,
                    exclude_id=reservation.id,
                )
                self._reserve_row(
                    reservation,
                    actor_id=request.actor_id,
                    reason=request.reason,
                    forced=True,
                    admission=admission,
                )
            elif action == WorkspaceResourceConflictAction.DEFER:
                if reservation is not None:
                    reservation.status = (
                        WorkspaceResourceReservationStatus.CANCELLED.value
                    )
                    reservation.finished_at = utc_now()
                    reservation.released_at = reservation.finished_at
                    reservation.reason = request.reason
                    reservation.version += 1
            elif action == WorkspaceResourceConflictAction.WAIVE:
                conflict.status = WorkspaceResourceConflictStatus.WAIVED.value
            elif action == WorkspaceResourceConflictAction.REBALANCE:
                # The conflict is resolved by a separately persisted rebalance plan.
                pass

            if conflict.status == WorkspaceResourceConflictStatus.OPEN.value:
                conflict.status = WorkspaceResourceConflictStatus.RESOLVED.value
            conflict.resolution_action = action.value
            conflict.resolution_reason = request.reason
            conflict.resolved_by = request.actor_id
            conflict.resolved_at = utc_now()
            metadata = dict(conflict.metadata_json or {})
            metadata.update(request.metadata)
            conflict.metadata_json = metadata
            result = self._conflict_to_dict(conflict)

        self._conflicts_resolved += 1
        await self._publish(
            "mission.workspace_resource.conflict.resolved",
            workspace_id=result["workspace_id"],
            correlation_id=result["mission_id"],
            payload={"conflict": result},
        )
        if request.action == WorkspaceResourceConflictAction.REBALANCE:
            await self.rebalance(
                result["workspace_id"],
                WorkspaceResourceRebalanceRequest(
                    actor_id=request.actor_id,
                    automatic=False,
                    apply=True,
                    force=request.force,
                    reason=request.reason,
                    metadata={"conflict_id": conflict_id},
                ),
            )
        return result

    async def rebalance(
        self,
        workspace_id: str,
        request: WorkspaceResourceRebalanceRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            self._require_workspace_presence(session, workspace_id)
            policy = self._policy_row(session, workspace_id)
            if policy is None or not policy.enabled:
                raise MissionStateError("Workspace Resource Policy is not enabled.")
            if request.automatic and (
                not policy.auto_rebalance_enabled
                or policy.allocation_mode == WorkspaceResourceAllocationMode.MANUAL.value
            ):
                raise MissionStateError("Automatic Workspace rebalance is disabled.")
            rows = list(
                session.scalars(
                    select(WorkspaceResourceReservationModel).where(
                        WorkspaceResourceReservationModel.workspace_id == workspace_id,
                        WorkspaceResourceReservationModel.status.in_(
                            [
                                WorkspaceResourceReservationStatus.PENDING_APPROVAL.value,
                                WorkspaceResourceReservationStatus.RESERVED.value,
                                WorkspaceResourceReservationStatus.ACTIVE.value,
                            ]
                        ),
                    )
                ).all()
            )
            ordered = sorted(
                rows,
                key=lambda row: (
                    row.status == WorkspaceResourceReservationStatus.ACTIVE.value,
                    row.priority_score,
                    -row.created_at.timestamp(),
                ),
                reverse=True,
            )
            selected: list[str] = []
            deferred: list[str] = []
            used = {
                "budget_usd": 0.0,
                "parallel_cycles": 0,
                "agent_slots": 0,
                "tool_slots": 0,
                "compute_units": 0.0,
            }
            capacity = self._capacity(policy)
            for rank, row in enumerate(ordered, start=1):
                row.rank = rank
                fits = (
                    used["parallel_cycles"] + 1 <= capacity["max_parallel_cycles"]
                    and used["budget_usd"] + row.budget_usd <= capacity["budget_usd"] + 1e-9
                    and used["agent_slots"] + row.agent_slots <= capacity["agent_slots"]
                    and used["tool_slots"] + row.tool_slots <= capacity["tool_slots"]
                    and used["compute_units"] + row.compute_units <= capacity["compute_units"] + 1e-9
                )
                if fits:
                    selected.append(row.id)
                    used["parallel_cycles"] += 1
                    used["budget_usd"] += row.budget_usd
                    used["agent_slots"] += row.agent_slots
                    used["tool_slots"] += row.tool_slots
                    used["compute_units"] += row.compute_units
                else:
                    deferred.append(row.id)
            proposal = {
                "selected_reservation_ids": selected,
                "deferred_reservation_ids": deferred,
                "used": {
                    **used,
                    "budget_usd": money(used["budget_usd"]),
                    "compute_units": round(used["compute_units"], 3),
                },
                "capacity": capacity,
            }
            plan = WorkspaceResourceRebalanceModel(
                workspace_id=workspace_id,
                policy_id=policy.id,
                status=WorkspaceResourceRebalanceStatus.RECOMMENDED.value,
                actor_id=request.actor_id,
                automatic=request.automatic,
                reason=request.reason,
                snapshot_json=self._summary(session, workspace_id, policy, rows),
                proposal_json=proposal,
                metadata_json=dict(request.metadata),
            )
            session.add(plan)
            session.flush()
            applied: dict[str, Any] = {}
            if request.apply:
                if policy.require_human_approval and request.automatic and not request.force:
                    raise MissionStateError(
                        "Automatic apply requires require_human_approval=false."
                    )
                released: list[str] = []
                reserved: list[str] = []
                for row in rows:
                    if row.id in selected:
                        if (
                            row.status
                            == WorkspaceResourceReservationStatus.PENDING_APPROVAL.value
                            and (not policy.require_human_approval or request.force)
                        ):
                            self._reserve_row(
                                row,
                                actor_id=request.actor_id,
                                reason=request.reason,
                                forced=request.force,
                                admission={"allowed": True, "reasons": []},
                            )
                            reserved.append(row.id)
                    elif (
                        row.status
                        == WorkspaceResourceReservationStatus.RESERVED.value
                    ):
                        row.status = WorkspaceResourceReservationStatus.RELEASED.value
                        row.released_at = utc_now()
                        row.finished_at = row.released_at
                        row.reason = request.reason
                        row.version += 1
                        released.append(row.id)
                applied = {
                    "reserved_reservation_ids": reserved,
                    "released_reservation_ids": released,
                }
                plan.status = WorkspaceResourceRebalanceStatus.APPLIED.value
                plan.applied_at = utc_now()
                plan.applied_json = applied
                policy.last_rebalanced_at = plan.applied_at
                self._rebalances_applied += 1
            result = self._rebalance_to_dict(plan)

        self._rebalances_created += 1
        await self._publish(
            "mission.workspace_resource.rebalance.applied"
            if request.apply
            else "mission.workspace_resource.rebalance.recommended",
            workspace_id=workspace_id,
            correlation_id=plan.id,
            payload={"rebalance": result},
        )
        return result

    async def decide_rebalance(
        self,
        rebalance_id: str,
        request: WorkspaceResourceRebalanceDecision,
        *,
        apply: bool,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_rebalance(session, rebalance_id)
            if row.status in {
                WorkspaceResourceRebalanceStatus.APPLIED.value,
                WorkspaceResourceRebalanceStatus.REJECTED.value,
            }:
                return self._rebalance_to_dict(row)
            if not apply:
                row.status = WorkspaceResourceRebalanceStatus.REJECTED.value
                row.reason = request.reason
                result = self._rebalance_to_dict(row)
                event_type = "mission.workspace_resource.rebalance.rejected"
            else:
                proposal = dict(row.proposal_json or {})
                selected = set(proposal.get("selected_reservation_ids") or [])
                rows = list(
                    session.scalars(
                        select(WorkspaceResourceReservationModel).where(
                            WorkspaceResourceReservationModel.workspace_id
                            == row.workspace_id,
                            WorkspaceResourceReservationModel.status.in_(
                                [
                                    WorkspaceResourceReservationStatus.PENDING_APPROVAL.value,
                                    WorkspaceResourceReservationStatus.RESERVED.value,
                                ]
                            ),
                        )
                    ).all()
                )
                released: list[str] = []
                reserved: list[str] = []
                for reservation in rows:
                    if reservation.id in selected:
                        self._reserve_row(
                            reservation,
                            actor_id=request.actor_id,
                            reason=request.reason,
                            forced=request.force,
                            admission={"allowed": True, "reasons": []},
                        )
                        reserved.append(reservation.id)
                    elif reservation.status == WorkspaceResourceReservationStatus.RESERVED.value:
                        reservation.status = WorkspaceResourceReservationStatus.RELEASED.value
                        reservation.released_at = utc_now()
                        reservation.finished_at = reservation.released_at
                        reservation.reason = request.reason
                        reservation.version += 1
                        released.append(reservation.id)
                row.status = WorkspaceResourceRebalanceStatus.APPLIED.value
                row.applied_at = utc_now()
                row.applied_json = {
                    "reserved_reservation_ids": reserved,
                    "released_reservation_ids": released,
                }
                policy = self._policy_row(session, row.workspace_id)
                if policy is not None:
                    policy.last_rebalanced_at = row.applied_at
                result = self._rebalance_to_dict(row)
                event_type = "mission.workspace_resource.rebalance.applied"
                self._rebalances_applied += 1

        await self._publish(
            event_type,
            workspace_id=result["workspace_id"],
            correlation_id=result["id"],
            payload={"rebalance": result},
        )
        return result

    def list_rebalances(
        self,
        workspace_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_workspace_presence(session, workspace_id)
            rows = list(
                session.scalars(
                    select(WorkspaceResourceRebalanceModel)
                    .where(
                        WorkspaceResourceRebalanceModel.workspace_id == workspace_id
                    )
                    .order_by(WorkspaceResourceRebalanceModel.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._rebalance_to_dict(row) for row in rows]

    async def scheduler_tick(self) -> dict[str, Any]:
        workspaces: list[str] = []
        with self._session_factory() as session:
            policies = list(
                session.scalars(
                    select(WorkspaceResourcePolicyModel).where(
                        WorkspaceResourcePolicyModel.enabled.is_(True),
                        WorkspaceResourcePolicyModel.auto_rebalance_enabled.is_(True),
                        WorkspaceResourcePolicyModel.allocation_mode
                        != WorkspaceResourceAllocationMode.MANUAL.value,
                    )
                ).all()
            )
            now = utc_now()
            for policy in policies:
                elapsed = (
                    (now - policy.last_rebalanced_at).total_seconds()
                    if policy.last_rebalanced_at is not None
                    else policy.rebalance_interval_seconds
                )
                if elapsed >= policy.rebalance_interval_seconds:
                    workspaces.append(policy.workspace_id)
        results = []
        for workspace_id in workspaces:
            try:
                results.append(
                    await self.rebalance(
                        workspace_id,
                        WorkspaceResourceRebalanceRequest(
                            actor_id="system",
                            automatic=True,
                            apply=True,
                            reason="Scheduled adaptive Workspace resource rebalance.",
                        ),
                    )
                )
            except AutonomousMissionError:
                continue
        return {"processed": len(results), "workspace_ids": workspaces}

    async def handle_event(self, event: Event) -> None:
        allocation_payload = dict(event.payload.get("allocation") or {})
        cycle_id = str(
            event.payload.get("cycle_id")
            or allocation_payload.get("cycle_id")
            or ""
        )
        if not cycle_id:
            return
        with self._session_factory() as session:
            row = self._reservation_by_cycle(session, cycle_id)
            if row is None:
                return
            allocation = (
                session.get(MissionResourceAllocationModel, row.allocation_id)
                if row.allocation_id
                else self._allocation_by_cycle(session, cycle_id)
            )
            if allocation is not None:
                row.actual_cost_usd = money(allocation.actual_cost_usd)
            now = utc_now()
            if event.event_type == "mission.resource.usage.recorded":
                if allocation_payload:
                    row.actual_cost_usd = money(
                        allocation_payload.get("actual_cost_usd")
                        or row.actual_cost_usd
                    )
                if row.actual_cost_usd > row.budget_usd + 1e-9:
                    row.status = WorkspaceResourceReservationStatus.EXCEEDED.value
            elif event.event_type == "mission.cycle.plan_created":
                if row.status == WorkspaceResourceReservationStatus.RESERVED.value:
                    row.status = WorkspaceResourceReservationStatus.ACTIVE.value
                    row.started_at = now
            elif event.event_type == "mission.cycle.completed":
                row.status = (
                    WorkspaceResourceReservationStatus.EXCEEDED.value
                    if row.actual_cost_usd > row.budget_usd + 1e-9
                    else WorkspaceResourceReservationStatus.CONSUMED.value
                )
                row.finished_at = now
                row.released_at = now
            elif event.event_type in {
                "mission.cycle.failed",
                "mission.cycle.cancelled",
            }:
                row.status = WorkspaceResourceReservationStatus.RELEASED.value
                row.finished_at = now
                row.released_at = now
            else:
                return
            row.version += 1
            result = self._reservation_to_dict(row)
        await self._publish(
            "mission.workspace_resource.reservation.settled",
            workspace_id=result["workspace_id"],
            correlation_id=result["mission_id"],
            payload={"reservation": result, "source_event": event.event_type},
        )

    def _summary(
        self,
        session: Session,
        workspace_id: str,
        policy: WorkspaceResourcePolicyModel | None,
        reservations: list[WorkspaceResourceReservationModel] | None = None,
    ) -> dict[str, Any]:
        reservations = reservations or list(
            session.scalars(
                select(WorkspaceResourceReservationModel).where(
                    WorkspaceResourceReservationModel.workspace_id == workspace_id
                )
            ).all()
        )
        active = [row for row in reservations if row.status in self.ACTIVE_STATUSES]
        spent = float(
            session.scalar(
                select(func.coalesce(func.sum(MissionResourceUsageModel.cost_usd), 0.0)).where(
                    MissionResourceUsageModel.workspace_id == workspace_id
                )
            )
            or 0.0
        )
        committed = sum(
            max(0.0, float(row.budget_usd) - float(row.actual_cost_usd))
            for row in active
        )
        total = float(policy.total_budget_usd) if policy is not None else 0.0
        reserve = total * float(policy.reserve_percent) / 100.0 if policy else 0.0
        allocatable = max(0.0, total - reserve)
        available = max(0.0, allocatable - spent - committed)
        return {
            "currency": policy.currency if policy is not None else "USD",
            "total_budget_usd": money(total),
            "reserve_budget_usd": money(reserve),
            "allocatable_budget_usd": money(allocatable),
            "spent_usd": money(spent),
            "committed_usd": money(committed),
            "available_usd": money(available),
            "active_reservations": len(active),
            "agent_slots_committed": sum(row.agent_slots for row in active),
            "tool_slots_committed": sum(row.tool_slots for row in active),
            "compute_units_committed": round(
                sum(float(row.compute_units) for row in active), 3
            ),
            "budget_utilization_percent": (
                round((spent + committed) / allocatable * 100.0, 3)
                if allocatable > 0
                else (100.0 if spent + committed > 0 else 0.0)
            ),
        }

    def _evaluate_reservation(
        self,
        session: Session,
        policy: WorkspaceResourcePolicyModel | None,
        row: WorkspaceResourceReservationModel,
        *,
        exclude_id: str | None,
    ) -> dict[str, Any]:
        if policy is None or not policy.enabled:
            return {"allowed": True, "reasons": [], "policy_enabled": False}
        statement = select(WorkspaceResourceReservationModel).where(
            WorkspaceResourceReservationModel.workspace_id == row.workspace_id,
            WorkspaceResourceReservationModel.status.in_(self.ACTIVE_STATUSES),
        )
        if exclude_id:
            statement = statement.where(
                WorkspaceResourceReservationModel.id != exclude_id
            )
        active = list(session.scalars(statement).all())
        spent = float(
            session.scalar(
                select(func.coalesce(func.sum(MissionResourceUsageModel.cost_usd), 0.0)).where(
                    MissionResourceUsageModel.workspace_id == row.workspace_id
                )
            )
            or 0.0
        )
        committed = sum(
            max(0.0, item.budget_usd - item.actual_cost_usd) for item in active
        )
        capacity = self._capacity(policy)
        projected_budget = spent + committed + row.budget_usd
        reasons: list[str] = []
        if policy.max_cycle_budget_usd > 0 and row.budget_usd > policy.max_cycle_budget_usd:
            reasons.append("workspace_max_cycle_budget_exceeded")
        if projected_budget > capacity["budget_usd"] + 1e-9:
            reasons.append("workspace_budget_exceeded")
        if len(active) + 1 > capacity["max_parallel_cycles"]:
            reasons.append("workspace_parallel_cycle_capacity_exceeded")
        if sum(item.agent_slots for item in active) + row.agent_slots > capacity["agent_slots"]:
            reasons.append("workspace_agent_slot_capacity_exceeded")
        if sum(item.tool_slots for item in active) + row.tool_slots > capacity["tool_slots"]:
            reasons.append("workspace_tool_slot_capacity_exceeded")
        if sum(item.compute_units for item in active) + row.compute_units > capacity["compute_units"] + 1e-9:
            reasons.append("workspace_compute_capacity_exceeded")
        available = {
            "budget_usd": money(
                max(0.0, capacity["budget_usd"] - spent - committed)
            ),
            "parallel_cycles": max(
                0, capacity["max_parallel_cycles"] - len(active)
            ),
            "agent_slots": max(
                0, capacity["agent_slots"] - sum(item.agent_slots for item in active)
            ),
            "tool_slots": max(
                0, capacity["tool_slots"] - sum(item.tool_slots for item in active)
            ),
            "compute_units": round(
                max(
                    0.0,
                    capacity["compute_units"]
                    - sum(item.compute_units for item in active),
                ),
                3,
            ),
        }
        requested = {
            "budget_usd": money(row.budget_usd),
            "parallel_cycles": 1,
            "agent_slots": row.agent_slots,
            "tool_slots": row.tool_slots,
            "compute_units": round(float(row.compute_units), 3),
        }
        shortfall = {
            key: round(max(0.0, float(requested[key]) - float(available[key])), 6)
            for key in requested
        }
        return {
            "allowed": not reasons,
            "reasons": reasons,
            "policy_enabled": True,
            "requested": requested,
            "available": available,
            "shortfall": shortfall,
            "active_reservations": len(active),
            "conflicting_reservation_ids": [item.id for item in active],
            "capacity": capacity,
        }

    def _ensure_conflict(
        self,
        session: Session,
        reservation: WorkspaceResourceReservationModel,
        admission: dict[str, Any],
    ) -> WorkspaceResourceConflictModel:
        dedupe_key = f"reservation:{reservation.id}:open"
        existing = session.scalar(
            select(WorkspaceResourceConflictModel).where(
                WorkspaceResourceConflictModel.dedupe_key == dedupe_key
            )
        )
        resource_types = sorted(
            {
                reason.removeprefix("workspace_").removesuffix("_exceeded")
                for reason in admission.get("reasons") or []
            }
        )
        severity = "critical" if "workspace_budget_exceeded" in admission.get("reasons", []) else "high"
        recommended = (
            "preempt"
            if admission.get("conflicting_reservation_ids")
            else "increase_capacity"
        )
        if existing is None:
            existing = WorkspaceResourceConflictModel(
                dedupe_key=dedupe_key,
                workspace_id=reservation.workspace_id,
                mission_id=reservation.mission_id,
                cycle_id=reservation.cycle_id,
                reservation_id=reservation.id,
                allocation_id=reservation.allocation_id,
                status=WorkspaceResourceConflictStatus.OPEN.value,
                severity=severity,
                resource_types_json=resource_types,
                requested_json=dict(admission.get("requested") or {}),
                available_json=dict(admission.get("available") or {}),
                shortfall_json=dict(admission.get("shortfall") or {}),
                conflicting_reservation_ids_json=list(
                    admission.get("conflicting_reservation_ids") or []
                ),
                recommended_action=recommended,
                metadata_json={"reasons": list(admission.get("reasons") or [])},
            )
            session.add(existing)
            session.flush()
            self._conflicts_created += 1
        else:
            existing.status = WorkspaceResourceConflictStatus.OPEN.value
            existing.resolution_action = None
            existing.resolution_reason = None
            existing.resolved_by = None
            existing.resolved_at = None
            existing.severity = severity
            existing.resource_types_json = resource_types
            existing.requested_json = dict(admission.get("requested") or {})
            existing.available_json = dict(admission.get("available") or {})
            existing.shortfall_json = dict(admission.get("shortfall") or {})
            existing.conflicting_reservation_ids_json = list(
                admission.get("conflicting_reservation_ids") or []
            )
            existing.recommended_action = recommended
        return existing

    def _preempt_lower_priority(
        self,
        session: Session,
        requested: WorkspaceResourceReservationModel,
        *,
        force: bool,
    ) -> list[str]:
        candidates = list(
            session.scalars(
                select(WorkspaceResourceReservationModel).where(
                    WorkspaceResourceReservationModel.workspace_id
                    == requested.workspace_id,
                    WorkspaceResourceReservationModel.id != requested.id,
                    WorkspaceResourceReservationModel.status.in_(
                        [
                            WorkspaceResourceReservationStatus.RESERVED.value,
                            WorkspaceResourceReservationStatus.ACTIVE.value,
                        ]
                    ),
                    WorkspaceResourceReservationModel.priority_score
                    < requested.priority_score,
                )
                .order_by(
                    WorkspaceResourceReservationModel.priority_score.asc(),
                    WorkspaceResourceReservationModel.created_at.desc(),
                )
            ).all()
        )
        released: list[str] = []
        policy = self._policy_row(session, requested.workspace_id)
        for candidate in candidates:
            if (
                candidate.status == WorkspaceResourceReservationStatus.ACTIVE.value
                and not force
            ):
                continue
            candidate.status = WorkspaceResourceReservationStatus.RELEASED.value
            candidate.released_at = utc_now()
            candidate.finished_at = candidate.released_at
            candidate.reason = (
                "Preempted by higher-priority Workspace resource request."
            )
            candidate.version += 1
            released.append(candidate.id)
            admission = self._evaluate_reservation(
                session,
                policy,
                requested,
                exclude_id=requested.id,
            )
            if admission["allowed"]:
                break
        return released

    @staticmethod
    def _reserve_row(
        row: WorkspaceResourceReservationModel,
        *,
        actor_id: str,
        reason: str,
        forced: bool,
        admission: dict[str, Any],
    ) -> None:
        row.status = WorkspaceResourceReservationStatus.RESERVED.value
        row.approved_by = actor_id
        row.approved_at = row.approved_at or utc_now()
        row.reserved_at = row.reserved_at or utc_now()
        row.reason = reason
        row.forced = row.forced or forced
        row.admission_json = {**admission, "forced": forced}
        row.version += 1

    @staticmethod
    def _capacity(policy: WorkspaceResourcePolicyModel) -> dict[str, Any]:
        allocatable = policy.total_budget_usd * (
            1.0 - policy.reserve_percent / 100.0
        )
        factor = 1.0 + policy.overcommit_tolerance_percent / 100.0
        return {
            "budget_usd": money(allocatable * factor),
            "max_parallel_cycles": max(
                1, int(round(policy.max_parallel_cycles * factor))
            ),
            "agent_slots": max(1, int(round(policy.agent_slots * factor))),
            "tool_slots": max(1, int(round(policy.tool_slots * factor))),
            "compute_units": round(policy.compute_units * factor, 3),
        }

    def _requested_values(
        self,
        policy: WorkspaceResourcePolicyModel | None,
        allocation: MissionResourceAllocationModel | None,
        request: WorkspaceResourceReservationCreate | None,
    ) -> dict[str, Any]:
        default_budget = float(
            allocation.budget_usd
            if allocation is not None
            else (policy.default_cycle_budget_usd if policy is not None else 0.0)
        )
        return {
            "budget_usd": money(
                request.budget_usd
                if request is not None and request.budget_usd is not None
                else default_budget
            ),
            "agent_slots": int(
                request.agent_slots
                if request is not None and request.agent_slots is not None
                else (allocation.agent_slots if allocation is not None else 1)
            ),
            "tool_slots": int(
                request.tool_slots
                if request is not None and request.tool_slots is not None
                else (allocation.tool_slots if allocation is not None else 1)
            ),
            "compute_units": float(
                request.compute_units
                if request is not None and request.compute_units is not None
                else (allocation.compute_units if allocation is not None else 1.0)
            ),
        }

    def _priority_score(self, session: Session, mission_id: str) -> float:
        mission = self._require_mission(session, mission_id)
        assignment = session.scalar(
            select(MissionPortfolioAssignmentModel).where(
                MissionPortfolioAssignmentModel.mission_id == mission_id
            )
        )
        if assignment is None:
            return round(float(mission.priority), 3)
        return round(
            min(100.0, max(0.0, (float(mission.priority) + assignment.score) / 2.0)),
            3,
        )

    @staticmethod
    def _policy_row(
        session: Session, workspace_id: str
    ) -> WorkspaceResourcePolicyModel | None:
        return session.scalar(
            select(WorkspaceResourcePolicyModel).where(
                WorkspaceResourcePolicyModel.workspace_id == workspace_id
            )
        )

    @staticmethod
    def _allocation_by_cycle(
        session: Session, cycle_id: str
    ) -> MissionResourceAllocationModel | None:
        return session.scalar(
            select(MissionResourceAllocationModel).where(
                MissionResourceAllocationModel.cycle_id == cycle_id
            )
        )

    @staticmethod
    def _reservation_by_cycle(
        session: Session, cycle_id: str
    ) -> WorkspaceResourceReservationModel | None:
        return session.scalar(
            select(WorkspaceResourceReservationModel).where(
                WorkspaceResourceReservationModel.cycle_id == cycle_id
            )
        )

    @staticmethod
    def _require_workspace_presence(session: Session, workspace_id: str) -> None:
        mission_id = session.scalar(
            select(WorkspaceMissionModel.id)
            .where(WorkspaceMissionModel.workspace_id == workspace_id)
            .limit(1)
        )
        if mission_id is None:
            raise MissionNotFound(
                f"Workspace {workspace_id} has no Mission and cannot be governed."
            )

    @staticmethod
    def _require_mission(session: Session, mission_id: str) -> WorkspaceMissionModel:
        row = session.get(WorkspaceMissionModel, mission_id)
        if row is None:
            raise MissionNotFound(f"Mission {mission_id} не найдена.")
        return row

    @staticmethod
    def _require_cycle(session: Session, cycle_id: str) -> MissionCycleModel:
        row = session.get(MissionCycleModel, cycle_id)
        if row is None:
            raise MissionCycleNotFound(f"Mission Cycle {cycle_id} не найден.")
        return row

    @staticmethod
    def _require_reservation(
        session: Session, reservation_id: str
    ) -> WorkspaceResourceReservationModel:
        row = session.get(WorkspaceResourceReservationModel, reservation_id)
        if row is None:
            raise WorkspaceResourceReservationNotFound(
                f"Workspace Resource Reservation {reservation_id} не найден."
            )
        return row

    @staticmethod
    def _require_conflict(
        session: Session, conflict_id: str
    ) -> WorkspaceResourceConflictModel:
        row = session.get(WorkspaceResourceConflictModel, conflict_id)
        if row is None:
            raise WorkspaceResourceConflictNotFound(
                f"Workspace Resource Conflict {conflict_id} не найден."
            )
        return row

    @staticmethod
    def _require_rebalance(
        session: Session, rebalance_id: str
    ) -> WorkspaceResourceRebalanceModel:
        row = session.get(WorkspaceResourceRebalanceModel, rebalance_id)
        if row is None:
            raise WorkspaceResourceRebalanceNotFound(
                f"Workspace Resource Rebalance {rebalance_id} не найден."
            )
        return row

    @staticmethod
    def _policy_to_dict(
        row: WorkspaceResourcePolicyModel | None,
        workspace_id: str,
    ) -> dict[str, Any]:
        if row is None:
            return {
                "id": None,
                "workspace_id": workspace_id,
                "enabled": False,
                "allocation_mode": "manual",
                "require_human_approval": True,
                "auto_rebalance_enabled": False,
                "enforce_cycle_admission": False,
                "currency": "USD",
                "total_budget_usd": 0.0,
                "default_cycle_budget_usd": 0.0,
                "max_cycle_budget_usd": 0.0,
                "reserve_percent": 10.0,
                "max_parallel_cycles": 4,
                "agent_slots": 16,
                "tool_slots": 16,
                "compute_units": 16.0,
                "min_mission_guarantee_percent": 0.0,
                "overcommit_tolerance_percent": 0.0,
                "rebalance_interval_seconds": 300,
                "last_rebalanced_at": None,
                "metadata": {},
                "created_at": None,
                "updated_at": None,
            }
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "enabled": row.enabled,
            "allocation_mode": row.allocation_mode,
            "require_human_approval": row.require_human_approval,
            "auto_rebalance_enabled": row.auto_rebalance_enabled,
            "enforce_cycle_admission": row.enforce_cycle_admission,
            "currency": row.currency,
            "total_budget_usd": money(row.total_budget_usd),
            "default_cycle_budget_usd": money(row.default_cycle_budget_usd),
            "max_cycle_budget_usd": money(row.max_cycle_budget_usd),
            "reserve_percent": row.reserve_percent,
            "max_parallel_cycles": row.max_parallel_cycles,
            "agent_slots": row.agent_slots,
            "tool_slots": row.tool_slots,
            "compute_units": row.compute_units,
            "min_mission_guarantee_percent": row.min_mission_guarantee_percent,
            "overcommit_tolerance_percent": row.overcommit_tolerance_percent,
            "rebalance_interval_seconds": row.rebalance_interval_seconds,
            "last_rebalanced_at": iso(row.last_rebalanced_at),
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _reservation_to_dict(
        row: WorkspaceResourceReservationModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "cycle_id": row.cycle_id,
            "allocation_id": row.allocation_id,
            "policy_id": row.policy_id,
            "status": row.status,
            "automatic": row.automatic,
            "actor_id": row.actor_id,
            "approved_by": row.approved_by,
            "budget_usd": money(row.budget_usd),
            "actual_cost_usd": money(row.actual_cost_usd),
            "agent_slots": row.agent_slots,
            "tool_slots": row.tool_slots,
            "compute_units": row.compute_units,
            "priority_score": row.priority_score,
            "rank": row.rank,
            "forced": row.forced,
            "reason": row.reason,
            "admission": dict(row.admission_json or {}),
            "metadata": dict(row.metadata_json or {}),
            "version": row.version,
            "created_at": iso(row.created_at),
            "approved_at": iso(row.approved_at),
            "reserved_at": iso(row.reserved_at),
            "started_at": iso(row.started_at),
            "finished_at": iso(row.finished_at),
            "released_at": iso(row.released_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _conflict_to_dict(row: WorkspaceResourceConflictModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "dedupe_key": row.dedupe_key,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "cycle_id": row.cycle_id,
            "reservation_id": row.reservation_id,
            "allocation_id": row.allocation_id,
            "status": row.status,
            "severity": row.severity,
            "resource_types": list(row.resource_types_json or []),
            "requested": dict(row.requested_json or {}),
            "available": dict(row.available_json or {}),
            "shortfall": dict(row.shortfall_json or {}),
            "conflicting_reservation_ids": list(
                row.conflicting_reservation_ids_json or []
            ),
            "recommended_action": row.recommended_action,
            "resolution_action": row.resolution_action,
            "resolution_reason": row.resolution_reason,
            "resolved_by": row.resolved_by,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "resolved_at": iso(row.resolved_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _rebalance_to_dict(row: WorkspaceResourceRebalanceModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "policy_id": row.policy_id,
            "status": row.status,
            "actor_id": row.actor_id,
            "automatic": row.automatic,
            "reason": row.reason,
            "snapshot": dict(row.snapshot_json or {}),
            "proposal": dict(row.proposal_json or {}),
            "applied": dict(row.applied_json or {}),
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "applied_at": iso(row.applied_at),
            "updated_at": iso(row.updated_at),
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
                source="workspace_resource_coordinator",
                workspace_id=workspace_id,
                correlation_id=correlation_id,
                payload=payload,
            )
        )
