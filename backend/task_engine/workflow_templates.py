from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.events import Event, EventBus
from backend.task_engine.approval_schemas import ApprovalGateDefinition
from backend.task_engine.approvals import ApprovalGateRuntime
from backend.task_engine.dependencies import (
    DependencyCreateRequest,
    DependencyCycleError,
    TaskDependencyRepository,
    TaskDependencyType,
)
from backend.task_engine.enums import TaskPriority, TaskStatus, TaskType
from backend.task_engine.models import (
    TaskModel,
    WorkflowInstanceModel,
    WorkflowTemplateModel,
)
from backend.task_engine.repository import TaskRepository
from backend.task_engine.schemas import TaskCreate, TaskLogCreate
from backend.task_engine.state_machine import TaskStateMachine


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ConditionOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    EXISTS = "exists"
    TRUTHY = "truthy"
    FALSY = "falsy"
    IN = "in"
    NOT_IN = "not_in"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"


class WorkflowNodeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    task_type: TaskType = TaskType.SYSTEM
    priority: TaskPriority = TaskPriority.NORMAL
    title: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    executor: str | None = None
    max_retries: int = Field(default=0, ge=0, le=100)
    retry_delay_seconds: float = Field(default=1.0, ge=0, le=3600)
    timeout_seconds: int = Field(default=300, ge=1, le=86400)
    condition: dict[str, Any] | None = None
    result_mapping: dict[str, str] = Field(default_factory=dict)
    approval: ApprovalGateDefinition | None = None


class WorkflowEdgeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_node: str = Field(..., min_length=1, max_length=64)
    to_node: str = Field(..., min_length=1, max_length=64)
    dependency_type: TaskDependencyType = TaskDependencyType.HARD
    required_status: TaskStatus = TaskStatus.COMPLETED


class WorkflowTemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = None
    name: str = Field(..., min_length=1, max_length=255)
    version: int = Field(default=1, ge=1)
    description: str = ""
    root_node_key: str = Field(..., min_length=1, max_length=64)
    nodes: list[WorkflowNodeDefinition] = Field(..., min_length=1, max_length=200)
    edges: list[WorkflowEdgeDefinition] = Field(default_factory=list, max_length=1000)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip()


class WorkflowInstantiateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    creator: str | None = None


class WorkflowTemplateError(ValueError):
    pass


class WorkflowConditionError(WorkflowTemplateError):
    pass


class WorkflowTemplateService:
    TERMINAL_STATUSES = {
        TaskStatus.COMPLETED.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
        TaskStatus.SKIPPED.value,
    }

    def __init__(self, session: Session, event_bus: EventBus) -> None:
        self.session = session
        self.event_bus = event_bus

    async def create_template(self, request: WorkflowTemplateCreate) -> dict[str, Any]:
        definition = {
            "root_node_key": request.root_node_key,
            "nodes": [node.model_dump(mode="json") for node in request.nodes],
            "edges": [edge.model_dump(mode="json") for edge in request.edges],
        }
        validation = self.validate_definition(definition)
        if not validation["valid"]:
            raise WorkflowTemplateError("; ".join(validation["errors"]))

        duplicate = self.session.scalar(
            select(WorkflowTemplateModel).where(
                WorkflowTemplateModel.workspace_id == request.workspace_id,
                WorkflowTemplateModel.name == request.name,
                WorkflowTemplateModel.version == request.version,
            )
        )
        if duplicate is not None:
            raise WorkflowTemplateError("Template с таким именем и версией уже существует.")

        row = WorkflowTemplateModel(
            workspace_id=request.workspace_id,
            name=request.name,
            version=request.version,
            description=request.description.strip(),
            definition_json=definition,
            input_schema_json=request.input_schema,
            metadata_json=request.metadata,
            enabled=request.enabled,
        )
        self.session.add(row)
        self.session.flush()

        await self.event_bus.publish(
            Event(
                event_type="workflow.template.created",
                source="workflow_template_service",
                workspace_id=row.workspace_id,
                payload={"template_id": row.id, "name": row.name, "version": row.version},
            )
        )
        return self._serialize_template(row)

    def get_template(self, template_id: str) -> dict[str, Any] | None:
        row = self.session.get(WorkflowTemplateModel, template_id)
        return None if row is None else self._serialize_template(row)

    def list_templates(
        self,
        *,
        workspace_id: str | None = None,
        enabled: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        statement = select(WorkflowTemplateModel)
        if workspace_id is not None:
            statement = statement.where(WorkflowTemplateModel.workspace_id == workspace_id)
        if enabled is not None:
            statement = statement.where(WorkflowTemplateModel.enabled == enabled)
        statement = statement.order_by(
            WorkflowTemplateModel.name.asc(), WorkflowTemplateModel.version.desc()
        ).offset(offset).limit(limit)
        return [self._serialize_template(row) for row in self.session.scalars(statement)]

    async def set_enabled(self, template_id: str, enabled: bool) -> dict[str, Any] | None:
        row = self.session.get(WorkflowTemplateModel, template_id)
        if row is None:
            return None
        row.enabled = enabled
        self.session.flush()
        await self.event_bus.publish(
            Event(
                event_type="workflow.template.enabled" if enabled else "workflow.template.disabled",
                source="workflow_template_service",
                workspace_id=row.workspace_id,
                payload={"template_id": row.id},
            )
        )
        return self._serialize_template(row)

    async def instantiate(
        self,
        template_id: str,
        request: WorkflowInstantiateRequest,
    ) -> dict[str, Any] | None:
        template = self.session.get(WorkflowTemplateModel, template_id)
        if template is None:
            return None
        if not template.enabled:
            raise WorkflowTemplateError("Workflow Template отключён.")

        definition = dict(template.definition_json or {})
        validation = self.validate_definition(definition)
        if not validation["valid"]:
            raise WorkflowTemplateError("Template definition invalid: " + "; ".join(validation["errors"]))

        resolved_input = self._validate_input(template.input_schema_json or {}, request.input)
        workspace_id = request.workspace_id if request.workspace_id is not None else template.workspace_id
        instance = WorkflowInstanceModel(
            template_id=template.id,
            workspace_id=workspace_id,
            status="created",
            input_json=resolved_input,
            node_task_map_json={},
            context_json={},
        )
        self.session.add(instance)
        self.session.flush()

        tasks = TaskRepository(self.session)
        dependencies = TaskDependencyRepository(self.session)
        node_task_map: dict[str, str] = {}

        for raw_node in definition["nodes"]:
            node = WorkflowNodeDefinition.model_validate(raw_node)
            payload = deepcopy(node.payload)
            payload["_workflow"] = {
                "instance_id": instance.id,
                "template_id": template.id,
                "node_key": node.key,
                "condition": deepcopy(node.condition),
                "result_mapping": deepcopy(node.result_mapping),
                "approval": (
                    node.approval.model_dump(mode="json")
                    if node.approval is not None
                    else None
                ),
                "base_payload": deepcopy(node.payload),
            }
            task = tasks.create(
                TaskCreate(
                    workspace_id=workspace_id,
                    task_type=node.task_type,
                    priority=node.priority,
                    title=self._render_text(node.title, resolved_input),
                    description=self._render_text(node.description, resolved_input),
                    payload=payload,
                    creator=request.creator,
                    executor=node.executor,
                    max_retries=node.max_retries,
                    retry_delay_seconds=node.retry_delay_seconds,
                    timeout_seconds=node.timeout_seconds,
                )
            )
            node_task_map[node.key] = task.id

        for raw_edge in definition.get("edges", []):
            edge = WorkflowEdgeDefinition.model_validate(raw_edge)
            dependencies.add(
                task_id=node_task_map[edge.to_node],
                depends_on_task_id=node_task_map[edge.from_node],
                dependency_type=edge.dependency_type,
                required_status=edge.required_status,
            )

        instance.node_task_map_json = node_task_map
        instance.root_task_id = node_task_map[definition["root_node_key"]]
        self.session.flush()

        await self.event_bus.publish(
            Event(
                event_type="workflow.instance.created",
                source="workflow_template_service",
                workspace_id=workspace_id,
                payload={
                    "instance_id": instance.id,
                    "template_id": template.id,
                    "root_task_id": instance.root_task_id,
                    "task_count": len(node_task_map),
                },
            )
        )
        return self._serialize_instance(instance, include_tasks=True)

    def get_instance(self, instance_id: str) -> dict[str, Any] | None:
        return self.refresh_instance(instance_id)

    def prepare_task(self, task_id: str) -> dict[str, Any]:
        task = self.session.get(TaskModel, task_id)
        if task is None:
            return {"managed": False, "action": "ready"}

        workflow_meta = (task.payload_json or {}).get("_workflow")

        if not isinstance(workflow_meta, dict):
            approval = ApprovalGateRuntime(
                self.session
            ).ensure_for_task(task)

            if approval is None or approval["status"] == "approved":
                return {
                    "managed": False,
                    "action": "ready",
                    "approval": approval,
                }

            if approval["status"] == "pending":
                return {
                    "managed": False,
                    "action": "approval_pending",
                    "approval": approval,
                }

            if task.status not in {
                TaskStatus.CANCELLED.value,
                TaskStatus.COMPLETED.value,
                TaskStatus.FAILED.value,
                TaskStatus.SKIPPED.value,
            }:
                TaskStateMachine.transition(
                    task,
                    TaskStatus.CANCELLED,
                )

            return {
                "managed": False,
                "action": "approval_rejected",
                "approval": approval,
            }

        instance_id = workflow_meta.get("instance_id")
        instance = self.session.get(WorkflowInstanceModel, instance_id)
        if instance is None:
            raise WorkflowTemplateError("Workflow instance for Task not found.")

        context = self._build_context(instance)
        condition = workflow_meta.get("condition")
        if condition is not None and not self.evaluate_condition(condition, context):
            TaskStateMachine.transition(task, TaskStatus.SKIPPED)
            tasks = TaskRepository(self.session)
            tasks.append_log(
                task.id,
                TaskLogCreate(
                    level="INFO",
                    message="Task skipped because workflow condition evaluated to false.",
                    metadata={"workflow": True, "condition": condition},
                ),
            )
            self.refresh_instance(instance.id)
            return {
                "managed": True,
                "action": "skipped",
                "instance_id": instance.id,
            }

        approval_raw = workflow_meta.get("approval")
        approval_spec = (
            ApprovalGateDefinition.model_validate(approval_raw)
            if isinstance(approval_raw, dict)
            else None
        )
        approval = ApprovalGateRuntime(self.session).ensure_for_task(
            task,
            approval_spec,
            requested_by="workflow",
        )

        if approval is not None:
            if approval["status"] == "pending":
                self.refresh_instance(instance.id)
                return {
                    "managed": True,
                    "action": "approval_pending",
                    "instance_id": instance.id,
                    "approval": approval,
                }

            if approval["status"] in {
                "rejected",
                "expired",
                "cancelled",
            }:
                if task.status not in {
                    TaskStatus.CANCELLED.value,
                    TaskStatus.COMPLETED.value,
                    TaskStatus.FAILED.value,
                    TaskStatus.SKIPPED.value,
                }:
                    TaskStateMachine.transition(
                        task,
                        TaskStatus.CANCELLED,
                    )

                TaskRepository(self.session).append_log(
                    task.id,
                    TaskLogCreate(
                        level="WARNING",
                        message=(
                            "Task stopped because Approval Gate "
                            f"finished with status {approval['status']}."
                        ),
                        metadata={
                            "workflow": True,
                            "approval_id": approval["id"],
                            "approval_status": approval["status"],
                        },
                    ),
                )
                self.refresh_instance(instance.id)
                return {
                    "managed": True,
                    "action": "approval_rejected",
                    "instance_id": instance.id,
                    "approval": approval,
                }

        base_payload = deepcopy(workflow_meta.get("base_payload", {}))
        mapped: dict[str, Any] = {}
        for target_path, source_path in dict(workflow_meta.get("result_mapping", {})).items():
            value = self._read_path(context, source_path)
            self._write_path(base_payload, target_path, deepcopy(value))
            mapped[target_path] = value

        base_payload["_workflow"] = workflow_meta
        task.payload_json = base_payload
        self.session.flush()
        return {
            "managed": True,
            "action": "ready",
            "instance_id": instance.id,
            "mapped": mapped,
        }

    def refresh_instance(self, instance_id: str) -> dict[str, Any] | None:
        instance = self.session.get(WorkflowInstanceModel, instance_id)
        if instance is None:
            return None

        tasks = [
            self.session.get(TaskModel, task_id)
            for task_id in (instance.node_task_map_json or {}).values()
        ]
        tasks = [task for task in tasks if task is not None]
        statuses = {task.status for task in tasks}
        instance.context_json = self._build_context(instance)

        if tasks and statuses <= self.TERMINAL_STATUSES:
            if TaskStatus.FAILED.value in statuses or TaskStatus.CANCELLED.value in statuses:
                instance.status = "finished_with_errors"
            else:
                instance.status = "completed"
            instance.finished_at = utc_now()
        elif TaskStatus.RUNNING.value in statuses:
            instance.status = "running"
            instance.started_at = instance.started_at or utc_now()
        elif statuses & {TaskStatus.QUEUED.value, TaskStatus.PLANNING.value, TaskStatus.WAITING.value}:
            instance.status = "waiting"
            instance.started_at = instance.started_at or utc_now()
        else:
            instance.status = "created"

        self.session.flush()
        return self._serialize_instance(instance, include_tasks=True)

    def validate_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        errors: list[str] = []
        try:
            nodes = [WorkflowNodeDefinition.model_validate(item) for item in definition.get("nodes", [])]
            edges = [WorkflowEdgeDefinition.model_validate(item) for item in definition.get("edges", [])]
        except Exception as exc:
            return {"valid": False, "errors": [str(exc)], "topological_order": []}

        keys = [node.key for node in nodes]
        key_set = set(keys)
        if len(keys) != len(key_set):
            errors.append("Node keys must be unique.")

        root = definition.get("root_node_key")
        if root not in key_set:
            errors.append("root_node_key does not reference an existing node.")

        pairs: set[tuple[str, str]] = set()
        graph = {key: set() for key in key_set}
        reverse = {key: set() for key in key_set}

        for node in nodes:
            try:
                self._validate_condition(node.condition)
            except WorkflowConditionError as exc:
                errors.append(f"Node {node.key}: {exc}")
            for target, source in node.result_mapping.items():
                if not target.strip() or not source.startswith(("input.", "nodes.")):
                    errors.append(f"Node {node.key}: invalid result mapping {target} <- {source}.")

        for edge in edges:
            if edge.from_node not in key_set or edge.to_node not in key_set:
                errors.append(f"Edge {edge.from_node}->{edge.to_node} references an unknown node.")
                continue
            if edge.from_node == edge.to_node:
                errors.append("Self dependency is not allowed.")
            pair = (edge.from_node, edge.to_node)
            if pair in pairs:
                errors.append(f"Duplicate edge: {edge.from_node}->{edge.to_node}.")
            pairs.add(pair)
            graph[edge.from_node].add(edge.to_node)
            reverse[edge.to_node].add(edge.from_node)

        indegree = {key: len(reverse[key]) for key in key_set}
        ready = sorted(key for key, degree in indegree.items() if degree == 0)
        order: list[str] = []
        while ready:
            current = ready.pop(0)
            order.append(current)
            for child in sorted(graph[current]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
                    ready.sort()
        if len(order) != len(key_set):
            errors.append("Workflow definition contains a cycle.")

        if root in key_set:
            ancestors: set[str] = set()
            stack = [root]
            while stack:
                current = stack.pop()
                if current in ancestors:
                    continue
                ancestors.add(current)
                stack.extend(reverse[current])
            missing = sorted(key_set - ancestors)
            if missing:
                errors.append("All nodes must lead to root_node_key. Disconnected: " + ", ".join(missing))

        return {"valid": not errors, "errors": errors, "topological_order": order}

    @classmethod
    def evaluate_condition(cls, condition: dict[str, Any], context: dict[str, Any]) -> bool:
        cls._validate_condition(condition)
        if "all" in condition:
            return all(cls.evaluate_condition(item, context) for item in condition["all"])
        if "any" in condition:
            return any(cls.evaluate_condition(item, context) for item in condition["any"])
        if "not" in condition:
            return not cls.evaluate_condition(condition["not"], context)

        actual = cls._read_path(context, str(condition["path"]), missing=None)
        operator = ConditionOperator(str(condition.get("operator", "eq")))
        expected = condition.get("value")
        if operator == ConditionOperator.EXISTS:
            return actual is not None
        if operator == ConditionOperator.TRUTHY:
            return bool(actual)
        if operator == ConditionOperator.FALSY:
            return not bool(actual)
        if operator == ConditionOperator.EQ:
            return actual == expected
        if operator == ConditionOperator.NE:
            return actual != expected
        if operator == ConditionOperator.IN:
            return actual in expected
        if operator == ConditionOperator.NOT_IN:
            return actual not in expected
        if operator == ConditionOperator.GT:
            return actual > expected
        if operator == ConditionOperator.GTE:
            return actual >= expected
        if operator == ConditionOperator.LT:
            return actual < expected
        if operator == ConditionOperator.LTE:
            return actual <= expected
        return False

    @classmethod
    def _validate_condition(cls, condition: dict[str, Any] | None) -> None:
        if condition is None:
            return
        if not isinstance(condition, dict):
            raise WorkflowConditionError("Condition must be an object.")
        groups = [key for key in ("all", "any", "not") if key in condition]
        if groups:
            if len(groups) != 1:
                raise WorkflowConditionError("Condition group must use exactly one of all/any/not.")
            key = groups[0]
            values = condition[key] if key != "not" else [condition[key]]
            if not isinstance(values, list) or not values:
                raise WorkflowConditionError(f"Condition group {key} cannot be empty.")
            for item in values:
                cls._validate_condition(item)
            return
        if not isinstance(condition.get("path"), str) or not condition["path"].startswith(("input.", "nodes.")):
            raise WorkflowConditionError("Condition path must start with input. or nodes.")
        try:
            ConditionOperator(str(condition.get("operator", "eq")))
        except ValueError as exc:
            raise WorkflowConditionError("Unsupported condition operator.") from exc

    def _build_context(self, instance: WorkflowInstanceModel) -> dict[str, Any]:
        nodes: dict[str, Any] = {}
        for node_key, task_id in (instance.node_task_map_json or {}).items():
            task = self.session.get(TaskModel, task_id)
            if task is None:
                continue
            nodes[node_key] = {
                "task_id": task.id,
                "status": task.status,
                "payload": task.payload_json,
                "result": task.result_json,
            }
        return {"input": dict(instance.input_json or {}), "nodes": nodes}

    @staticmethod
    def _validate_input(schema: dict[str, Any], supplied: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(dict(schema.get("defaults", {})))
        result.update(deepcopy(supplied))
        missing = [name for name in schema.get("required", []) if name not in result]
        if missing:
            raise WorkflowTemplateError("Missing required workflow input: " + ", ".join(missing))
        return result

    @staticmethod
    def _render_text(value: str, data: dict[str, Any]) -> str:
        rendered = value
        for key, item in data.items():
            rendered = rendered.replace("{{ input." + str(key) + " }}", str(item))
        return rendered

    @staticmethod
    def _read_path(data: Any, path: str, missing: Any = ... ) -> Any:
        current = data
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            elif missing is not ...:
                return missing
            else:
                raise WorkflowTemplateError(f"Result mapping source not found: {path}")
        return current

    @staticmethod
    def _write_path(data: dict[str, Any], path: str, value: Any) -> None:
        parts = [part for part in path.split(".") if part]
        if not parts:
            raise WorkflowTemplateError("Result mapping target path is empty.")
        current = data
        for part in parts[:-1]:
            next_value = current.get(part)
            if not isinstance(next_value, dict):
                next_value = {}
                current[part] = next_value
            current = next_value
        current[parts[-1]] = value

    def _serialize_template(self, row: WorkflowTemplateModel) -> dict[str, Any]:
        validation = self.validate_definition(dict(row.definition_json or {}))
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "name": row.name,
            "version": row.version,
            "description": row.description,
            "definition": row.definition_json,
            "input_schema": row.input_schema_json,
            "metadata": row.metadata_json,
            "enabled": row.enabled,
            "validation": validation,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def _serialize_instance(self, row: WorkflowInstanceModel, *, include_tasks: bool) -> dict[str, Any]:
        payload = {
            "id": row.id,
            "template_id": row.template_id,
            "workspace_id": row.workspace_id,
            "root_task_id": row.root_task_id,
            "status": row.status,
            "input": row.input_json,
            "node_task_map": row.node_task_map_json,
            "context": row.context_json,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
        }
        if include_tasks:
            payload["tasks"] = {
                key: {
                    "id": task.id,
                    "status": task.status,
                    "result": task.result_json,
                }
                for key, task_id in (row.node_task_map_json or {}).items()
                if (task := self.session.get(TaskModel, task_id)) is not None
            }
        return payload
