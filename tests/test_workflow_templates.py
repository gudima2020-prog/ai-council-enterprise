from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database import models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.task_engine.workflow_templates import (
    WorkflowEdgeDefinition,
    WorkflowNodeDefinition,
    WorkflowTemplateCreate,
    WorkflowTemplateService,
)


def test_template_validator_rejects_cycle() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        service = WorkflowTemplateService(session, EventBus())
        result = service.validate_definition(
            {
                "root_node_key": "b",
                "nodes": [
                    WorkflowNodeDefinition(key="a", title="A").model_dump(mode="json"),
                    WorkflowNodeDefinition(key="b", title="B").model_dump(mode="json"),
                ],
                "edges": [
                    WorkflowEdgeDefinition(from_node="a", to_node="b").model_dump(mode="json"),
                    WorkflowEdgeDefinition(from_node="b", to_node="a").model_dump(mode="json"),
                ],
            }
        )

    assert result["valid"] is False
    assert any("cycle" in error.lower() for error in result["errors"])
