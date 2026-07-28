from __future__ import annotations

from typing import Any, Protocol

from backend.orchestration.enums import ExecutionStepType


class PlanStep(Protocol):
    step_key: str
    sequence: int
    step_type: str
    agent_role: str | None
    tool_name: str | None
    depends_on_json: list[str]


class ExecutionPlanValidator:
    @classmethod
    def validate(
        cls,
        steps: list[PlanStep],
        *,
        max_parallel_steps: int,
    ) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []

        if not steps:
            return {
                "valid": False,
                "errors": ["Execution Plan должен содержать хотя бы один шаг."],
                "warnings": [],
                "topological_order": [],
                "parallel_groups": [],
                "roots": [],
                "leaves": [],
                "max_width": 0,
                "step_count": 0,
                "edge_count": 0,
            }

        by_key: dict[str, PlanStep] = {}
        duplicates: set[str] = set()

        for step in steps:
            if step.step_key in by_key:
                duplicates.add(step.step_key)
            by_key[step.step_key] = step

        if duplicates:
            errors.append(
                "Повторяющиеся step_key: "
                + ", ".join(sorted(duplicates))
                + "."
            )

        graph: dict[str, set[str]] = {}
        outgoing: dict[str, set[str]] = {
            key: set() for key in by_key
        }

        for key, step in by_key.items():
            dependencies = set(step.depends_on_json or [])
            graph[key] = dependencies

            if key in dependencies:
                errors.append(f"Шаг {key} не может зависеть от самого себя.")

            missing = sorted(dependencies - set(by_key))
            if missing:
                errors.append(
                    f"Шаг {key} содержит неизвестные зависимости: "
                    + ", ".join(missing)
                    + "."
                )

            for dependency in dependencies & set(by_key):
                outgoing[dependency].add(key)

            step_type = ExecutionStepType(step.step_type)
            if step_type == ExecutionStepType.AGENT and not step.agent_role:
                errors.append(
                    f"Для agent-шага {key} требуется agent_role."
                )
            if step_type == ExecutionStepType.TOOL and not step.tool_name:
                errors.append(
                    f"Для tool-шага {key} требуется tool_name."
                )

        order, groups, cyclic = cls._topological_layers(
            by_key,
            graph,
            outgoing,
        )

        if cyclic:
            errors.append(
                "Execution Plan содержит цикл: "
                + ", ".join(cyclic)
                + "."
            )

        roots = sorted(
            key for key, dependencies in graph.items() if not dependencies
        )
        leaves = sorted(
            key for key, children in outgoing.items() if not children
        )
        max_width = max((len(group) for group in groups), default=0)

        if max_width > max_parallel_steps:
            warnings.append(
                "DAG допускает до "
                f"{max_width} параллельных шагов, но Plan ограничен "
                f"значением {max_parallel_steps}."
            )

        if len(roots) > 1:
            warnings.append(
                "Plan содержит несколько стартовых шагов: "
                + ", ".join(roots)
                + "."
            )

        return {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "topological_order": order if not cyclic else [],
            "parallel_groups": groups if not cyclic else [],
            "roots": roots,
            "leaves": leaves,
            "max_width": max_width,
            "step_count": len(by_key),
            "edge_count": sum(len(items) for items in graph.values()),
        }

    @staticmethod
    def _topological_layers(
        by_key: dict[str, PlanStep],
        graph: dict[str, set[str]],
        outgoing: dict[str, set[str]],
    ) -> tuple[list[str], list[list[str]], list[str]]:
        indegree = {
            key: len(dependencies & set(by_key))
            for key, dependencies in graph.items()
        }

        def sort_key(key: str) -> tuple[int, str]:
            return by_key[key].sequence, key

        current = sorted(
            (key for key, degree in indegree.items() if degree == 0),
            key=sort_key,
        )
        order: list[str] = []
        groups: list[list[str]] = []

        while current:
            groups.append(list(current))
            next_group: list[str] = []

            for key in current:
                order.append(key)
                for child in sorted(outgoing.get(key, set()), key=sort_key):
                    indegree[child] -= 1
                    if indegree[child] == 0:
                        next_group.append(child)

            current = sorted(set(next_group), key=sort_key)

        cyclic = sorted(
            key for key, degree in indegree.items() if degree > 0
        )
        return order, groups, cyclic
