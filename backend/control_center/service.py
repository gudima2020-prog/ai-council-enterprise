from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from backend.autonomy.governance_schemas import (
    MissionCheckpointDecision,
    MissionCheckpointDecisionRequest,
)
from backend.autonomy.learning_schemas import MissionLearningRunDecisionRequest
from backend.autonomy.models import (
    MissionDecisionCheckpointModel,
    MissionLearningRunModel,
    MissionResourceAllocationModel,
    WorkspaceResourceReservationModel,
)
from backend.autonomy.resources_schemas import (
    MissionResourceAllocationApprove,
    MissionResourceAllocationCancel,
)
from backend.autonomy.workspace_resources_schemas import (
    WorkspaceResourceReservationDecision,
)
from backend.control_center.models import (
    HumanControlActionModel,
    HumanControlItemModel,
)
from backend.control_center.schemas import (
    HumanControlBulkDecisionRequest,
    HumanControlClaimRequest,
    HumanControlDecisionAction,
    HumanControlDecisionRequest,
    HumanControlItemStatus,
    HumanControlReleaseRequest,
    HumanControlRiskLevel,
    HumanControlSnoozeRequest,
    HumanControlSourceType,
)
from backend.core.events import Event, EventBus
from backend.database.session import session_scope
from backend.task_engine.approval_schemas import (
    ApprovalDecision,
    ApprovalDecisionRequest,
)
from backend.task_engine.models import TaskApprovalModel


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


class HumanControlError(ValueError):
    pass


class HumanControlNotFound(HumanControlError):
    pass


class HumanControlConflict(HumanControlError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    aware = ensure_utc(value)
    return aware.isoformat() if aware is not None else None


def parse_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return ensure_utc(value)
    if isinstance(value, str):
        try:
            return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def clamp_priority(value: Any, default: int = 50) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return default


def _risk(value: Any, default: str = "medium") -> str:
    normalized = str(value or default).strip().lower()
    valid = {item.value for item in HumanControlRiskLevel}
    return normalized if normalized in valid else default


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "value"):
        return _json_safe(value.value)
    return value


class HumanControlCenterService:
    """Aggregates human decisions from Task Engine and Mission services.

    The service does not create a second source of truth. Every inbox item
    points to an existing approval, checkpoint, allocation, reservation, or
    learning proposal. Decisions are routed to the owning service and the
    normalized inbox is reconciled afterwards.
    """

    ACTIVE_ITEM_STATUSES = {
        HumanControlItemStatus.PENDING.value,
        HumanControlItemStatus.CLAIMED.value,
        HumanControlItemStatus.SNOOZED.value,
    }
    TERMINAL_ITEM_STATUSES = {
        HumanControlItemStatus.RESOLVED.value,
        HumanControlItemStatus.EXPIRED.value,
        HumanControlItemStatus.SUPERSEDED.value,
    }

    def __init__(
        self,
        *,
        event_bus: EventBus,
        task_approval_manager: Any | None = None,
        mission_governance_service: Any | None = None,
        mission_resource_service: Any | None = None,
        workspace_resource_coordinator: Any | None = None,
        mission_learning_service: Any | None = None,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._event_bus = event_bus
        self._task_approval_manager = task_approval_manager
        self._mission_governance_service = mission_governance_service
        self._mission_resource_service = mission_resource_service
        self._workspace_resource_coordinator = workspace_resource_coordinator
        self._mission_learning_service = mission_learning_service
        self._session_factory = session_factory
        self._sync_count = 0
        self._decisions_routed = 0
        self._claims_created = 0

    def status(self) -> dict[str, Any]:
        with self._session_factory() as session:
            total = int(
                session.scalar(
                    select(func.count()).select_from(HumanControlItemModel)
                )
                or 0
            )
            active = int(
                session.scalar(
                    select(func.count())
                    .select_from(HumanControlItemModel)
                    .where(HumanControlItemModel.status.in_(self.ACTIVE_ITEM_STATUSES))
                )
                or 0
            )
            actions = int(
                session.scalar(
                    select(func.count()).select_from(HumanControlActionModel)
                )
                or 0
            )
        return {
            "running": True,
            "items": total,
            "active_items": active,
            "actions": actions,
            "sync_count": self._sync_count,
            "decisions_routed": self._decisions_routed,
            "claims_created": self._claims_created,
            "source_of_truth": "originating services",
            "supported_sources": [item.value for item in HumanControlSourceType],
            "capabilities": [
                "unified_operator_inbox",
                "workspace_and_risk_filtering",
                "claim_lease_and_snooze",
                "idempotent_decision_routing",
                "bulk_operator_decisions",
                "overdue_and_critical_dashboard",
                "immutable_operator_action_history",
                "audit_and_distributed_event_transport",
            ],
        }

    async def sync(
        self,
        *,
        workspace_id: str | None = None,
        actor_id: str = "system",
    ) -> dict[str, Any]:
        now = utc_now()
        created = 0
        updated = 0
        resolved = 0
        reopened = 0
        released_claims = 0
        awakened = 0

        with self._session_factory() as session:
            active_rows = list(
                session.scalars(
                    select(HumanControlItemModel).where(
                        HumanControlItemModel.status.in_(self.ACTIVE_ITEM_STATUSES),
                        *(
                            [HumanControlItemModel.workspace_id == workspace_id]
                            if workspace_id is not None
                            else []
                        ),
                    )
                ).all()
            )
            for row in active_rows:
                claim_expires = ensure_utc(row.claim_expires_at)
                if (
                    row.status == HumanControlItemStatus.CLAIMED.value
                    and claim_expires is not None
                    and claim_expires <= now
                ):
                    row.status = HumanControlItemStatus.PENDING.value
                    row.assigned_to = None
                    row.claimed_at = None
                    row.claim_expires_at = None
                    released_claims += 1
                snoozed_until = ensure_utc(row.snoozed_until)
                if (
                    row.status == HumanControlItemStatus.SNOOZED.value
                    and snoozed_until is not None
                    and snoozed_until <= now
                ):
                    row.status = HumanControlItemStatus.PENDING.value
                    row.snoozed_until = None
                    awakened += 1

            descriptors = self._collect_descriptors(
                session,
                workspace_id=workspace_id,
                now=now,
            )
            seen: set[tuple[str, str]] = set()
            for descriptor in descriptors:
                key = (descriptor["source_type"], descriptor["source_id"])
                seen.add(key)
                row = session.scalar(
                    select(HumanControlItemModel).where(
                        HumanControlItemModel.source_type == key[0],
                        HumanControlItemModel.source_id == key[1],
                    )
                )
                if row is None:
                    row = HumanControlItemModel(**descriptor)
                    session.add(row)
                    session.flush()
                    created += 1
                    continue

                was_terminal = row.status in self.TERMINAL_ITEM_STATUSES
                self._apply_descriptor(row, descriptor, now=now)
                if was_terminal and row.status in self.ACTIVE_ITEM_STATUSES:
                    reopened += 1
                updated += 1

            remaining = list(
                session.scalars(
                    select(HumanControlItemModel).where(
                        HumanControlItemModel.status.in_(self.ACTIVE_ITEM_STATUSES),
                        *(
                            [HumanControlItemModel.workspace_id == workspace_id]
                            if workspace_id is not None
                            else []
                        ),
                    )
                ).all()
            )
            for row in remaining:
                key = (row.source_type, row.source_id)
                if key in seen:
                    continue
                source_status = self._read_source_status(session, row)
                previous_status = row.status
                row.source_status = source_status or "missing"
                row.assigned_to = None
                row.claimed_at = None
                row.claim_expires_at = None
                row.snoozed_until = None
                row.resolved_at = row.resolved_at or now
                row.resolved_by = row.resolved_by or "system"
                if source_status in {"expired"}:
                    row.status = HumanControlItemStatus.EXPIRED.value
                    row.resolution = "expired"
                elif source_status is None:
                    row.status = HumanControlItemStatus.SUPERSEDED.value
                    row.resolution = "source_missing"
                else:
                    row.status = HumanControlItemStatus.RESOLVED.value
                    row.resolution = source_status
                row.resolution_note = (
                    "Source item is no longer actionable after reconciliation."
                )
                self._record_action(
                    session,
                    item=row,
                    action_type="source_resolved",
                    actor_id=actor_id,
                    previous_status=previous_status,
                    new_status=row.status,
                    reason=row.resolution_note,
                    result={"source_status": source_status},
                )
                resolved += 1

        self._sync_count += 1
        result = {
            "workspace_id": workspace_id,
            "created": created,
            "updated": updated,
            "resolved": resolved,
            "reopened": reopened,
            "released_claims": released_claims,
            "awakened": awakened,
            "synced_at": iso(now),
        }
        await self._publish(
            "human_control.synced",
            workspace_id=workspace_id,
            correlation_id=workspace_id,
            payload=result,
        )
        return result

    async def handle_event(self, event: Event) -> None:
        await self.sync(
            workspace_id=event.workspace_id,
            actor_id="event_bus",
        )

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(HumanControlItemModel, item_id)
            return None if row is None else self._item_to_dict(row)

    def list_items(
        self,
        *,
        workspace_id: str | None = None,
        status: str | None = None,
        source_type: str | None = None,
        risk_level: str | None = None,
        assigned_to: str | None = None,
        overdue_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        now = utc_now()
        with self._session_factory() as session:
            statement = select(HumanControlItemModel)
            if workspace_id is not None:
                statement = statement.where(
                    HumanControlItemModel.workspace_id == workspace_id
                )
            if status is not None:
                statement = statement.where(HumanControlItemModel.status == status)
            if source_type is not None:
                statement = statement.where(
                    HumanControlItemModel.source_type == source_type
                )
            if risk_level is not None:
                statement = statement.where(
                    HumanControlItemModel.risk_level == risk_level
                )
            if assigned_to is not None:
                statement = statement.where(
                    HumanControlItemModel.assigned_to == assigned_to
                )
            if overdue_only:
                statement = statement.where(
                    HumanControlItemModel.status.in_(self.ACTIVE_ITEM_STATUSES),
                    HumanControlItemModel.due_at.is_not(None),
                    HumanControlItemModel.due_at < now,
                )
            risk_order = case(
                (HumanControlItemModel.risk_level == "critical", 4),
                (HumanControlItemModel.risk_level == "high", 3),
                (HumanControlItemModel.risk_level == "medium", 2),
                else_=1,
            )
            rows = list(
                session.scalars(
                    statement.order_by(
                        risk_order.desc(),
                        HumanControlItemModel.priority.desc(),
                        HumanControlItemModel.created_at.asc(),
                    )
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._item_to_dict(row) for row in rows]

    def list_actions(
        self,
        *,
        workspace_id: str | None = None,
        item_id: str | None = None,
        actor_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlActionModel)
            if workspace_id is not None:
                statement = statement.where(
                    HumanControlActionModel.workspace_id == workspace_id
                )
            if item_id is not None:
                statement = statement.where(HumanControlActionModel.item_id == item_id)
            if actor_id is not None:
                statement = statement.where(HumanControlActionModel.actor_id == actor_id)
            rows = list(
                session.scalars(
                    statement.order_by(HumanControlActionModel.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._action_to_dict(row) for row in rows]

    async def dashboard(
        self,
        *,
        workspace_id: str | None = None,
        refresh: bool = True,
    ) -> dict[str, Any]:
        sync_result = None
        if refresh:
            sync_result = await self.sync(workspace_id=workspace_id)
        now = utc_now()
        with self._session_factory() as session:
            filters = []
            if workspace_id is not None:
                filters.append(HumanControlItemModel.workspace_id == workspace_id)
            rows = list(
                session.scalars(
                    select(HumanControlItemModel).where(*filters)
                ).all()
            )
            active = [row for row in rows if row.status in self.ACTIVE_ITEM_STATUSES]
            overdue = [
                row
                for row in active
                if ensure_utc(row.due_at) is not None
                and ensure_utc(row.due_at) < now
            ]
            oldest = min((row.created_at for row in active), default=None)
            source_counts = Counter(row.source_type for row in active)
            risk_counts = Counter(row.risk_level for row in active)
            status_counts = Counter(row.status for row in rows)
            top_rows = sorted(
                active,
                key=lambda row: (
                    {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(
                        row.risk_level,
                        4,
                    ),
                    -row.priority,
                    ensure_utc(row.due_at) or datetime.max.replace(tzinfo=timezone.utc),
                    ensure_utc(row.created_at) or now,
                ),
            )[:20]
            recent_actions = list(
                session.scalars(
                    select(HumanControlActionModel)
                    .where(
                        *(
                            [HumanControlActionModel.workspace_id == workspace_id]
                            if workspace_id is not None
                            else []
                        )
                    )
                    .order_by(HumanControlActionModel.created_at.desc())
                    .limit(20)
                ).all()
            )
        return {
            "workspace_id": workspace_id,
            "generated_at": iso(now),
            "sync": sync_result,
            "summary": {
                "total": len(rows),
                "active": len(active),
                "pending": status_counts.get("pending", 0),
                "claimed": status_counts.get("claimed", 0),
                "snoozed": status_counts.get("snoozed", 0),
                "resolved": status_counts.get("resolved", 0),
                "expired": status_counts.get("expired", 0),
                "critical": risk_counts.get("critical", 0),
                "overdue": len(overdue),
                "oldest_active_at": iso(oldest),
                "oldest_active_age_seconds": (
                    max(0, int((now - ensure_utc(oldest)).total_seconds()))
                    if oldest is not None
                    else 0
                ),
            },
            "by_source": dict(sorted(source_counts.items())),
            "by_risk": dict(sorted(risk_counts.items())),
            "attention": [self._item_to_dict(row) for row in top_rows],
            "recent_actions": [
                self._action_to_dict(row) for row in recent_actions
            ],
        }

    async def claim(
        self,
        item_id: str,
        request: HumanControlClaimRequest,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._session_factory() as session:
            row = self._require_item(session, item_id)
            self._ensure_actionable(row)
            claim_expires = ensure_utc(row.claim_expires_at)
            if (
                row.assigned_to
                and row.assigned_to != request.actor_id
                and claim_expires is not None
                and claim_expires > now
                and not request.force
            ):
                raise HumanControlConflict(
                    f"Item уже закреплён за оператором {row.assigned_to}."
                )
            previous_status = row.status
            row.status = HumanControlItemStatus.CLAIMED.value
            row.assigned_to = request.actor_id
            row.claimed_at = now
            row.claim_expires_at = now + timedelta(seconds=request.ttl_seconds)
            row.snoozed_until = None
            self._record_action(
                session,
                item=row,
                action_type="claim",
                actor_id=request.actor_id,
                previous_status=previous_status,
                new_status=row.status,
                reason=request.reason,
                request=request.model_dump(mode="json"),
                result={"claim_expires_at": iso(row.claim_expires_at)},
            )
            result = self._item_to_dict(row)
        self._claims_created += 1
        await self._publish(
            "human_control.item.claimed",
            workspace_id=result["workspace_id"],
            correlation_id=item_id,
            payload={"item": result},
        )
        return result

    async def release_claim(
        self,
        item_id: str,
        request: HumanControlReleaseRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_item(session, item_id)
            self._ensure_actionable(row)
            if (
                row.assigned_to
                and row.assigned_to != request.actor_id
                and not request.force
            ):
                raise HumanControlConflict(
                    f"Item закреплён за оператором {row.assigned_to}."
                )
            previous_status = row.status
            row.status = HumanControlItemStatus.PENDING.value
            row.assigned_to = None
            row.claimed_at = None
            row.claim_expires_at = None
            self._record_action(
                session,
                item=row,
                action_type="release",
                actor_id=request.actor_id,
                previous_status=previous_status,
                new_status=row.status,
                reason=request.reason,
                request=request.model_dump(mode="json"),
            )
            result = self._item_to_dict(row)
        await self._publish(
            "human_control.item.released",
            workspace_id=result["workspace_id"],
            correlation_id=item_id,
            payload={"item": result},
        )
        return result

    async def snooze(
        self,
        item_id: str,
        request: HumanControlSnoozeRequest,
    ) -> dict[str, Any]:
        until = ensure_utc(request.until)
        if until is None or until <= utc_now():
            raise HumanControlError("Время snooze должно быть позже текущего времени.")
        with self._session_factory() as session:
            row = self._require_item(session, item_id)
            self._ensure_actionable(row)
            if row.assigned_to and row.assigned_to != request.actor_id:
                raise HumanControlConflict(
                    f"Item закреплён за оператором {row.assigned_to}."
                )
            previous_status = row.status
            row.status = HumanControlItemStatus.SNOOZED.value
            row.snoozed_until = until
            row.assigned_to = None
            row.claimed_at = None
            row.claim_expires_at = None
            self._record_action(
                session,
                item=row,
                action_type="snooze",
                actor_id=request.actor_id,
                previous_status=previous_status,
                new_status=row.status,
                reason=request.reason,
                request=request.model_dump(mode="json"),
                result={"snoozed_until": iso(until)},
            )
            result = self._item_to_dict(row)
        await self._publish(
            "human_control.item.snoozed",
            workspace_id=result["workspace_id"],
            correlation_id=item_id,
            payload={"item": result},
        )
        return result

    async def decide(
        self,
        item_id: str,
        request: HumanControlDecisionRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            existing_action = session.scalar(
                select(HumanControlActionModel).where(
                    HumanControlActionModel.idempotency_key
                    == request.idempotency_key
                )
            )
            if existing_action is not None:
                item = session.get(HumanControlItemModel, existing_action.item_id)
                return {
                    "item": self._item_to_dict(item) if item is not None else None,
                    "source_result": existing_action.result_json.get(
                        "source_result"
                    ),
                    "action": self._action_to_dict(existing_action),
                    "idempotent_replay": True,
                }

            row = self._require_item(session, item_id)
            self._ensure_actionable(row)
            if (
                row.assigned_to
                and row.assigned_to != request.actor_id
                and not request.force
            ):
                raise HumanControlConflict(
                    f"Item закреплён за оператором {row.assigned_to}."
                )
            if request.action.value not in row.decision_options_json:
                raise HumanControlConflict(
                    f"Действие {request.action.value} недоступно для "
                    f"источника {row.source_type}. Доступно: "
                    + ", ".join(row.decision_options_json)
                )
            source_type = row.source_type
            source_id = row.source_id
            workspace_id = row.workspace_id
            previous_status = row.status

        try:
            source_result = await self._route_decision(
                source_type=source_type,
                source_id=source_id,
                request=request,
            )
        except HumanControlError:
            raise
        except Exception as exc:
            raise HumanControlConflict(str(exc)) from exc
        await self.sync(workspace_id=workspace_id, actor_id="decision_router")

        with self._session_factory() as session:
            row = self._require_item(session, item_id)
            if row.status in self.ACTIVE_ITEM_STATUSES:
                self._apply_source_result(row, source_result, request)
            row.resolved_by = request.actor_id if row.status not in self.ACTIVE_ITEM_STATUSES else None
            row.resolution_note = request.reason if row.status not in self.ACTIVE_ITEM_STATUSES else None
            action = self._record_action(
                session,
                item=row,
                action_type="decision",
                actor_id=request.actor_id,
                idempotency_key=request.idempotency_key,
                requested_action=request.action.value,
                previous_status=previous_status,
                new_status=row.status,
                reason=request.reason,
                request=request.model_dump(mode="json"),
                result={"source_result": _json_safe(source_result)},
            )
            item_result = self._item_to_dict(row)
            action_result = self._action_to_dict(action)

        self._decisions_routed += 1
        result = {
            "item": item_result,
            "source_result": _json_safe(source_result),
            "action": action_result,
            "idempotent_replay": False,
        }
        await self._publish(
            "human_control.item.decided",
            workspace_id=workspace_id,
            correlation_id=item_id,
            payload=result,
        )
        return result

    async def bulk_decide(
        self,
        request: HumanControlBulkDecisionRequest,
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        failed = 0
        for entry in request.decisions:
            try:
                result = await self.decide(entry.item_id, entry.decision)
                results.append(
                    {
                        "item_id": entry.item_id,
                        "ok": True,
                        "result": result,
                    }
                )
            except Exception as exc:  # Per-item result is intentional for bulk mode.
                failed += 1
                results.append(
                    {
                        "item_id": entry.item_id,
                        "ok": False,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    }
                )
                if request.stop_on_error:
                    break
        return {
            "requested": len(request.decisions),
            "processed": len(results),
            "succeeded": len(results) - failed,
            "failed": failed,
            "stopped_early": len(results) < len(request.decisions),
            "results": results,
        }

    def _collect_descriptors(
        self,
        session: Session,
        *,
        workspace_id: str | None,
        now: datetime,
    ) -> list[dict[str, Any]]:
        descriptors: list[dict[str, Any]] = []

        task_statement = select(TaskApprovalModel).where(
            TaskApprovalModel.status == "pending"
        )
        if workspace_id is not None:
            task_statement = task_statement.where(
                TaskApprovalModel.workspace_id == workspace_id
            )
        for row in session.scalars(task_statement).all():
            metadata = dict(row.metadata_json or {})
            descriptors.append(
                {
                    "workspace_id": row.workspace_id,
                    "source_type": HumanControlSourceType.TASK_APPROVAL.value,
                    "source_id": row.id,
                    "source_status": row.status,
                    "status": HumanControlItemStatus.PENDING.value,
                    "action_kind": "task_execution_approval",
                    "title": f"Подтверждение Task: {row.gate_key}",
                    "summary": row.prompt,
                    "risk_level": _risk(metadata.get("risk_level"), "medium"),
                    "priority": clamp_priority(metadata.get("priority"), 60),
                    "requires_human": True,
                    "decision_options_json": ["approve", "reject"],
                    "payload_json": {
                        "task_id": row.task_id,
                        "gate_key": row.gate_key,
                        "prompt": row.prompt,
                        "requested_by": row.requested_by,
                        "requested_at": iso(row.requested_at),
                        "metadata": _json_safe(metadata),
                    },
                    "due_at": row.expires_at,
                    "expires_at": row.expires_at,
                    "source_updated_at": row.updated_at,
                }
            )

        checkpoint_statement = select(MissionDecisionCheckpointModel).where(
            MissionDecisionCheckpointModel.requires_human.is_(True),
            MissionDecisionCheckpointModel.status.in_(["ready", "deferred"]),
        )
        if workspace_id is not None:
            checkpoint_statement = checkpoint_statement.where(
                MissionDecisionCheckpointModel.workspace_id == workspace_id
            )
        for row in session.scalars(checkpoint_statement).all():
            due_at = ensure_utc(row.due_at)
            deferred = row.status == "deferred" and due_at is not None and due_at > now
            status = (
                HumanControlItemStatus.SNOOZED.value
                if deferred
                else HumanControlItemStatus.PENDING.value
            )
            recommended = row.recommended_decision
            if row.blocking and recommended in {"cancel", "pause"}:
                risk_level = "critical"
            elif row.blocking:
                risk_level = "high"
            else:
                risk_level = "medium"
            metadata = dict(row.metadata_json or {})
            risk_level = _risk(metadata.get("risk_level"), risk_level)
            descriptors.append(
                {
                    "workspace_id": row.workspace_id,
                    "source_type": HumanControlSourceType.MISSION_CHECKPOINT.value,
                    "source_id": row.id,
                    "source_status": row.status,
                    "status": status,
                    "action_kind": "mission_governance_decision",
                    "title": row.title,
                    "summary": row.description,
                    "risk_level": risk_level,
                    "priority": clamp_priority(
                        metadata.get("priority"),
                        90 if row.blocking else 65,
                    ),
                    "requires_human": row.requires_human,
                    "decision_options_json": [
                        "continue",
                        "pause",
                        "replan",
                        "cancel",
                        "accept_risk",
                        "request_review",
                        "defer",
                    ],
                    "payload_json": {
                        "mission_id": row.mission_id,
                        "goal_id": row.goal_id,
                        "cycle_id": row.cycle_id,
                        "risk_id": row.risk_id,
                        "hypothesis_id": row.hypothesis_id,
                        "checkpoint_key": row.checkpoint_key,
                        "checkpoint_type": row.checkpoint_type,
                        "blocking": row.blocking,
                        "recommended_decision": row.recommended_decision,
                        "recommendation_confidence_percent": (
                            row.recommendation_confidence_percent
                        ),
                        "options": _json_safe(row.options_json or []),
                        "context": _json_safe(row.context_snapshot_json or {}),
                        "metadata": _json_safe(metadata),
                    },
                    "due_at": row.due_at,
                    "expires_at": row.expires_at,
                    "snoozed_until": row.due_at if deferred else None,
                    "source_updated_at": row.updated_at,
                }
            )

        allocation_statement = select(MissionResourceAllocationModel).where(
            MissionResourceAllocationModel.status == "pending_approval"
        )
        if workspace_id is not None:
            allocation_statement = allocation_statement.where(
                MissionResourceAllocationModel.workspace_id == workspace_id
            )
        for row in session.scalars(allocation_statement).all():
            admission = dict(row.admission_json or {})
            risk_level = "high" if admission.get("allowed") is False else "medium"
            if row.budget_usd >= 1000:
                risk_level = "critical"
            elif row.budget_usd >= 100:
                risk_level = "high"
            metadata = dict(row.metadata_json or {})
            descriptors.append(
                {
                    "workspace_id": row.workspace_id,
                    "source_type": HumanControlSourceType.MISSION_RESOURCE_ALLOCATION.value,
                    "source_id": row.id,
                    "source_status": row.status,
                    "status": HumanControlItemStatus.PENDING.value,
                    "action_kind": "mission_resource_approval",
                    "title": "Распределение ресурсов Mission Cycle",
                    "summary": row.rationale or "Требуется подтверждение ресурсов цикла.",
                    "risk_level": _risk(metadata.get("risk_level"), risk_level),
                    "priority": clamp_priority(metadata.get("priority"), 70),
                    "requires_human": True,
                    "decision_options_json": ["approve", "reject"],
                    "payload_json": {
                        "mission_id": row.mission_id,
                        "cycle_id": row.cycle_id,
                        "strategy_id": row.strategy_id,
                        "budget_usd": row.budget_usd,
                        "agent_slots": row.agent_slots,
                        "tool_slots": row.tool_slots,
                        "compute_units": row.compute_units,
                        "requested_by": row.requested_by,
                        "admission": _json_safe(admission),
                        "metadata": _json_safe(metadata),
                    },
                    "source_updated_at": row.updated_at,
                }
            )

        reservation_statement = select(WorkspaceResourceReservationModel).where(
            WorkspaceResourceReservationModel.status == "pending_approval"
        )
        if workspace_id is not None:
            reservation_statement = reservation_statement.where(
                WorkspaceResourceReservationModel.workspace_id == workspace_id
            )
        for row in session.scalars(reservation_statement).all():
            admission = dict(row.admission_json or {})
            risk_level = "high" if admission.get("allowed") is False else "medium"
            if row.forced:
                risk_level = "critical"
            metadata = dict(row.metadata_json or {})
            descriptors.append(
                {
                    "workspace_id": row.workspace_id,
                    "source_type": HumanControlSourceType.WORKSPACE_RESOURCE_RESERVATION.value,
                    "source_id": row.id,
                    "source_status": row.status,
                    "status": HumanControlItemStatus.PENDING.value,
                    "action_kind": "workspace_resource_approval",
                    "title": "Резерв общего ресурса Workspace",
                    "summary": row.reason or "Требуется подтверждение общего резерва.",
                    "risk_level": _risk(metadata.get("risk_level"), risk_level),
                    "priority": clamp_priority(row.priority_score, 70),
                    "requires_human": True,
                    "decision_options_json": ["approve", "release"],
                    "payload_json": {
                        "mission_id": row.mission_id,
                        "cycle_id": row.cycle_id,
                        "allocation_id": row.allocation_id,
                        "budget_usd": row.budget_usd,
                        "agent_slots": row.agent_slots,
                        "tool_slots": row.tool_slots,
                        "compute_units": row.compute_units,
                        "priority_score": row.priority_score,
                        "rank": row.rank,
                        "forced": row.forced,
                        "admission": _json_safe(admission),
                        "metadata": _json_safe(metadata),
                    },
                    "source_updated_at": row.updated_at,
                }
            )

        learning_statement = select(MissionLearningRunModel).where(
            MissionLearningRunModel.status == "proposed"
        )
        if workspace_id is not None:
            learning_statement = learning_statement.where(
                MissionLearningRunModel.workspace_id == workspace_id
            )
        for row in session.scalars(learning_statement).all():
            risk_level = "high" if abs(row.improvement_percent) >= 25 else "medium"
            descriptors.append(
                {
                    "workspace_id": row.workspace_id,
                    "source_type": HumanControlSourceType.MISSION_LEARNING_RUN.value,
                    "source_id": row.id,
                    "source_status": row.status,
                    "status": HumanControlItemStatus.PENDING.value,
                    "action_kind": "forecast_calibration_approval",
                    "title": "Калибровка прогноза Mission",
                    "summary": row.reason or "Требуется проверка результата обучения.",
                    "risk_level": risk_level,
                    "priority": 55,
                    "requires_human": True,
                    "decision_options_json": ["apply", "reject"],
                    "payload_json": {
                        "mission_id": row.mission_id,
                        "calibration_id": row.calibration_id,
                        "scope_type": row.scope_type,
                        "sample_count": row.sample_count,
                        "baseline_score": row.baseline_score,
                        "candidate_score": row.candidate_score,
                        "improvement_percent": row.improvement_percent,
                        "automatic": row.automatic,
                        "recommendations": _json_safe(row.recommendations_json or {}),
                    },
                    "source_updated_at": row.created_at,
                }
            )

        return descriptors

    @staticmethod
    def _apply_descriptor(
        row: HumanControlItemModel,
        descriptor: dict[str, Any],
        *,
        now: datetime,
    ) -> None:
        preserve_claim = (
            row.status == HumanControlItemStatus.CLAIMED.value
            and ensure_utc(row.claim_expires_at) is not None
            and ensure_utc(row.claim_expires_at) > now
        )
        preserve_snooze = (
            row.status == HumanControlItemStatus.SNOOZED.value
            and ensure_utc(row.snoozed_until) is not None
            and ensure_utc(row.snoozed_until) > now
        )
        requested_status = descriptor.get("status", HumanControlItemStatus.PENDING.value)
        for key, value in descriptor.items():
            if key in {"status", "snoozed_until"}:
                continue
            setattr(row, key, value)
        if row.status in {
            HumanControlItemStatus.RESOLVED.value,
            HumanControlItemStatus.EXPIRED.value,
            HumanControlItemStatus.SUPERSEDED.value,
        }:
            row.resolution = None
            row.resolved_by = None
            row.resolved_at = None
            row.resolution_note = None
        if preserve_claim:
            row.status = HumanControlItemStatus.CLAIMED.value
        elif preserve_snooze:
            row.status = HumanControlItemStatus.SNOOZED.value
        else:
            row.status = requested_status
            row.snoozed_until = descriptor.get("snoozed_until")
            if row.status != HumanControlItemStatus.CLAIMED.value:
                row.assigned_to = None
                row.claimed_at = None
                row.claim_expires_at = None

    @staticmethod
    def _read_source_status(
        session: Session,
        item: HumanControlItemModel,
    ) -> str | None:
        mapping = {
            HumanControlSourceType.TASK_APPROVAL.value: TaskApprovalModel,
            HumanControlSourceType.MISSION_CHECKPOINT.value: MissionDecisionCheckpointModel,
            HumanControlSourceType.MISSION_RESOURCE_ALLOCATION.value: MissionResourceAllocationModel,
            HumanControlSourceType.WORKSPACE_RESOURCE_RESERVATION.value: WorkspaceResourceReservationModel,
            HumanControlSourceType.MISSION_LEARNING_RUN.value: MissionLearningRunModel,
        }
        model = mapping.get(item.source_type)
        if model is None:
            return None
        row = session.get(model, item.source_id)
        return None if row is None else str(row.status)

    async def _route_decision(
        self,
        *,
        source_type: str,
        source_id: str,
        request: HumanControlDecisionRequest,
    ) -> dict[str, Any]:
        action = request.action
        if source_type == HumanControlSourceType.TASK_APPROVAL.value:
            manager = self._require_dependency(
                self._task_approval_manager,
                "Task Approval Manager",
            )
            decision = {
                HumanControlDecisionAction.APPROVE: ApprovalDecision.APPROVE,
                HumanControlDecisionAction.REJECT: ApprovalDecision.REJECT,
            }.get(action)
            if decision is None:
                raise HumanControlConflict("Task Approval поддерживает approve/reject.")
            result = await manager.decide(
                approval_id=source_id,
                request=ApprovalDecisionRequest(
                    decision=decision,
                    decided_by=request.actor_id,
                    note=request.reason,
                ),
            )
            if result is None:
                raise HumanControlNotFound("Task Approval не найден.")
            return result

        if source_type == HumanControlSourceType.MISSION_CHECKPOINT.value:
            service = self._require_dependency(
                self._mission_governance_service,
                "Mission Governance Service",
            )
            try:
                decision = MissionCheckpointDecision(action.value)
            except ValueError as exc:
                raise HumanControlConflict(
                    f"Действие {action.value} не является решением Checkpoint."
                ) from exc
            return await service.decide_checkpoint(
                source_id,
                MissionCheckpointDecisionRequest(
                    decision=decision,
                    actor_id=request.actor_id,
                    rationale=request.reason,
                    selected_option=request.selected_option,
                    defer_until=request.defer_until,
                    metadata=request.metadata,
                ),
                automatic=False,
            )

        if source_type == HumanControlSourceType.MISSION_RESOURCE_ALLOCATION.value:
            service = self._require_dependency(
                self._mission_resource_service,
                "Mission Resource Service",
            )
            if action == HumanControlDecisionAction.APPROVE:
                return await service.approve_allocation(
                    source_id,
                    MissionResourceAllocationApprove(
                        actor_id=request.actor_id,
                        rationale=request.reason,
                        force=request.force,
                        reserve_now=request.reserve_now,
                        metadata=request.metadata,
                    ),
                )
            if action in {
                HumanControlDecisionAction.REJECT,
                HumanControlDecisionAction.CANCEL,
            }:
                return await service.cancel_allocation(
                    source_id,
                    MissionResourceAllocationCancel(
                        actor_id=request.actor_id,
                        reason=request.reason,
                    ),
                )
            raise HumanControlConflict(
                "Mission Resource Allocation поддерживает approve/reject."
            )

        if source_type == HumanControlSourceType.WORKSPACE_RESOURCE_RESERVATION.value:
            service = self._require_dependency(
                self._workspace_resource_coordinator,
                "Workspace Resource Coordinator",
            )
            decision_request = WorkspaceResourceReservationDecision(
                actor_id=request.actor_id,
                reason=request.reason,
                force=request.force,
                metadata=request.metadata,
            )
            if action == HumanControlDecisionAction.APPROVE:
                return await service.approve_reservation(source_id, decision_request)
            if action in {
                HumanControlDecisionAction.RELEASE,
                HumanControlDecisionAction.REJECT,
                HumanControlDecisionAction.CANCEL,
            }:
                return await service.release_reservation(source_id, decision_request)
            raise HumanControlConflict(
                "Workspace Resource Reservation поддерживает approve/release."
            )

        if source_type == HumanControlSourceType.MISSION_LEARNING_RUN.value:
            service = self._require_dependency(
                self._mission_learning_service,
                "Mission Learning Service",
            )
            decision_request = MissionLearningRunDecisionRequest(
                actor_id=request.actor_id,
                reason=request.reason,
                force=request.force,
            )
            if action in {
                HumanControlDecisionAction.APPLY,
                HumanControlDecisionAction.APPROVE,
            }:
                return await service.apply_run(source_id, decision_request)
            if action == HumanControlDecisionAction.REJECT:
                return await service.reject_run(source_id, decision_request)
            raise HumanControlConflict(
                "Mission Learning Run поддерживает apply/reject."
            )

        raise HumanControlConflict(f"Неизвестный source_type: {source_type}.")

    @staticmethod
    def _require_dependency(value: Any | None, name: str) -> Any:
        if value is None:
            raise HumanControlConflict(f"{name} не запущен.")
        return value

    @staticmethod
    def _apply_source_result(
        row: HumanControlItemModel,
        source_result: dict[str, Any],
        request: HumanControlDecisionRequest,
    ) -> None:
        status = source_result.get("status")
        if row.source_type == HumanControlSourceType.MISSION_CHECKPOINT.value:
            checkpoint = source_result.get("checkpoint") or {}
            status = checkpoint.get("status", status)
            parsed_due_at = parse_datetime(checkpoint.get("due_at"))
            if parsed_due_at is not None:
                row.due_at = parsed_due_at
        row.source_status = str(status or request.action.value)
        row.assigned_to = None
        row.claimed_at = None
        row.claim_expires_at = None
        if status == "deferred" or request.action == HumanControlDecisionAction.DEFER:
            row.status = HumanControlItemStatus.SNOOZED.value
            row.snoozed_until = request.defer_until
            return
        if status in {"ready", "pending", "proposed", "pending_approval"}:
            row.status = HumanControlItemStatus.PENDING.value
            return
        row.status = HumanControlItemStatus.RESOLVED.value
        row.resolution = request.action.value
        row.resolved_at = utc_now()
        row.snoozed_until = None

    @staticmethod
    def _ensure_actionable(row: HumanControlItemModel) -> None:
        if row.status not in HumanControlCenterService.ACTIVE_ITEM_STATUSES:
            raise HumanControlConflict(
                f"Item {row.id} не является активным: status={row.status}."
            )

    @staticmethod
    def _require_item(session: Session, item_id: str) -> HumanControlItemModel:
        row = session.get(HumanControlItemModel, item_id)
        if row is None:
            raise HumanControlNotFound(f"Human Control Item {item_id} не найден.")
        return row

    @staticmethod
    def _record_action(
        session: Session,
        *,
        item: HumanControlItemModel,
        action_type: str,
        actor_id: str,
        previous_status: str | None,
        new_status: str | None,
        reason: str,
        idempotency_key: str | None = None,
        requested_action: str | None = None,
        request: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> HumanControlActionModel:
        row = HumanControlActionModel(
            item_id=item.id,
            workspace_id=item.workspace_id,
            action_type=action_type,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            requested_action=requested_action,
            previous_status=previous_status,
            new_status=new_status,
            reason=reason,
            request_json=_json_safe(request or {}),
            result_json=_json_safe(result or {}),
        )
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def _item_to_dict(row: HumanControlItemModel | None) -> dict[str, Any] | None:
        if row is None:
            return None
        now = utc_now()
        due_at = ensure_utc(row.due_at)
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "source_status": row.source_status,
            "status": row.status,
            "action_kind": row.action_kind,
            "title": row.title,
            "summary": row.summary,
            "risk_level": row.risk_level,
            "priority": row.priority,
            "requires_human": row.requires_human,
            "decision_options": list(row.decision_options_json or []),
            "payload": _json_safe(row.payload_json or {}),
            "due_at": iso(row.due_at),
            "expires_at": iso(row.expires_at),
            "overdue": bool(
                row.status in HumanControlCenterService.ACTIVE_ITEM_STATUSES
                and due_at is not None
                and due_at < now
            ),
            "assigned_to": row.assigned_to,
            "claimed_at": iso(row.claimed_at),
            "claim_expires_at": iso(row.claim_expires_at),
            "snoozed_until": iso(row.snoozed_until),
            "resolution": row.resolution,
            "resolved_by": row.resolved_by,
            "resolved_at": iso(row.resolved_at),
            "resolution_note": row.resolution_note,
            "source_updated_at": iso(row.source_updated_at),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _action_to_dict(row: HumanControlActionModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "item_id": row.item_id,
            "workspace_id": row.workspace_id,
            "action_type": row.action_type,
            "actor_id": row.actor_id,
            "idempotency_key": row.idempotency_key,
            "requested_action": row.requested_action,
            "previous_status": row.previous_status,
            "new_status": row.new_status,
            "reason": row.reason,
            "request": _json_safe(row.request_json or {}),
            "result": _json_safe(row.result_json or {}),
            "created_at": iso(row.created_at),
        }

    async def _publish(
        self,
        event_type: str,
        *,
        workspace_id: str | None,
        correlation_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="human_control_center",
                workspace_id=workspace_id,
                correlation_id=correlation_id,
                payload=_json_safe(payload),
            )
        )
