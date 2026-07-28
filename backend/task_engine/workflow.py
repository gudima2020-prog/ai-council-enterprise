from __future__ import annotations

from collections import deque
from contextlib import AbstractContextManager
from typing import Any, Callable

from sqlalchemy.orm import Session

from backend.core.events import Event, EventBus
from backend.database.session import session_scope
from backend.task_engine.dependencies import (
    DependencyCreateRequest,
    DependencyCycleError,
    DependencyError,
    TaskDependencyRepository,
)
from backend.task_engine.enums import TaskStatus
from backend.task_engine.models import TaskModel
from backend.task_engine.queue import TaskQueue
from backend.task_engine.repository import TaskRepository
from backend.task_engine.schemas import TaskLogCreate
from backend.task_engine.state_machine import (
    InvalidTaskTransition,
    TaskStateMachine,
)
from backend.task_engine.workflow_templates import WorkflowTemplateService


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


class TaskWorkflowEngine:
    """Durable dependency graph and template-aware DAG coordinator."""

    def __init__(
        self,
        *,
        queue: TaskQueue,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._queue = queue
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._enqueued = 0
        self._blocked = 0
        self._dependency_failures = 0
        self._skipped = 0

    def stats(self) -> dict[str, Any]:
        return {
            "enqueued": self._enqueued,
            "blocked": self._blocked,
            "dependency_failures": self._dependency_failures,
            "skipped": self._skipped,
        }

    async def add_dependency(
        self,
        *,
        task_id: str,
        request: DependencyCreateRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            tasks = TaskRepository(session)
            dependencies = TaskDependencyRepository(session)
            task = tasks.get(task_id)
            upstream = tasks.get(request.depends_on_task_id)

            if task is None:
                raise DependencyError("Task не найдена.")
            if upstream is None:
                raise DependencyError("Dependency Task не найдена.")
            if task.id == upstream.id:
                raise DependencyError("Task не может зависеть от самой себя.")
            if task.workspace_id != upstream.workspace_id:
                raise DependencyError(
                    "Связанные Task должны принадлежать одному Workspace."
                )
            if task.status not in {
                TaskStatus.CREATED.value,
                TaskStatus.WAITING.value,
            }:
                raise DependencyError(
                    "Зависимости можно изменять только для Task "
                    "в статусе created или waiting."
                )
            if dependencies.get_pair(task.id, upstream.id) is not None:
                raise DependencyError("Такая зависимость уже существует.")
            if dependencies.would_create_cycle(
                task_id=task.id,
                depends_on_task_id=upstream.id,
            ):
                raise DependencyCycleError(
                    "Добавление зависимости создаёт цикл в DAG."
                )

            row = dependencies.add(
                task_id=task.id,
                depends_on_task_id=upstream.id,
                dependency_type=request.dependency_type,
                required_status=request.required_status,
            )
            payload = {
                "id": row.id,
                "task_id": row.task_id,
                "depends_on_task_id": row.depends_on_task_id,
                "dependency_type": row.dependency_type,
                "required_status": row.required_status,
                "created_at": row.created_at,
            }

        await self._event_bus.publish(
            Event(
                event_type="task.dependency.created",
                source="task_workflow_engine",
                payload=payload,
            )
        )
        return payload

    async def remove_dependency(
        self,
        *,
        task_id: str,
        depends_on_task_id: str,
    ) -> bool:
        with self._session_factory() as session:
            tasks = TaskRepository(session)
            dependencies = TaskDependencyRepository(session)
            task = tasks.get(task_id)

            if task is None:
                raise DependencyError("Task не найдена.")
            if task.status not in {
                TaskStatus.CREATED.value,
                TaskStatus.WAITING.value,
            }:
                raise DependencyError(
                    "Зависимости можно изменять только для Task "
                    "в статусе created или waiting."
                )
            removed = dependencies.remove(task_id, depends_on_task_id)

        if removed:
            await self._event_bus.publish(
                Event(
                    event_type="task.dependency.deleted",
                    source="task_workflow_engine",
                    payload={
                        "task_id": task_id,
                        "depends_on_task_id": depends_on_task_id,
                    },
                )
            )
        return removed

    def list_dependencies(self, task_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            tasks = TaskRepository(session)
            dependencies = TaskDependencyRepository(session)
            if tasks.get(task_id) is None:
                return None
            return dependencies.evaluate(task_id).as_dict(task_id)

    def graph(self, task_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            tasks = TaskRepository(session)
            dependencies = TaskDependencyRepository(session)
            if tasks.get(task_id) is None:
                return None

            connected = dependencies.connected_task_ids(task_id)
            order = dependencies.topological_order(connected)
            all_edges = dependencies.list_all()
            nodes: list[dict[str, Any]] = []

            for node_id in order:
                task = tasks.get(node_id)
                if task is None:
                    continue
                nodes.append(
                    {
                        "id": task.id,
                        "title": task.title,
                        "task_type": task.task_type,
                        "status": task.status,
                        "priority": task.priority,
                        "workspace_id": task.workspace_id,
                    }
                )

            edges = [
                {
                    "id": edge.id,
                    "from": edge.depends_on_task_id,
                    "to": edge.task_id,
                    "dependency_type": edge.dependency_type,
                    "required_status": edge.required_status,
                }
                for edge in all_edges
                if edge.task_id in connected
                and edge.depends_on_task_id in connected
            ]
            validation = dependencies.validate_dag()
            return {
                "root_task_id": task_id,
                "valid": validation["valid"],
                "cycle": validation["cycle"],
                "topological_order": order,
                "nodes": nodes,
                "edges": edges,
                "summary": self._summary(nodes),
            }

    def validate(self) -> dict[str, Any]:
        with self._session_factory() as session:
            return TaskDependencyRepository(session).validate_dag()

    async def enqueue_task(
        self,
        task_id: str,
        *,
        source: str,
        propagate_terminal: bool = True,
    ) -> dict[str, Any] | None:
        queue_data: tuple[str, str, str | None] | None = None
        terminal = False

        with self._session_factory() as session:
            tasks = TaskRepository(session)
            dependencies = TaskDependencyRepository(session)
            task = tasks.get(task_id)
            if task is None:
                return None

            result = self._prepare_task(
                task=task,
                tasks=tasks,
                dependencies=dependencies,
                session=session,
            )
            if result["ready"]:
                queue_data = (task.id, task.priority, task.workspace_id)
            terminal = bool(result.get("terminal"))

        if queue_data is not None:
            added = await self._queue.put(
                task_id=queue_data[0],
                priority=queue_data[1],
                metadata={
                    "workspace_id": queue_data[2],
                    "source": source,
                    "dependency_checked": True,
                },
            )
            if added:
                self._enqueued += 1
            result.update({"enqueued": added, "queue_size": self._queue.qsize()})

            await self._event_bus.publish(
                Event(
                    event_type="task.workflow.enqueued",
                    source="task_workflow_engine",
                    workspace_id=queue_data[2],
                    payload=result,
                )
            )
        elif terminal and propagate_terminal:
            await self.handle_terminal_task(task_id)

        return result

    async def start_workflow(self, task_id: str) -> dict[str, Any] | None:
        graph = self.graph(task_id)
        if graph is None:
            return None
        if not graph["valid"]:
            raise DependencyCycleError("Workflow DAG содержит цикл.")

        results = []
        for node_id in graph["topological_order"]:
            result = await self.enqueue_task(
                node_id,
                source="workflow_start",
            )
            if result is not None:
                results.append(result)

        refreshed = self.graph(task_id)
        assert refreshed is not None
        return {
            "root_task_id": task_id,
            "valid": True,
            "results": results,
            "graph": refreshed,
        }

    async def ensure_ready_for_execution(
        self,
        task_id: str,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            tasks = TaskRepository(session)
            dependencies = TaskDependencyRepository(session)
            task = tasks.get(task_id)
            if task is None:
                return None

            evaluation = dependencies.evaluate(task_id)
            result = evaluation.as_dict(task_id)

            if evaluation.failed_by:
                self._mark_dependency_failed(task, tasks, evaluation.failed_by)
                self._dependency_failures += 1
                result.update({"ready": False, "terminal": True, "status": TaskStatus.FAILED.value})
                return result
            if not evaluation.ready:
                self._move_to_waiting(task, tasks, evaluation.blocked_by)
                self._blocked += 1
                result.update({"ready": False, "terminal": False, "status": TaskStatus.WAITING.value})
                return result

            runtime = WorkflowTemplateService(
                session,
                self._event_bus,
            ).prepare_task(task_id)

            if runtime["action"] == "skipped":
                self._skipped += 1
                result.update(
                    {
                        "ready": False,
                        "terminal": True,
                        "status": TaskStatus.SKIPPED.value,
                        "runtime": runtime,
                    }
                )
                return result

            if runtime["action"] == "approval_pending":
                self._blocked += 1
                result.update(
                    {
                        "ready": False,
                        "terminal": False,
                        "status": TaskStatus.WAITING.value,
                        "runtime": runtime,
                        "approval_pending": True,
                    }
                )
                return result

            if runtime["action"] == "approval_rejected":
                result.update(
                    {
                        "ready": False,
                        "terminal": True,
                        "status": TaskStatus.CANCELLED.value,
                        "runtime": runtime,
                    }
                )
                return result

            result.update(
                {
                    "ready": True,
                    "terminal": False,
                    "status": task.status,
                    "runtime": runtime,
                }
            )
            return result

    async def handle_terminal_task(self, task_id: str) -> dict[str, Any]:
        pending = deque([task_id])
        visited: set[str] = set()
        enqueued: list[str] = []
        terminal_followups: list[str] = []

        while pending:
            current = pending.popleft()
            if current in visited:
                continue
            visited.add(current)

            dependent_ids: list[str] = []
            with self._session_factory() as session:
                dependencies = TaskDependencyRepository(session)
                dependent_ids = sorted(
                    {edge.task_id for edge in dependencies.list_dependents(current)}
                )
                task = session.get(TaskModel, current)
                if task is not None:
                    meta = (task.payload_json or {}).get("_workflow")
                    if isinstance(meta, dict) and meta.get("instance_id"):
                        WorkflowTemplateService(session, self._event_bus).refresh_instance(
                            str(meta["instance_id"])
                        )

            for dependent_id in dependent_ids:
                result = await self.enqueue_task(
                    dependent_id,
                    source="dependency_resolved",
                    propagate_terminal=False,
                )
                if result is None:
                    continue
                if result.get("enqueued"):
                    enqueued.append(dependent_id)
                elif result.get("terminal"):
                    terminal_followups.append(dependent_id)
                    pending.append(dependent_id)

        workflow = self.graph(task_id)
        if workflow is not None:
            statuses = {node["status"] for node in workflow["nodes"]}
            terminal_statuses = {
                TaskStatus.COMPLETED.value,
                TaskStatus.FAILED.value,
                TaskStatus.CANCELLED.value,
                TaskStatus.SKIPPED.value,
            }
            if statuses and statuses <= terminal_statuses:
                event_type = (
                    "task.workflow.completed"
                    if not statuses & {TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}
                    else "task.workflow.finished_with_errors"
                )
                await self._event_bus.publish(
                    Event(
                        event_type=event_type,
                        source="task_workflow_engine",
                        payload={
                            "root_task_id": task_id,
                            "summary": workflow["summary"],
                        },
                    )
                )

        return {
            "source_task_id": task_id,
            "enqueued": enqueued,
            "terminal_followups": terminal_followups,
        }

    async def reconcile_all(self) -> dict[str, int]:
        with self._session_factory() as session:
            dependencies = TaskDependencyRepository(session)
            task_ids = sorted({row.task_id for row in dependencies.list_all()})

        enqueued = blocked = failed = skipped = approval_pending = 0
        for task_id in task_ids:
            result = await self.enqueue_task(task_id, source="workflow_reconcile")
            if result is None:
                continue
            if result.get("enqueued"):
                enqueued += 1
            elif result.get("status") == TaskStatus.SKIPPED.value:
                skipped += 1
            elif result.get("approval_pending"):
                approval_pending += 1
            elif result.get("failed_by"):
                failed += 1
            elif result.get("blocked_by"):
                blocked += 1

        return {
            "enqueued": enqueued,
            "blocked": blocked,
            "dependency_failed": failed,
            "skipped": skipped,
            "approval_pending": approval_pending,
        }

    def _prepare_task(
        self,
        *,
        task: TaskModel,
        tasks: TaskRepository,
        dependencies: TaskDependencyRepository,
        session: Session,
    ) -> dict[str, Any]:
        if task.status in {
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
            TaskStatus.SKIPPED.value,
            TaskStatus.RUNNING.value,
            TaskStatus.PLANNING.value,
            TaskStatus.POST_PROCESSING.value,
        }:
            raise InvalidTaskTransition(
                "Task в текущем статусе нельзя добавить в workflow queue: "
                f"{task.status}."
            )

        evaluation = dependencies.evaluate(task.id)
        result = evaluation.as_dict(task.id)
        result.update({"status": task.status, "enqueued": False, "terminal": False})

        if evaluation.failed_by:
            self._mark_dependency_failed(task, tasks, evaluation.failed_by)
            result.update({"status": TaskStatus.FAILED.value, "terminal": True})
            self._dependency_failures += 1
            return result

        if not evaluation.ready:
            self._move_to_waiting(task, tasks, evaluation.blocked_by)
            result["status"] = TaskStatus.WAITING.value
            self._blocked += 1
            return result

        runtime = WorkflowTemplateService(
            session,
            self._event_bus,
        ).prepare_task(task.id)
        result["runtime"] = runtime

        if runtime["action"] == "skipped":
            result.update(
                {
                    "ready": False,
                    "status": TaskStatus.SKIPPED.value,
                    "terminal": True,
                }
            )
            self._skipped += 1
            return result

        if runtime["action"] == "approval_pending":
            result.update(
                {
                    "ready": False,
                    "status": TaskStatus.WAITING.value,
                    "terminal": False,
                    "approval_pending": True,
                }
            )
            self._blocked += 1
            return result

        if runtime["action"] == "approval_rejected":
            result.update(
                {
                    "ready": False,
                    "status": TaskStatus.CANCELLED.value,
                    "terminal": True,
                }
            )
            return result

        self._move_to_queued(task, tasks)
        result.update(
            {
                "ready": True,
                "status": TaskStatus.QUEUED.value,
            }
        )
        return result

    @staticmethod
    def _move_to_queued(task: TaskModel, tasks: TaskRepository) -> None:
        if task.status == TaskStatus.QUEUED.value:
            return
        TaskStateMachine.transition(task, TaskStatus.QUEUED)
        tasks.append_log(
            task.id,
            TaskLogCreate(
                level="INFO",
                message="Task dependencies and workflow policy satisfied; queued.",
                metadata={"workflow": True},
            ),
        )

    @staticmethod
    def _move_to_waiting(
        task: TaskModel,
        tasks: TaskRepository,
        blocked_by: tuple[str, ...],
    ) -> None:
        if task.status != TaskStatus.WAITING.value:
            TaskStateMachine.transition(task, TaskStatus.WAITING)
        tasks.append_log(
            task.id,
            TaskLogCreate(
                level="INFO",
                message="Task is waiting for dependencies.",
                metadata={"workflow": True, "blocked_by": list(blocked_by)},
            ),
        )

    @staticmethod
    def _mark_dependency_failed(
        task: TaskModel,
        tasks: TaskRepository,
        failed_by: tuple[str, ...],
    ) -> None:
        if task.status != TaskStatus.FAILED.value:
            TaskStateMachine.transition(task, TaskStatus.FAILED)
        tasks.append_log(
            task.id,
            TaskLogCreate(
                level="ERROR",
                message="Task failed because a hard dependency failed.",
                metadata={"workflow": True, "failed_by": list(failed_by)},
            ),
        )

    @staticmethod
    def _summary(nodes: list[dict[str, Any]]) -> dict[str, int]:
        summary: dict[str, int] = {}
        for node in nodes:
            status = str(node["status"])
            summary[status] = summary.get(status, 0) + 1
        return summary
