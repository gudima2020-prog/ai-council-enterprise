from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database import models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.task_engine.governance import (
    GovernanceControl,
    TaskGovernancePolicy,
    TaskRiskClass,
)
from backend.task_engine.governance_materialization import (
    GOVERNANCE_PAYLOAD_KEY,
    GovernanceMaterializationError,
    GovernanceMaterializationSpec,
    TaskGovernanceMaterializer,
)
from backend.task_engine.repository import TaskRepository
from backend.task_engine.schemas import TaskCreate, TaskUpdate
from backend.task_engine.workflow_templates import (
    WorkflowEdgeDefinition,
    WorkflowInstantiateRequest,
    WorkflowNodeDefinition,
    WorkflowTemplateCreate,
    WorkflowTemplateError,
    WorkflowTemplateService,
)


def make_scope():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return scope


def governance_spec(
    *,
    revision_id: str = "rev_001",
    risk_class: TaskRiskClass = TaskRiskClass.HIGH,
) -> GovernanceMaterializationSpec:
    return GovernanceMaterializationSpec(
        revision_id=revision_id,
        risk_class=risk_class,
        execution_initiator="human",
        requested_capabilities=(
            "shell.execute",
            "github.issue.comment",
        ),
        policy_tags=("release",),
    )


def create_task(repository: TaskRepository, title: str = "Governed") -> object:
    return repository.create(
        TaskCreate(
            title=title,
            payload={"action": "echo", "value": "original"},
        )
    )


def test_materializes_real_task_and_recomputes_high_risk_plan() -> None:
    scope = make_scope()

    with scope() as session:
        repository = TaskRepository(session)
        task = create_task(repository)

        envelope = repository.materialize_governance(
            task,
            governance_spec(),
        )

        assert envelope["authorization_state"] == "requirements_only"
        assert envelope["plan"]["request"]["task_id"] == task.id
        assert (
            GovernanceControl.INDEPENDENT_REVIEW.value
            in envelope["plan"]["controls"]
        )
        assert envelope["plan"]["requested_capabilities"] == [
            "github.issue.comment",
            "shell.execute",
        ]
        assert "granted_capabilities" not in envelope["plan"]

        validation = repository.validate_governance(task)
        assert validation is not None
        assert validation["valid"] is True
        assert validation["task_id"] == task.id

        plan = repository.recompute_governance_plan(task)
        assert plan.fingerprint == envelope["plan_fingerprint"]
        assert plan.request.revision_id == "rev_001"


def test_exact_rematerialization_is_idempotent_but_change_fails() -> None:
    scope = make_scope()

    with scope() as session:
        repository = TaskRepository(session)
        task = create_task(repository)
        first = repository.materialize_governance(task, governance_spec())
        second = repository.materialize_governance(task, governance_spec())

        assert first == second

        with pytest.raises(
            GovernanceMaterializationError,
            match="different request or policy",
        ):
            repository.materialize_governance(
                task,
                governance_spec(revision_id="rev_002"),
            )


def test_materialized_governance_tampering_fails_closed() -> None:
    scope = make_scope()

    mutations = [
        lambda raw: raw["plan"].__setitem__(
            "human_gate_required",
            not raw["plan"]["human_gate_required"],
        ),
        lambda raw: raw["policy_snapshot"].__setitem__(
            "human_gate_min_risk",
            "low",
        ),
        lambda raw: raw["plan"]["request"].__setitem__(
            "revision_id",
            "rev_tampered",
        ),
        lambda raw: raw.__setitem__("plan_fingerprint", "0" * 64),
        lambda raw: raw["plan"]["request"].__setitem__(
            "task_id",
            "task_other",
        ),
    ]

    with scope() as session:
        repository = TaskRepository(session)

        for index, mutate in enumerate(mutations):
            task = create_task(repository, title=f"Governed {index}")
            repository.materialize_governance(task, governance_spec())

            payload = deepcopy(task.payload_json)
            mutate(payload[GOVERNANCE_PAYLOAD_KEY])
            task.payload_json = payload
            session.flush()

            with pytest.raises(GovernanceMaterializationError):
                repository.validate_governance(task)


def test_reserved_governance_key_cannot_be_injected() -> None:
    scope = make_scope()

    with scope() as session:
        repository = TaskRepository(session)

        with pytest.raises(
            GovernanceMaterializationError,
            match="reserved",
        ):
            repository.create(
                TaskCreate(
                    title="Injected",
                    payload={GOVERNANCE_PAYLOAD_KEY: {}},
                )
            )

        task = create_task(repository)
        with pytest.raises(
            GovernanceMaterializationError,
            match="cannot be injected",
        ):
            repository.update(
                task,
                TaskUpdate(
                    payload={GOVERNANCE_PAYLOAD_KEY: {}},
                ),
            )


def test_generic_payload_update_preserves_governance_and_rejects_change() -> None:
    scope = make_scope()

    with scope() as session:
        repository = TaskRepository(session)
        task = create_task(repository)
        envelope = repository.materialize_governance(task, governance_spec())

        repository.update(
            task,
            TaskUpdate(payload={"action": "echo", "value": "changed"}),
        )

        assert task.payload_json["value"] == "changed"
        assert task.payload_json[GOVERNANCE_PAYLOAD_KEY] == envelope

        changed = deepcopy(envelope)
        changed["plan_fingerprint"] = "f" * 64

        with pytest.raises(
            GovernanceMaterializationError,
            match="cannot be changed",
        ):
            repository.update(
                task,
                TaskUpdate(
                    payload={
                        "action": "echo",
                        GOVERNANCE_PAYLOAD_KEY: changed,
                    }
                ),
            )


def test_custom_policy_snapshot_is_recomputed_not_trusted() -> None:
    scope = make_scope()
    policy = replace(
        TaskGovernancePolicy(),
        human_gate_min_risk=TaskRiskClass.HIGH,
    )
    materializer = TaskGovernanceMaterializer(policy)

    with scope() as session:
        repository = TaskRepository(
            session,
            governance_materializer=materializer,
        )
        task = create_task(repository)
        envelope = repository.materialize_governance(task, governance_spec())

        assert envelope["plan"]["human_gate_required"] is True
        assert envelope["policy_snapshot"] == policy.to_dict()
        assert repository.validate_governance(task)["valid"] is True


@pytest.mark.asyncio
async def test_workflow_materializes_only_selected_real_node_tasks() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        service = WorkflowTemplateService(session, event_bus)
        template = await service.create_template(
            WorkflowTemplateCreate(
                name="Governed workflow",
                root_node_key="final",
                nodes=[
                    WorkflowNodeDefinition(
                        key="source",
                        title="Source",
                        payload={"action": "echo", "value": "source"},
                    ),
                    WorkflowNodeDefinition(
                        key="final",
                        title="Final",
                        payload={"action": "echo", "value": "final"},
                    ),
                ],
                edges=[
                    WorkflowEdgeDefinition(
                        from_node="source",
                        to_node="final",
                    )
                ],
            )
        )
        instance = await service.instantiate(
            template["id"],
            WorkflowInstantiateRequest(creator="test"),
        )
        assert instance is not None

        result = service.materialize_governance(
            instance["id"],
            {"source": governance_spec()},
        )
        assert result is not None
        assert set(result["materialized"]) == {"source"}

        source = session.get(
            task_models.TaskModel,
            instance["node_task_map"]["source"],
        )
        final = session.get(
            task_models.TaskModel,
            instance["node_task_map"]["final"],
        )
        assert source is not None
        assert final is not None
        assert GOVERNANCE_PAYLOAD_KEY in source.payload_json
        assert GOVERNANCE_PAYLOAD_KEY not in final.payload_json


@pytest.mark.asyncio
async def test_workflow_result_mapping_preserves_governance_envelope() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        service = WorkflowTemplateService(session, event_bus)
        template = await service.create_template(
            WorkflowTemplateCreate(
                name="Governed mapping",
                root_node_key="execute",
                input_schema={"required": ["mapped_value"]},
                nodes=[
                    WorkflowNodeDefinition(
                        key="execute",
                        title="Execute",
                        payload={"action": "echo"},
                        result_mapping={
                            "value": "input.mapped_value"
                        },
                    )
                ],
            )
        )
        instance = await service.instantiate(
            template["id"],
            WorkflowInstantiateRequest(
                input={"mapped_value": "mapped"},
            ),
        )
        assert instance is not None

        service.materialize_governance(
            instance["id"],
            {"execute": governance_spec()},
        )
        task_id = instance["node_task_map"]["execute"]
        task = session.get(task_models.TaskModel, task_id)
        assert task is not None
        envelope = deepcopy(
            task.payload_json[GOVERNANCE_PAYLOAD_KEY]
        )

        prepared = service.prepare_task(task_id)

        assert prepared["action"] == "ready"
        assert task.payload_json["value"] == "mapped"
        assert task.payload_json[GOVERNANCE_PAYLOAD_KEY] == envelope
        assert (
            TaskRepository(session).validate_governance(task)["valid"]
            is True
        )


@pytest.mark.asyncio
async def test_workflow_without_materialization_has_no_governance_metadata() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        service = WorkflowTemplateService(session, event_bus)
        template = await service.create_template(
            WorkflowTemplateCreate(
                name="Plain workflow",
                root_node_key="execute",
                nodes=[
                    WorkflowNodeDefinition(
                        key="execute",
                        title="Execute",
                        payload={"action": "echo"},
                    )
                ],
            )
        )
        instance = await service.instantiate(
            template["id"],
            WorkflowInstantiateRequest(),
        )
        assert instance is not None

        task = session.get(
            task_models.TaskModel,
            instance["node_task_map"]["execute"],
        )
        assert task is not None
        assert GOVERNANCE_PAYLOAD_KEY not in task.payload_json


@pytest.mark.asyncio
async def test_unknown_workflow_node_materialization_fails_closed() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        service = WorkflowTemplateService(session, event_bus)
        template = await service.create_template(
            WorkflowTemplateCreate(
                name="Unknown node check",
                root_node_key="execute",
                nodes=[
                    WorkflowNodeDefinition(
                        key="execute",
                        title="Execute",
                        payload={"action": "echo"},
                    )
                ],
            )
        )
        instance = await service.instantiate(
            template["id"],
            WorkflowInstantiateRequest(),
        )
        assert instance is not None

        with pytest.raises(
            WorkflowTemplateError,
            match="unknown workflow nodes",
        ):
            service.materialize_governance(
                instance["id"],
                {"missing": governance_spec()},
            )



@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_path",
    [
        "_governance",
        "_governance.attacker",
    ],
)
async def test_workflow_definition_rejects_governance_result_mapping(
    target_path: str,
) -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        service = WorkflowTemplateService(session, event_bus)

        with pytest.raises(
            WorkflowTemplateError,
            match="reserved _governance metadata",
        ):
            await service.create_template(
                WorkflowTemplateCreate(
                    name=f"Reserved mapping {target_path}",
                    root_node_key="execute",
                    nodes=[
                        WorkflowNodeDefinition(
                            key="execute",
                            title="Execute",
                            payload={"action": "echo"},
                            result_mapping={
                                target_path: "input.envelope",
                            },
                        )
                    ],
                )
            )


@pytest.mark.asyncio
async def test_prepare_task_rejects_tampered_governance_mapping() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        service = WorkflowTemplateService(session, event_bus)
        template = await service.create_template(
            WorkflowTemplateCreate(
                name="Runtime reserved mapping check",
                root_node_key="execute",
                input_schema={"required": ["envelope"]},
                nodes=[
                    WorkflowNodeDefinition(
                        key="execute",
                        title="Execute",
                        payload={"action": "echo"},
                        result_mapping={
                            "value": "input.envelope",
                        },
                    )
                ],
            )
        )
        instance = await service.instantiate(
            template["id"],
            WorkflowInstantiateRequest(
                input={"envelope": {"attacker": True}},
            ),
        )
        assert instance is not None

        task_id = instance["node_task_map"]["execute"]
        task = session.get(task_models.TaskModel, task_id)
        assert task is not None

        payload = deepcopy(task.payload_json)
        payload["_workflow"]["result_mapping"] = {
            "_governance": "input.envelope",
        }
        TaskRepository(session).update(
            task,
            TaskUpdate(payload=payload),
        )

        with pytest.raises(
            WorkflowTemplateError,
            match="reserved _governance metadata",
        ):
            service.prepare_task(task_id)

        assert GOVERNANCE_PAYLOAD_KEY not in task.payload_json


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("binding_field", "tampered_value"),
    [
        ("instance_id", "other_instance"),
        ("template_id", "other_template"),
        ("node_key", "other_node"),
    ],
)
async def test_workflow_materialization_rejects_inconsistent_binding(
    binding_field: str,
    tampered_value: str,
) -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        service = WorkflowTemplateService(session, event_bus)
        template = await service.create_template(
            WorkflowTemplateCreate(
                name=f"Binding check {binding_field}",
                root_node_key="execute",
                nodes=[
                    WorkflowNodeDefinition(
                        key="execute",
                        title="Execute",
                        payload={"action": "echo"},
                    )
                ],
            )
        )
        instance = await service.instantiate(
            template["id"],
            WorkflowInstantiateRequest(),
        )
        assert instance is not None

        task_id = instance["node_task_map"]["execute"]
        task = session.get(task_models.TaskModel, task_id)
        assert task is not None

        payload = deepcopy(task.payload_json)
        payload["_workflow"][binding_field] = tampered_value
        TaskRepository(session).update(
            task,
            TaskUpdate(payload=payload),
        )

        with pytest.raises(
            WorkflowTemplateError,
            match="inconsistent workflow instance/node binding",
        ):
            service.materialize_governance(
                instance["id"],
                {"execute": governance_spec()},
            )

        assert GOVERNANCE_PAYLOAD_KEY not in task.payload_json
