from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.orchestration.models import (
    ExecutionPlanModel,
    ExecutionPlanStepModel,
    ExecutionStepRunModel,
)
from backend.orchestration.schemas import (
    ExecutionPlanCreate,
    ExecutionPlanStepCreate,
    ExecutionPlanStepUpdate,
    ExecutionPlanUpdate,
)


class ExecutionPlanRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, request: ExecutionPlanCreate) -> ExecutionPlanModel:
        row = ExecutionPlanModel(
            workspace_id=request.workspace_id,
            source_task_id=request.source_task_id,
            title=request.title,
            objective=request.objective,
            strategy=request.strategy.strip(),
            version=request.version,
            max_parallel_steps=request.max_parallel_steps,
            planner=request.planner,
            metadata_json=request.metadata,
        )
        self.session.add(row)
        self.session.flush()

        for step in request.steps:
            self.add_step(row.id, step)

        self.session.flush()
        return row

    def get(self, plan_id: str) -> ExecutionPlanModel | None:
        return self.session.get(ExecutionPlanModel, plan_id)

    def get_full(self, plan_id: str) -> ExecutionPlanModel | None:
        statement = (
            select(ExecutionPlanModel)
            .options(selectinload(ExecutionPlanModel.steps))
            .where(ExecutionPlanModel.id == plan_id)
            .execution_options(populate_existing=True)
        )
        return self.session.scalar(statement)

    def list(
        self,
        *,
        workspace_id: str | None = None,
        source_task_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ExecutionPlanModel]:
        statement = select(ExecutionPlanModel)

        if workspace_id is not None:
            statement = statement.where(
                ExecutionPlanModel.workspace_id == workspace_id
            )
        if source_task_id is not None:
            statement = statement.where(
                ExecutionPlanModel.source_task_id == source_task_id
            )
        if status is not None:
            statement = statement.where(
                ExecutionPlanModel.status == status
            )

        statement = (
            statement
            .order_by(ExecutionPlanModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.session.scalars(statement).all())

    def update(
        self,
        row: ExecutionPlanModel,
        request: ExecutionPlanUpdate,
    ) -> ExecutionPlanModel:
        values = request.model_dump(exclude_unset=True)

        if "title" in values:
            row.title = values["title"]
        if "objective" in values:
            row.objective = values["objective"]
        if "strategy" in values:
            row.strategy = values["strategy"].strip()
        if "max_parallel_steps" in values:
            row.max_parallel_steps = values["max_parallel_steps"]
        if "planner" in values:
            row.planner = values["planner"]
        if "metadata" in values:
            row.metadata_json = values["metadata"]

        self.session.flush()
        return row

    def delete(self, row: ExecutionPlanModel) -> None:
        self.session.delete(row)
        self.session.flush()

    def get_step(
        self,
        plan_id: str,
        step_id: str,
    ) -> ExecutionPlanStepModel | None:
        statement = select(ExecutionPlanStepModel).where(
            ExecutionPlanStepModel.plan_id == plan_id,
            ExecutionPlanStepModel.id == step_id,
        )
        return self.session.scalar(statement)

    def get_step_by_key(
        self,
        plan_id: str,
        step_key: str,
    ) -> ExecutionPlanStepModel | None:
        statement = select(ExecutionPlanStepModel).where(
            ExecutionPlanStepModel.plan_id == plan_id,
            ExecutionPlanStepModel.step_key == step_key,
        )
        return self.session.scalar(statement)

    def list_steps(self, plan_id: str) -> list[ExecutionPlanStepModel]:
        statement = (
            select(ExecutionPlanStepModel)
            .where(ExecutionPlanStepModel.plan_id == plan_id)
            .order_by(
                ExecutionPlanStepModel.sequence.asc(),
                ExecutionPlanStepModel.step_key.asc(),
            )
        )
        return list(self.session.scalars(statement).all())

    def add_step(
        self,
        plan_id: str,
        request: ExecutionPlanStepCreate,
    ) -> ExecutionPlanStepModel:
        row = ExecutionPlanStepModel(
            plan_id=plan_id,
            step_key=request.step_key,
            sequence=request.sequence,
            step_type=request.step_type.value,
            title=request.title,
            description=request.description.strip(),
            agent_role=request.agent_role,
            capability=request.capability,
            tool_name=request.tool_name,
            input_json=request.input,
            depends_on_json=request.depends_on,
            condition_json=request.condition,
            timeout_seconds=request.timeout_seconds,
            max_retries=request.max_retries,
            metadata_json=request.metadata,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def update_step(
        self,
        row: ExecutionPlanStepModel,
        request: ExecutionPlanStepUpdate,
    ) -> ExecutionPlanStepModel:
        values = request.model_dump(exclude_unset=True)

        mapping = {
            "step_key": "step_key",
            "sequence": "sequence",
            "title": "title",
            "description": "description",
            "agent_role": "agent_role",
            "capability": "capability",
            "tool_name": "tool_name",
            "input": "input_json",
            "depends_on": "depends_on_json",
            "condition": "condition_json",
            "timeout_seconds": "timeout_seconds",
            "max_retries": "max_retries",
            "metadata": "metadata_json",
        }

        if "step_type" in values:
            row.step_type = values.pop("step_type").value

        for source, target in mapping.items():
            if source not in values:
                continue
            value = values[source]
            if source == "description" and value is not None:
                value = value.strip()
            setattr(row, target, value)

        self.session.flush()
        return row

    def delete_step(self, row: ExecutionPlanStepModel) -> None:
        self.session.delete(row)
        self.session.flush()


    def create_step_run(
        self,
        *,
        step: ExecutionPlanStepModel,
        attempt: int,
        input_data: dict,
        agent_id: str | None,
        executor_ref: str | None,
        metadata: dict | None = None,
    ) -> ExecutionStepRunModel:
        row = ExecutionStepRunModel(
            plan_id=step.plan_id,
            step_id=step.id,
            step_key=step.step_key,
            attempt=attempt,
            status="running",
            agent_id=agent_id,
            executor_ref=executor_ref,
            input_json=input_data,
            metadata_json=metadata or {},
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_step_run(
        self,
        run_id: str,
    ) -> ExecutionStepRunModel | None:
        return self.session.get(ExecutionStepRunModel, run_id)

    def list_step_runs(
        self,
        *,
        plan_id: str | None = None,
        step_id: str | None = None,
    ) -> list[ExecutionStepRunModel]:
        statement = select(ExecutionStepRunModel)

        if plan_id is not None:
            statement = statement.where(
                ExecutionStepRunModel.plan_id == plan_id
            )
        if step_id is not None:
            statement = statement.where(
                ExecutionStepRunModel.step_id == step_id
            )

        statement = statement.order_by(
            ExecutionStepRunModel.created_at.asc(),
            ExecutionStepRunModel.attempt.asc(),
        )
        return list(self.session.scalars(statement).all())

    def next_step_attempt(self, step_id: str) -> int:
        runs = self.list_step_runs(step_id=step_id)
        return max((row.attempt for row in runs), default=0) + 1
