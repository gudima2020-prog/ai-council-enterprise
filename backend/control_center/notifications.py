from __future__ import annotations

import asyncio
import fnmatch
import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.control_center.governance_schemas import HumanControlPermission
from backend.control_center.models import (
    HumanControlItemModel,
    HumanControlNotificationAttemptModel,
    HumanControlNotificationChannelModel,
    HumanControlNotificationModel,
    HumanControlNotificationReceiptModel,
    HumanControlNotificationSubscriptionModel,
    HumanControlRoleBindingModel,
    HumanControlRoleModel,
)
from backend.control_center.notification_schemas import (
    HumanControlNotificationAcknowledgeRequest,
    HumanControlNotificationCancelRequest,
    HumanControlNotificationChannelCreate,
    HumanControlNotificationChannelTestRequest,
    HumanControlNotificationChannelUpdate,
    HumanControlNotificationManualCreate,
    HumanControlNotificationReadRequest,
    HumanControlNotificationRetryRequest,
    HumanControlNotificationSubscriptionCreate,
    HumanControlNotificationSubscriptionUpdate,
)
from backend.control_center.service import (
    HumanControlConflict,
    HumanControlError,
    HumanControlNotFound,
    ensure_utc,
    iso,
    utc_now,
)
from backend.core.events import Event, EventBus
from backend.database.session import session_scope


SessionContextFactory = Callable[[], AbstractContextManager[Session]]
NotificationAdapter = Callable[
    [dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]
]

logger = logging.getLogger(__name__)


class HumanControlNotificationService:
    """Persistent operator notifications, delivery retries and acknowledgements."""

    ACTIVE_STATUSES = {"pending", "delivering", "retrying", "delivered"}
    TERMINAL_STATUSES = {"acknowledged", "failed", "cancelled", "expired"}

    def __init__(
        self,
        *,
        event_bus: EventBus,
        control_center: Any | None = None,
        governance: Any | None = None,
        session_factory: SessionContextFactory = session_scope,
        dispatch_interval_seconds: int = 10,
        delivery_batch_size: int = 100,
    ) -> None:
        self._event_bus = event_bus
        self._control_center = control_center
        self._governance = governance
        self._routing_service: Any | None = None
        self._session_factory = session_factory
        self._dispatch_interval_seconds = max(2, int(dispatch_interval_seconds))
        self._delivery_batch_size = max(1, min(500, int(delivery_batch_size)))
        self._monitor_task: asyncio.Task[None] | None = None
        self._dispatch_lock = asyncio.Lock()
        self._adapters: dict[str, NotificationAdapter] = {}
        self._notifications_created = 0
        self._deliveries_completed = 0
        self._deliveries_failed = 0
        self._acknowledgements = 0
        self.register_adapter("in_app", self._deliver_in_app)
        self.register_adapter("log", self._deliver_log)
        self.register_adapter("webhook", self._deliver_webhook)

    @property
    def running(self) -> bool:
        return self._monitor_task is not None and not self._monitor_task.done()

    def register_adapter(self, channel_type: str, adapter: NotificationAdapter) -> None:
        key = str(channel_type).strip().lower()
        if not key:
            raise ValueError("channel_type cannot be empty")
        self._adapters[key] = adapter

    def set_routing_service(self, service: Any | None) -> None:
        """Attach the availability-aware recipient resolver lazily."""
        self._routing_service = service

    async def start(self) -> None:
        if self.running:
            return
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(), name="human-control-notification-monitor"
        )

    async def shutdown(self) -> None:
        task = self._monitor_task
        self._monitor_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _monitor_loop(self) -> None:
        while True:
            try:
                await self.reconcile(actor_id="notification_monitor")
                await self.dispatch_due(limit=self._delivery_batch_size)
                await self.scan_ack_deadlines(actor_id="notification_monitor")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Human Control notification monitor iteration failed")
            await asyncio.sleep(self._dispatch_interval_seconds)

    def seed_defaults(self) -> dict[str, int]:
        channels = 0
        subscriptions = 0
        with self._session_factory() as session:
            channel = session.scalar(
                select(HumanControlNotificationChannelModel).where(
                    HumanControlNotificationChannelModel.scope_key
                    == "global:builtin.in_app"
                )
            )
            if channel is None:
                channel = HumanControlNotificationChannelModel(
                    scope_key="global:builtin.in_app",
                    workspace_id=None,
                    channel_key="builtin.in_app",
                    name="Встроенные уведомления",
                    channel_type="in_app",
                    enabled=True,
                    config_json={"builtin": True},
                    timeout_seconds=5,
                    max_attempts=3,
                    retry_base_seconds=10,
                    default_ack_required=False,
                    default_ack_timeout_seconds=1800,
                    created_by="system",
                    updated_by="system",
                )
                session.add(channel)
                session.flush()
                channels += 1

            subscription = session.scalar(
                select(HumanControlNotificationSubscriptionModel).where(
                    HumanControlNotificationSubscriptionModel.scope_key
                    == "global:builtin.owner-critical"
                )
            )
            if subscription is None:
                session.add(
                    HumanControlNotificationSubscriptionModel(
                        scope_key="global:builtin.owner-critical",
                        workspace_id=None,
                        subscription_key="builtin.owner-critical",
                        name="Критические уведомления владельцу",
                        channel_id=channel.id,
                        actor_id=None,
                        role_keys_json=["owner", "control_admin"],
                        event_patterns_json=[
                            "human_control.item.*",
                            "human_control.governance.escalation.*",
                            "human_control.auth.break_glass.*",
                        ],
                        source_types_json=[],
                        risk_levels_json=["high", "critical"],
                        min_priority=70,
                        enabled=True,
                        ack_required=True,
                        ack_timeout_seconds=1800,
                        metadata_json={"builtin": True},
                        created_by="system",
                        updated_by="system",
                    )
                )
                subscriptions += 1
        return {"channels": channels, "subscriptions": subscriptions}

    def status(self) -> dict[str, Any]:
        with self._session_factory() as session:
            channel_count = int(
                session.scalar(
                    select(func.count()).select_from(
                        HumanControlNotificationChannelModel
                    )
                )
                or 0
            )
            subscription_count = int(
                session.scalar(
                    select(func.count()).select_from(
                        HumanControlNotificationSubscriptionModel
                    )
                )
                or 0
            )
            status_rows = session.execute(
                select(
                    HumanControlNotificationModel.status,
                    func.count(HumanControlNotificationModel.id),
                ).group_by(HumanControlNotificationModel.status)
            ).all()
            ack_overdue = int(
                session.scalar(
                    select(func.count())
                    .select_from(HumanControlNotificationModel)
                    .where(HumanControlNotificationModel.ack_status == "overdue")
                )
                or 0
            )
        return {
            "running": self.running,
            "dispatch_interval_seconds": self._dispatch_interval_seconds,
            "delivery_batch_size": self._delivery_batch_size,
            "channels": channel_count,
            "subscriptions": subscription_count,
            "notifications_by_status": {
                str(status): int(count) for status, count in status_rows
            },
            "ack_overdue": ack_overdue,
            "registered_adapters": sorted(self._adapters),
            "notifications_created": self._notifications_created,
            "deliveries_completed": self._deliveries_completed,
            "deliveries_failed": self._deliveries_failed,
            "acknowledgements": self._acknowledgements,
            "delivery_guarantee": "at-least-once",
            "capabilities": [
                "persistent_operator_notifications",
                "role_and_actor_subscriptions",
                "in_app_log_and_webhook_delivery",
                "adapter_registry_for_external_channels",
                "exponential_retry",
                "delivery_attempt_history",
                "read_and_acknowledgement_receipts",
                "acknowledgement_deadline_monitoring",
                "availability_aware_routing_hook",
                "audit_and_event_transport",
            ],
        }

    def dashboard(self, *, workspace_id: str | None = None) -> dict[str, Any]:
        with self._session_factory() as session:
            statement = select(HumanControlNotificationModel)
            if workspace_id is not None:
                statement = statement.where(
                    HumanControlNotificationModel.workspace_id == workspace_id
                )
            rows = list(session.scalars(statement).all())
        now = utc_now()
        status_counts = Counter(row.status for row in rows)
        severity_counts = Counter(row.severity for row in rows)
        pending_ack = sum(
            1 for row in rows if row.ack_status in {"pending", "overdue"}
        )
        overdue = sum(1 for row in rows if row.ack_status == "overdue")
        delivery_latency = [
            max(0.0, (ensure_utc(row.delivered_at) - ensure_utc(row.created_at)).total_seconds())
            for row in rows
            if ensure_utc(row.delivered_at) is not None
            and ensure_utc(row.created_at) is not None
        ]
        return {
            "workspace_id": workspace_id,
            "generated_at": iso(now),
            "total": len(rows),
            "by_status": dict(status_counts),
            "by_severity": dict(severity_counts),
            "pending_acknowledgements": pending_ack,
            "overdue_acknowledgements": overdue,
            "average_delivery_latency_seconds": (
                round(sum(delivery_latency) / len(delivery_latency), 3)
                if delivery_latency
                else 0.0
            ),
        }

    async def create_channel(
        self, request: HumanControlNotificationChannelCreate
    ) -> dict[str, Any]:
        scope_key = self._scope_key(request.workspace_id, request.channel_key)
        with self._session_factory() as session:
            if session.scalar(
                select(HumanControlNotificationChannelModel).where(
                    HumanControlNotificationChannelModel.scope_key == scope_key
                )
            ):
                raise HumanControlConflict("Канал с таким ключом уже существует.")
            row = HumanControlNotificationChannelModel(
                scope_key=scope_key,
                workspace_id=request.workspace_id,
                channel_key=request.channel_key,
                name=request.name,
                channel_type=request.channel_type.value,
                enabled=request.enabled,
                endpoint_url=request.endpoint_url,
                credential_ref=request.credential_ref,
                config_json=dict(request.config),
                timeout_seconds=request.timeout_seconds,
                max_attempts=request.max_attempts,
                retry_base_seconds=request.retry_base_seconds,
                default_ack_required=request.default_ack_required,
                default_ack_timeout_seconds=request.default_ack_timeout_seconds,
                created_by=request.created_by,
                updated_by=request.created_by,
            )
            session.add(row)
            session.flush()
            result = self._channel_to_dict(row)
        await self._publish(
            "human_control.notification.channel.created",
            request.workspace_id,
            row.id,
            {"channel": result},
        )
        return result

    async def update_channel(
        self, channel_id: str, request: HumanControlNotificationChannelUpdate
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(HumanControlNotificationChannelModel, channel_id)
            if row is None:
                raise HumanControlNotFound("Канал уведомлений не найден.")
            updates = request.model_dump(exclude_unset=True)
            updates.pop("actor_id", None)
            if "config" in updates:
                row.config_json = dict(updates.pop("config") or {})
            for field, value in updates.items():
                setattr(row, field, value)
            row.updated_by = request.actor_id
            result = self._channel_to_dict(row)
        await self._publish(
            "human_control.notification.channel.updated",
            result["workspace_id"],
            channel_id,
            {"channel": result, "actor_id": request.actor_id},
        )
        return result

    def list_channels(
        self, *, workspace_id: str | None = None, enabled: bool | None = None
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlNotificationChannelModel)
            if workspace_id is not None:
                statement = statement.where(
                    or_(
                        HumanControlNotificationChannelModel.workspace_id
                        == workspace_id,
                        HumanControlNotificationChannelModel.workspace_id.is_(None),
                    )
                )
            if enabled is not None:
                statement = statement.where(
                    HumanControlNotificationChannelModel.enabled.is_(enabled)
                )
            statement = statement.order_by(
                HumanControlNotificationChannelModel.workspace_id,
                HumanControlNotificationChannelModel.channel_key,
            )
            return [
                self._channel_to_dict(row)
                for row in session.scalars(statement).all()
            ]

    def get_channel(self, channel_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(HumanControlNotificationChannelModel, channel_id)
            return None if row is None else self._channel_to_dict(row)

    async def create_subscription(
        self, request: HumanControlNotificationSubscriptionCreate
    ) -> dict[str, Any]:
        scope_key = self._scope_key(request.workspace_id, request.subscription_key)
        with self._session_factory() as session:
            channel = session.get(HumanControlNotificationChannelModel, request.channel_id)
            if channel is None:
                raise HumanControlNotFound("Канал уведомлений не найден.")
            self._validate_workspace_compatibility(
                request.workspace_id, channel.workspace_id
            )
            if session.scalar(
                select(HumanControlNotificationSubscriptionModel).where(
                    HumanControlNotificationSubscriptionModel.scope_key == scope_key
                )
            ):
                raise HumanControlConflict("Подписка с таким ключом уже существует.")
            row = HumanControlNotificationSubscriptionModel(
                scope_key=scope_key,
                workspace_id=request.workspace_id,
                subscription_key=request.subscription_key,
                name=request.name,
                channel_id=request.channel_id,
                actor_id=request.actor_id,
                role_keys_json=list(request.role_keys),
                event_patterns_json=list(request.event_patterns),
                source_types_json=[item.value for item in request.source_types],
                risk_levels_json=[item.value for item in request.risk_levels],
                min_priority=request.min_priority,
                enabled=request.enabled,
                ack_required=request.ack_required,
                ack_timeout_seconds=request.ack_timeout_seconds,
                metadata_json=dict(request.metadata),
                created_by=request.created_by,
                updated_by=request.created_by,
            )
            session.add(row)
            session.flush()
            result = self._subscription_to_dict(row)
        await self._publish(
            "human_control.notification.subscription.created",
            result["workspace_id"],
            row.id,
            {"subscription": result},
        )
        return result

    async def update_subscription(
        self,
        subscription_id: str,
        request: HumanControlNotificationSubscriptionUpdate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(
                HumanControlNotificationSubscriptionModel, subscription_id
            )
            if row is None:
                raise HumanControlNotFound("Подписка на уведомления не найдена.")
            updates = request.model_dump(exclude_unset=True)
            actor_updater = updates.pop("actor_id_updated_by")
            if "channel_id" in updates:
                channel = session.get(
                    HumanControlNotificationChannelModel, updates["channel_id"]
                )
                if channel is None:
                    raise HumanControlNotFound("Канал уведомлений не найден.")
                self._validate_workspace_compatibility(
                    row.workspace_id, channel.workspace_id
                )
            mapping = {
                "role_keys": "role_keys_json",
                "event_patterns": "event_patterns_json",
                "source_types": "source_types_json",
                "risk_levels": "risk_levels_json",
                "metadata": "metadata_json",
            }
            for field, value in updates.items():
                target = mapping.get(field, field)
                if field in {"source_types", "risk_levels"} and value is not None:
                    value = [getattr(item, "value", str(item)) for item in value]
                if field in {"role_keys", "event_patterns"} and value is not None:
                    value = list(value)
                if field == "metadata" and value is not None:
                    value = dict(value)
                setattr(row, target, value)
            if not row.actor_id and not list(row.role_keys_json or []):
                raise HumanControlError(
                    "Подписка должна содержать actor_id или role_keys."
                )
            if not list(row.event_patterns_json or []):
                raise HumanControlError("Подписка должна содержать event patterns.")
            row.updated_by = actor_updater
            result = self._subscription_to_dict(row)
        await self._publish(
            "human_control.notification.subscription.updated",
            result["workspace_id"],
            subscription_id,
            {"subscription": result, "actor_id": actor_updater},
        )
        return result

    def list_subscriptions(
        self,
        *,
        workspace_id: str | None = None,
        actor_id: str | None = None,
        enabled: bool | None = None,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlNotificationSubscriptionModel)
            if workspace_id is not None:
                statement = statement.where(
                    or_(
                        HumanControlNotificationSubscriptionModel.workspace_id
                        == workspace_id,
                        HumanControlNotificationSubscriptionModel.workspace_id.is_(
                            None
                        ),
                    )
                )
            if actor_id is not None:
                statement = statement.where(
                    HumanControlNotificationSubscriptionModel.actor_id == actor_id
                )
            if enabled is not None:
                statement = statement.where(
                    HumanControlNotificationSubscriptionModel.enabled.is_(enabled)
                )
            statement = statement.order_by(
                HumanControlNotificationSubscriptionModel.created_at
            )
            return [
                self._subscription_to_dict(row)
                for row in session.scalars(statement).all()
            ]

    async def handle_event(self, event: Event) -> dict[str, Any]:
        if event.event_type.startswith("human_control.notification."):
            return {"skipped": True, "reason": "notification_event"}
        if event.event_type == "human_control.synced":
            return await self.sync_open_items(workspace_id=event.workspace_id)
        return await self._create_from_event(event)

    async def sync_open_items(
        self, *, workspace_id: str | None = None
    ) -> dict[str, Any]:
        if self._control_center is None:
            return {"created": 0, "reason": "control_center_unavailable"}
        items = self._control_center.list_items(
            workspace_id=workspace_id, limit=500, offset=0
        )
        created = 0
        for item in items:
            if item.get("status") not in {"pending", "claimed"}:
                continue
            event = Event(
                event_type="human_control.item.pending",
                source="human_control.notification_sync",
                workspace_id=item.get("workspace_id"),
                correlation_id=item.get("id"),
                payload={"item": item, "notification_sync": True},
                id=f"sync_{item.get('id')}",
            )
            result = await self._create_from_event(event, open_item_sync=True)
            created += int(result.get("created", 0))
        return {"workspace_id": workspace_id, "scanned": len(items), "created": created}

    async def _create_from_event(
        self, event: Event, *, open_item_sync: bool = False
    ) -> dict[str, Any]:
        context = self._event_context(event)
        with self._session_factory() as session:
            subscriptions = self._matching_subscriptions(
                session, event=event, context=context
            )
            created_rows: list[dict[str, Any]] = []
            unroutable: list[dict[str, Any]] = []
            for subscription, channel in subscriptions:
                base_recipients = self._resolve_recipients(
                    session,
                    subscription=subscription,
                    workspace_id=context["workspace_id"],
                )
                routing = {
                    "recipients": base_recipients,
                    "routing_rule_id": None,
                    "strategy": "subscription_default",
                    "reason": "routing_service_unavailable",
                    "ack_required": None,
                    "ack_timeout_seconds": None,
                    "candidates": [],
                }
                if self._routing_service is not None:
                    routing = self._routing_service.resolve_event_recipients(
                        session=session,
                        workspace_id=context["workspace_id"],
                        event_type=event.event_type,
                        context=context,
                        role_keys=list(subscription.role_keys_json or []),
                        base_recipients=base_recipients,
                    )
                recipients = list(routing.get("recipients") or [])
                if not recipients:
                    unroutable.append(
                        {
                            "subscription_id": subscription.id,
                            "channel_id": channel.id,
                            "routing": routing,
                        }
                    )
                for recipient in recipients:
                    idempotency_key = self._event_idempotency_key(
                        event,
                        subscription=subscription,
                        recipient_actor_id=recipient,
                        source_id=context.get("source_id"),
                        open_item_sync=open_item_sync,
                    )
                    if session.scalar(
                        select(HumanControlNotificationModel).where(
                            HumanControlNotificationModel.idempotency_key
                            == idempotency_key
                        )
                    ):
                        continue
                    ack_required = routing.get("ack_required")
                    if ack_required is None:
                        ack_required = (
                            subscription.ack_required
                            if subscription.ack_required is not None
                            else channel.default_ack_required
                        )
                    ack_timeout = int(
                        routing.get("ack_timeout_seconds")
                        or subscription.ack_timeout_seconds
                        or channel.default_ack_timeout_seconds
                    )
                    row = HumanControlNotificationModel(
                        workspace_id=context["workspace_id"],
                        item_id=context.get("item_id"),
                        channel_id=channel.id,
                        subscription_id=subscription.id,
                        recipient_actor_id=recipient,
                        event_type=event.event_type,
                        source_type=context.get("source_type"),
                        source_id=context.get("source_id"),
                        severity=context["severity"],
                        priority=context["priority"],
                        title=context["title"],
                        body=context["body"],
                        payload_json={
                            "event": event.to_dict(),
                            "context": context,
                            "routing": routing,
                        },
                        idempotency_key=idempotency_key,
                        status="pending",
                        ack_required=bool(ack_required),
                        ack_status="pending" if ack_required else "not_required",
                        attempt_count=0,
                        max_attempts=channel.max_attempts,
                        available_at=utc_now(),
                        ack_due_at=(
                            utc_now() + timedelta(seconds=ack_timeout)
                            if ack_required
                            else None
                        ),
                        expires_at=ensure_utc(context.get("expires_at")),
                        created_by="event_bus",
                    )
                    session.add(row)
                    session.flush()
                    created_rows.append(self._notification_to_dict(row))
        self._notifications_created += len(created_rows)
        for row in created_rows:
            await self._publish(
                "human_control.notification.created",
                row["workspace_id"],
                row["id"],
                {"notification": row},
            )
        for failure in unroutable:
            await self._publish(
                "human_control.notification.unroutable",
                context["workspace_id"],
                context.get("source_id") or event.id,
                {
                    "event_type": event.event_type,
                    "source_type": context.get("source_type"),
                    "severity": context.get("severity"),
                    "priority": context.get("priority"),
                    "routing_context": context,
                    **failure,
                },
            )
        return {
            "event_id": event.id,
            "event_type": event.event_type,
            "created": len(created_rows),
            "unroutable": len(unroutable),
            "notifications": created_rows,
        }

    async def create_manual(
        self, request: HumanControlNotificationManualCreate
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            existing = session.scalar(
                select(HumanControlNotificationModel).where(
                    HumanControlNotificationModel.idempotency_key
                    == request.idempotency_key
                )
            )
            if existing is not None:
                return self._notification_to_dict(existing)
            channel = session.get(HumanControlNotificationChannelModel, request.channel_id)
            if channel is None:
                raise HumanControlNotFound("Канал уведомлений не найден.")
            self._validate_workspace_compatibility(
                request.workspace_id, channel.workspace_id
            )
            ack_required = (
                request.ack_required
                if request.ack_required is not None
                else channel.default_ack_required
            )
            ack_timeout = int(
                request.ack_timeout_seconds
                or channel.default_ack_timeout_seconds
            )
            row = HumanControlNotificationModel(
                workspace_id=request.workspace_id,
                channel_id=channel.id,
                recipient_actor_id=request.recipient_actor_id,
                event_type="human_control.notification.manual",
                source_type="manual",
                source_id=None,
                severity=request.severity.value,
                priority=request.priority,
                title=request.title,
                body=request.body,
                payload_json=dict(request.payload),
                idempotency_key=request.idempotency_key,
                status="pending",
                ack_required=bool(ack_required),
                ack_status="pending" if ack_required else "not_required",
                max_attempts=channel.max_attempts,
                available_at=utc_now(),
                ack_due_at=(
                    utc_now() + timedelta(seconds=ack_timeout)
                    if ack_required
                    else None
                ),
                expires_at=ensure_utc(request.expires_at),
                created_by=request.created_by,
            )
            session.add(row)
            session.flush()
            result = self._notification_to_dict(row)
        self._notifications_created += 1
        await self._publish(
            "human_control.notification.created",
            result["workspace_id"],
            result["id"],
            {"notification": result, "manual": True},
        )
        return result

    async def test_channel(
        self, channel_id: str, request: HumanControlNotificationChannelTestRequest
    ) -> dict[str, Any]:
        channel = self.get_channel(channel_id)
        if channel is None:
            raise HumanControlNotFound("Канал уведомлений не найден.")
        notification = await self.create_manual(
            HumanControlNotificationManualCreate(
                workspace_id=channel["workspace_id"],
                channel_id=channel_id,
                recipient_actor_id=request.recipient_actor_id or request.actor_id,
                title=request.title,
                body=request.body,
                ack_required=request.require_ack,
                idempotency_key=(
                    f"channel-test:{channel_id}:{request.actor_id}:"
                    f"{int(utc_now().timestamp() * 1000000)}"
                ),
                created_by=request.actor_id,
                payload={"test": True, **dict(request.metadata)},
            )
        )
        dispatch = await self.dispatch_due(
            limit=1, notification_ids={notification["id"]}
        )
        return {"notification": self.get_notification(notification["id"]), "dispatch": dispatch}

    async def dispatch_due(
        self,
        *,
        limit: int = 100,
        notification_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        async with self._dispatch_lock:
            now = utc_now()
            with self._session_factory() as session:
                statement = (
                    select(HumanControlNotificationModel)
                    .where(
                        HumanControlNotificationModel.status.in_(
                            ["pending", "retrying"]
                        ),
                        HumanControlNotificationModel.available_at <= now,
                    )
                    .order_by(
                        HumanControlNotificationModel.priority.desc(),
                        HumanControlNotificationModel.created_at,
                    )
                    .limit(max(1, min(500, limit)))
                )
                if notification_ids:
                    statement = statement.where(
                        HumanControlNotificationModel.id.in_(notification_ids)
                    )
                rows = list(session.scalars(statement).all())
                selected: list[tuple[str, int]] = []
                for row in rows:
                    expires_at = ensure_utc(row.expires_at)
                    if expires_at is not None and expires_at <= now:
                        row.status = "expired"
                        row.last_error = "Notification expired before delivery."
                        continue
                    row.status = "delivering"
                    row.delivery_started_at = now
                    row.attempt_count += 1
                    attempt = HumanControlNotificationAttemptModel(
                        notification_id=row.id,
                        channel_id=row.channel_id,
                        attempt_number=row.attempt_count,
                        adapter="pending",
                        status="started",
                        started_at=now,
                    )
                    session.add(attempt)
                    session.flush()
                    selected.append((row.id, attempt.id))

            results: list[dict[str, Any]] = []
            for notification_id, attempt_id in selected:
                results.append(
                    await self._deliver_one(notification_id, attempt_id)
                )
            return {
                "selected": len(selected),
                "delivered": sum(1 for row in results if row.get("delivered")),
                "failed": sum(1 for row in results if row.get("failed")),
                "retrying": sum(1 for row in results if row.get("retrying")),
                "results": results,
                "dispatched_at": iso(utc_now()),
            }

    async def _deliver_one(
        self, notification_id: str, attempt_id: str
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            notification = session.get(HumanControlNotificationModel, notification_id)
            attempt = session.get(HumanControlNotificationAttemptModel, attempt_id)
            if notification is None or attempt is None:
                return {"notification_id": notification_id, "failed": True, "error": "missing_row"}
            channel = (
                session.get(HumanControlNotificationChannelModel, notification.channel_id)
                if notification.channel_id
                else None
            )
            if channel is None:
                channel_dict = None
                notification_dict = self._notification_to_dict(notification)
            else:
                channel_dict = self._channel_to_dict(channel)
                notification_dict = self._notification_to_dict(notification)
                attempt.adapter = channel.channel_type

        error = ""
        response: dict[str, Any] = {}
        response_code: int | None = None
        delivered = False
        try:
            if channel_dict is None:
                raise HumanControlError("Канал уведомлений удалён или недоступен.")
            if not channel_dict["enabled"]:
                raise HumanControlError("Канал уведомлений отключён.")
            adapter = self._adapters.get(channel_dict["channel_type"])
            if adapter is None:
                raise HumanControlError(
                    "Для типа канала не зарегистрирован delivery adapter: "
                    + channel_dict["channel_type"]
                )
            response = await asyncio.wait_for(
                adapter(channel_dict, notification_dict),
                timeout=channel_dict["timeout_seconds"],
            )
            response_code = response.get("status_code")
            delivered = bool(response.get("ok", True))
            if not delivered:
                raise HumanControlError(str(response.get("error") or "Delivery rejected."))
        except Exception as exc:
            error = str(exc)

        now = utc_now()
        with self._session_factory() as session:
            notification = session.get(HumanControlNotificationModel, notification_id)
            attempt = session.get(HumanControlNotificationAttemptModel, attempt_id)
            if notification is None or attempt is None:
                return {"notification_id": notification_id, "failed": True, "error": "missing_row_after_delivery"}
            attempt.finished_at = now
            attempt.response_code = response_code
            attempt.response_json = dict(response)
            if delivered:
                attempt.status = "delivered"
                notification.status = "delivered"
                notification.delivered_at = now
                notification.delivery_started_at = None
                notification.last_error = ""
                if not notification.ack_required:
                    notification.ack_status = "not_required"
                self._record_receipt(
                    session,
                    notification_id=notification.id,
                    receipt_type="delivered",
                    actor_id="delivery_adapter",
                    idempotency_key=f"delivery:{notification.id}:{attempt.attempt_number}",
                    metadata={"channel_id": notification.channel_id, "response": response},
                )
                result = {
                    "notification_id": notification.id,
                    "delivered": True,
                    "status": notification.status,
                }
            else:
                attempt.status = "failed"
                attempt.error = error
                notification.delivery_started_at = None
                notification.last_error = error
                if notification.attempt_count < notification.max_attempts:
                    channel = (
                        session.get(HumanControlNotificationChannelModel, notification.channel_id)
                        if notification.channel_id
                        else None
                    )
                    retry_base = channel.retry_base_seconds if channel else 30
                    delay = min(86400, retry_base * (2 ** max(0, notification.attempt_count - 1)))
                    notification.status = "retrying"
                    notification.available_at = now + timedelta(seconds=delay)
                    result = {
                        "notification_id": notification.id,
                        "retrying": True,
                        "status": notification.status,
                        "retry_at": iso(notification.available_at),
                        "error": error,
                    }
                else:
                    notification.status = "failed"
                    result = {
                        "notification_id": notification.id,
                        "failed": True,
                        "status": notification.status,
                        "error": error,
                    }
            final = self._notification_to_dict(notification)

        if delivered:
            self._deliveries_completed += 1
            await self._publish(
                "human_control.notification.delivered",
                final["workspace_id"],
                notification_id,
                {"notification": final, "delivery_response": response},
            )
        else:
            self._deliveries_failed += 1
            await self._publish(
                "human_control.notification.delivery_failed",
                final["workspace_id"],
                notification_id,
                {"notification": final, "error": error},
            )
        return result

    async def reconcile(self, *, actor_id: str = "system") -> dict[str, Any]:
        now = utc_now()
        recovered = 0
        expired = 0
        with self._session_factory() as session:
            rows = list(
                session.scalars(
                    select(HumanControlNotificationModel).where(
                        HumanControlNotificationModel.status.in_(
                            ["delivering", "pending", "retrying", "delivered"]
                        )
                    )
                ).all()
            )
            for row in rows:
                expires = ensure_utc(row.expires_at)
                if expires is not None and expires <= now and row.status != "delivered":
                    row.status = "expired"
                    row.last_error = "Notification expired."
                    expired += 1
                    continue
                started = ensure_utc(row.delivery_started_at)
                if (
                    row.status == "delivering"
                    and started is not None
                    and started <= now - timedelta(minutes=10)
                ):
                    row.status = "retrying"
                    row.available_at = now
                    row.delivery_started_at = None
                    row.last_error = "Recovered stale delivery lease."
                    recovered += 1
        if recovered or expired:
            await self._publish(
                "human_control.notification.reconciled",
                None,
                actor_id,
                {"recovered": recovered, "expired": expired, "actor_id": actor_id},
            )
        return {"recovered": recovered, "expired": expired, "reconciled_at": iso(now)}

    async def scan_ack_deadlines(
        self, *, actor_id: str = "system"
    ) -> dict[str, Any]:
        now = utc_now()
        overdue_rows: list[dict[str, Any]] = []
        with self._session_factory() as session:
            rows = list(
                session.scalars(
                    select(HumanControlNotificationModel).where(
                        HumanControlNotificationModel.status == "delivered",
                        HumanControlNotificationModel.ack_required.is_(True),
                        HumanControlNotificationModel.ack_status == "pending",
                        HumanControlNotificationModel.ack_due_at.is_not(None),
                        HumanControlNotificationModel.ack_due_at <= now,
                    )
                ).all()
            )
            for row in rows:
                row.ack_status = "overdue"
                self._record_receipt(
                    session,
                    notification_id=row.id,
                    receipt_type="overdue",
                    actor_id=actor_id,
                    idempotency_key=f"ack-overdue:{row.id}",
                    metadata={"ack_due_at": iso(row.ack_due_at)},
                )
                overdue_rows.append(self._notification_to_dict(row))
        for row in overdue_rows:
            await self._publish(
                "human_control.notification.ack_overdue",
                row["workspace_id"],
                row["id"],
                {"notification": row, "actor_id": actor_id},
            )
        return {"overdue": len(overdue_rows), "notifications": overdue_rows}

    def list_notifications(
        self,
        *,
        workspace_id: str | None = None,
        recipient_actor_id: str | None = None,
        status: str | None = None,
        ack_status: str | None = None,
        severity: str | None = None,
        channel_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlNotificationModel)
            if workspace_id is not None:
                statement = statement.where(
                    HumanControlNotificationModel.workspace_id == workspace_id
                )
            if recipient_actor_id is not None:
                statement = statement.where(
                    HumanControlNotificationModel.recipient_actor_id
                    == recipient_actor_id
                )
            if status is not None:
                statement = statement.where(
                    HumanControlNotificationModel.status == status
                )
            if ack_status is not None:
                statement = statement.where(
                    HumanControlNotificationModel.ack_status == ack_status
                )
            if severity is not None:
                statement = statement.where(
                    HumanControlNotificationModel.severity == severity
                )
            if channel_id is not None:
                statement = statement.where(
                    HumanControlNotificationModel.channel_id == channel_id
                )
            statement = (
                statement.order_by(
                    HumanControlNotificationModel.priority.desc(),
                    HumanControlNotificationModel.created_at.desc(),
                )
                .limit(max(1, min(500, limit)))
                .offset(max(0, offset))
            )
            return [
                self._notification_to_dict(row)
                for row in session.scalars(statement).all()
            ]

    def get_notification(self, notification_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(HumanControlNotificationModel, notification_id)
            return None if row is None else self._notification_to_dict(row)

    async def mark_read(
        self, notification_id: str, request: HumanControlNotificationReadRequest
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_notification(session, notification_id)
            self._authorize_recipient(row, request.actor_id, force=False)
            receipt = session.scalar(
                select(HumanControlNotificationReceiptModel).where(
                    HumanControlNotificationReceiptModel.idempotency_key
                    == request.idempotency_key
                )
            )
            if receipt is None:
                row.read_at = row.read_at or utc_now()
                self._record_receipt(
                    session,
                    notification_id=row.id,
                    receipt_type="read",
                    actor_id=request.actor_id,
                    idempotency_key=request.idempotency_key,
                    metadata=dict(request.metadata),
                )
            result = self._notification_to_dict(row)
        await self._publish(
            "human_control.notification.read",
            result["workspace_id"],
            notification_id,
            {"notification": result, "actor_id": request.actor_id},
        )
        return result

    async def acknowledge(
        self,
        notification_id: str,
        request: HumanControlNotificationAcknowledgeRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_notification(session, notification_id)
            self._authorize_recipient(row, request.actor_id, force=request.force)
            existing = session.scalar(
                select(HumanControlNotificationReceiptModel).where(
                    HumanControlNotificationReceiptModel.idempotency_key
                    == request.idempotency_key
                )
            )
            if existing is not None:
                return self._notification_to_dict(row)
            if row.status not in {"delivered", "acknowledged"}:
                raise HumanControlConflict(
                    "Подтвердить можно только доставленное уведомление."
                )
            if not row.ack_required and not request.force:
                raise HumanControlConflict(
                    "Для этого уведомления подтверждение не требуется."
                )
            now = utc_now()
            row.status = "acknowledged"
            row.ack_status = "acknowledged"
            row.acknowledged_at = now
            row.acknowledged_by = request.actor_id
            row.acknowledgement_note = request.note
            row.read_at = row.read_at or now
            self._record_receipt(
                session,
                notification_id=row.id,
                receipt_type="acknowledged",
                actor_id=request.actor_id,
                idempotency_key=request.idempotency_key,
                metadata={"note": request.note, **dict(request.metadata)},
            )
            result = self._notification_to_dict(row)
        self._acknowledgements += 1
        await self._publish(
            "human_control.notification.acknowledged",
            result["workspace_id"],
            notification_id,
            {"notification": result, "actor_id": request.actor_id},
        )
        return result

    async def retry_notification(
        self, notification_id: str, request: HumanControlNotificationRetryRequest
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_notification(session, notification_id)
            if row.status not in {"failed", "cancelled", "expired"} and not request.force:
                raise HumanControlConflict(
                    "Повтор разрешён только для failed/cancelled/expired уведомления."
                )
            row.status = "retrying"
            row.available_at = utc_now()
            row.delivery_started_at = None
            row.last_error = f"Manual retry by {request.actor_id}: {request.reason}"
            if request.force and row.attempt_count >= row.max_attempts:
                row.max_attempts = row.attempt_count + 1
            result = self._notification_to_dict(row)
        await self._publish(
            "human_control.notification.retry_requested",
            result["workspace_id"],
            notification_id,
            {"notification": result, "actor_id": request.actor_id, "reason": request.reason},
        )
        return result

    async def cancel_notification(
        self, notification_id: str, request: HumanControlNotificationCancelRequest
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_notification(session, notification_id)
            if row.status in {"acknowledged", "cancelled"} and not request.force:
                raise HumanControlConflict("Уведомление уже завершено.")
            row.status = "cancelled"
            row.last_error = f"Cancelled by {request.actor_id}: {request.reason}"
            result = self._notification_to_dict(row)
        await self._publish(
            "human_control.notification.cancelled",
            result["workspace_id"],
            notification_id,
            {"notification": result, "actor_id": request.actor_id, "reason": request.reason},
        )
        return result

    def list_attempts(
        self, *, notification_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlNotificationAttemptModel)
            if notification_id is not None:
                statement = statement.where(
                    HumanControlNotificationAttemptModel.notification_id
                    == notification_id
                )
            statement = statement.order_by(
                HumanControlNotificationAttemptModel.started_at.desc()
            ).limit(max(1, min(500, limit)))
            return [self._attempt_to_dict(row) for row in session.scalars(statement).all()]

    def list_receipts(
        self, *, notification_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlNotificationReceiptModel)
            if notification_id is not None:
                statement = statement.where(
                    HumanControlNotificationReceiptModel.notification_id
                    == notification_id
                )
            statement = statement.order_by(
                HumanControlNotificationReceiptModel.created_at.desc()
            ).limit(max(1, min(500, limit)))
            return [self._receipt_to_dict(row) for row in session.scalars(statement).all()]

    def _matching_subscriptions(
        self,
        session: Session,
        *,
        event: Event,
        context: dict[str, Any],
    ) -> list[
        tuple[
            HumanControlNotificationSubscriptionModel,
            HumanControlNotificationChannelModel,
        ]
    ]:
        statement = (
            select(
                HumanControlNotificationSubscriptionModel,
                HumanControlNotificationChannelModel,
            )
            .join(
                HumanControlNotificationChannelModel,
                HumanControlNotificationChannelModel.id
                == HumanControlNotificationSubscriptionModel.channel_id,
            )
            .where(
                HumanControlNotificationSubscriptionModel.enabled.is_(True),
                HumanControlNotificationChannelModel.enabled.is_(True),
            )
        )
        workspace_id = context["workspace_id"]
        if workspace_id is not None:
            statement = statement.where(
                or_(
                    HumanControlNotificationSubscriptionModel.workspace_id
                    == workspace_id,
                    HumanControlNotificationSubscriptionModel.workspace_id.is_(None),
                )
            )
        result = []
        for subscription, channel in session.execute(statement).all():
            patterns = list(subscription.event_patterns_json or [])
            if not any(
                fnmatch.fnmatchcase(event.event_type, pattern)
                for pattern in patterns
            ):
                continue
            source_types = set(subscription.source_types_json or [])
            if source_types and context.get("source_type") not in source_types:
                continue
            risk_levels = set(subscription.risk_levels_json or [])
            if risk_levels and context["severity"] not in risk_levels:
                continue
            if context["priority"] < subscription.min_priority:
                continue
            result.append((subscription, channel))
        return result

    def _resolve_recipients(
        self,
        session: Session,
        *,
        subscription: HumanControlNotificationSubscriptionModel,
        workspace_id: str | None,
    ) -> list[str]:
        recipients: set[str] = set()
        if subscription.actor_id:
            recipients.add(subscription.actor_id)
        role_keys = set(subscription.role_keys_json or [])
        if role_keys:
            now = utc_now()
            statement = (
                select(HumanControlRoleBindingModel.actor_id)
                .join(
                    HumanControlRoleModel,
                    HumanControlRoleModel.id == HumanControlRoleBindingModel.role_id,
                )
                .where(
                    HumanControlRoleBindingModel.enabled.is_(True),
                    HumanControlRoleModel.enabled.is_(True),
                    HumanControlRoleModel.role_key.in_(role_keys),
                    or_(
                        HumanControlRoleBindingModel.valid_from.is_(None),
                        HumanControlRoleBindingModel.valid_from <= now,
                    ),
                    or_(
                        HumanControlRoleBindingModel.expires_at.is_(None),
                        HumanControlRoleBindingModel.expires_at > now,
                    ),
                )
            )
            if workspace_id is not None:
                statement = statement.where(
                    or_(
                        HumanControlRoleBindingModel.workspace_id == workspace_id,
                        HumanControlRoleBindingModel.workspace_id.is_(None),
                    )
                )
            recipients.update(str(value) for value in session.scalars(statement).all())
        return sorted(value for value in recipients if value)

    def _event_context(self, event: Event) -> dict[str, Any]:
        payload = dict(event.payload or {})
        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        escalation = (
            payload.get("escalation")
            if isinstance(payload.get("escalation"), dict)
            else {}
        )
        case = payload.get("case") if isinstance(payload.get("case"), dict) else {}
        source = item or escalation or case or payload
        workspace_id = event.workspace_id or source.get("workspace_id")
        severity = str(
            item.get("risk_level")
            or source.get("risk_level")
            or source.get("severity")
            or ("critical" if "break_glass" in event.event_type else "medium")
        ).lower()
        if severity not in {"low", "medium", "high", "critical"}:
            severity = "medium"
        priority = int(
            item.get("priority")
            or source.get("priority")
            or {"low": 25, "medium": 50, "high": 80, "critical": 100}[severity]
        )
        priority = max(0, min(100, priority))
        title = str(
            item.get("title")
            or source.get("title")
            or source.get("name")
            or self._humanize_event_type(event.event_type)
        )[:500]
        body = str(
            item.get("summary")
            or source.get("summary")
            or source.get("reason")
            or source.get("description")
            or f"Event {event.event_type} requires operator attention."
        )[:20000]
        source_type = item.get("source_type") or source.get("source_type")
        if not source_type:
            if event.event_type.startswith("human_control.governance.escalation"):
                source_type = "governance_escalation"
            elif event.event_type.startswith("human_control.auth"):
                source_type = "security_event"
            else:
                source_type = "human_control"
        source_id = (
            item.get("id")
            or escalation.get("id")
            or case.get("id")
            or event.correlation_id
            or event.id
        )
        return {
            "workspace_id": workspace_id,
            "item_id": item.get("id"),
            "source_type": str(source_type),
            "source_id": str(source_id) if source_id else None,
            "severity": severity,
            "priority": priority,
            "title": title,
            "body": body,
            "expires_at": item.get("expires_at") or source.get("expires_at"),
        }

    @staticmethod
    def _event_idempotency_key(
        event: Event,
        *,
        subscription: HumanControlNotificationSubscriptionModel,
        recipient_actor_id: str,
        source_id: str | None,
        open_item_sync: bool,
    ) -> str:
        event_key = f"open:{source_id}" if open_item_sync else event.id
        return (
            f"event:{event_key}:subscription:{subscription.id}:"
            f"recipient:{recipient_actor_id}"
        )[:255]

    @staticmethod
    def _scope_key(workspace_id: str | None, key: str) -> str:
        return f"workspace:{workspace_id}:{key}" if workspace_id else f"global:{key}"

    @staticmethod
    def _validate_workspace_compatibility(
        requested_workspace_id: str | None, channel_workspace_id: str | None
    ) -> None:
        if (
            channel_workspace_id is not None
            and requested_workspace_id != channel_workspace_id
        ):
            raise HumanControlConflict(
                "Канал уведомлений принадлежит другому Workspace."
            )

    @staticmethod
    def _humanize_event_type(event_type: str) -> str:
        return event_type.replace("human_control.", "").replace(".", " ").strip().title()

    @staticmethod
    def _require_notification(
        session: Session, notification_id: str
    ) -> HumanControlNotificationModel:
        row = session.get(HumanControlNotificationModel, notification_id)
        if row is None:
            raise HumanControlNotFound("Уведомление не найдено.")
        return row

    @staticmethod
    def _authorize_recipient(
        row: HumanControlNotificationModel, actor_id: str, *, force: bool
    ) -> None:
        if row.recipient_actor_id != actor_id and not force:
            raise HumanControlConflict(
                "Уведомление адресовано другому оператору."
            )

    @staticmethod
    def _record_receipt(
        session: Session,
        *,
        notification_id: str,
        receipt_type: str,
        actor_id: str,
        idempotency_key: str,
        metadata: dict[str, Any],
    ) -> HumanControlNotificationReceiptModel:
        existing = session.scalar(
            select(HumanControlNotificationReceiptModel).where(
                HumanControlNotificationReceiptModel.idempotency_key
                == idempotency_key
            )
        )
        if existing is not None:
            return existing
        row = HumanControlNotificationReceiptModel(
            notification_id=notification_id,
            receipt_type=receipt_type,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            metadata_json=dict(metadata),
        )
        session.add(row)
        return row

    async def _deliver_in_app(
        self, channel: dict[str, Any], notification: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "adapter": "in_app",
            "stored_notification_id": notification["id"],
        }

    async def _deliver_log(
        self, channel: dict[str, Any], notification: dict[str, Any]
    ) -> dict[str, Any]:
        logger.warning(
            "Operator notification recipient=%s severity=%s title=%s",
            notification["recipient_actor_id"],
            notification["severity"],
            notification["title"],
        )
        return {"ok": True, "adapter": "log"}

    async def _deliver_webhook(
        self, channel: dict[str, Any], notification: dict[str, Any]
    ) -> dict[str, Any]:
        endpoint = channel.get("endpoint_url")
        if not endpoint:
            raise HumanControlError("Webhook channel has no endpoint_url.")
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - dependency is in project.
            raise HumanControlError("Для webhook-доставки требуется httpx.") from exc
        config = dict(channel.get("config") or {})
        headers = {
            str(key): str(value)
            for key, value in dict(config.get("headers") or {}).items()
        }
        payload = {
            "schema_version": 1,
            "notification": notification,
            "channel": {
                "id": channel["id"],
                "channel_key": channel["channel_key"],
                "channel_type": channel["channel_type"],
                "credential_ref": channel.get("credential_ref"),
            },
        }
        async with httpx.AsyncClient(timeout=channel["timeout_seconds"]) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
        if response.status_code < 200 or response.status_code >= 300:
            raise HumanControlError(
                f"Webhook returned HTTP {response.status_code}."
            )
        return {
            "ok": True,
            "adapter": "webhook",
            "status_code": response.status_code,
            "response_text": response.text[:2000],
        }

    async def _publish(
        self,
        event_type: str,
        workspace_id: str | None,
        correlation_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="human_control.notifications",
                workspace_id=workspace_id,
                correlation_id=correlation_id,
                payload=payload,
            )
        )

    @staticmethod
    def _channel_to_dict(row: HumanControlNotificationChannelModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope_key": row.scope_key,
            "workspace_id": row.workspace_id,
            "channel_key": row.channel_key,
            "name": row.name,
            "channel_type": row.channel_type,
            "enabled": row.enabled,
            "endpoint_url": row.endpoint_url,
            "credential_ref": row.credential_ref,
            "config": dict(row.config_json or {}),
            "timeout_seconds": row.timeout_seconds,
            "max_attempts": row.max_attempts,
            "retry_base_seconds": row.retry_base_seconds,
            "default_ack_required": row.default_ack_required,
            "default_ack_timeout_seconds": row.default_ack_timeout_seconds,
            "created_by": row.created_by,
            "updated_by": row.updated_by,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _subscription_to_dict(
        row: HumanControlNotificationSubscriptionModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope_key": row.scope_key,
            "workspace_id": row.workspace_id,
            "subscription_key": row.subscription_key,
            "name": row.name,
            "channel_id": row.channel_id,
            "actor_id": row.actor_id,
            "role_keys": list(row.role_keys_json or []),
            "event_patterns": list(row.event_patterns_json or []),
            "source_types": list(row.source_types_json or []),
            "risk_levels": list(row.risk_levels_json or []),
            "min_priority": row.min_priority,
            "enabled": row.enabled,
            "ack_required": row.ack_required,
            "ack_timeout_seconds": row.ack_timeout_seconds,
            "metadata": dict(row.metadata_json or {}),
            "created_by": row.created_by,
            "updated_by": row.updated_by,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _notification_to_dict(row: HumanControlNotificationModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "item_id": row.item_id,
            "channel_id": row.channel_id,
            "subscription_id": row.subscription_id,
            "recipient_actor_id": row.recipient_actor_id,
            "event_type": row.event_type,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "severity": row.severity,
            "priority": row.priority,
            "title": row.title,
            "body": row.body,
            "payload": dict(row.payload_json or {}),
            "idempotency_key": row.idempotency_key,
            "status": row.status,
            "ack_required": row.ack_required,
            "ack_status": row.ack_status,
            "attempt_count": row.attempt_count,
            "max_attempts": row.max_attempts,
            "available_at": iso(row.available_at),
            "delivery_started_at": iso(row.delivery_started_at),
            "delivered_at": iso(row.delivered_at),
            "ack_due_at": iso(row.ack_due_at),
            "read_at": iso(row.read_at),
            "acknowledged_at": iso(row.acknowledged_at),
            "acknowledged_by": row.acknowledged_by,
            "acknowledgement_note": row.acknowledgement_note,
            "expires_at": iso(row.expires_at),
            "last_error": row.last_error,
            "created_by": row.created_by,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _attempt_to_dict(row: HumanControlNotificationAttemptModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "notification_id": row.notification_id,
            "channel_id": row.channel_id,
            "attempt_number": row.attempt_number,
            "adapter": row.adapter,
            "status": row.status,
            "response_code": row.response_code,
            "response": dict(row.response_json or {}),
            "error": row.error,
            "started_at": iso(row.started_at),
            "finished_at": iso(row.finished_at),
        }

    @staticmethod
    def _receipt_to_dict(row: HumanControlNotificationReceiptModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "notification_id": row.notification_id,
            "receipt_type": row.receipt_type,
            "actor_id": row.actor_id,
            "idempotency_key": row.idempotency_key,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
        }
