from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.autonomy.models import MissionDecisionCheckpointModel
from backend.control_center.schemas import (
    HumanControlClaimRequest,
    HumanControlDecisionAction,
    HumanControlDecisionRequest,
    HumanControlSnoozeRequest,
)
from backend.control_center.service import (
    HumanControlCenterService,
    HumanControlConflict,
)
from backend.core.events import EventBus
from backend.database import models as database_models
from backend.database.base import Base
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.task_engine.models import TaskApprovalModel


def make_scope():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

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

    with scope() as session:
        session.add(
            database_models.WorkspaceModel(
                id="workspace_control",
                name="Human Control Test",
            )
        )
    return scope


class FakeTaskApprovalManager:
    def __init__(self, scope) -> None:
        self.scope = scope
        self.calls = 0

    async def decide(self, *, approval_id, request):
        self.calls += 1
        with self.scope() as session:
            row = session.get(TaskApprovalModel, approval_id)
            assert row is not None
            row.status = (
                "approved" if request.decision.value == "approve" else "rejected"
            )
            row.decided_by = request.decided_by
            row.decision_note = request.note
            row.decided_at = datetime.now(timezone.utc)
            return {
                "id": row.id,
                "task_id": row.task_id,
                "workspace_id": row.workspace_id,
                "status": row.status,
            }


def seed_task_approval(scope, *, approval_id="approval_control") -> None:
    with scope() as session:
        session.add(
            TaskApprovalModel(
                id=approval_id,
                task_id="task_control",
                workspace_id="workspace_control",
                gate_key="execution",
                status="pending",
                prompt="Подтвердить выполнение Task?",
                requested_by="agent",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                metadata_json={"risk_level": "high", "priority": 88},
            )
        )


@pytest.mark.asyncio
async def test_sync_builds_unified_inbox_and_dashboard() -> None:
    scope = make_scope()
    seed_task_approval(scope)
    with scope() as session:
        session.add(
            MissionDecisionCheckpointModel(
                id="checkpoint_control",
                workspace_id="workspace_control",
                mission_id="mission_control",
                checkpoint_key="risk.review",
                checkpoint_type="risk",
                title="Подтвердить продолжение Mission",
                description="Открыт высокий риск.",
                status="ready",
                blocking=True,
                requires_human=True,
                recommended_decision="pause",
                recommendation_confidence_percent=92,
                options_json=[],
                context_snapshot_json={},
                metadata_json={},
            )
        )

    service = HumanControlCenterService(
        event_bus=EventBus(),
        session_factory=scope,
    )
    result = await service.sync(workspace_id="workspace_control")
    assert result["created"] == 2

    items = service.list_items(workspace_id="workspace_control")
    assert len(items) == 2
    assert {item["source_type"] for item in items} == {
        "task_approval",
        "mission_checkpoint",
    }
    assert items[0]["risk_level"] in {"critical", "high"}

    dashboard = await service.dashboard(
        workspace_id="workspace_control",
        refresh=False,
    )
    assert dashboard["summary"]["active"] == 2
    assert dashboard["summary"]["critical"] == 1
    assert dashboard["by_source"]["task_approval"] == 1


@pytest.mark.asyncio
async def test_claim_lease_prevents_parallel_operator_decision() -> None:
    scope = make_scope()
    seed_task_approval(scope)
    service = HumanControlCenterService(
        event_bus=EventBus(),
        session_factory=scope,
    )
    await service.sync()
    item = service.list_items()[0]

    claimed = await service.claim(
        item["id"],
        HumanControlClaimRequest(actor_id="operator-a", ttl_seconds=600),
    )
    assert claimed["status"] == "claimed"
    assert claimed["assigned_to"] == "operator-a"

    with pytest.raises(HumanControlConflict):
        await service.claim(
            item["id"],
            HumanControlClaimRequest(actor_id="operator-b", ttl_seconds=600),
        )


@pytest.mark.asyncio
async def test_decision_is_routed_and_idempotent() -> None:
    scope = make_scope()
    seed_task_approval(scope)
    manager = FakeTaskApprovalManager(scope)
    service = HumanControlCenterService(
        event_bus=EventBus(),
        task_approval_manager=manager,
        session_factory=scope,
    )
    await service.sync()
    item = service.list_items()[0]
    request = HumanControlDecisionRequest(
        action=HumanControlDecisionAction.APPROVE,
        actor_id="owner",
        reason="Выполнение подтверждено.",
        idempotency_key="control-decision-001",
    )

    first = await service.decide(item["id"], request)
    second = await service.decide(item["id"], request)

    assert first["item"]["status"] == "resolved"
    assert first["item"]["resolution"] in {"approved", "approve"}
    assert second["idempotent_replay"] is True
    assert manager.calls == 1
    actions = service.list_actions(item_id=item["id"])
    assert any(action["action_type"] == "decision" for action in actions)


@pytest.mark.asyncio
async def test_operator_can_snooze_unclaimed_item() -> None:
    scope = make_scope()
    seed_task_approval(scope)
    service = HumanControlCenterService(
        event_bus=EventBus(),
        session_factory=scope,
    )
    await service.sync()
    item = service.list_items()[0]
    until = datetime.now(timezone.utc) + timedelta(minutes=30)

    result = await service.snooze(
        item["id"],
        HumanControlSnoozeRequest(
            actor_id="operator",
            until=until,
            reason="Вернуться после проверки входных данных.",
        ),
    )
    assert result["status"] == "snoozed"
    assert result["snoozed_until"] is not None
