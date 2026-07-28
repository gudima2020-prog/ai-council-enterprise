from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.control_center import models as control_models  # noqa: F401
from backend.control_center.notification_schemas import (
    HumanControlNotificationAcknowledgeRequest,
    HumanControlNotificationChannelCreate,
    HumanControlNotificationChannelType,
    HumanControlNotificationManualCreate,
    HumanControlNotificationSubscriptionCreate,
)
from backend.control_center.notifications import HumanControlNotificationService
from backend.control_center.service import utc_now
from backend.core.events import Event, EventBus
from backend.database import models as database_models
from backend.database.base import Base
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401


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
                id="workspace_notifications",
                name="Notification Test",
            )
        )
    return scope


async def create_in_app_channel(service: HumanControlNotificationService) -> dict:
    return await service.create_channel(
        HumanControlNotificationChannelCreate(
            workspace_id="workspace_notifications",
            channel_key="test.in-app",
            name="Test In-app",
            channel_type=HumanControlNotificationChannelType.IN_APP,
            default_ack_required=True,
            default_ack_timeout_seconds=60,
            created_by="owner",
        )
    )


@pytest.mark.asyncio
async def test_manual_notification_is_delivered_and_acknowledged() -> None:
    scope = make_scope()
    service = HumanControlNotificationService(
        event_bus=EventBus(),
        session_factory=scope,
    )
    channel = await create_in_app_channel(service)
    notification = await service.create_manual(
        HumanControlNotificationManualCreate(
            workspace_id="workspace_notifications",
            channel_id=channel["id"],
            recipient_actor_id="operator-a",
            title="Требуется подтверждение",
            body="Проверьте решение.",
            ack_required=True,
            idempotency_key="manual-notification-001",
            created_by="owner",
        )
    )

    dispatched = await service.dispatch_due()
    assert dispatched["delivered"] == 1
    delivered = service.get_notification(notification["id"])
    assert delivered is not None
    assert delivered["status"] == "delivered"
    assert delivered["ack_status"] == "pending"

    acknowledged = await service.acknowledge(
        notification["id"],
        HumanControlNotificationAcknowledgeRequest(
            actor_id="operator-a",
            note="Получено.",
            idempotency_key="ack-notification-001",
        ),
    )
    assert acknowledged["status"] == "acknowledged"
    assert acknowledged["acknowledged_by"] == "operator-a"
    receipts = service.list_receipts(notification_id=notification["id"])
    assert {row["receipt_type"] for row in receipts} == {
        "delivered",
        "acknowledged",
    }


@pytest.mark.asyncio
async def test_event_subscription_creates_idempotent_notification() -> None:
    scope = make_scope()
    service = HumanControlNotificationService(
        event_bus=EventBus(),
        session_factory=scope,
    )
    channel = await create_in_app_channel(service)
    await service.create_subscription(
        HumanControlNotificationSubscriptionCreate(
            workspace_id="workspace_notifications",
            subscription_key="operator-high-risk",
            name="High risk events",
            channel_id=channel["id"],
            actor_id="operator-a",
            event_patterns=["human_control.item.*"],
            risk_levels=["high", "critical"],
            min_priority=70,
            ack_required=True,
            created_by="owner",
        )
    )
    event = Event(
        id="event_notification_test",
        event_type="human_control.item.claimed",
        source="test",
        workspace_id="workspace_notifications",
        correlation_id="hcitem_test",
        payload={
            "item": {
                "id": "hcitem_test",
                "workspace_id": "workspace_notifications",
                "source_type": "task_approval",
                "risk_level": "high",
                "priority": 90,
                "title": "Опасное действие",
                "summary": "Требуется проверка оператора.",
            }
        },
    )
    first = await service.handle_event(event)
    second = await service.handle_event(event)
    assert first["created"] == 1
    assert second["created"] == 0
    notifications = service.list_notifications(
        recipient_actor_id="operator-a"
    )
    assert len(notifications) == 1
    assert notifications[0]["source_type"] == "task_approval"


@pytest.mark.asyncio
async def test_failed_delivery_retries_and_then_succeeds() -> None:
    scope = make_scope()
    service = HumanControlNotificationService(
        event_bus=EventBus(),
        session_factory=scope,
    )
    attempts = 0

    async def flaky_adapter(channel, notification):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")
        return {"ok": True, "adapter": "custom-test"}

    service.register_adapter("custom", flaky_adapter)
    channel = await service.create_channel(
        HumanControlNotificationChannelCreate(
            workspace_id="workspace_notifications",
            channel_key="test.custom",
            name="Custom",
            channel_type=HumanControlNotificationChannelType.CUSTOM,
            max_attempts=3,
            retry_base_seconds=1,
            created_by="owner",
        )
    )
    notification = await service.create_manual(
        HumanControlNotificationManualCreate(
            workspace_id="workspace_notifications",
            channel_id=channel["id"],
            recipient_actor_id="operator-a",
            title="Retry test",
            idempotency_key="retry-notification-001",
            created_by="owner",
        )
    )
    first = await service.dispatch_due()
    assert first["retrying"] == 1
    with scope() as session:
        row = session.get(
            control_models.HumanControlNotificationModel,
            notification["id"],
        )
        row.available_at = utc_now() - timedelta(seconds=1)
    second = await service.dispatch_due()
    assert second["delivered"] == 1
    final = service.get_notification(notification["id"])
    assert final is not None
    assert final["attempt_count"] == 2
    assert len(service.list_attempts(notification_id=notification["id"])) == 2


@pytest.mark.asyncio
async def test_ack_deadline_becomes_overdue() -> None:
    scope = make_scope()
    service = HumanControlNotificationService(
        event_bus=EventBus(),
        session_factory=scope,
    )
    channel = await create_in_app_channel(service)
    notification = await service.create_manual(
        HumanControlNotificationManualCreate(
            workspace_id="workspace_notifications",
            channel_id=channel["id"],
            recipient_actor_id="operator-a",
            title="Deadline test",
            ack_required=True,
            ack_timeout_seconds=30,
            idempotency_key="deadline-notification-001",
            created_by="owner",
        )
    )
    await service.dispatch_due()
    with scope() as session:
        row = session.get(
            control_models.HumanControlNotificationModel,
            notification["id"],
        )
        row.ack_due_at = utc_now() - timedelta(seconds=1)
    result = await service.scan_ack_deadlines()
    assert result["overdue"] == 1
    current = service.get_notification(notification["id"])
    assert current is not None
    assert current["ack_status"] == "overdue"


def test_seed_defaults_is_idempotent() -> None:
    scope = make_scope()
    service = HumanControlNotificationService(
        event_bus=EventBus(),
        session_factory=scope,
    )
    first = service.seed_defaults()
    second = service.seed_defaults()
    assert first == {"channels": 1, "subscriptions": 1}
    assert second == {"channels": 0, "subscriptions": 0}
    assert service.status()["channels"] == 1
    assert service.status()["subscriptions"] == 1
