from __future__ import annotations

import asyncio
import fnmatch
import logging
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, time, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.control_center.models import (
    HumanControlNotificationChannelModel,
    HumanControlNotificationEscalationAttemptModel,
    HumanControlNotificationEscalationModel,
    HumanControlNotificationEscalationRuleModel,
    HumanControlNotificationModel,
    HumanControlNotificationRoutingRuleModel,
    HumanControlOnCallMemberModel,
    HumanControlOnCallScheduleModel,
    HumanControlOperatorAvailabilityModel,
    HumanControlRoleBindingModel,
    HumanControlRoleModel,
)
from backend.control_center.notification_schemas import (
    HumanControlNotificationManualCreate,
)
from backend.control_center.routing_schemas import (
    HumanControlAvailabilityHeartbeat,
    HumanControlAvailabilityUpsert,
    HumanControlEscalationManualRequest,
    HumanControlEscalationResolveRequest,
    HumanControlEscalationRuleCreate,
    HumanControlEscalationRuleUpdate,
    HumanControlOnCallMemberCreate,
    HumanControlOnCallMemberUpdate,
    HumanControlOnCallScheduleCreate,
    HumanControlOnCallScheduleUpdate,
    HumanControlRoutingEvaluateRequest,
    HumanControlRoutingRuleCreate,
    HumanControlRoutingRuleUpdate,
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
logger = logging.getLogger(__name__)


class HumanControlRoutingService:
    """On-call routing, operator availability and notification escalation."""

    def __init__(
        self,
        *,
        event_bus: EventBus,
        notification_service: Any | None = None,
        session_factory: SessionContextFactory = session_scope,
        scan_interval_seconds: int = 15,
    ) -> None:
        self._event_bus = event_bus
        self._notification_service = notification_service
        self._session_factory = session_factory
        self._scan_interval_seconds = max(5, int(scan_interval_seconds))
        self._monitor_task: asyncio.Task[None] | None = None
        self._scan_lock = asyncio.Lock()
        self._route_decisions = 0
        self._escalations_created = 0
        self._escalations_routed = 0

    @property
    def running(self) -> bool:
        return self._monitor_task is not None and not self._monitor_task.done()

    def set_notification_service(self, service: Any) -> None:
        self._notification_service = service

    async def start(self) -> None:
        if self.running:
            return
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(), name="human-control-routing-monitor"
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
                await self.reconcile(actor_id="routing_monitor")
                await self.scan_escalations(actor_id="routing_monitor")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Human Control routing monitor iteration failed")
            await asyncio.sleep(self._scan_interval_seconds)

    def status(self) -> dict[str, Any]:
        with self._session_factory() as session:
            counts = {
                "schedules": self._count(session, HumanControlOnCallScheduleModel),
                "members": self._count(session, HumanControlOnCallMemberModel),
                "availability_records": self._count(
                    session, HumanControlOperatorAvailabilityModel
                ),
                "routing_rules": self._count(
                    session, HumanControlNotificationRoutingRuleModel
                ),
                "escalation_rules": self._count(
                    session, HumanControlNotificationEscalationRuleModel
                ),
            }
            case_rows = session.execute(
                select(
                    HumanControlNotificationEscalationModel.status,
                    func.count(HumanControlNotificationEscalationModel.id),
                ).group_by(HumanControlNotificationEscalationModel.status)
            ).all()
        return {
            "running": self.running,
            "scan_interval_seconds": self._scan_interval_seconds,
            **counts,
            "escalations_by_status": {
                str(status): int(count) for status, count in case_rows
            },
            "route_decisions": self._route_decisions,
            "escalations_created": self._escalations_created,
            "escalations_routed": self._escalations_routed,
            "capabilities": [
                "on_call_schedules",
                "time_window_membership",
                "operator_availability_and_heartbeat",
                "availability_aware_notification_routing",
                "first_available_round_robin_broadcast_primary_backup",
                "ack_and_delivery_escalation_rules",
                "persistent_escalation_history",
                "audit_and_event_transport",
            ],
        }

    def dashboard(self, *, workspace_id: str | None = None) -> dict[str, Any]:
        with self._session_factory() as session:
            schedules = self.list_schedules(workspace_id=workspace_id, session=session)
            availability = self.list_availability(
                workspace_id=workspace_id, session=session
            )
            escalations = self.list_escalations(
                workspace_id=workspace_id, limit=500, session=session
            )
        return {
            "workspace_id": workspace_id,
            "generated_at": iso(utc_now()),
            "enabled_schedules": sum(1 for row in schedules if row["enabled"]),
            "availability_by_status": dict(
                Counter(row["status"] for row in availability)
            ),
            "open_escalations": sum(
                1 for row in escalations if row["status"] == "open"
            ),
            "exhausted_escalations": sum(
                1 for row in escalations if row["status"] == "exhausted"
            ),
            "schedules": schedules,
            "availability": availability,
        }

    # ------------------------------------------------------------------
    # On-call schedules and membership
    # ------------------------------------------------------------------
    async def create_schedule(
        self, request: HumanControlOnCallScheduleCreate
    ) -> dict[str, Any]:
        self._validate_timezone(request.timezone)
        scope_key = self._scope_key(request.workspace_id, request.schedule_key)
        with self._session_factory() as session:
            if session.scalar(
                select(HumanControlOnCallScheduleModel).where(
                    HumanControlOnCallScheduleModel.scope_key == scope_key
                )
            ):
                raise HumanControlConflict("On-call расписание с таким ключом уже существует.")
            row = HumanControlOnCallScheduleModel(
                scope_key=scope_key,
                workspace_id=request.workspace_id,
                schedule_key=request.schedule_key,
                name=request.name,
                description=request.description,
                timezone=request.timezone,
                enabled=request.enabled,
                routing_strategy=request.routing_strategy.value,
                fallback_role_keys_json=list(request.fallback_role_keys),
                metadata_json=dict(request.metadata),
                created_by=request.created_by,
                updated_by=request.created_by,
            )
            session.add(row)
            session.flush()
            result = self._schedule_to_dict(row)
        await self._publish(
            "human_control.routing.schedule.created",
            result["workspace_id"],
            result["id"],
            {"schedule": result},
        )
        return result

    async def update_schedule(
        self, schedule_id: str, request: HumanControlOnCallScheduleUpdate
    ) -> dict[str, Any]:
        if request.timezone is not None:
            self._validate_timezone(request.timezone)
        with self._session_factory() as session:
            row = self._require(session, HumanControlOnCallScheduleModel, schedule_id, "On-call расписание")
            changes = request.model_dump(exclude_unset=True)
            actor_id = changes.pop("actor_id")
            field_map = {
                "fallback_role_keys": "fallback_role_keys_json",
                "metadata": "metadata_json",
            }
            for key, value in changes.items():
                target = field_map.get(key, key)
                if hasattr(value, "value"):
                    value = value.value
                setattr(row, target, value)
            row.updated_by = actor_id
            result = self._schedule_to_dict(row)
        await self._publish(
            "human_control.routing.schedule.updated",
            result["workspace_id"],
            schedule_id,
            {"schedule": result, "actor_id": actor_id},
        )
        return result

    def list_schedules(
        self,
        *,
        workspace_id: str | None = None,
        enabled: bool | None = None,
        session: Session | None = None,
    ) -> list[dict[str, Any]]:
        def execute(db: Session) -> list[dict[str, Any]]:
            statement = select(HumanControlOnCallScheduleModel)
            if workspace_id is not None:
                statement = statement.where(
                    HumanControlOnCallScheduleModel.workspace_id == workspace_id
                )
            if enabled is not None:
                statement = statement.where(
                    HumanControlOnCallScheduleModel.enabled.is_(enabled)
                )
            statement = statement.order_by(HumanControlOnCallScheduleModel.name)
            return [self._schedule_to_dict(row) for row in db.scalars(statement).all()]
        if session is not None:
            return execute(session)
        with self._session_factory() as db:
            return execute(db)

    def get_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(HumanControlOnCallScheduleModel, schedule_id)
            return None if row is None else self._schedule_to_dict(row)

    async def add_member(
        self, schedule_id: str, request: HumanControlOnCallMemberCreate
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            schedule = self._require(
                session, HumanControlOnCallScheduleModel, schedule_id, "On-call расписание"
            )
            existing = session.scalar(
                select(HumanControlOnCallMemberModel).where(
                    HumanControlOnCallMemberModel.schedule_id == schedule_id,
                    HumanControlOnCallMemberModel.actor_id == request.actor_id,
                )
            )
            if existing is not None:
                raise HumanControlConflict("Оператор уже добавлен в это расписание.")
            row = HumanControlOnCallMemberModel(
                schedule_id=schedule_id,
                actor_id=request.actor_id,
                role_key=request.role_key,
                priority=request.priority,
                is_backup=request.is_backup,
                weekdays_json=list(request.weekdays),
                start_time=request.start_time,
                end_time=request.end_time,
                valid_from=ensure_utc(request.valid_from),
                valid_until=ensure_utc(request.valid_until),
                max_active_notifications=request.max_active_notifications,
                enabled=request.enabled,
                metadata_json=dict(request.metadata),
                created_by=request.created_by,
                updated_by=request.created_by,
            )
            session.add(row)
            session.flush()
            result = self._member_to_dict(row)
            workspace_id = schedule.workspace_id
        await self._publish(
            "human_control.routing.schedule.member_added",
            workspace_id,
            result["id"],
            {"member": result, "schedule_id": schedule_id},
        )
        return result

    async def update_member(
        self, member_id: str, request: HumanControlOnCallMemberUpdate
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require(session, HumanControlOnCallMemberModel, member_id, "Участник on-call")
            changes = request.model_dump(exclude_unset=True)
            actor = changes.pop("actor_id_updated_by")
            field_map = {"weekdays": "weekdays_json", "metadata": "metadata_json"}
            for key, value in changes.items():
                setattr(row, field_map.get(key, key), ensure_utc(value) if key in {"valid_from", "valid_until"} else value)
            row.updated_by = actor
            schedule = session.get(HumanControlOnCallScheduleModel, row.schedule_id)
            result = self._member_to_dict(row)
            workspace_id = schedule.workspace_id if schedule else None
        await self._publish(
            "human_control.routing.schedule.member_updated",
            workspace_id,
            member_id,
            {"member": result, "actor_id": actor},
        )
        return result

    async def remove_member(self, member_id: str, *, actor_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require(session, HumanControlOnCallMemberModel, member_id, "Участник on-call")
            schedule = session.get(HumanControlOnCallScheduleModel, row.schedule_id)
            result = self._member_to_dict(row)
            session.delete(row)
            workspace_id = schedule.workspace_id if schedule else None
        await self._publish(
            "human_control.routing.schedule.member_removed",
            workspace_id,
            member_id,
            {"member": result, "actor_id": actor_id},
        )
        return result

    def list_members(
        self, schedule_id: str, *, active_only: bool = False
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlOnCallMemberModel).where(
                HumanControlOnCallMemberModel.schedule_id == schedule_id
            )
            if active_only:
                statement = statement.where(HumanControlOnCallMemberModel.enabled.is_(True))
            statement = statement.order_by(
                HumanControlOnCallMemberModel.is_backup,
                HumanControlOnCallMemberModel.priority.desc(),
                HumanControlOnCallMemberModel.actor_id,
            )
            return [self._member_to_dict(row) for row in session.scalars(statement).all()]

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------
    async def upsert_availability(
        self, request: HumanControlAvailabilityUpsert
    ) -> dict[str, Any]:
        key = self._workspace_actor_key(request.workspace_id, request.actor_id)
        with self._session_factory() as session:
            row = session.scalar(
                select(HumanControlOperatorAvailabilityModel).where(
                    HumanControlOperatorAvailabilityModel.workspace_actor_key == key
                )
            )
            if row is None:
                row = HumanControlOperatorAvailabilityModel(
                    workspace_actor_key=key,
                    workspace_id=request.workspace_id,
                    actor_id=request.actor_id,
                    status=request.status.value,
                    capacity_percent=request.capacity_percent,
                    active_notification_limit=request.active_notification_limit,
                    source=request.source.value,
                    available_until=ensure_utc(request.available_until),
                    last_seen_at=ensure_utc(request.last_seen_at) or utc_now(),
                    note=request.note,
                    metadata_json=dict(request.metadata),
                    updated_by=request.updated_by,
                )
                session.add(row)
            else:
                row.status = request.status.value
                row.capacity_percent = request.capacity_percent
                row.active_notification_limit = request.active_notification_limit
                row.source = request.source.value
                row.available_until = ensure_utc(request.available_until)
                row.last_seen_at = ensure_utc(request.last_seen_at) or utc_now()
                row.note = request.note
                row.metadata_json = dict(request.metadata)
                row.updated_by = request.updated_by
            session.flush()
            result = self._availability_to_dict(row)
        await self._publish(
            "human_control.routing.availability.updated",
            result["workspace_id"],
            result["actor_id"],
            {"availability": result},
        )
        return result

    async def heartbeat(
        self, request: HumanControlAvailabilityHeartbeat
    ) -> dict[str, Any]:
        return await self.upsert_availability(
            HumanControlAvailabilityUpsert(
                workspace_id=request.workspace_id,
                actor_id=request.actor_id,
                status=request.status,
                capacity_percent=request.capacity_percent,
                active_notification_limit=request.active_notification_limit,
                source="heartbeat",
                available_until=None,
                last_seen_at=utc_now(),
                note=request.note,
                updated_by=request.actor_id,
                metadata=request.metadata,
            )
        )

    def list_availability(
        self,
        *,
        workspace_id: str | None = None,
        actor_id: str | None = None,
        session: Session | None = None,
    ) -> list[dict[str, Any]]:
        def execute(db: Session) -> list[dict[str, Any]]:
            statement = select(HumanControlOperatorAvailabilityModel)
            if workspace_id is not None:
                statement = statement.where(
                    HumanControlOperatorAvailabilityModel.workspace_id == workspace_id
                )
            if actor_id is not None:
                statement = statement.where(
                    HumanControlOperatorAvailabilityModel.actor_id == actor_id
                )
            statement = statement.order_by(
                HumanControlOperatorAvailabilityModel.actor_id
            )
            return [self._availability_to_dict(row) for row in db.scalars(statement).all()]
        if session is not None:
            return execute(session)
        with self._session_factory() as db:
            return execute(db)

    # ------------------------------------------------------------------
    # Routing rules
    # ------------------------------------------------------------------
    async def create_routing_rule(
        self, request: HumanControlRoutingRuleCreate
    ) -> dict[str, Any]:
        scope_key = self._scope_key(request.workspace_id, request.rule_key)
        with self._session_factory() as session:
            if session.scalar(
                select(HumanControlNotificationRoutingRuleModel).where(
                    HumanControlNotificationRoutingRuleModel.scope_key == scope_key
                )
            ):
                raise HumanControlConflict("Правило маршрутизации с таким ключом уже существует.")
            self._validate_optional_schedule(session, request.schedule_id, request.workspace_id)
            row = HumanControlNotificationRoutingRuleModel(
                scope_key=scope_key,
                workspace_id=request.workspace_id,
                rule_key=request.rule_key,
                name=request.name,
                enabled=request.enabled,
                rule_priority=request.rule_priority,
                event_patterns_json=list(request.event_patterns),
                source_types_json=list(request.source_types),
                risk_levels_json=list(request.risk_levels),
                min_priority=request.min_priority,
                schedule_id=request.schedule_id,
                role_keys_json=list(request.role_keys),
                fallback_actor_ids_json=list(request.fallback_actor_ids),
                strategy=request.strategy.value,
                availability_required=request.availability_required,
                min_capacity_percent=request.min_capacity_percent,
                heartbeat_ttl_seconds=request.heartbeat_ttl_seconds,
                max_recipients=request.max_recipients,
                fallback_mode=request.fallback_mode.value,
                ack_required=request.ack_required,
                ack_timeout_seconds=request.ack_timeout_seconds,
                metadata_json=dict(request.metadata),
                created_by=request.created_by,
                updated_by=request.created_by,
            )
            session.add(row)
            session.flush()
            result = self._routing_rule_to_dict(row)
        await self._publish(
            "human_control.routing.rule.created",
            result["workspace_id"],
            result["id"],
            {"routing_rule": result},
        )
        return result

    async def update_routing_rule(
        self, rule_id: str, request: HumanControlRoutingRuleUpdate
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require(session, HumanControlNotificationRoutingRuleModel, rule_id, "Правило маршрутизации")
            changes = request.model_dump(exclude_unset=True)
            actor = changes.pop("actor_id")
            if "schedule_id" in changes:
                self._validate_optional_schedule(session, changes["schedule_id"], row.workspace_id)
            field_map = {
                "event_patterns": "event_patterns_json",
                "source_types": "source_types_json",
                "risk_levels": "risk_levels_json",
                "role_keys": "role_keys_json",
                "fallback_actor_ids": "fallback_actor_ids_json",
                "metadata": "metadata_json",
            }
            for key, value in changes.items():
                if hasattr(value, "value"):
                    value = value.value
                setattr(row, field_map.get(key, key), value)
            row.updated_by = actor
            result = self._routing_rule_to_dict(row)
        await self._publish(
            "human_control.routing.rule.updated",
            result["workspace_id"],
            rule_id,
            {"routing_rule": result, "actor_id": actor},
        )
        return result

    def list_routing_rules(
        self, *, workspace_id: str | None = None, enabled: bool | None = None
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlNotificationRoutingRuleModel)
            if workspace_id is not None:
                statement = statement.where(
                    HumanControlNotificationRoutingRuleModel.workspace_id == workspace_id
                )
            if enabled is not None:
                statement = statement.where(
                    HumanControlNotificationRoutingRuleModel.enabled.is_(enabled)
                )
            statement = statement.order_by(
                HumanControlNotificationRoutingRuleModel.rule_priority.desc(),
                HumanControlNotificationRoutingRuleModel.created_at,
            )
            return [self._routing_rule_to_dict(row) for row in session.scalars(statement).all()]

    def evaluate_route(
        self, request: HumanControlRoutingEvaluateRequest
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            return self.resolve_event_recipients(
                session=session,
                workspace_id=request.workspace_id,
                event_type=request.event_type,
                context={
                    "workspace_id": request.workspace_id,
                    "source_type": request.source_type,
                    "severity": request.severity,
                    "priority": request.priority,
                },
                role_keys=request.role_keys,
                base_recipients=request.base_recipients,
            )

    def resolve_event_recipients(
        self,
        *,
        session: Session,
        workspace_id: str | None,
        event_type: str,
        context: dict[str, Any],
        role_keys: list[str],
        base_recipients: list[str],
    ) -> dict[str, Any]:
        rule = self._matching_routing_rule(
            session,
            workspace_id=workspace_id,
            event_type=event_type,
            context=context,
        )
        if rule is None:
            return {
                "recipients": sorted(set(base_recipients)),
                "routing_rule_id": None,
                "strategy": "subscription_default",
                "reason": "no_matching_routing_rule",
                "ack_required": None,
                "ack_timeout_seconds": None,
                "candidates": [],
            }

        schedule = (
            session.get(HumanControlOnCallScheduleModel, rule.schedule_id)
            if rule.schedule_id
            else None
        )
        candidates = self._candidate_rows(
            session,
            workspace_id=workspace_id,
            schedule=schedule,
            role_keys=list(rule.role_keys_json or []) or list(role_keys),
            explicit_actor_ids=list(rule.fallback_actor_ids_json or []),
            base_recipients=list(base_recipients),
            heartbeat_ttl_seconds=rule.heartbeat_ttl_seconds,
            min_capacity_percent=rule.min_capacity_percent,
        )
        eligible = [
            candidate
            for candidate in candidates
            if candidate["eligible"] or not rule.availability_required
        ]
        selected = self._select_candidates(
            eligible,
            strategy=rule.strategy,
            max_recipients=rule.max_recipients,
            last_selected_actor_id=rule.last_selected_actor_id,
        )
        fallback_used = False
        if not selected:
            fallback_used = True
            if rule.fallback_mode == "base_recipients":
                selected = sorted(set(base_recipients))[: rule.max_recipients]
            elif rule.fallback_mode == "fallback_actors":
                selected = sorted(set(rule.fallback_actor_ids_json or []))[
                    : rule.max_recipients
                ]
            elif rule.fallback_mode == "broadcast_roles":
                selected = self._role_actor_ids(
                    session,
                    workspace_id=workspace_id,
                    role_keys=list(rule.role_keys_json or []) or list(role_keys),
                )[: rule.max_recipients]
            else:
                selected = []
        if selected:
            rule.last_selected_actor_id = selected[-1]
        self._route_decisions += 1
        return {
            "recipients": selected,
            "routing_rule_id": rule.id,
            "routing_rule_key": rule.rule_key,
            "strategy": rule.strategy,
            "fallback_used": fallback_used,
            "reason": "selected" if selected else "no_available_operator",
            "ack_required": rule.ack_required,
            "ack_timeout_seconds": rule.ack_timeout_seconds,
            "candidates": candidates,
        }

    # ------------------------------------------------------------------
    # Escalation rules and cases
    # ------------------------------------------------------------------
    async def create_escalation_rule(
        self, request: HumanControlEscalationRuleCreate
    ) -> dict[str, Any]:
        scope_key = self._scope_key(request.workspace_id, request.rule_key)
        with self._session_factory() as session:
            if session.scalar(
                select(HumanControlNotificationEscalationRuleModel).where(
                    HumanControlNotificationEscalationRuleModel.scope_key == scope_key
                )
            ):
                raise HumanControlConflict("Правило эскалации с таким ключом уже существует.")
            self._validate_optional_schedule(
                session, request.target_schedule_id, request.workspace_id
            )
            if request.channel_id and session.get(
                HumanControlNotificationChannelModel, request.channel_id
            ) is None:
                raise HumanControlNotFound("Канал уведомлений для эскалации не найден.")
            row = HumanControlNotificationEscalationRuleModel(
                scope_key=scope_key,
                workspace_id=request.workspace_id,
                rule_key=request.rule_key,
                name=request.name,
                enabled=request.enabled,
                rule_priority=request.rule_priority,
                event_patterns_json=list(request.event_patterns),
                source_types_json=list(request.source_types),
                risk_levels_json=list(request.risk_levels),
                min_priority=request.min_priority,
                trigger_on_json=[value.value for value in request.trigger_on],
                initial_delay_seconds=request.initial_delay_seconds,
                repeat_interval_seconds=request.repeat_interval_seconds,
                max_escalations=request.max_escalations,
                target_schedule_id=request.target_schedule_id,
                target_role_keys_json=list(request.target_role_keys),
                target_actor_ids_json=list(request.target_actor_ids),
                channel_id=request.channel_id,
                strategy=request.strategy.value,
                priority_increment=request.priority_increment,
                ack_required=request.ack_required,
                ack_timeout_seconds=request.ack_timeout_seconds,
                auto_resolve_on_ack=request.auto_resolve_on_ack,
                metadata_json=dict(request.metadata),
                created_by=request.created_by,
                updated_by=request.created_by,
            )
            session.add(row)
            session.flush()
            result = self._escalation_rule_to_dict(row)
        await self._publish(
            "human_control.routing.escalation_rule.created",
            result["workspace_id"],
            result["id"],
            {"escalation_rule": result},
        )
        return result

    async def update_escalation_rule(
        self, rule_id: str, request: HumanControlEscalationRuleUpdate
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require(session, HumanControlNotificationEscalationRuleModel, rule_id, "Правило эскалации")
            changes = request.model_dump(exclude_unset=True)
            actor = changes.pop("actor_id")
            if "target_schedule_id" in changes:
                self._validate_optional_schedule(
                    session, changes["target_schedule_id"], row.workspace_id
                )
            field_map = {
                "event_patterns": "event_patterns_json",
                "source_types": "source_types_json",
                "risk_levels": "risk_levels_json",
                "trigger_on": "trigger_on_json",
                "target_role_keys": "target_role_keys_json",
                "target_actor_ids": "target_actor_ids_json",
                "metadata": "metadata_json",
            }
            for key, value in changes.items():
                if key == "trigger_on" and value is not None:
                    value = [item.value if hasattr(item, "value") else str(item) for item in value]
                elif hasattr(value, "value"):
                    value = value.value
                setattr(row, field_map.get(key, key), value)
            row.updated_by = actor
            result = self._escalation_rule_to_dict(row)
        await self._publish(
            "human_control.routing.escalation_rule.updated",
            result["workspace_id"],
            rule_id,
            {"escalation_rule": result, "actor_id": actor},
        )
        return result

    def list_escalation_rules(
        self, *, workspace_id: str | None = None, enabled: bool | None = None
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlNotificationEscalationRuleModel)
            if workspace_id is not None:
                statement = statement.where(
                    HumanControlNotificationEscalationRuleModel.workspace_id
                    == workspace_id
                )
            if enabled is not None:
                statement = statement.where(
                    HumanControlNotificationEscalationRuleModel.enabled.is_(enabled)
                )
            statement = statement.order_by(
                HumanControlNotificationEscalationRuleModel.rule_priority.desc(),
                HumanControlNotificationEscalationRuleModel.created_at,
            )
            return [self._escalation_rule_to_dict(row) for row in session.scalars(statement).all()]

    async def handle_event(self, event: Event) -> dict[str, Any]:
        event_type = event.event_type
        notification = event.payload.get("notification")
        if not isinstance(notification, dict):
            notification = {}
        if event_type == "human_control.notification.acknowledged":
            return await self._resolve_by_ack(
                notification_id=notification.get("id") or event.correlation_id,
                actor_id=str(event.payload.get("actor_id") or "system"),
            )
        trigger_map = {
            "human_control.notification.ack_overdue": "ack_overdue",
            "human_control.notification.delivery_failed": "delivery_failed",
            "human_control.notification.unroutable": "unroutable",
        }
        trigger = trigger_map.get(event_type)
        if trigger is None:
            return {"skipped": True, "reason": "unsupported_event"}
        return await self._open_from_event(event, trigger_type=trigger)

    async def manual_escalate(
        self,
        notification_id: str,
        request: HumanControlEscalationManualRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            notification = self._require(
                session, HumanControlNotificationModel, notification_id, "Уведомление"
            )
            rule = (
                session.get(HumanControlNotificationEscalationRuleModel, request.rule_id)
                if request.rule_id
                else self._matching_escalation_rule(
                    session,
                    workspace_id=notification.workspace_id,
                    event_type=notification.event_type,
                    source_type=notification.source_type,
                    severity=notification.severity,
                    priority=notification.priority,
                    trigger_type="manual",
                )
            )
            if rule is None:
                raise HumanControlNotFound(
                    "Подходящее правило эскалации не найдено. Укажите rule_id."
                )
            case = self._create_escalation_case(
                session,
                notification=notification,
                rule=rule,
                trigger_type="manual",
                idempotency_key=request.idempotency_key,
                metadata={
                    "reason": request.reason,
                    "requested_by": request.actor_id,
                    **dict(request.metadata),
                },
                immediate=request.immediate,
            )
            result = self._escalation_to_dict(case)
        self._escalations_created += 1
        await self._publish(
            "human_control.routing.escalation.opened",
            result["workspace_id"],
            result["id"],
            {"escalation": result, "actor_id": request.actor_id},
        )
        if request.immediate:
            await self.scan_escalations(actor_id=request.actor_id, limit=1, case_ids={result["id"]})
        return self.get_escalation(result["id"]) or result

    async def _open_from_event(
        self, event: Event, *, trigger_type: str
    ) -> dict[str, Any]:
        notification_payload = event.payload.get("notification")
        if not isinstance(notification_payload, dict):
            notification_payload = {}
        notification_id = notification_payload.get("id")
        with self._session_factory() as session:
            notification = (
                session.get(HumanControlNotificationModel, notification_id)
                if notification_id
                else None
            )
            workspace_id = (
                notification.workspace_id
                if notification is not None
                else event.workspace_id
            )
            event_type = (
                notification.event_type
                if notification is not None
                else str(event.payload.get("event_type") or event_type_from_payload(event))
            )
            source_type = (
                notification.source_type
                if notification is not None
                else str(event.payload.get("source_type") or "human_control")
            )
            severity = (
                notification.severity
                if notification is not None
                else str(event.payload.get("severity") or "critical")
            )
            priority = (
                notification.priority
                if notification is not None
                else int(event.payload.get("priority") or 100)
            )
            rule = self._matching_escalation_rule(
                session,
                workspace_id=workspace_id,
                event_type=event_type,
                source_type=source_type,
                severity=severity,
                priority=priority,
                trigger_type=trigger_type,
            )
            if rule is None:
                return {"created": 0, "reason": "no_matching_escalation_rule"}
            idempotency_key = (
                f"event:{event.id}:rule:{rule.id}:trigger:{trigger_type}"
            )[:255]
            existing = session.scalar(
                select(HumanControlNotificationEscalationModel).where(
                    HumanControlNotificationEscalationModel.idempotency_key
                    == idempotency_key
                )
            )
            if existing is not None:
                return {"created": 0, "escalation": self._escalation_to_dict(existing)}
            case = self._create_escalation_case(
                session,
                notification=notification,
                rule=rule,
                trigger_type=trigger_type,
                idempotency_key=idempotency_key,
                metadata={
                    "event": event.to_dict(),
                    "notification": notification_payload,
                },
                immediate=rule.initial_delay_seconds == 0,
            )
            result = self._escalation_to_dict(case)
        self._escalations_created += 1
        await self._publish(
            "human_control.routing.escalation.opened",
            result["workspace_id"],
            result["id"],
            {"escalation": result, "trigger_event_id": event.id},
        )
        if ensure_utc(case.next_escalation_at) and ensure_utc(case.next_escalation_at) <= utc_now():
            await self.scan_escalations(limit=1, case_ids={result["id"]})
        return {"created": 1, "escalation": self.get_escalation(result["id"])}

    async def scan_escalations(
        self,
        *,
        actor_id: str = "system",
        limit: int = 100,
        case_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        async with self._scan_lock:
            now = utc_now()
            with self._session_factory() as session:
                statement = (
                    select(HumanControlNotificationEscalationModel.id)
                    .where(
                        HumanControlNotificationEscalationModel.status == "open",
                        HumanControlNotificationEscalationModel.next_escalation_at.is_not(None),
                        HumanControlNotificationEscalationModel.next_escalation_at <= now,
                    )
                    .order_by(
                        HumanControlNotificationEscalationModel.next_escalation_at,
                        HumanControlNotificationEscalationModel.created_at,
                    )
                    .limit(max(1, min(500, limit)))
                )
                if case_ids:
                    statement = statement.where(
                        HumanControlNotificationEscalationModel.id.in_(case_ids)
                    )
                ids = list(session.scalars(statement).all())
            routed = no_candidate = exhausted = failed = 0
            results = []
            for case_id in ids:
                try:
                    result = await self._execute_escalation(case_id, actor_id=actor_id)
                except Exception as exc:
                    logger.exception("Escalation execution failed id=%s", case_id)
                    result = {"case_id": case_id, "status": "failed", "error": str(exc)}
                results.append(result)
                status = result.get("status")
                routed += int(status == "routed")
                no_candidate += int(status == "no_candidate")
                exhausted += int(status == "exhausted")
                failed += int(status == "failed")
            return {
                "scanned": len(ids),
                "routed": routed,
                "no_candidate": no_candidate,
                "exhausted": exhausted,
                "failed": failed,
                "results": results,
                "scanned_at": iso(now),
            }

    async def _execute_escalation(
        self, case_id: str, *, actor_id: str
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            case = self._require(
                session,
                HumanControlNotificationEscalationModel,
                case_id,
                "Эскалация",
            )
            if case.status != "open":
                return {"case_id": case_id, "status": case.status}
            rule = (
                session.get(HumanControlNotificationEscalationRuleModel, case.rule_id)
                if case.rule_id
                else None
            )
            if rule is None or not rule.enabled:
                case.status = "exhausted"
                case.last_error = "Escalation rule is unavailable."
                return {"case_id": case_id, "status": "exhausted"}
            if case.escalation_count >= rule.max_escalations:
                case.status = "exhausted"
                case.next_escalation_at = None
                case.last_error = "Maximum escalation count reached."
                return {"case_id": case_id, "status": "exhausted"}
            original = (
                session.get(HumanControlNotificationModel, case.original_notification_id)
                if case.original_notification_id
                else None
            )
            schedule = (
                session.get(HumanControlOnCallScheduleModel, rule.target_schedule_id)
                if rule.target_schedule_id
                else None
            )
            candidates = self._candidate_rows(
                session,
                workspace_id=case.workspace_id,
                schedule=schedule,
                role_keys=list(rule.target_role_keys_json or []),
                explicit_actor_ids=list(rule.target_actor_ids_json or []),
                base_recipients=(
                    [original.recipient_actor_id] if original is not None else []
                ),
                heartbeat_ttl_seconds=300,
                min_capacity_percent=1,
            )
            eligible = [row for row in candidates if row["eligible"]]
            targets = self._select_candidates(
                eligible,
                strategy=rule.strategy,
                max_recipients=(100 if rule.strategy == "broadcast" else 1),
                last_selected_actor_id=None,
            )
            sequence = case.escalation_count + 1
            attempt = HumanControlNotificationEscalationAttemptModel(
                escalation_id=case.id,
                sequence=sequence,
                status="started",
                target_actor_ids_json=list(targets),
                notification_ids_json=[],
                reason="",
                metadata_json={"actor_id": actor_id, "candidates": candidates},
            )
            session.add(attempt)
            session.flush()
            channel_id = rule.channel_id or (original.channel_id if original else None)
            if channel_id is None:
                channel_id = session.scalar(
                    select(HumanControlNotificationChannelModel.id)
                    .where(
                        HumanControlNotificationChannelModel.enabled.is_(True),
                        HumanControlNotificationChannelModel.channel_type == "in_app",
                    )
                    .order_by(HumanControlNotificationChannelModel.created_at)
                )
            source = {
                "channel_id": channel_id,
                "title": original.title if original else "Эскалация уведомления",
                "body": original.body if original else str(case.metadata_json.get("reason") or "Требуется вмешательство оператора."),
                "severity": original.severity if original else "critical",
                "priority": original.priority if original else 100,
                "source_type": original.source_type if original else "notification_escalation",
                "source_id": original.source_id if original else case.id,
                "event_type": original.event_type if original else "human_control.notification.unroutable",
            }
            if not targets or channel_id is None:
                attempt.status = "no_candidate"
                attempt.reason = "No available target or delivery channel."
                case.escalation_count = sequence
                case.last_escalated_at = utc_now()
                case.last_error = attempt.reason
                if sequence >= rule.max_escalations:
                    case.status = "exhausted"
                    case.next_escalation_at = None
                else:
                    case.next_escalation_at = utc_now() + timedelta(
                        seconds=rule.repeat_interval_seconds
                    )
                result = {"case_id": case_id, "status": "no_candidate", "sequence": sequence}
                return result
            workspace_id = case.workspace_id
            attempt_id = attempt.id
            case_snapshot = self._escalation_to_dict(case)
            rule_snapshot = self._escalation_rule_to_dict(rule)

        notification_ids: list[str] = []
        errors: list[str] = []
        if self._notification_service is None:
            errors.append("Notification service is unavailable.")
        else:
            for target in targets:
                try:
                    created = await self._notification_service.create_manual(
                        HumanControlNotificationManualCreate(
                            workspace_id=workspace_id,
                            channel_id=source["channel_id"],
                            recipient_actor_id=target,
                            severity=source["severity"],
                            priority=min(
                                100,
                                int(source["priority"])
                                + int(rule_snapshot["priority_increment"]),
                            ),
                            title=f"[Эскалация {sequence}] {source['title']}",
                            body=(
                                f"Эскалация уведомления {case_snapshot.get('original_notification_id') or case_id}.\n\n"
                                f"{source['body']}"
                            ),
                            ack_required=bool(rule_snapshot["ack_required"]),
                            ack_timeout_seconds=int(
                                rule_snapshot["ack_timeout_seconds"]
                            ),
                            idempotency_key=(
                                f"escalation:{case_id}:sequence:{sequence}:recipient:{target}"
                            )[:255],
                            created_by=actor_id,
                            payload={
                                "escalation_id": case_id,
                                "original_notification_id": case_snapshot.get(
                                    "original_notification_id"
                                ),
                                "rule_id": rule_snapshot["id"],
                                "sequence": sequence,
                                "source_event_type": source["event_type"],
                                "source_type": source["source_type"],
                                "source_id": source["source_id"],
                            },
                        )
                    )
                    notification_ids.append(created["id"])
                except Exception as exc:
                    errors.append(f"{target}: {exc}")

        with self._session_factory() as session:
            case = self._require(session, HumanControlNotificationEscalationModel, case_id, "Эскалация")
            rule = self._require(session, HumanControlNotificationEscalationRuleModel, case.rule_id, "Правило эскалации")
            attempt = self._require(session, HumanControlNotificationEscalationAttemptModel, attempt_id, "Попытка эскалации")
            attempt.notification_ids_json = list(notification_ids)
            case.escalation_count = sequence
            case.last_escalated_at = utc_now()
            if notification_ids:
                attempt.status = "routed"
                attempt.reason = "Escalation notifications created."
                case.spawned_notification_ids_json = list(
                    dict.fromkeys(
                        list(case.spawned_notification_ids_json or []) + notification_ids
                    )
                )
                case.next_escalation_at = utc_now() + timedelta(
                    seconds=rule.repeat_interval_seconds
                )
                case.last_error = "; ".join(errors)
                status = "routed"
            else:
                attempt.status = "failed"
                attempt.reason = "; ".join(errors) or "No escalation notification was created."
                case.last_error = attempt.reason
                if sequence >= rule.max_escalations:
                    case.status = "exhausted"
                    case.next_escalation_at = None
                else:
                    case.next_escalation_at = utc_now() + timedelta(
                        seconds=rule.repeat_interval_seconds
                    )
                status = "failed"
            result = self._escalation_to_dict(case)
        if notification_ids:
            self._escalations_routed += 1
            await self._publish(
                "human_control.routing.escalation.routed",
                workspace_id,
                case_id,
                {
                    "escalation": result,
                    "sequence": sequence,
                    "targets": targets,
                    "notification_ids": notification_ids,
                },
            )
        return {
            "case_id": case_id,
            "status": status,
            "sequence": sequence,
            "targets": targets,
            "notification_ids": notification_ids,
            "errors": errors,
        }

    async def resolve_escalation(
        self,
        escalation_id: str,
        request: HumanControlEscalationResolveRequest,
        *,
        cancel: bool = False,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require(session, HumanControlNotificationEscalationModel, escalation_id, "Эскалация")
            if row.status != "open" and not request.force:
                raise HumanControlConflict("Эскалация уже завершена.")
            row.status = "cancelled" if cancel else "resolved"
            row.resolved_by = request.actor_id
            row.resolved_at = utc_now()
            row.resolution = request.resolution
            row.next_escalation_at = None
            result = self._escalation_to_dict(row)
        await self._publish(
            "human_control.routing.escalation.cancelled" if cancel else "human_control.routing.escalation.resolved",
            result["workspace_id"],
            escalation_id,
            {"escalation": result, "actor_id": request.actor_id},
        )
        return result

    def list_escalations(
        self,
        *,
        workspace_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        session: Session | None = None,
    ) -> list[dict[str, Any]]:
        def execute(db: Session) -> list[dict[str, Any]]:
            statement = select(HumanControlNotificationEscalationModel)
            if workspace_id is not None:
                statement = statement.where(
                    HumanControlNotificationEscalationModel.workspace_id == workspace_id
                )
            if status is not None:
                statement = statement.where(
                    HumanControlNotificationEscalationModel.status == status
                )
            statement = statement.order_by(
                HumanControlNotificationEscalationModel.created_at.desc()
            ).limit(max(1, min(500, limit)))
            return [self._escalation_to_dict(row) for row in db.scalars(statement).all()]
        if session is not None:
            return execute(session)
        with self._session_factory() as db:
            return execute(db)

    def get_escalation(self, escalation_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(HumanControlNotificationEscalationModel, escalation_id)
            return None if row is None else self._escalation_to_dict(row)

    def list_escalation_attempts(
        self, *, escalation_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlNotificationEscalationAttemptModel)
            if escalation_id is not None:
                statement = statement.where(
                    HumanControlNotificationEscalationAttemptModel.escalation_id
                    == escalation_id
                )
            statement = statement.order_by(
                HumanControlNotificationEscalationAttemptModel.created_at.desc()
            ).limit(max(1, min(500, limit)))
            return [self._attempt_to_dict(row) for row in session.scalars(statement).all()]

    async def reconcile(self, *, actor_id: str = "system") -> dict[str, Any]:
        now = utc_now()
        expired_availability = 0
        exhausted = 0
        with self._session_factory() as session:
            availability_rows = list(
                session.scalars(
                    select(HumanControlOperatorAvailabilityModel).where(
                        HumanControlOperatorAvailabilityModel.available_until.is_not(None),
                        HumanControlOperatorAvailabilityModel.available_until <= now,
                        HumanControlOperatorAvailabilityModel.status.in_(["available", "busy"]),
                    )
                ).all()
            )
            for row in availability_rows:
                row.status = "unknown"
                row.note = "Availability window expired."
                row.updated_by = actor_id
                expired_availability += 1
            cases = list(
                session.scalars(
                    select(HumanControlNotificationEscalationModel).where(
                        HumanControlNotificationEscalationModel.status == "open"
                    )
                ).all()
            )
            for case in cases:
                rule = (
                    session.get(HumanControlNotificationEscalationRuleModel, case.rule_id)
                    if case.rule_id
                    else None
                )
                if rule is None or case.escalation_count >= rule.max_escalations and case.next_escalation_at and ensure_utc(case.next_escalation_at) <= now:
                    case.status = "exhausted"
                    case.next_escalation_at = None
                    exhausted += 1
        if expired_availability or exhausted:
            await self._publish(
                "human_control.routing.reconciled",
                None,
                actor_id,
                {
                    "expired_availability": expired_availability,
                    "exhausted": exhausted,
                    "actor_id": actor_id,
                },
            )
        return {
            "expired_availability": expired_availability,
            "exhausted": exhausted,
            "reconciled_at": iso(now),
        }

    # ------------------------------------------------------------------
    # Internal matching and availability helpers
    # ------------------------------------------------------------------
    def _matching_routing_rule(
        self,
        session: Session,
        *,
        workspace_id: str | None,
        event_type: str,
        context: dict[str, Any],
    ) -> HumanControlNotificationRoutingRuleModel | None:
        statement = select(HumanControlNotificationRoutingRuleModel).where(
            HumanControlNotificationRoutingRuleModel.enabled.is_(True)
        )
        if workspace_id is not None:
            statement = statement.where(
                or_(
                    HumanControlNotificationRoutingRuleModel.workspace_id
                    == workspace_id,
                    HumanControlNotificationRoutingRuleModel.workspace_id.is_(None),
                )
            )
        rows = list(
            session.scalars(
                statement.order_by(
                    HumanControlNotificationRoutingRuleModel.workspace_id.is_(None),
                    HumanControlNotificationRoutingRuleModel.rule_priority.desc(),
                    HumanControlNotificationRoutingRuleModel.created_at,
                )
            ).all()
        )
        for row in rows:
            if not self._matches(
                event_type=event_type,
                source_type=str(context.get("source_type") or "human_control"),
                severity=str(context.get("severity") or "medium"),
                priority=int(context.get("priority") or 0),
                patterns=list(row.event_patterns_json or []),
                source_types=list(row.source_types_json or []),
                risk_levels=list(row.risk_levels_json or []),
                min_priority=row.min_priority,
            ):
                continue
            return row
        return None

    def _matching_escalation_rule(
        self,
        session: Session,
        *,
        workspace_id: str | None,
        event_type: str,
        source_type: str | None,
        severity: str,
        priority: int,
        trigger_type: str,
    ) -> HumanControlNotificationEscalationRuleModel | None:
        statement = select(HumanControlNotificationEscalationRuleModel).where(
            HumanControlNotificationEscalationRuleModel.enabled.is_(True)
        )
        if workspace_id is not None:
            statement = statement.where(
                or_(
                    HumanControlNotificationEscalationRuleModel.workspace_id
                    == workspace_id,
                    HumanControlNotificationEscalationRuleModel.workspace_id.is_(None),
                )
            )
        rows = list(
            session.scalars(
                statement.order_by(
                    HumanControlNotificationEscalationRuleModel.workspace_id.is_(None),
                    HumanControlNotificationEscalationRuleModel.rule_priority.desc(),
                    HumanControlNotificationEscalationRuleModel.created_at,
                )
            ).all()
        )
        for row in rows:
            if trigger_type not in set(row.trigger_on_json or []):
                continue
            if self._matches(
                event_type=event_type,
                source_type=str(source_type or "human_control"),
                severity=severity,
                priority=priority,
                patterns=list(row.event_patterns_json or []),
                source_types=list(row.source_types_json or []),
                risk_levels=list(row.risk_levels_json or []),
                min_priority=row.min_priority,
            ):
                return row
        return None

    @staticmethod
    def _matches(
        *,
        event_type: str,
        source_type: str,
        severity: str,
        priority: int,
        patterns: list[str],
        source_types: list[str],
        risk_levels: list[str],
        min_priority: int,
    ) -> bool:
        if patterns and not any(
            fnmatch.fnmatchcase(event_type, pattern) for pattern in patterns
        ):
            return False
        if source_types and source_type not in set(source_types):
            return False
        if risk_levels and severity not in set(risk_levels):
            return False
        return priority >= min_priority

    def _candidate_rows(
        self,
        session: Session,
        *,
        workspace_id: str | None,
        schedule: HumanControlOnCallScheduleModel | None,
        role_keys: list[str],
        explicit_actor_ids: list[str],
        base_recipients: list[str],
        heartbeat_ttl_seconds: int,
        min_capacity_percent: int,
    ) -> list[dict[str, Any]]:
        now = utc_now()
        members: dict[str, HumanControlOnCallMemberModel] = {}
        schedule_active = schedule is not None and schedule.enabled
        if schedule_active:
            for member in session.scalars(
                select(HumanControlOnCallMemberModel).where(
                    HumanControlOnCallMemberModel.schedule_id == schedule.id,
                    HumanControlOnCallMemberModel.enabled.is_(True),
                )
            ).all():
                if self._member_is_active(member, schedule=schedule, now=now):
                    members[member.actor_id] = member
            role_keys = list(dict.fromkeys(role_keys + list(schedule.fallback_role_keys_json or [])))
        actor_ids = set(explicit_actor_ids) | set(base_recipients) | set(members)
        actor_ids.update(
            self._role_actor_ids(
                session, workspace_id=workspace_id, role_keys=role_keys
            )
        )
        candidates: list[dict[str, Any]] = []
        for actor_id in sorted(actor_ids):
            member = members.get(actor_id)
            availability = self._effective_availability(
                session,
                workspace_id=workspace_id,
                actor_id=actor_id,
                schedule_active=member is not None,
                heartbeat_ttl_seconds=heartbeat_ttl_seconds,
            )
            active_load = self._active_notification_load(session, actor_id)
            load_limit = (
                availability.get("active_notification_limit")
                or (member.max_active_notifications if member else 0)
            )
            load_ok = not load_limit or active_load < int(load_limit)
            eligible = (
                availability["status"] in {"available", "busy"}
                and int(availability["capacity_percent"]) >= min_capacity_percent
                and load_ok
            )
            priority = member.priority if member else 0
            backup = bool(member.is_backup) if member else False
            score = (
                priority
                + int(availability["capacity_percent"])
                - active_load * 5
                - (50 if backup else 0)
                + (10 if availability["status"] == "available" else 0)
            )
            candidates.append(
                {
                    "actor_id": actor_id,
                    "eligible": eligible,
                    "status": availability["status"],
                    "capacity_percent": availability["capacity_percent"],
                    "source": availability["source"],
                    "active_notifications": active_load,
                    "active_notification_limit": int(load_limit or 0),
                    "on_call": member is not None,
                    "backup": backup,
                    "priority": priority,
                    "score": score,
                }
            )
        return sorted(
            candidates,
            key=lambda row: (
                not row["eligible"],
                row["backup"],
                -row["score"],
                row["actor_id"],
            ),
        )

    @staticmethod
    def _select_candidates(
        candidates: list[dict[str, Any]],
        *,
        strategy: str,
        max_recipients: int,
        last_selected_actor_id: str | None,
    ) -> list[str]:
        if not candidates:
            return []
        actors = [row["actor_id"] for row in candidates]
        if strategy == "broadcast":
            return actors[:max_recipients]
        if strategy == "round_robin":
            ordered = sorted(actors)
            if last_selected_actor_id in ordered:
                index = (ordered.index(last_selected_actor_id) + 1) % len(ordered)
                ordered = ordered[index:] + ordered[:index]
            return ordered[:max_recipients]
        if strategy == "primary_backup":
            primary = [row["actor_id"] for row in candidates if not row["backup"]]
            backup = [row["actor_id"] for row in candidates if row["backup"]]
            return (primary or backup)[:max_recipients]
        return actors[:max_recipients]

    def _effective_availability(
        self,
        session: Session,
        *,
        workspace_id: str | None,
        actor_id: str,
        schedule_active: bool,
        heartbeat_ttl_seconds: int,
    ) -> dict[str, Any]:
        statement = select(HumanControlOperatorAvailabilityModel).where(
            HumanControlOperatorAvailabilityModel.actor_id == actor_id
        )
        if workspace_id is not None:
            statement = statement.where(
                or_(
                    HumanControlOperatorAvailabilityModel.workspace_id == workspace_id,
                    HumanControlOperatorAvailabilityModel.workspace_id.is_(None),
                )
            ).order_by(HumanControlOperatorAvailabilityModel.workspace_id.is_(None))
        row = session.scalar(statement)
        now = utc_now()
        if row is None:
            return {
                "status": "available" if schedule_active else "unknown",
                "capacity_percent": 100 if schedule_active else 0,
                "source": "schedule" if schedule_active else "unknown",
                "active_notification_limit": 0,
            }
        available_until = ensure_utc(row.available_until)
        if available_until is not None and available_until <= now:
            status = "unknown"
        else:
            status = row.status
        if row.source == "heartbeat":
            last_seen = ensure_utc(row.last_seen_at)
            if last_seen is None or last_seen <= now - timedelta(
                seconds=heartbeat_ttl_seconds
            ):
                status = "unknown"
        return {
            "status": status,
            "capacity_percent": row.capacity_percent,
            "source": row.source,
            "active_notification_limit": row.active_notification_limit,
        }

    @staticmethod
    def _member_is_active(
        member: HumanControlOnCallMemberModel,
        *,
        schedule: HumanControlOnCallScheduleModel,
        now: datetime,
    ) -> bool:
        valid_from = ensure_utc(member.valid_from)
        valid_until = ensure_utc(member.valid_until)
        if valid_from is not None and now < valid_from:
            return False
        if valid_until is not None and now >= valid_until:
            return False
        local_now = now.astimezone(HumanControlRoutingService._zone(schedule.timezone))
        weekdays = set(member.weekdays_json or [])
        if weekdays and local_now.weekday() not in weekdays:
            return False
        if member.start_time is None or member.end_time is None:
            return True
        start = HumanControlRoutingService._parse_time(member.start_time)
        end = HumanControlRoutingService._parse_time(member.end_time)
        current = local_now.timetz().replace(tzinfo=None)
        if start <= end:
            return start <= current < end
        return current >= start or current < end

    @staticmethod
    def _active_notification_load(session: Session, actor_id: str) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(HumanControlNotificationModel)
                .where(
                    HumanControlNotificationModel.recipient_actor_id == actor_id,
                    HumanControlNotificationModel.status.in_(
                        ["pending", "delivering", "retrying", "delivered"]
                    ),
                    HumanControlNotificationModel.ack_status.in_(
                        ["not_required", "pending", "overdue"]
                    ),
                )
            )
            or 0
        )

    @staticmethod
    def _role_actor_ids(
        session: Session,
        *,
        workspace_id: str | None,
        role_keys: list[str],
    ) -> list[str]:
        if not role_keys:
            return []
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
                HumanControlRoleModel.role_key.in_(set(role_keys)),
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
        return sorted(set(str(value) for value in session.scalars(statement).all()))

    def _create_escalation_case(
        self,
        session: Session,
        *,
        notification: HumanControlNotificationModel | None,
        rule: HumanControlNotificationEscalationRuleModel,
        trigger_type: str,
        idempotency_key: str,
        metadata: dict[str, Any],
        immediate: bool,
    ) -> HumanControlNotificationEscalationModel:
        existing = session.scalar(
            select(HumanControlNotificationEscalationModel).where(
                HumanControlNotificationEscalationModel.idempotency_key
                == idempotency_key
            )
        )
        if existing is not None:
            return existing
        delay = 0 if immediate else rule.initial_delay_seconds
        row = HumanControlNotificationEscalationModel(
            workspace_id=(notification.workspace_id if notification else rule.workspace_id),
            original_notification_id=(notification.id if notification else None),
            rule_id=rule.id,
            idempotency_key=idempotency_key,
            status="open",
            trigger_type=trigger_type,
            escalation_count=0,
            next_escalation_at=utc_now() + timedelta(seconds=delay),
            spawned_notification_ids_json=[],
            metadata_json=dict(metadata),
        )
        session.add(row)
        session.flush()
        return row

    async def _resolve_by_ack(
        self, *, notification_id: str | None, actor_id: str
    ) -> dict[str, Any]:
        if not notification_id:
            return {"resolved": 0}
        resolved_rows = []
        with self._session_factory() as session:
            cases = list(
                session.scalars(
                    select(HumanControlNotificationEscalationModel).where(
                        HumanControlNotificationEscalationModel.status == "open"
                    )
                ).all()
            )
            for case in cases:
                if (
                    case.original_notification_id != notification_id
                    and notification_id not in set(case.spawned_notification_ids_json or [])
                ):
                    continue
                rule = (
                    session.get(HumanControlNotificationEscalationRuleModel, case.rule_id)
                    if case.rule_id
                    else None
                )
                if rule is not None and not rule.auto_resolve_on_ack:
                    continue
                case.status = "resolved"
                case.resolved_by = actor_id
                case.resolved_at = utc_now()
                case.resolution = f"Acknowledged notification {notification_id}."
                case.next_escalation_at = None
                resolved_rows.append(self._escalation_to_dict(case))
        for row in resolved_rows:
            await self._publish(
                "human_control.routing.escalation.resolved",
                row["workspace_id"],
                row["id"],
                {"escalation": row, "actor_id": actor_id, "notification_id": notification_id},
            )
        return {"resolved": len(resolved_rows), "escalations": resolved_rows}

    def _validate_optional_schedule(
        self,
        session: Session,
        schedule_id: str | None,
        workspace_id: str | None,
    ) -> None:
        if schedule_id is None:
            return
        schedule = session.get(HumanControlOnCallScheduleModel, schedule_id)
        if schedule is None:
            raise HumanControlNotFound("On-call расписание не найдено.")
        if schedule.workspace_id is not None and schedule.workspace_id != workspace_id:
            raise HumanControlConflict("On-call расписание принадлежит другому Workspace.")

    @staticmethod
    def _validate_timezone(name: str) -> None:
        HumanControlRoutingService._zone(name, strict=True)

    @staticmethod
    def _zone(name: str, *, strict: bool = False) -> tzinfo:
        normalized = (name or "UTC").strip()
        if normalized.upper() in {"UTC", "GMT", "Z"} or normalized in {
            "Etc/UTC",
            "Etc/GMT",
        }:
            return timezone.utc
        try:
            return ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            if strict:
                raise HumanControlError(
                    f"Неизвестный часовой пояс IANA: {normalized}. "
                    "На Windows установите пакет tzdata."
                ) from exc
            return timezone.utc

    @staticmethod
    def _parse_time(value: str) -> time:
        hour, minute = value.split(":", 1)
        return time(hour=int(hour), minute=int(minute))

    @staticmethod
    def _scope_key(workspace_id: str | None, key: str) -> str:
        return f"workspace:{workspace_id}:{key}" if workspace_id else f"global:{key}"

    @staticmethod
    def _workspace_actor_key(workspace_id: str | None, actor_id: str) -> str:
        return f"workspace:{workspace_id}:actor:{actor_id}" if workspace_id else f"global:actor:{actor_id}"

    @staticmethod
    def _count(session: Session, model: Any) -> int:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)

    @staticmethod
    def _require(session: Session, model: Any, row_id: str, label: str) -> Any:
        row = session.get(model, row_id)
        if row is None:
            raise HumanControlNotFound(f"{label} не найдено.")
        return row

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
                source="human_control.routing",
                workspace_id=workspace_id,
                correlation_id=correlation_id,
                payload=payload,
            )
        )

    @staticmethod
    def _schedule_to_dict(row: HumanControlOnCallScheduleModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope_key": row.scope_key,
            "workspace_id": row.workspace_id,
            "schedule_key": row.schedule_key,
            "name": row.name,
            "description": row.description,
            "timezone": row.timezone,
            "enabled": row.enabled,
            "routing_strategy": row.routing_strategy,
            "fallback_role_keys": list(row.fallback_role_keys_json or []),
            "metadata": dict(row.metadata_json or {}),
            "created_by": row.created_by,
            "updated_by": row.updated_by,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _member_to_dict(row: HumanControlOnCallMemberModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "schedule_id": row.schedule_id,
            "actor_id": row.actor_id,
            "role_key": row.role_key,
            "priority": row.priority,
            "is_backup": row.is_backup,
            "weekdays": list(row.weekdays_json or []),
            "start_time": row.start_time,
            "end_time": row.end_time,
            "valid_from": iso(row.valid_from),
            "valid_until": iso(row.valid_until),
            "max_active_notifications": row.max_active_notifications,
            "enabled": row.enabled,
            "metadata": dict(row.metadata_json or {}),
            "created_by": row.created_by,
            "updated_by": row.updated_by,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _availability_to_dict(
        row: HumanControlOperatorAvailabilityModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "actor_id": row.actor_id,
            "status": row.status,
            "capacity_percent": row.capacity_percent,
            "active_notification_limit": row.active_notification_limit,
            "source": row.source,
            "available_until": iso(row.available_until),
            "last_seen_at": iso(row.last_seen_at),
            "note": row.note,
            "metadata": dict(row.metadata_json or {}),
            "updated_by": row.updated_by,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _routing_rule_to_dict(
        row: HumanControlNotificationRoutingRuleModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope_key": row.scope_key,
            "workspace_id": row.workspace_id,
            "rule_key": row.rule_key,
            "name": row.name,
            "enabled": row.enabled,
            "rule_priority": row.rule_priority,
            "event_patterns": list(row.event_patterns_json or []),
            "source_types": list(row.source_types_json or []),
            "risk_levels": list(row.risk_levels_json or []),
            "min_priority": row.min_priority,
            "schedule_id": row.schedule_id,
            "role_keys": list(row.role_keys_json or []),
            "fallback_actor_ids": list(row.fallback_actor_ids_json or []),
            "strategy": row.strategy,
            "availability_required": row.availability_required,
            "min_capacity_percent": row.min_capacity_percent,
            "heartbeat_ttl_seconds": row.heartbeat_ttl_seconds,
            "max_recipients": row.max_recipients,
            "fallback_mode": row.fallback_mode,
            "ack_required": row.ack_required,
            "ack_timeout_seconds": row.ack_timeout_seconds,
            "last_selected_actor_id": row.last_selected_actor_id,
            "metadata": dict(row.metadata_json or {}),
            "created_by": row.created_by,
            "updated_by": row.updated_by,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _escalation_rule_to_dict(
        row: HumanControlNotificationEscalationRuleModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope_key": row.scope_key,
            "workspace_id": row.workspace_id,
            "rule_key": row.rule_key,
            "name": row.name,
            "enabled": row.enabled,
            "rule_priority": row.rule_priority,
            "event_patterns": list(row.event_patterns_json or []),
            "source_types": list(row.source_types_json or []),
            "risk_levels": list(row.risk_levels_json or []),
            "min_priority": row.min_priority,
            "trigger_on": list(row.trigger_on_json or []),
            "initial_delay_seconds": row.initial_delay_seconds,
            "repeat_interval_seconds": row.repeat_interval_seconds,
            "max_escalations": row.max_escalations,
            "target_schedule_id": row.target_schedule_id,
            "target_role_keys": list(row.target_role_keys_json or []),
            "target_actor_ids": list(row.target_actor_ids_json or []),
            "channel_id": row.channel_id,
            "strategy": row.strategy,
            "priority_increment": row.priority_increment,
            "ack_required": row.ack_required,
            "ack_timeout_seconds": row.ack_timeout_seconds,
            "auto_resolve_on_ack": row.auto_resolve_on_ack,
            "metadata": dict(row.metadata_json or {}),
            "created_by": row.created_by,
            "updated_by": row.updated_by,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _escalation_to_dict(
        row: HumanControlNotificationEscalationModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "original_notification_id": row.original_notification_id,
            "rule_id": row.rule_id,
            "idempotency_key": row.idempotency_key,
            "status": row.status,
            "trigger_type": row.trigger_type,
            "escalation_count": row.escalation_count,
            "next_escalation_at": iso(row.next_escalation_at),
            "last_escalated_at": iso(row.last_escalated_at),
            "spawned_notification_ids": list(row.spawned_notification_ids_json or []),
            "resolved_by": row.resolved_by,
            "resolved_at": iso(row.resolved_at),
            "resolution": row.resolution,
            "last_error": row.last_error,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _attempt_to_dict(
        row: HumanControlNotificationEscalationAttemptModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "escalation_id": row.escalation_id,
            "sequence": row.sequence,
            "status": row.status,
            "target_actor_ids": list(row.target_actor_ids_json or []),
            "notification_ids": list(row.notification_ids_json or []),
            "reason": row.reason,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
        }


def event_type_from_payload(event: Event) -> str:
    value = event.payload.get("routing_context")
    if isinstance(value, dict) and value.get("event_type"):
        return str(value["event_type"])
    return event.event_type
