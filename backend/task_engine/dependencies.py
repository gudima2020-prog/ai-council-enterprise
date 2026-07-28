from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.task_engine.enums import TaskStatus
from backend.task_engine.models import TaskDependencyModel, TaskModel


class TaskDependencyType(StrEnum):
    HARD = "hard"
    SOFT = "soft"


class DependencyCreateRequest(BaseModel):
    depends_on_task_id: str = Field(..., min_length=1, max_length=64)
    dependency_type: TaskDependencyType = TaskDependencyType.HARD
    required_status: TaskStatus = TaskStatus.COMPLETED


class DependencyError(ValueError):
    pass


class DependencyCycleError(DependencyError):
    pass


@dataclass(frozen=True)
class DependencyEvaluation:
    ready: bool
    blocked_by: tuple[str, ...]
    failed_by: tuple[str, ...]
    dependencies: tuple[dict[str, Any], ...]

    def as_dict(self, task_id: str) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "ready": self.ready,
            "blocked_by": list(self.blocked_by),
            "failed_by": list(self.failed_by),
            "dependencies": list(self.dependencies),
        }


class TaskDependencyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(
        self,
        *,
        task_id: str,
        depends_on_task_id: str,
        dependency_type: TaskDependencyType,
        required_status: TaskStatus,
    ) -> TaskDependencyModel:
        row = TaskDependencyModel(
            task_id=task_id,
            depends_on_task_id=depends_on_task_id,
            dependency_type=dependency_type.value,
            required_status=required_status.value,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_pair(
        self,
        task_id: str,
        depends_on_task_id: str,
    ) -> TaskDependencyModel | None:
        statement = select(TaskDependencyModel).where(
            TaskDependencyModel.task_id == task_id,
            TaskDependencyModel.depends_on_task_id == depends_on_task_id,
        )
        return self.session.scalar(statement)

    def remove(
        self,
        task_id: str,
        depends_on_task_id: str,
    ) -> bool:
        row = self.get_pair(task_id, depends_on_task_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.flush()
        return True

    def list_for_task(self, task_id: str) -> list[TaskDependencyModel]:
        statement = (
            select(TaskDependencyModel)
            .where(TaskDependencyModel.task_id == task_id)
            .order_by(TaskDependencyModel.created_at.asc())
        )
        return list(self.session.scalars(statement).all())

    def list_dependents(
        self,
        depends_on_task_id: str,
    ) -> list[TaskDependencyModel]:
        statement = (
            select(TaskDependencyModel)
            .where(
                TaskDependencyModel.depends_on_task_id
                == depends_on_task_id
            )
            .order_by(TaskDependencyModel.created_at.asc())
        )
        return list(self.session.scalars(statement).all())

    def list_all(self) -> list[TaskDependencyModel]:
        statement = select(TaskDependencyModel).order_by(
            TaskDependencyModel.created_at.asc()
        )
        return list(self.session.scalars(statement).all())

    def adjacency(self) -> dict[str, set[str]]:
        graph: dict[str, set[str]] = {}
        for row in self.list_all():
            graph.setdefault(row.task_id, set()).add(
                row.depends_on_task_id
            )
            graph.setdefault(row.depends_on_task_id, set())
        return graph

    def would_create_cycle(
        self,
        *,
        task_id: str,
        depends_on_task_id: str,
    ) -> bool:
        graph = self.adjacency()
        graph.setdefault(task_id, set()).add(depends_on_task_id)
        stack = [depends_on_task_id]
        visited: set[str] = set()

        while stack:
            current = stack.pop()
            if current == task_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            stack.extend(graph.get(current, set()))
        return False

    def validate_dag(self) -> dict[str, Any]:
        graph = self.adjacency()
        visiting: set[str] = set()
        visited: set[str] = set()
        cycle_path: list[str] = []

        def visit(node: str, path: list[str]) -> bool:
            nonlocal cycle_path
            if node in visiting:
                start = path.index(node)
                cycle_path = path[start:] + [node]
                return False
            if node in visited:
                return True

            visiting.add(node)
            path.append(node)
            for dependency in sorted(graph.get(node, set())):
                if not visit(dependency, path):
                    return False
            path.pop()
            visiting.remove(node)
            visited.add(node)
            return True

        valid = all(
            visit(node, [])
            for node in sorted(graph)
            if node not in visited
        )
        return {
            "valid": valid,
            "cycle": cycle_path,
            "nodes": len(graph),
            "edges": sum(len(items) for items in graph.values()),
        }

    def connected_task_ids(self, task_id: str) -> set[str]:
        dependencies = self.adjacency()
        dependents: dict[str, set[str]] = {}
        for child, parents in dependencies.items():
            for parent in parents:
                dependents.setdefault(parent, set()).add(child)

        connected: set[str] = set()
        stack = [task_id]
        while stack:
            current = stack.pop()
            if current in connected:
                continue
            connected.add(current)
            stack.extend(dependencies.get(current, set()))
            stack.extend(dependents.get(current, set()))
        return connected

    def topological_order(self, task_ids: set[str]) -> list[str]:
        dependencies = self.adjacency()
        indegree = {task_id: 0 for task_id in task_ids}
        outgoing = {task_id: set() for task_id in task_ids}

        for child in task_ids:
            for parent in dependencies.get(child, set()):
                if parent not in task_ids:
                    continue
                indegree[child] += 1
                outgoing[parent].add(child)

        ready = sorted(
            task_id
            for task_id, degree in indegree.items()
            if degree == 0
        )
        order: list[str] = []
        while ready:
            current = ready.pop(0)
            order.append(current)
            for child in sorted(outgoing[current]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
                    ready.sort()

        if len(order) != len(task_ids):
            raise DependencyCycleError(
                "Task dependency graph contains a cycle."
            )
        return order

    def evaluate(self, task_id: str) -> DependencyEvaluation:
        blocked_by: list[str] = []
        failed_by: list[str] = []
        serialized: list[dict[str, Any]] = []
        terminal = {
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
            TaskStatus.SKIPPED.value,
        }

        for dependency in self.list_for_task(task_id):
            upstream = self.session.get(
                TaskModel,
                dependency.depends_on_task_id,
            )
            upstream_status = upstream.status if upstream else "missing"
            dependency_type = TaskDependencyType(
                dependency.dependency_type
            )
            satisfied = False
            failed = False

            if upstream is None:
                failed = True
            elif dependency_type == TaskDependencyType.SOFT:
                satisfied = upstream.status in terminal
            elif upstream.status == dependency.required_status:
                satisfied = True
            elif upstream.status in terminal:
                failed = True

            if not satisfied:
                if failed:
                    failed_by.append(dependency.depends_on_task_id)
                else:
                    blocked_by.append(dependency.depends_on_task_id)

            serialized.append(
                {
                    "id": dependency.id,
                    "task_id": dependency.task_id,
                    "depends_on_task_id": dependency.depends_on_task_id,
                    "dependency_type": dependency.dependency_type,
                    "required_status": dependency.required_status,
                    "upstream_status": upstream_status,
                    "satisfied": satisfied,
                    "failed": failed,
                    "created_at": dependency.created_at,
                }
            )

        return DependencyEvaluation(
            ready=not blocked_by and not failed_by,
            blocked_by=tuple(blocked_by),
            failed_by=tuple(failed_by),
            dependencies=tuple(serialized),
        )
