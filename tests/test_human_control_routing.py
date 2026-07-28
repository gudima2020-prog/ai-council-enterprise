from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.control_center import models as control_models  # noqa: F401
from backend.control_center.governance import HumanControlGovernanceService
from backend.control_center.governance_schemas import (
    HumanControlBootstrapOwnerRequest,
    HumanControlRoleBindingCreate,
)
from backend.control_center.notification_schemas import (
    HumanControlNotificationChannelCreate,
    HumanControlNotificationChannelType,
    HumanControlNotificationManualCreate,
    HumanControlNotificationSubscriptionCreate,
)
from backend.control_center.notifications import HumanControlNotificationService
from backend.control_center.routing import HumanControlRoutingService
from backend.control_center.routing_schemas import (
    HumanControlAvailabilityStatus,
    HumanControlAvailabilityUpsert,
    HumanControlEscalationRuleCreate,
    HumanControlEscalationTrigger,
    HumanControlOnCallMemberCreate,
    HumanControlOnCallScheduleCreate,
    HumanControlRoutingRuleCreate,
    HumanControlRoutingStrategy,
)
from backend.control_center.service import HumanControlCenterService, utc_now
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
                id="workspace_routing",
                name="Routing Test",
            )
        )
    return scope


async def make_services(scope):
    event_bus = EventBus()
    center = HumanControlCenterService(
        event_bus=event_bus,
        session_factory=scope,
    )
    governance = HumanControlGovernanceService(
        event_bus=event_bus,
        control_center=center,
        session_factory=scope,
    )
    governance.seed_builtin_roles()
    await governance.bootstrap_owner(
        HumanControlBootstrapOwnerRequest(
            workspace_id="workspace_routing",
            actor_id="owner",
        )
    )
    notifications = HumanControlNotificationService(
        event_bus=event_bus,
        session_factory=scope,
    )
    routing = HumanControlRoutingService(
        event_bus=event_bus,
        notification_service=notifications,
        session_factory=scope,
    )
    notifications.set_routing_service(routing)
    event_bus.subscribe("human_control.notification.*", routing.handle_event)
    channel = await notifications.create_channel(
        HumanControlNotificationChannelCreate(
            workspace_id="workspace_routing",
            channel_key="routing.in-app",
            name="Routing in-app",
            channel_type=HumanControlNotificationChannelType.IN_APP,
            default_ack_required=True,
            default_ack_timeout_seconds=60,
            created_by="owner",
        )
    )
    return event_bus, governance, notifications, routing, channel


@pytest.mark.asyncio
async def test_role_subscription_routes_to_available_on_call_operator() -> None:
    scope = make_scope()
    event_bus, governance, notifications, routing, channel = await make_services(scope)
    for actor_id in ("alice", "bob"):
        await governance.grant_binding(
            HumanControlRoleBindingCreate(
                workspace_id="workspace_routing",
                actor_id=actor_id,
                role_key="senior_operator",
                granted_by="owner",
                reason="routing test",
            )
        )
    schedule = await routing.create_schedule(
        HumanControlOnCallScheduleCreate(
            workspace_id="workspace_routing",
            schedule_key="operations",
            name="Operations",
            timezone="UTC",
            created_by="owner",
        )
    )
    await routing.add_member(
        schedule["id"],
        HumanControlOnCallMemberCreate(
            actor_id="alice",
            priority=90,
            created_by="owner",
        ),
    )
    await routing.add_member(
        schedule["id"],
        HumanControlOnCallMemberCreate(
            actor_id="bob",
            priority=80,
            created_by="owner",
        ),
    )
    await routing.upsert_availability(
        HumanControlAvailabilityUpsert(
            workspace_id="workspace_routing",
            actor_id="alice",
            status=HumanControlAvailabilityStatus.OFFLINE,
            updated_by="owner",
        )
    )
    await routing.upsert_availability(
        HumanControlAvailabilityUpsert(
            workspace_id="workspace_routing",
            actor_id="bob",
            status=HumanControlAvailabilityStatus.AVAILABLE,
            updated_by="owner",
        )
    )
    await routing.create_routing_rule(
        HumanControlRoutingRuleCreate(
            workspace_id="workspace_routing",
            rule_key="critical-on-call",
            name="Critical on-call",
            event_patterns=["human_control.item.*"],
            risk_levels=["critical"],
            schedule_id=schedule["id"],
            role_keys=["senior_operator"],
            strategy=HumanControlRoutingStrategy.FIRST_AVAILABLE,
            fallback_mode="fail_closed",
            created_by="owner",
        )
    )
    await notifications.create_subscription(
        HumanControlNotificationSubscriptionCreate(
            workspace_id="workspace_routing",
            subscription_key="critical-role",
            name="Critical role",
            channel_id=channel["id"],
            role_keys=["senior_operator"],
            event_patterns=["human_control.item.*"],
            risk_levels=["critical"],
            created_by="owner",
        )
    )
    result = await notifications.handle_event(
        Event(
            id="routing_event_001",
            event_type="human_control.item.pending",
            source="test",
            workspace_id="workspace_routing",
            payload={
                "item": {
                    "id": "item_routing_001",
                    "workspace_id": "workspace_routing",
                    "source_type": "task_approval",
                    "risk_level": "critical",
                    "priority": 100,
                    "title": "Critical operation",
                }
            },
        )
    )
    assert result["created"] == 1
    rows = notifications.list_notifications(workspace_id="workspace_routing")
    assert [row["recipient_actor_id"] for row in rows] == ["bob"]
    assert rows[0]["payload"]["routing"]["routing_rule_key"] == "critical-on-call"


@pytest.mark.asyncio
async def test_round_robin_routing_rotates_available_operators() -> None:
    scope = make_scope()
    _, governance, notifications, routing, channel = await make_services(scope)
    for actor_id in ("alice", "bob"):
        await governance.grant_binding(
            HumanControlRoleBindingCreate(
                workspace_id="workspace_routing",
                actor_id=actor_id,
                role_key="operator",
                granted_by="owner",
                reason="round robin",
            )
        )
        await routing.upsert_availability(
            HumanControlAvailabilityUpsert(
                workspace_id="workspace_routing",
                actor_id=actor_id,
                status=HumanControlAvailabilityStatus.AVAILABLE,
                updated_by="owner",
            )
        )
    await routing.create_routing_rule(
        HumanControlRoutingRuleCreate(
            workspace_id="workspace_routing",
            rule_key="round-robin",
            name="Round robin",
            event_patterns=["human_control.item.*"],
            role_keys=["operator"],
            strategy=HumanControlRoutingStrategy.ROUND_ROBIN,
            fallback_mode="fail_closed",
            created_by="owner",
        )
    )
    await notifications.create_subscription(
        HumanControlNotificationSubscriptionCreate(
            workspace_id="workspace_routing",
            subscription_key="operator-events",
            name="Operator events",
            channel_id=channel["id"],
            role_keys=["operator"],
            event_patterns=["human_control.item.*"],
            created_by="owner",
        )
    )
    for index in range(2):
        await notifications.handle_event(
            Event(
                id=f"round_robin_event_{index}",
                event_type="human_control.item.pending",
                source="test",
                workspace_id="workspace_routing",
                payload={
                    "item": {
                        "id": f"item_round_{index}",
                        "workspace_id": "workspace_routing",
                        "source_type": "task_approval",
                        "risk_level": "medium",
                        "priority": 50,
                        "title": "Operator event",
                    }
                },
            )
        )
    recipients = {
        row["recipient_actor_id"]
        for row in notifications.list_notifications(workspace_id="workspace_routing")
    }
    assert recipients == {"alice", "bob"}


@pytest.mark.asyncio
async def test_overdue_ack_opens_and_routes_escalation() -> None:
    scope = make_scope()
    event_bus, _, notifications, routing, channel = await make_services(scope)
    await routing.upsert_availability(
        HumanControlAvailabilityUpsert(
            workspace_id="workspace_routing",
            actor_id="supervisor",
            status=HumanControlAvailabilityStatus.AVAILABLE,
            updated_by="owner",
        )
    )
    await routing.create_escalation_rule(
        HumanControlEscalationRuleCreate(
            workspace_id="workspace_routing",
            rule_key="ack-overdue",
            name="Ack overdue",
            event_patterns=["human_control.notification.manual"],
            trigger_on=[HumanControlEscalationTrigger.ACK_OVERDUE],
            target_actor_ids=["supervisor"],
            channel_id=channel["id"],
            initial_delay_seconds=0,
            repeat_interval_seconds=60,
            max_escalations=2,
            created_by="owner",
        )
    )
    original = await notifications.create_manual(
        HumanControlNotificationManualCreate(
            workspace_id="workspace_routing",
            channel_id=channel["id"],
            recipient_actor_id="operator",
            title="Needs acknowledgement",
            ack_required=True,
            ack_timeout_seconds=30,
            idempotency_key="routing-escalation-original",
            created_by="owner",
        )
    )
    await notifications.dispatch_due()
    with scope() as session:
        row = session.get(control_models.HumanControlNotificationModel, original["id"])
        row.ack_due_at = utc_now() - timedelta(seconds=1)
    result = await notifications.scan_ack_deadlines()
    assert result["overdue"] == 1
    cases = routing.list_escalations(workspace_id="workspace_routing")
    assert len(cases) == 1
    assert cases[0]["escalation_count"] == 1
    spawned = notifications.list_notifications(
        workspace_id="workspace_routing",
        recipient_actor_id="supervisor",
    )
    assert len(spawned) == 1
    assert spawned[0]["payload"]["escalation_id"] == cases[0]["id"]


def test_utc_schedule_does_not_require_external_tzdata() -> None:
    assert HumanControlRoutingService._zone("UTC").utcoffset(None) == timedelta(0)
