from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from backend.core.events import Event, EventBus
from backend.orchestration.agent_schemas import (
    AgentAssignmentRequest,
    AgentCapabilityCreate,
    AgentCapabilityUpdate,
    AgentCreate,
    AgentManualAssignmentRequest,
    AgentMatchRequest,
    AgentUpdate,
)
from backend.orchestration.enums import (
    ExecutionPlanStatus,
    ExecutionStepStatus,
    ExecutionStepType,
)
from backend.orchestration.models import (
    AgentCapabilityModel,
    AgentProfileModel,
    ExecutionPlanModel,
    ExecutionPlanStepModel,
)
from backend.orchestration.repository import ExecutionPlanRepository


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AgentRegistryError(ValueError):
    pass


class AgentNotFound(AgentRegistryError):
    pass


class AgentRepository:
    ACTIVE_STEP_STATUSES = {
        ExecutionStepStatus.READY.value,
        ExecutionStepStatus.RUNNING.value,
    }

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, request: AgentCreate) -> AgentProfileModel:
        row = AgentProfileModel(
            workspace_id=request.workspace_id,
            agent_key=request.agent_key,
            display_name=request.display_name,
            description=request.description,
            roles_json=request.roles,
            tools_json=request.tools,
            enabled=request.enabled,
            status=request.status,
            priority=request.priority,
            max_concurrency=request.max_concurrency,
            executor_ref=request.executor_ref,
            model_slug=request.model_slug,
            metadata_json=request.metadata,
        )
        self.session.add(row)
        self.session.flush()

        for capability in request.capabilities:
            self.add_capability(row.id, capability)

        self.session.flush()
        return row

    def get(self, agent_id: str) -> AgentProfileModel | None:
        return self.session.get(AgentProfileModel, agent_id)

    def get_full(self, agent_id: str) -> AgentProfileModel | None:
        statement = (
            select(AgentProfileModel)
            .options(selectinload(AgentProfileModel.capabilities))
            .where(AgentProfileModel.id == agent_id)
            .execution_options(populate_existing=True)
        )
        return self.session.scalar(statement)

    def get_by_key(self, agent_key: str) -> AgentProfileModel | None:
        statement = (
            select(AgentProfileModel)
            .options(selectinload(AgentProfileModel.capabilities))
            .where(AgentProfileModel.agent_key == agent_key)
        )
        return self.session.scalar(statement)

    def list(
        self,
        *,
        workspace_id: str | None = None,
        enabled: bool | None = None,
        status: str | None = None,
        role: str | None = None,
        capability: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentProfileModel]:
        statement = select(AgentProfileModel).options(
            selectinload(AgentProfileModel.capabilities)
        )

        if workspace_id is not None:
            statement = statement.where(
                or_(
                    AgentProfileModel.workspace_id == workspace_id,
                    AgentProfileModel.workspace_id.is_(None),
                )
            )
        if enabled is not None:
            statement = statement.where(AgentProfileModel.enabled == enabled)
        if status is not None:
            statement = statement.where(AgentProfileModel.status == status)

        statement = statement.order_by(
            AgentProfileModel.priority.desc(),
            AgentProfileModel.agent_key.asc(),
        )
        rows = list(
            self.session.scalars(
                statement.offset(offset).limit(limit)
            ).unique().all()
        )

        if role is not None:
            rows = [row for row in rows if role in (row.roles_json or [])]
        if capability is not None:
            rows = [
                row
                for row in rows
                if any(
                    item.enabled and item.name == capability
                    for item in row.capabilities
                )
            ]

        return rows

    def update(
        self,
        row: AgentProfileModel,
        request: AgentUpdate,
    ) -> AgentProfileModel:
        values = request.model_dump(exclude_unset=True)
        mapping = {
            "display_name": "display_name",
            "description": "description",
            "roles": "roles_json",
            "tools": "tools_json",
            "enabled": "enabled",
            "status": "status",
            "priority": "priority",
            "max_concurrency": "max_concurrency",
            "executor_ref": "executor_ref",
            "model_slug": "model_slug",
            "metadata": "metadata_json",
        }

        for source, target in mapping.items():
            if source in values:
                setattr(row, target, values[source])

        self.session.flush()
        return row

    def delete(self, row: AgentProfileModel) -> None:
        self.session.delete(row)
        self.session.flush()

    def add_capability(
        self,
        agent_id: str,
        request: AgentCapabilityCreate,
    ) -> AgentCapabilityModel:
        row = AgentCapabilityModel(
            agent_id=agent_id,
            name=request.name,
            proficiency=request.proficiency,
            enabled=request.enabled,
            metadata_json=request.metadata,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_capability(
        self,
        agent_id: str,
        capability_id: str,
    ) -> AgentCapabilityModel | None:
        statement = select(AgentCapabilityModel).where(
            AgentCapabilityModel.agent_id == agent_id,
            AgentCapabilityModel.id == capability_id,
        )
        return self.session.scalar(statement)

    def get_capability_by_name(
        self,
        agent_id: str,
        name: str,
    ) -> AgentCapabilityModel | None:
        statement = select(AgentCapabilityModel).where(
            AgentCapabilityModel.agent_id == agent_id,
            AgentCapabilityModel.name == name,
        )
        return self.session.scalar(statement)

    def update_capability(
        self,
        row: AgentCapabilityModel,
        request: AgentCapabilityUpdate,
    ) -> AgentCapabilityModel:
        values = request.model_dump(exclude_unset=True)

        if "proficiency" in values:
            row.proficiency = values["proficiency"]
        if "enabled" in values:
            row.enabled = values["enabled"]
        if "metadata" in values:
            row.metadata_json = values["metadata"]

        self.session.flush()
        return row

    def delete_capability(self, row: AgentCapabilityModel) -> None:
        self.session.delete(row)
        self.session.flush()

    def active_load(self, agent_id: str) -> int:
        statement = select(func.count(ExecutionPlanStepModel.id)).where(
            ExecutionPlanStepModel.assigned_agent_id == agent_id,
            ExecutionPlanStepModel.status.in_(self.ACTIVE_STEP_STATUSES),
        )
        return int(self.session.scalar(statement) or 0)

    def has_assignments(self, agent_id: str) -> bool:
        statement = select(func.count(ExecutionPlanStepModel.id)).where(
            ExecutionPlanStepModel.assigned_agent_id == agent_id
        )
        return bool(self.session.scalar(statement) or 0)


class AgentRegistryService:
    DEFAULT_AGENTS = (
        AgentCreate(
            agent_key="builtin.planner",
            display_name="Built-in Planner",
            description="Creates and reviews execution plans.",
            roles=["planner", "architect"],
            tools=[],
            priority=80,
            max_concurrency=2,
            executor_ref="builtin.planner",
            capabilities=[
                AgentCapabilityCreate(name="planning", proficiency=90),
                AgentCapabilityCreate(
                    name="execution_plan_design",
                    proficiency=90,
                ),
            ],
        ),
        AgentCreate(
            agent_key="builtin.analyst",
            display_name="Built-in Analyst",
            description="Analyzes tasks, evidence and risks.",
            roles=["analyst", "researcher"],
            tools=["web_search"],
            priority=75,
            max_concurrency=3,
            executor_ref="builtin.analyst",
            capabilities=[
                AgentCapabilityCreate(name="analysis", proficiency=90),
                AgentCapabilityCreate(name="task_analysis", proficiency=90),
                AgentCapabilityCreate(name="research", proficiency=80),
            ],
        ),
        AgentCreate(
            agent_key="builtin.writer",
            display_name="Built-in Writer",
            description="Produces reports and structured deliverables.",
            roles=["writer", "reporter"],
            tools=[],
            priority=70,
            max_concurrency=3,
            executor_ref="builtin.writer",
            capabilities=[
                AgentCapabilityCreate(name="writing", proficiency=90),
                AgentCapabilityCreate(
                    name="report_generation",
                    proficiency=90,
                ),
            ],
        ),
        AgentCreate(
            agent_key="builtin.worker",
            display_name="Built-in Worker",
            description="Executes approved digital tasks.",
            roles=["worker", "executor"],
            tools=[],
            priority=70,
            max_concurrency=2,
            executor_ref="builtin.worker",
            capabilities=[
                AgentCapabilityCreate(
                    name="task_execution",
                    proficiency=85,
                ),
                AgentCapabilityCreate(
                    name="digital_work",
                    proficiency=80,
                ),
            ],
        ),
    )

    def __init__(
        self,
        repository: AgentRepository,
        event_bus: EventBus,
    ) -> None:
        self._repository = repository
        self._event_bus = event_bus

    def seed_defaults(self) -> int:
        created = 0

        for request in self.DEFAULT_AGENTS:
            if self._repository.get_by_key(request.agent_key) is not None:
                continue
            self._repository.create(request)
            created += 1

        return created

    async def create(self, request: AgentCreate) -> dict[str, Any]:
        if self._repository.get_by_key(request.agent_key) is not None:
            raise AgentRegistryError(
                f"Agent с ключом {request.agent_key} уже существует."
            )

        try:
            row = self._repository.create(request)
        except IntegrityError as exc:
            raise AgentRegistryError(
                f"Agent с ключом {request.agent_key} уже существует."
            ) from exc

        await self._publish(
            "agent.created",
            row,
            {"agent_id": row.id, "agent_key": row.agent_key},
        )
        return self._serialize_full(row)

    def get(self, agent_id: str) -> dict[str, Any] | None:
        row = self._repository.get_full(agent_id)
        return None if row is None else self._serialize_full(row)

    def list(
        self,
        *,
        workspace_id: str | None = None,
        enabled: bool | None = None,
        status: str | None = None,
        role: str | None = None,
        capability: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        rows = self._repository.list(
            workspace_id=workspace_id,
            enabled=enabled,
            status=status,
            role=role,
            capability=capability,
            limit=limit,
            offset=offset,
        )
        return [self._serialize_full(row) for row in rows]

    async def update(
        self,
        agent_id: str,
        request: AgentUpdate,
    ) -> dict[str, Any] | None:
        row = self._repository.get(agent_id)
        if row is None:
            return None

        self._repository.update(row, request)
        await self._publish(
            "agent.updated",
            row,
            {"agent_id": row.id, "agent_key": row.agent_key},
        )
        return self._serialize_full(row)

    async def delete(self, agent_id: str) -> bool:
        row = self._repository.get(agent_id)
        if row is None:
            return False
        if self._repository.has_assignments(agent_id):
            raise AgentRegistryError(
                "Нельзя удалить Agent, связанный с Execution Plan Steps. "
                "Сначала отключите Agent или очистите назначения."
            )

        workspace_id = row.workspace_id
        agent_key = row.agent_key
        self._repository.delete(row)
        await self._event_bus.publish(
            Event(
                event_type="agent.deleted",
                source="agent_registry_service",
                workspace_id=workspace_id,
                payload={
                    "agent_id": agent_id,
                    "agent_key": agent_key,
                },
            )
        )
        return True

    async def add_capability(
        self,
        agent_id: str,
        request: AgentCapabilityCreate,
    ) -> dict[str, Any] | None:
        agent = self._repository.get(agent_id)
        if agent is None:
            return None
        if self._repository.get_capability_by_name(
            agent_id,
            request.name,
        ) is not None:
            raise AgentRegistryError(
                f"Capability {request.name} уже зарегистрирована."
            )

        capability = self._repository.add_capability(agent_id, request)
        await self._publish(
            "agent.capability.created",
            agent,
            {
                "agent_id": agent.id,
                "capability_id": capability.id,
                "name": capability.name,
            },
        )
        return self._serialize_full(agent)

    async def update_capability(
        self,
        agent_id: str,
        capability_id: str,
        request: AgentCapabilityUpdate,
    ) -> dict[str, Any] | None:
        agent = self._repository.get(agent_id)
        if agent is None:
            return None
        capability = self._repository.get_capability(
            agent_id,
            capability_id,
        )
        if capability is None:
            raise AgentNotFound("Capability не найдена.")

        self._repository.update_capability(capability, request)
        await self._publish(
            "agent.capability.updated",
            agent,
            {
                "agent_id": agent.id,
                "capability_id": capability.id,
                "name": capability.name,
            },
        )
        return self._serialize_full(agent)

    async def delete_capability(
        self,
        agent_id: str,
        capability_id: str,
    ) -> dict[str, Any] | None:
        agent = self._repository.get(agent_id)
        if agent is None:
            return None
        capability = self._repository.get_capability(
            agent_id,
            capability_id,
        )
        if capability is None:
            raise AgentNotFound("Capability не найдена.")

        name = capability.name
        self._repository.delete_capability(capability)
        await self._publish(
            "agent.capability.deleted",
            agent,
            {
                "agent_id": agent.id,
                "capability_id": capability_id,
                "name": name,
            },
        )
        return self._serialize_full(agent)

    def match(self, request: AgentMatchRequest) -> list[dict[str, Any]]:
        selector = AgentSelector(self._repository)
        return selector.rank(
            workspace_id=request.workspace_id,
            role=request.role,
            capability=request.capability,
            required_tools=request.required_tools,
            limit=request.limit,
        )

    async def _publish(
        self,
        event_type: str,
        row: AgentProfileModel,
        payload: dict[str, Any],
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="agent_registry_service",
                workspace_id=row.workspace_id,
                correlation_id=row.id,
                payload=payload,
            )
        )

    def _serialize_full(self, row: AgentProfileModel) -> dict[str, Any]:
        capabilities = sorted(
            row.capabilities,
            key=lambda item: item.name,
        )
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "agent_key": row.agent_key,
            "display_name": row.display_name,
            "description": row.description,
            "roles": row.roles_json,
            "tools": row.tools_json,
            "enabled": row.enabled,
            "status": row.status,
            "priority": row.priority,
            "max_concurrency": row.max_concurrency,
            "active_load": self._repository.active_load(row.id),
            "executor_ref": row.executor_ref,
            "model_slug": row.model_slug,
            "metadata": row.metadata_json,
            "capabilities": [
                {
                    "id": item.id,
                    "name": item.name,
                    "proficiency": item.proficiency,
                    "enabled": item.enabled,
                    "metadata": item.metadata_json,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
                for item in capabilities
            ],
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }


class AgentSelector:
    def __init__(self, repository: AgentRepository) -> None:
        self._repository = repository

    def rank(
        self,
        *,
        workspace_id: str | None,
        role: str | None,
        capability: str | None,
        required_tools: list[str],
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        normalized_role = role.strip().lower() if role else None
        normalized_capability = (
            capability.strip().lower() if capability else None
        )
        required = {
            item.strip().lower()
            for item in required_tools
            if item.strip()
        }

        candidates = self._repository.list(
            workspace_id=workspace_id,
            enabled=True,
            status="available",
            limit=10_000,
        )
        ranked: list[dict[str, Any]] = []

        for agent in candidates:
            if workspace_id is None and agent.workspace_id is not None:
                continue

            roles = set(agent.roles_json or [])
            tools = set(agent.tools_json or [])
            capability_map = {
                item.name: item
                for item in agent.capabilities
                if item.enabled
            }
            reasons: list[str] = []

            if normalized_role and normalized_role not in roles:
                continue
            if (
                normalized_capability
                and normalized_capability not in capability_map
            ):
                continue
            if not required.issubset(tools):
                continue

            load = self._repository.active_load(agent.id)
            free_slots = agent.max_concurrency - load
            if free_slots <= 0:
                continue

            role_score = 40.0 if normalized_role else 10.0
            if normalized_role:
                reasons.append(f"role:{normalized_role}")

            proficiency = 50
            if normalized_capability:
                proficiency = capability_map[
                    normalized_capability
                ].proficiency
                reasons.append(
                    f"capability:{normalized_capability}={proficiency}"
                )
            capability_score = proficiency * 0.4

            if workspace_id is not None and agent.workspace_id == workspace_id:
                workspace_score = 15.0
                reasons.append("workspace:exact")
            elif agent.workspace_id is None:
                workspace_score = 5.0
                reasons.append("workspace:global")
            else:
                workspace_score = 0.0

            priority_score = agent.priority * 0.1
            capacity_score = (
                free_slots / agent.max_concurrency
            ) * 10.0
            tool_score = min(len(required) * 2.0, 10.0)

            score = round(
                role_score
                + capability_score
                + workspace_score
                + priority_score
                + capacity_score
                + tool_score,
                3,
            )

            ranked.append(
                {
                    "agent_id": agent.id,
                    "agent_key": agent.agent_key,
                    "display_name": agent.display_name,
                    "executor_ref": agent.executor_ref,
                    "model_slug": agent.model_slug,
                    "workspace_id": agent.workspace_id,
                    "roles": agent.roles_json,
                    "tools": agent.tools_json,
                    "active_load": load,
                    "max_concurrency": agent.max_concurrency,
                    "free_slots": free_slots,
                    "score": score,
                    "reasons": reasons,
                }
            )

        ranked.sort(
            key=lambda item: (
                -float(item["score"]),
                str(item["agent_key"]),
            )
        )
        return ranked[:limit]


class AgentAssignmentService:
    EDITABLE_PLAN_STATUSES = {
        ExecutionPlanStatus.DRAFT.value,
        ExecutionPlanStatus.VALIDATED.value,
        ExecutionPlanStatus.READY.value,
        ExecutionPlanStatus.FAILED.value,
    }

    def __init__(
        self,
        agent_repository: AgentRepository,
        plan_repository: ExecutionPlanRepository,
        event_bus: EventBus,
    ) -> None:
        self._agents = agent_repository
        self._plans = plan_repository
        self._event_bus = event_bus
        self._selector = AgentSelector(agent_repository)

    def preview_step(
        self,
        plan_id: str,
        step_id: str,
        *,
        limit: int = 20,
    ) -> dict[str, Any] | None:
        plan = self._plans.get(plan_id)
        if plan is None:
            return None
        step = self._plans.get_step(plan_id, step_id)
        if step is None:
            raise AgentNotFound("Execution Plan Step не найден.")
        if step.step_type != ExecutionStepType.AGENT.value:
            raise AgentRegistryError(
                "Выбор Agent доступен только для agent-step."
            )

        matches = self._rank_for_step(plan, step, limit=limit)
        return {
            "plan_id": plan.id,
            "step_id": step.id,
            "step_key": step.step_key,
            "required_role": step.agent_role,
            "required_capability": step.capability,
            "required_tools": self._required_tools(step),
            "assigned_agent_id": step.assigned_agent_id,
            "matches": matches,
        }

    async def assign_step(
        self,
        plan_id: str,
        step_id: str,
        request: AgentManualAssignmentRequest | None = None,
    ) -> dict[str, Any] | None:
        plan = self._plans.get(plan_id)
        if plan is None:
            return None
        self._assert_assignable(plan)

        step = self._plans.get_step(plan_id, step_id)
        if step is None:
            raise AgentNotFound("Execution Plan Step не найден.")
        if step.step_type != ExecutionStepType.AGENT.value:
            raise AgentRegistryError(
                "Назначить Agent можно только для agent-step."
            )

        if request is None:
            matches = self._rank_for_step(plan, step, limit=1)
            if not matches:
                raise AgentRegistryError(
                    f"Для шага {step.step_key} не найден подходящий Agent."
                )
            selected = matches[0]
            reason = "automatic_selection"
        else:
            agent = self._agents.get_full(request.agent_id)
            if agent is None:
                raise AgentNotFound("Agent не найден.")

            matches = self._rank_for_step(plan, step, limit=10_000)
            selected = next(
                (
                    candidate
                    for candidate in matches
                    if candidate["agent_id"] == agent.id
                ),
                None,
            )
            if selected is None and not request.force:
                raise AgentRegistryError(
                    "Agent не соответствует требованиям шага или "
                    "не имеет свободной capacity."
                )
            if selected is None:
                selected = {
                    "agent_id": agent.id,
                    "agent_key": agent.agent_key,
                    "display_name": agent.display_name,
                    "executor_ref": agent.executor_ref,
                    "model_slug": agent.model_slug,
                    "workspace_id": agent.workspace_id,
                    "roles": agent.roles_json,
                    "tools": agent.tools_json,
                    "active_load": self._agents.active_load(agent.id),
                    "max_concurrency": agent.max_concurrency,
                    "free_slots": max(
                        agent.max_concurrency
                        - self._agents.active_load(agent.id),
                        0,
                    ),
                    "score": 0.0,
                    "reasons": ["manual_force"],
                }
            reason = request.reason or (
                "manual_force" if request.force else "manual_assignment"
            )

        self._apply_assignment(step, selected, reason=reason)
        self._plans.session.flush()

        await self._publish_assignment(plan, step, selected, reason)
        return self._serialize_assignment(plan, step)

    async def assign_plan(
        self,
        plan_id: str,
        request: AgentAssignmentRequest,
    ) -> dict[str, Any] | None:
        plan = self._plans.get(plan_id)
        if plan is None:
            return None
        self._assert_assignable(plan)

        agent_steps = [
            step
            for step in self._plans.list_steps(plan_id)
            if step.step_type == ExecutionStepType.AGENT.value
        ]
        decisions: list[tuple[ExecutionPlanStepModel, dict[str, Any]]] = []
        unmatched: list[dict[str, Any]] = []
        preserved: list[str] = []

        for step in agent_steps:
            if step.assigned_agent_id and not request.replace_existing:
                preserved.append(step.step_key)
                continue

            matches = self._rank_for_step(plan, step, limit=1)
            if not matches:
                unmatched.append(
                    {
                        "step_id": step.id,
                        "step_key": step.step_key,
                        "agent_role": step.agent_role,
                        "capability": step.capability,
                        "required_tools": self._required_tools(step),
                    }
                )
                continue

            decisions.append((step, matches[0]))

        if request.strict and unmatched:
            keys = ", ".join(item["step_key"] for item in unmatched)
            raise AgentRegistryError(
                "Не удалось назначить Agent для шагов: " + keys + "."
            )

        assigned: list[dict[str, Any]] = []
        for step, selected in decisions:
            self._apply_assignment(
                step,
                selected,
                reason="automatic_plan_assignment",
            )
            assigned.append(self._serialize_assignment(plan, step))

        self._plans.session.flush()

        await self._event_bus.publish(
            Event(
                event_type="execution_plan.agents.assigned",
                source="agent_assignment_service",
                workspace_id=plan.workspace_id,
                correlation_id=plan.id,
                payload={
                    "plan_id": plan.id,
                    "assigned_count": len(assigned),
                    "unmatched_count": len(unmatched),
                    "preserved_count": len(preserved),
                    "strict": request.strict,
                    "replace_existing": request.replace_existing,
                },
            )
        )

        return {
            "plan_id": plan.id,
            "assigned": assigned,
            "unmatched": unmatched,
            "preserved": preserved,
            "complete": not unmatched,
            "summary": self.assignment_status(plan.id),
        }

    async def clear_assignment(
        self,
        plan_id: str,
        step_id: str,
    ) -> dict[str, Any] | None:
        plan = self._plans.get(plan_id)
        if plan is None:
            return None
        self._assert_assignable(plan)

        step = self._plans.get_step(plan_id, step_id)
        if step is None:
            raise AgentNotFound("Execution Plan Step не найден.")

        previous = step.assigned_agent_id
        step.assigned_agent_id = None
        step.assignment_json = {}
        step.assigned_at = None
        self._plans.session.flush()

        await self._event_bus.publish(
            Event(
                event_type="execution_plan.agent.unassigned",
                source="agent_assignment_service",
                workspace_id=plan.workspace_id,
                correlation_id=plan.id,
                payload={
                    "plan_id": plan.id,
                    "step_id": step.id,
                    "step_key": step.step_key,
                    "previous_agent_id": previous,
                },
            )
        )
        return self._serialize_assignment(plan, step)

    def assignment_status(self, plan_id: str) -> dict[str, Any] | None:
        plan = self._plans.get(plan_id)
        if plan is None:
            return None

        rows = []
        for step in self._plans.list_steps(plan_id):
            if step.step_type != ExecutionStepType.AGENT.value:
                continue
            rows.append(self._serialize_assignment(plan, step))

        assigned_count = sum(
            1 for item in rows if item["assigned_agent_id"] is not None
        )
        return {
            "plan_id": plan.id,
            "agent_step_count": len(rows),
            "assigned_count": assigned_count,
            "unassigned_count": len(rows) - assigned_count,
            "complete": assigned_count == len(rows),
            "assignments": rows,
        }

    def _rank_for_step(
        self,
        plan: ExecutionPlanModel,
        step: ExecutionPlanStepModel,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        return self._selector.rank(
            workspace_id=plan.workspace_id,
            role=step.agent_role,
            capability=step.capability,
            required_tools=self._required_tools(step),
            limit=limit,
        )

    @staticmethod
    def _required_tools(step: ExecutionPlanStepModel) -> list[str]:
        metadata = step.metadata_json or {}
        raw = metadata.get("required_tools", [])
        if not isinstance(raw, list):
            return []
        return sorted(
            {
                str(item).strip().lower()
                for item in raw
                if str(item).strip()
            }
        )

    @staticmethod
    def _apply_assignment(
        step: ExecutionPlanStepModel,
        selected: dict[str, Any],
        *,
        reason: str,
    ) -> None:
        now = utc_now()
        step.assigned_agent_id = str(selected["agent_id"])
        step.assigned_at = now
        step.assignment_json = {
            "agent_key": selected["agent_key"],
            "display_name": selected["display_name"],
            "executor_ref": selected["executor_ref"],
            "model_slug": selected["model_slug"],
            "score": selected["score"],
            "reasons": selected["reasons"],
            "assigned_at": now.isoformat(),
            "reason": reason,
        }

    async def _publish_assignment(
        self,
        plan: ExecutionPlanModel,
        step: ExecutionPlanStepModel,
        selected: dict[str, Any],
        reason: str,
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type="execution_plan.agent.assigned",
                source="agent_assignment_service",
                workspace_id=plan.workspace_id,
                correlation_id=plan.id,
                payload={
                    "plan_id": plan.id,
                    "step_id": step.id,
                    "step_key": step.step_key,
                    "agent_id": selected["agent_id"],
                    "agent_key": selected["agent_key"],
                    "score": selected["score"],
                    "reason": reason,
                },
            )
        )

    @staticmethod
    def _assert_assignable(plan: ExecutionPlanModel) -> None:
        if plan.status not in AgentAssignmentService.EDITABLE_PLAN_STATUSES:
            raise AgentRegistryError(
                "Назначение Agent запрещено для Execution Plan "
                f"в статусе {plan.status}."
            )

    @staticmethod
    def _serialize_assignment(
        plan: ExecutionPlanModel,
        step: ExecutionPlanStepModel,
    ) -> dict[str, Any]:
        return {
            "plan_id": plan.id,
            "step_id": step.id,
            "step_key": step.step_key,
            "agent_role": step.agent_role,
            "capability": step.capability,
            "assigned_agent_id": step.assigned_agent_id,
            "assignment": step.assignment_json,
            "assigned_at": step.assigned_at,
        }
