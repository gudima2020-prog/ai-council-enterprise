from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from statistics import mean
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.autonomy.models import (
    MissionCapacityPlanModel,
    MissionCycleModel,
    MissionResourceAllocationModel,
    MissionResourcePolicyModel,
    MissionResourceUsageModel,
    MissionStrategyAssignmentModel,
    MissionStrategyModel,
    WorkspaceMissionModel,
)
from backend.autonomy.resources_schemas import (
    MissionCapacityPlanDecision,
    MissionCapacityPlanRequest,
    MissionCapacityPlanStatus,
    MissionResourceAllocationApprove,
    MissionResourceAllocationCancel,
    MissionResourceAllocationCreate,
    MissionResourceAllocationMode,
    MissionResourceAllocationStatus,
    MissionResourcePolicyUpsert,
    MissionResourceUsageCreate,
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


class MissionResourceAllocationNotFound(AutonomousMissionError):
    pass


class MissionCapacityPlanNotFound(AutonomousMissionError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def round_money(value: float) -> float:
    return round(max(0.0, float(value)), 6)


class MissionResourceService:
    """Mission-level budget and capacity governance.

    Resource governance is disabled by default. When enabled, a cycle must have
    an approved allocation before planning starts. Automatic allocation and
    adaptive rebalancing require explicit policy flags.
    """

    ACTIVE_STATUSES = {
        MissionResourceAllocationStatus.APPROVED.value,
        MissionResourceAllocationStatus.RESERVED.value,
        MissionResourceAllocationStatus.ACTIVE.value,
    }
    TERMINAL_STATUSES = {
        MissionResourceAllocationStatus.RELEASED.value,
        MissionResourceAllocationStatus.CONSUMED.value,
        MissionResourceAllocationStatus.EXCEEDED.value,
        MissionResourceAllocationStatus.CANCELLED.value,
    }

    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._allocations_created = 0
        self._allocations_reserved = 0
        self._usage_entries = 0
        self._capacity_plans = 0
        self._automatic_rebalances = 0

    def status(self) -> dict[str, Any]:
        with self._session_factory() as session:
            policies = int(
                session.scalar(select(func.count()).select_from(MissionResourcePolicyModel))
                or 0
            )
            allocations = int(
                session.scalar(select(func.count()).select_from(MissionResourceAllocationModel))
                or 0
            )
            active = int(
                session.scalar(
                    select(func.count())
                    .select_from(MissionResourceAllocationModel)
                    .where(MissionResourceAllocationModel.status.in_(self.ACTIVE_STATUSES))
                )
                or 0
            )
            usage = int(
                session.scalar(select(func.count()).select_from(MissionResourceUsageModel))
                or 0
            )
        return {
            "policies": policies,
            "allocations": allocations,
            "active_allocations": active,
            "usage_entries": usage,
            "allocations_created": self._allocations_created,
            "allocations_reserved": self._allocations_reserved,
            "usage_recorded": self._usage_entries,
            "capacity_plans_created": self._capacity_plans,
            "automatic_rebalances": self._automatic_rebalances,
            "default_safety": {
                "enabled": False,
                "allocation_mode": "manual",
                "require_human_approval": True,
                "auto_allocation_enabled": False,
                "auto_rebalance_enabled": False,
            },
            "capabilities": [
                "mission_budget_governance",
                "cycle_resource_allocations",
                "resource_usage_ledger",
                "capacity_admission_control",
                "strategy_budget_context",
                "adaptive_capacity_recommendations",
                "safe_manual_approval",
            ],
        }

    def get_policy(self, mission_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            policy = session.scalar(
                select(MissionResourcePolicyModel).where(
                    MissionResourcePolicyModel.mission_id == mission_id
                )
            )
            return self._policy_to_dict(policy, mission)

    async def upsert_policy(
        self,
        mission_id: str,
        request: MissionResourcePolicyUpsert,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            row = session.scalar(
                select(MissionResourcePolicyModel).where(
                    MissionResourcePolicyModel.mission_id == mission_id
                )
            )
            if row is None:
                row = MissionResourcePolicyModel(
                    workspace_id=mission.workspace_id,
                    mission_id=mission.id,
                )
                session.add(row)
            values = request.model_dump()
            for field, value in values.items():
                target = "metadata_json" if field == "metadata" else field
                if field == "allocation_mode":
                    value = value.value
                setattr(row, target, value)
            result = self._policy_to_dict(row, mission)

        await self._publish(
            "mission.resource.policy.updated",
            workspace_id=result["workspace_id"],
            mission_id=mission_id,
            payload={"policy": result},
        )
        return result

    def dashboard(self, mission_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            policy = session.scalar(
                select(MissionResourcePolicyModel).where(
                    MissionResourcePolicyModel.mission_id == mission_id
                )
            )
            allocations = list(
                session.scalars(
                    select(MissionResourceAllocationModel)
                    .where(MissionResourceAllocationModel.mission_id == mission_id)
                    .order_by(MissionResourceAllocationModel.created_at.desc())
                ).all()
            )
            usage_rows = list(
                session.scalars(
                    select(MissionResourceUsageModel).where(
                        MissionResourceUsageModel.mission_id == mission_id
                    )
                ).all()
            )
            plans = list(
                session.scalars(
                    select(MissionCapacityPlanModel)
                    .where(MissionCapacityPlanModel.mission_id == mission_id)
                    .order_by(MissionCapacityPlanModel.created_at.desc())
                    .limit(10)
                ).all()
            )
            summary = self._summary(policy, allocations, usage_rows)
            return {
                "mission_id": mission.id,
                "workspace_id": mission.workspace_id,
                "policy": self._policy_to_dict(policy, mission),
                "summary": summary,
                "allocations": [self._allocation_to_dict(row) for row in allocations[:50]],
                "recent_capacity_plans": [self._capacity_plan_to_dict(row) for row in plans],
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
                select(MissionResourcePolicyModel).where(
                    MissionResourcePolicyModel.mission_id == mission_id
                )
            )
            allocations = list(
                session.scalars(
                    select(MissionResourceAllocationModel)
                    .where(MissionResourceAllocationModel.mission_id == mission_id)
                    .order_by(MissionResourceAllocationModel.created_at.desc())
                    .limit(max(1, min(limit, 100)))
                ).all()
            )
            usage_rows = list(
                session.scalars(
                    select(MissionResourceUsageModel).where(
                        MissionResourceUsageModel.mission_id == mission_id
                    )
                ).all()
            )
            cycle_allocation = None
            if cycle_id:
                cycle_allocation = session.scalar(
                    select(MissionResourceAllocationModel).where(
                        MissionResourceAllocationModel.cycle_id == cycle_id
                    )
                )
            selected_strategy = session.scalar(
                select(MissionStrategyModel).where(
                    MissionStrategyModel.mission_id == mission_id,
                    MissionStrategyModel.status == "selected",
                )
            )
            return {
                "mission_id": mission.id,
                "workspace_id": mission.workspace_id,
                "policy": self._policy_to_dict(policy, mission),
                "summary": self._summary(policy, allocations, usage_rows),
                "cycle_allocation": (
                    self._allocation_to_dict(cycle_allocation)
                    if cycle_allocation is not None
                    else None
                ),
                "selected_strategy_budget_signal": (
                    {
                        "strategy_id": selected_strategy.id,
                        "strategy_key": selected_strategy.strategy_key,
                        "cost_percent": selected_strategy.cost_percent,
                        "risk_percent": selected_strategy.risk_percent,
                        "duration_percent": selected_strategy.duration_percent,
                    }
                    if selected_strategy is not None
                    else None
                ),
                "recent_allocations": [self._allocation_to_dict(row) for row in allocations],
            }

    async def create_allocation(
        self,
        cycle_id: str,
        request: MissionResourceAllocationCreate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            cycle = self._require_cycle(session, cycle_id)
            mission = self._require_mission(session, cycle.mission_id)
            existing = session.scalar(
                select(MissionResourceAllocationModel).where(
                    MissionResourceAllocationModel.cycle_id == cycle_id
                )
            )
            if existing is not None:
                raise MissionStateError("Для Mission Cycle уже существует Resource Allocation.")
            policy = session.scalar(
                select(MissionResourcePolicyModel).where(
                    MissionResourcePolicyModel.mission_id == mission.id
                )
            )
            strategy_id = request.strategy_id or self._cycle_strategy_id(session, cycle_id)
            self._validate_strategy(session, mission.id, strategy_id)
            budget = (
                float(request.budget_usd)
                if request.budget_usd is not None
                else float(policy.default_cycle_budget_usd if policy is not None else 0.0)
            )
            require_approval = bool(request.request_approval)
            if policy is not None and policy.require_human_approval:
                require_approval = True
            status = (
                MissionResourceAllocationStatus.PENDING_APPROVAL.value
                if require_approval
                else MissionResourceAllocationStatus.APPROVED.value
            )
            row = MissionResourceAllocationModel(
                workspace_id=mission.workspace_id,
                mission_id=mission.id,
                cycle_id=cycle.id,
                strategy_id=strategy_id,
                status=status,
                automatic=request.automatic,
                requested_by=request.requested_by,
                budget_usd=round_money(budget),
                agent_slots=request.agent_slots,
                tool_slots=request.tool_slots,
                compute_units=request.compute_units,
                rationale=request.rationale,
                metadata_json=dict(request.metadata),
            )
            admission = self._evaluate_allocation(session, policy, row, exclude_id=None)
            row.admission_json = admission
            session.add(row)
            session.flush()
            result = self._allocation_to_dict(row)

        self._allocations_created += 1
        await self._publish(
            "mission.resource.allocation.created",
            workspace_id=result["workspace_id"],
            mission_id=result["mission_id"],
            payload={"allocation": result},
        )
        return result

    def get_allocation(self, allocation_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(MissionResourceAllocationModel, allocation_id)
            return None if row is None else self._allocation_to_dict(row)

    def list_allocations(
        self,
        mission_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_mission(session, mission_id)
            statement = select(MissionResourceAllocationModel).where(
                MissionResourceAllocationModel.mission_id == mission_id
            )
            if status:
                statement = statement.where(MissionResourceAllocationModel.status == status)
            rows = list(
                session.scalars(
                    statement.order_by(MissionResourceAllocationModel.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._allocation_to_dict(row) for row in rows]

    async def approve_allocation(
        self,
        allocation_id: str,
        request: MissionResourceAllocationApprove,
    ) -> dict[str, Any]:
        publish_events: list[tuple[str, dict[str, Any]]] = []
        with self._session_factory() as session:
            row = self._require_allocation(session, allocation_id)
            if row.status in self.TERMINAL_STATUSES:
                raise MissionStateError("Нельзя подтвердить завершённый Resource Allocation.")
            policy = self._policy_row(session, row.mission_id)
            admission = self._evaluate_allocation(session, policy, row, exclude_id=row.id)
            if not admission["allowed"] and not request.force:
                raise MissionStateError(
                    "Resource Allocation превышает бюджет или доступную мощность: "
                    + ", ".join(admission["reasons"])
                )
            now = utc_now()
            row.status = MissionResourceAllocationStatus.APPROVED.value
            row.approved_by = request.actor_id
            row.approved_at = now
            row.rationale = request.rationale
            row.admission_json = {**admission, "forced": request.force}
            metadata = dict(row.metadata_json or {})
            metadata.update(request.metadata)
            row.metadata_json = metadata
            publish_events.append(("mission.resource.allocation.approved", {}))
            if request.reserve_now:
                row.status = MissionResourceAllocationStatus.RESERVED.value
                row.reserved_at = now
                self._allocations_reserved += 1
                publish_events.append(("mission.resource.allocation.reserved", {}))
            result = self._allocation_to_dict(row)

        for event_type, payload in publish_events:
            await self._publish(
                event_type,
                workspace_id=result["workspace_id"],
                mission_id=result["mission_id"],
                payload={"allocation": result, **payload},
            )
        return result

    async def cancel_allocation(
        self,
        allocation_id: str,
        request: MissionResourceAllocationCancel,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_allocation(session, allocation_id)
            if row.status in self.TERMINAL_STATUSES:
                return self._allocation_to_dict(row)
            row.status = MissionResourceAllocationStatus.CANCELLED.value
            row.finished_at = utc_now()
            row.released_at = row.finished_at
            row.rationale = request.reason
            metadata = dict(row.metadata_json or {})
            metadata["cancelled_by"] = request.actor_id
            row.metadata_json = metadata
            result = self._allocation_to_dict(row)
        await self._publish(
            "mission.resource.allocation.cancelled",
            workspace_id=result["workspace_id"],
            mission_id=result["mission_id"],
            payload={"allocation": result},
        )
        return result

    async def prepare_cycle(
        self,
        cycle_id: str,
        *,
        actor_id: str = "system",
        force: bool = False,
    ) -> dict[str, Any]:
        created = False
        reserved = False
        with self._session_factory() as session:
            cycle = self._require_cycle(session, cycle_id)
            mission = self._require_mission(session, cycle.mission_id)
            policy = self._policy_row(session, mission.id)
            if policy is None or not policy.enabled:
                return {
                    "allowed": True,
                    "governed": False,
                    "mission_id": mission.id,
                    "cycle_id": cycle.id,
                    "reasons": [],
                    "allocation": None,
                }
            row = session.scalar(
                select(MissionResourceAllocationModel).where(
                    MissionResourceAllocationModel.cycle_id == cycle_id
                )
            )
            if row is None:
                if not policy.auto_allocation_enabled and not force:
                    return {
                        "allowed": False,
                        "governed": True,
                        "mission_id": mission.id,
                        "cycle_id": cycle.id,
                        "reasons": ["resource_allocation_required"],
                        "allocation": None,
                    }
                row = MissionResourceAllocationModel(
                    workspace_id=mission.workspace_id,
                    mission_id=mission.id,
                    cycle_id=cycle.id,
                    strategy_id=self._cycle_strategy_id(session, cycle_id),
                    status=MissionResourceAllocationStatus.APPROVED.value,
                    automatic=True,
                    requested_by=actor_id,
                    approved_by=actor_id,
                    budget_usd=round_money(policy.default_cycle_budget_usd),
                    agent_slots=1,
                    tool_slots=1,
                    compute_units=min(1.0, policy.compute_units),
                    rationale="Automatic Mission Cycle resource allocation.",
                    approved_at=utc_now(),
                    metadata_json={"forced": force},
                )
                session.add(row)
                session.flush()
                created = True
                self._allocations_created += 1
            if row.status == MissionResourceAllocationStatus.PENDING_APPROVAL.value:
                if not force:
                    return {
                        "allowed": False,
                        "governed": True,
                        "mission_id": mission.id,
                        "cycle_id": cycle.id,
                        "reasons": ["resource_allocation_approval_required"],
                        "allocation": self._allocation_to_dict(row),
                    }
                row.status = MissionResourceAllocationStatus.APPROVED.value
                row.approved_by = actor_id
                row.approved_at = utc_now()
            admission = self._evaluate_allocation(session, policy, row, exclude_id=row.id)
            if not admission["allowed"] and not force:
                row.admission_json = admission
                return {
                    **admission,
                    "governed": True,
                    "mission_id": mission.id,
                    "cycle_id": cycle.id,
                    "allocation": self._allocation_to_dict(row),
                }
            if row.status in {
                MissionResourceAllocationStatus.APPROVED.value,
                MissionResourceAllocationStatus.PENDING_APPROVAL.value,
            }:
                row.status = MissionResourceAllocationStatus.RESERVED.value
                row.reserved_at = utc_now()
                reserved = True
                self._allocations_reserved += 1
            row.admission_json = {**admission, "forced": force}
            result = {
                "allowed": True,
                "governed": True,
                "mission_id": mission.id,
                "cycle_id": cycle.id,
                "reasons": admission["reasons"],
                "forced": force,
                "allocation": self._allocation_to_dict(row),
            }

        if created:
            await self._publish(
                "mission.resource.allocation.created",
                workspace_id=result["allocation"]["workspace_id"],
                mission_id=result["mission_id"],
                payload={"allocation": result["allocation"]},
            )
        if reserved:
            await self._publish(
                "mission.resource.allocation.reserved",
                workspace_id=result["allocation"]["workspace_id"],
                mission_id=result["mission_id"],
                payload={"allocation": result["allocation"]},
            )
        return result

    async def record_usage(
        self,
        allocation_id: str,
        request: MissionResourceUsageCreate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            allocation = self._require_allocation(session, allocation_id)
            existing = session.scalar(
                select(MissionResourceUsageModel).where(
                    MissionResourceUsageModel.mission_id == allocation.mission_id,
                    MissionResourceUsageModel.idempotency_key == request.idempotency_key,
                )
            )
            if existing is not None:
                return {
                    "usage": self._usage_to_dict(existing),
                    "allocation": self._allocation_to_dict(allocation),
                    "idempotent_replay": True,
                }
            occurred_at = utc_now()
            if request.occurred_at:
                try:
                    occurred_at = datetime.fromisoformat(request.occurred_at.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise AutonomousMissionError("occurred_at должен быть ISO-8601 datetime.") from exc
            row = MissionResourceUsageModel(
                workspace_id=allocation.workspace_id,
                mission_id=allocation.mission_id,
                cycle_id=allocation.cycle_id,
                allocation_id=allocation.id,
                strategy_id=allocation.strategy_id,
                category=request.category.value,
                quantity=request.quantity,
                unit=request.unit,
                cost_usd=round_money(request.cost_usd),
                source_type=request.source_type,
                source_ref=request.source_ref,
                idempotency_key=request.idempotency_key,
                actor_id=request.actor_id,
                details_json=dict(request.details),
                occurred_at=occurred_at,
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(MissionResourceUsageModel).where(
                        MissionResourceUsageModel.mission_id == allocation.mission_id,
                        MissionResourceUsageModel.idempotency_key == request.idempotency_key,
                    )
                )
                if existing is None:
                    raise
                return {
                    "usage": self._usage_to_dict(existing),
                    "allocation": self._allocation_to_dict(allocation),
                    "idempotent_replay": True,
                }
            allocation.actual_cost_usd = round_money(allocation.actual_cost_usd + row.cost_usd)
            policy = self._policy_row(session, allocation.mission_id)
            tolerance = float(policy.overrun_tolerance_percent if policy is not None else 0.0)
            allowed_cost = allocation.budget_usd * (1.0 + tolerance / 100.0)
            if allocation.actual_cost_usd > allowed_cost and allocation.budget_usd >= 0:
                allocation.status = MissionResourceAllocationStatus.EXCEEDED.value
            usage_result = self._usage_to_dict(row)
            allocation_result = self._allocation_to_dict(allocation)

        self._usage_entries += 1
        await self._publish(
            "mission.resource.usage.recorded",
            workspace_id=allocation_result["workspace_id"],
            mission_id=allocation_result["mission_id"],
            payload={"usage": usage_result, "allocation": allocation_result},
        )
        if allocation_result["status"] == MissionResourceAllocationStatus.EXCEEDED.value:
            await self._publish(
                "mission.resource.budget.exceeded",
                workspace_id=allocation_result["workspace_id"],
                mission_id=allocation_result["mission_id"],
                payload={"allocation": allocation_result},
            )
        return {
            "usage": usage_result,
            "allocation": allocation_result,
            "idempotent_replay": False,
        }

    def list_usage(
        self,
        mission_id: str,
        *,
        allocation_id: str | None = None,
        category: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_mission(session, mission_id)
            statement = select(MissionResourceUsageModel).where(
                MissionResourceUsageModel.mission_id == mission_id
            )
            if allocation_id:
                statement = statement.where(MissionResourceUsageModel.allocation_id == allocation_id)
            if category:
                statement = statement.where(MissionResourceUsageModel.category == category)
            rows = list(
                session.scalars(
                    statement.order_by(MissionResourceUsageModel.occurred_at.desc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._usage_to_dict(row) for row in rows]

    async def create_capacity_plan(
        self,
        mission_id: str,
        request: MissionCapacityPlanRequest,
    ) -> dict[str, Any]:
        applied = False
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            policy = self._policy_row(session, mission_id)
            if policy is None:
                raise MissionStateError("Сначала создай Mission Resource Policy.")
            horizon = request.sample_cycles or policy.planning_horizon_cycles
            allocations = list(
                session.scalars(
                    select(MissionResourceAllocationModel)
                    .where(
                        MissionResourceAllocationModel.mission_id == mission_id,
                        MissionResourceAllocationModel.status.in_(self.TERMINAL_STATUSES),
                    )
                    .order_by(MissionResourceAllocationModel.finished_at.desc())
                    .limit(horizon)
                ).all()
            )
            actual_costs = [float(row.actual_cost_usd) for row in allocations if row.actual_cost_usd > 0]
            budgets = [float(row.budget_usd) for row in allocations]
            sample_count = len(allocations)
            if actual_costs:
                sorted_costs = sorted(actual_costs)
                p80_index = min(len(sorted_costs) - 1, max(0, int(round((len(sorted_costs) - 1) * 0.8))))
                recommended_budget = max(mean(actual_costs) * 1.15, sorted_costs[p80_index])
            elif budgets:
                recommended_budget = mean(budgets)
            else:
                recommended_budget = policy.default_cycle_budget_usd
            if policy.max_cycle_budget_usd > 0:
                recommended_budget = min(recommended_budget, policy.max_cycle_budget_usd)
            recommended_budget = round_money(recommended_budget)
            success_count = sum(
                1 for row in allocations if row.status == MissionResourceAllocationStatus.CONSUMED.value
            )
            success_rate = (success_count / sample_count * 100.0) if sample_count else 0.0
            recommended_parallel = policy.max_parallel_cycles
            if sample_count >= 3 and success_rate >= 80 and policy.agent_slots >= 2:
                recommended_parallel = min(policy.max_parallel_cycles + 1, policy.agent_slots)
            elif sample_count >= 3 and success_rate < 50:
                recommended_parallel = 1
            avg_agents = mean([row.agent_slots for row in allocations]) if allocations else 1.0
            avg_tools = mean([row.tool_slots for row in allocations]) if allocations else 1.0
            avg_compute = mean([row.compute_units for row in allocations]) if allocations else 1.0
            confidence = min(100.0, sample_count / max(1, horizon) * 100.0)
            calculation = {
                "sample_count": sample_count,
                "horizon": horizon,
                "average_actual_cost_usd": round_money(mean(actual_costs)) if actual_costs else 0.0,
                "average_budget_usd": round_money(mean(budgets)) if budgets else 0.0,
                "success_rate_percent": round(success_rate, 3),
                "current_default_cycle_budget_usd": policy.default_cycle_budget_usd,
            }
            row = MissionCapacityPlanModel(
                workspace_id=mission.workspace_id,
                mission_id=mission.id,
                status=MissionCapacityPlanStatus.RECOMMENDED.value,
                actor_id=request.actor_id,
                automatic=request.actor_id == "system",
                sample_cycles=sample_count,
                confidence_percent=round(confidence, 3),
                recommended_cycle_budget_usd=recommended_budget,
                recommended_parallel_cycles=max(1, int(recommended_parallel)),
                recommended_agent_slots=max(1, int(round(avg_agents))),
                recommended_tool_slots=max(1, int(round(avg_tools))),
                recommended_compute_units=max(0.0, round(float(avg_compute), 3)),
                rationale=request.rationale,
                calculation_json=calculation,
                metadata_json=dict(request.metadata),
            )
            session.add(row)
            session.flush()
            if request.apply:
                improvement = self._improvement_percent(
                    policy.default_cycle_budget_usd,
                    row.recommended_cycle_budget_usd,
                )
                allowed = request.force or (
                    policy.auto_rebalance_enabled
                    and policy.allocation_mode == MissionResourceAllocationMode.ADAPTIVE.value
                    and improvement >= policy.min_rebalance_improvement_percent
                )
                if allowed:
                    self._apply_plan_row(policy, row)
                    applied = True
                    if row.automatic:
                        self._automatic_rebalances += 1
                else:
                    metadata = dict(row.metadata_json or {})
                    metadata["application_blocked_reason"] = (
                        "policy_disabled_or_improvement_below_threshold"
                    )
                    row.metadata_json = metadata
            result = self._capacity_plan_to_dict(row)
            policy_result = self._policy_to_dict(policy, mission)

        self._capacity_plans += 1
        await self._publish(
            "mission.resource.capacity.recommended",
            workspace_id=result["workspace_id"],
            mission_id=mission_id,
            payload={"capacity_plan": result},
        )
        if applied:
            await self._publish(
                "mission.resource.capacity.applied",
                workspace_id=result["workspace_id"],
                mission_id=mission_id,
                payload={"capacity_plan": result, "policy": policy_result},
            )
        return {"capacity_plan": result, "policy": policy_result, "applied": applied}

    async def apply_capacity_plan(
        self,
        plan_id: str,
        request: MissionCapacityPlanDecision,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_capacity_plan(session, plan_id)
            if row.status == MissionCapacityPlanStatus.APPLIED.value:
                mission = self._require_mission(session, row.mission_id)
                policy = self._policy_row(session, row.mission_id)
                return {
                    "capacity_plan": self._capacity_plan_to_dict(row),
                    "policy": self._policy_to_dict(policy, mission),
                    "applied": True,
                }
            if row.status == MissionCapacityPlanStatus.REJECTED.value:
                raise MissionStateError("Отклонённый Capacity Plan нельзя применить.")
            policy = self._policy_row(session, row.mission_id)
            if policy is None:
                raise MissionStateError("Mission Resource Policy не найдена.")
            if not request.force and policy.require_human_approval and request.actor_id == "system":
                raise MissionStateError("Для применения Capacity Plan требуется пользователь.")
            self._apply_plan_row(policy, row)
            metadata = dict(row.metadata_json or {})
            metadata["applied_by"] = request.actor_id
            metadata["application_reason"] = request.reason
            row.metadata_json = metadata
            mission = self._require_mission(session, row.mission_id)
            result = self._capacity_plan_to_dict(row)
            policy_result = self._policy_to_dict(policy, mission)
        await self._publish(
            "mission.resource.capacity.applied",
            workspace_id=result["workspace_id"],
            mission_id=result["mission_id"],
            payload={"capacity_plan": result, "policy": policy_result},
        )
        return {"capacity_plan": result, "policy": policy_result, "applied": True}

    async def reject_capacity_plan(
        self,
        plan_id: str,
        request: MissionCapacityPlanDecision,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_capacity_plan(session, plan_id)
            if row.status == MissionCapacityPlanStatus.APPLIED.value:
                raise MissionStateError("Применённый Capacity Plan нельзя отклонить.")
            row.status = MissionCapacityPlanStatus.REJECTED.value
            metadata = dict(row.metadata_json or {})
            metadata["rejected_by"] = request.actor_id
            metadata["rejection_reason"] = request.reason
            row.metadata_json = metadata
            result = self._capacity_plan_to_dict(row)
        await self._publish(
            "mission.resource.capacity.rejected",
            workspace_id=result["workspace_id"],
            mission_id=result["mission_id"],
            payload={"capacity_plan": result},
        )
        return result

    def list_capacity_plans(
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
                    select(MissionCapacityPlanModel)
                    .where(MissionCapacityPlanModel.mission_id == mission_id)
                    .order_by(MissionCapacityPlanModel.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._capacity_plan_to_dict(row) for row in rows]

    async def handle_event(self, event: Event) -> None:
        if event.event_type not in {
            "mission.cycle.plan_created",
            "mission.cycle.completed",
            "mission.cycle.failed",
            "mission.cycle.cancelled",
        }:
            return
        cycle_id = str(event.payload.get("cycle_id") or "")
        if not cycle_id:
            return
        with self._session_factory() as session:
            row = session.scalar(
                select(MissionResourceAllocationModel).where(
                    MissionResourceAllocationModel.cycle_id == cycle_id
                )
            )
            if row is None:
                return
            now = utc_now()
            if event.event_type == "mission.cycle.plan_created":
                if row.status in {
                    MissionResourceAllocationStatus.APPROVED.value,
                    MissionResourceAllocationStatus.RESERVED.value,
                }:
                    row.status = MissionResourceAllocationStatus.ACTIVE.value
                    row.started_at = now
            elif event.event_type == "mission.cycle.completed":
                if row.status != MissionResourceAllocationStatus.EXCEEDED.value:
                    row.status = MissionResourceAllocationStatus.CONSUMED.value
                row.finished_at = now
                row.released_at = now
            elif event.event_type in {"mission.cycle.failed", "mission.cycle.cancelled"}:
                if row.status != MissionResourceAllocationStatus.EXCEEDED.value:
                    row.status = MissionResourceAllocationStatus.RELEASED.value
                row.finished_at = now
                row.released_at = now
            result = self._allocation_to_dict(row)
        await self._publish(
            "mission.resource.allocation.settled",
            workspace_id=result["workspace_id"],
            mission_id=result["mission_id"],
            payload={"allocation": result, "source_event": event.event_type},
        )
        if event.event_type in {
            "mission.cycle.completed",
            "mission.cycle.failed",
            "mission.cycle.cancelled",
        }:
            with self._session_factory() as session:
                policy = self._policy_row(session, result["mission_id"])
                adaptive = bool(
                    policy is not None
                    and policy.enabled
                    and policy.auto_rebalance_enabled
                    and policy.allocation_mode
                    == MissionResourceAllocationMode.ADAPTIVE.value
                )
            if adaptive:
                await self.create_capacity_plan(
                    result["mission_id"],
                    MissionCapacityPlanRequest(
                        actor_id="system",
                        apply=True,
                        rationale=(
                            "Automatic capacity update after Mission Cycle settlement."
                        ),
                        metadata={
                            "source_event": event.event_type,
                            "cycle_id": cycle_id,
                        },
                    ),
                )

    def _summary(
        self,
        policy: MissionResourcePolicyModel | None,
        allocations: list[MissionResourceAllocationModel],
        usage_rows: list[MissionResourceUsageModel],
    ) -> dict[str, Any]:
        spent = round_money(sum(float(row.cost_usd) for row in usage_rows))
        active = [row for row in allocations if row.status in self.ACTIVE_STATUSES]
        committed = round_money(
            sum(max(0.0, float(row.budget_usd) - float(row.actual_cost_usd)) for row in active)
        )
        total = float(policy.total_budget_usd) if policy is not None else 0.0
        reserve = round_money(total * float(policy.reserve_percent) / 100.0) if policy else 0.0
        allocatable = round_money(max(0.0, total - reserve))
        available = round_money(max(0.0, allocatable - spent - committed))
        return {
            "currency": policy.currency if policy is not None else "USD",
            "total_budget_usd": round_money(total),
            "reserve_budget_usd": reserve,
            "allocatable_budget_usd": allocatable,
            "spent_usd": spent,
            "committed_usd": committed,
            "available_usd": available,
            "active_allocations": len(active),
            "agent_slots_committed": sum(row.agent_slots for row in active),
            "tool_slots_committed": sum(row.tool_slots for row in active),
            "compute_units_committed": round(sum(float(row.compute_units) for row in active), 3),
            "budget_utilization_percent": (
                round((spent + committed) / allocatable * 100.0, 3)
                if allocatable > 0
                else (100.0 if spent + committed > 0 else 0.0)
            ),
        }

    def _evaluate_allocation(
        self,
        session: Session,
        policy: MissionResourcePolicyModel | None,
        allocation: MissionResourceAllocationModel,
        *,
        exclude_id: str | None,
    ) -> dict[str, Any]:
        if policy is None or not policy.enabled:
            return {"allowed": True, "reasons": [], "policy_enabled": False}
        statement = select(MissionResourceAllocationModel).where(
            MissionResourceAllocationModel.mission_id == allocation.mission_id,
            MissionResourceAllocationModel.status.in_(self.ACTIVE_STATUSES),
        )
        if exclude_id:
            statement = statement.where(MissionResourceAllocationModel.id != exclude_id)
        active = list(session.scalars(statement).all())
        spent = float(
            session.scalar(
                select(func.coalesce(func.sum(MissionResourceUsageModel.cost_usd), 0.0)).where(
                    MissionResourceUsageModel.mission_id == allocation.mission_id
                )
            )
            or 0.0
        )
        committed = sum(max(0.0, row.budget_usd - row.actual_cost_usd) for row in active)
        allocatable = policy.total_budget_usd * (1.0 - policy.reserve_percent / 100.0)
        projected = spent + committed + allocation.budget_usd
        reasons: list[str] = []
        if policy.max_cycle_budget_usd > 0 and allocation.budget_usd > policy.max_cycle_budget_usd:
            reasons.append("max_cycle_budget_exceeded")
        if projected > allocatable + 1e-9:
            reasons.append("mission_budget_exceeded")
        if len(active) >= policy.max_parallel_cycles:
            reasons.append("parallel_cycle_capacity_exceeded")
        if sum(row.agent_slots for row in active) + allocation.agent_slots > policy.agent_slots:
            reasons.append("agent_slot_capacity_exceeded")
        if sum(row.tool_slots for row in active) + allocation.tool_slots > policy.tool_slots:
            reasons.append("tool_slot_capacity_exceeded")
        if sum(row.compute_units for row in active) + allocation.compute_units > policy.compute_units + 1e-9:
            reasons.append("compute_capacity_exceeded")
        return {
            "allowed": not reasons,
            "reasons": reasons,
            "policy_enabled": True,
            "projected_budget_usd": round_money(projected),
            "allocatable_budget_usd": round_money(allocatable),
            "active_allocations": len(active),
            "max_parallel_cycles": policy.max_parallel_cycles,
        }

    def _apply_plan_row(
        self,
        policy: MissionResourcePolicyModel,
        plan: MissionCapacityPlanModel,
    ) -> None:
        policy.default_cycle_budget_usd = plan.recommended_cycle_budget_usd
        policy.max_parallel_cycles = plan.recommended_parallel_cycles
        policy.agent_slots = max(policy.agent_slots, plan.recommended_agent_slots)
        policy.tool_slots = max(policy.tool_slots, plan.recommended_tool_slots)
        policy.compute_units = max(policy.compute_units, plan.recommended_compute_units)
        plan.status = MissionCapacityPlanStatus.APPLIED.value
        plan.applied_at = utc_now()

    @staticmethod
    def _improvement_percent(old: float, new: float) -> float:
        baseline = max(abs(float(old)), 1.0)
        return abs(float(new) - float(old)) / baseline * 100.0

    def _policy_row(
        self, session: Session, mission_id: str
    ) -> MissionResourcePolicyModel | None:
        return session.scalar(
            select(MissionResourcePolicyModel).where(
                MissionResourcePolicyModel.mission_id == mission_id
            )
        )

    def _cycle_strategy_id(self, session: Session, cycle_id: str) -> str | None:
        assignment = session.scalar(
            select(MissionStrategyAssignmentModel).where(
                MissionStrategyAssignmentModel.cycle_id == cycle_id
            )
        )
        if assignment is not None:
            return assignment.strategy_id
        cycle = self._require_cycle(session, cycle_id)
        strategy = session.scalar(
            select(MissionStrategyModel).where(
                MissionStrategyModel.mission_id == cycle.mission_id,
                MissionStrategyModel.status == "selected",
            )
        )
        return strategy.id if strategy is not None else None

    def _validate_strategy(
        self, session: Session, mission_id: str, strategy_id: str | None
    ) -> None:
        if not strategy_id:
            return
        strategy = session.get(MissionStrategyModel, strategy_id)
        if strategy is None or strategy.mission_id != mission_id:
            raise MissionStateError("Strategy не принадлежит Mission.")

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
    def _require_allocation(
        session: Session, allocation_id: str
    ) -> MissionResourceAllocationModel:
        row = session.get(MissionResourceAllocationModel, allocation_id)
        if row is None:
            raise MissionResourceAllocationNotFound(
                f"Mission Resource Allocation {allocation_id} не найден."
            )
        return row

    @staticmethod
    def _require_capacity_plan(
        session: Session, plan_id: str
    ) -> MissionCapacityPlanModel:
        row = session.get(MissionCapacityPlanModel, plan_id)
        if row is None:
            raise MissionCapacityPlanNotFound(
                f"Mission Capacity Plan {plan_id} не найден."
            )
        return row

    def _policy_to_dict(
        self,
        row: MissionResourcePolicyModel | None,
        mission: WorkspaceMissionModel,
    ) -> dict[str, Any]:
        if row is None:
            return {
                "id": None,
                "workspace_id": mission.workspace_id,
                "mission_id": mission.id,
                "enabled": False,
                "allocation_mode": "manual",
                "require_human_approval": True,
                "auto_allocation_enabled": False,
                "auto_rebalance_enabled": False,
                "currency": "USD",
                "total_budget_usd": 0.0,
                "default_cycle_budget_usd": 0.0,
                "max_cycle_budget_usd": 0.0,
                "reserve_percent": 10.0,
                "max_parallel_cycles": 1,
                "agent_slots": 4,
                "tool_slots": 4,
                "compute_units": 4.0,
                "planning_horizon_cycles": 10,
                "min_rebalance_improvement_percent": 10.0,
                "overrun_tolerance_percent": 10.0,
                "metadata": {},
                "created_at": None,
                "updated_at": None,
            }
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "enabled": row.enabled,
            "allocation_mode": row.allocation_mode,
            "require_human_approval": row.require_human_approval,
            "auto_allocation_enabled": row.auto_allocation_enabled,
            "auto_rebalance_enabled": row.auto_rebalance_enabled,
            "currency": row.currency,
            "total_budget_usd": round_money(row.total_budget_usd),
            "default_cycle_budget_usd": round_money(row.default_cycle_budget_usd),
            "max_cycle_budget_usd": round_money(row.max_cycle_budget_usd),
            "reserve_percent": row.reserve_percent,
            "max_parallel_cycles": row.max_parallel_cycles,
            "agent_slots": row.agent_slots,
            "tool_slots": row.tool_slots,
            "compute_units": row.compute_units,
            "planning_horizon_cycles": row.planning_horizon_cycles,
            "min_rebalance_improvement_percent": row.min_rebalance_improvement_percent,
            "overrun_tolerance_percent": row.overrun_tolerance_percent,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _allocation_to_dict(row: MissionResourceAllocationModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "cycle_id": row.cycle_id,
            "strategy_id": row.strategy_id,
            "status": row.status,
            "automatic": row.automatic,
            "requested_by": row.requested_by,
            "approved_by": row.approved_by,
            "budget_usd": round_money(row.budget_usd),
            "actual_cost_usd": round_money(row.actual_cost_usd),
            "remaining_budget_usd": round_money(row.budget_usd - row.actual_cost_usd),
            "agent_slots": row.agent_slots,
            "tool_slots": row.tool_slots,
            "compute_units": row.compute_units,
            "rationale": row.rationale,
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
    def _usage_to_dict(row: MissionResourceUsageModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "cycle_id": row.cycle_id,
            "allocation_id": row.allocation_id,
            "strategy_id": row.strategy_id,
            "category": row.category,
            "quantity": row.quantity,
            "unit": row.unit,
            "cost_usd": round_money(row.cost_usd),
            "source_type": row.source_type,
            "source_ref": row.source_ref,
            "idempotency_key": row.idempotency_key,
            "actor_id": row.actor_id,
            "details": dict(row.details_json or {}),
            "occurred_at": iso(row.occurred_at),
            "created_at": iso(row.created_at),
        }

    @staticmethod
    def _capacity_plan_to_dict(row: MissionCapacityPlanModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "status": row.status,
            "actor_id": row.actor_id,
            "automatic": row.automatic,
            "sample_cycles": row.sample_cycles,
            "confidence_percent": row.confidence_percent,
            "recommended_cycle_budget_usd": round_money(row.recommended_cycle_budget_usd),
            "recommended_parallel_cycles": row.recommended_parallel_cycles,
            "recommended_agent_slots": row.recommended_agent_slots,
            "recommended_tool_slots": row.recommended_tool_slots,
            "recommended_compute_units": row.recommended_compute_units,
            "rationale": row.rationale,
            "calculation": dict(row.calculation_json or {}),
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "applied_at": iso(row.applied_at),
        }

    async def _publish(
        self,
        event_type: str,
        *,
        workspace_id: str,
        mission_id: str,
        payload: dict[str, Any],
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="mission_resource_service",
                workspace_id=workspace_id,
                correlation_id=mission_id,
                payload={"mission_id": mission_id, **payload},
            )
        )
