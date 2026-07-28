from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import socket
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.core.events import Event, EventBus
from backend.database.session import session_scope
from backend.orchestration.models import (
    ExecutionEventEnvelopeModel,
    ExecutionEventReceiptModel,
    ExecutionRemoteSessionModel,
    ExecutionWorkerModel,
)
from backend.orchestration.transport_schemas import (
    RemoteSessionCloseRequest,
    RemoteSessionHeartbeatRequest,
    RemoteSessionOpenRequest,
    SUPPORTED_PROTOCOL_FEATURES,
    SUPPORTED_PROTOCOL_VERSIONS,
    TransportEventAckRequest,
    TransportEventClaimRequest,
    TransportEventNackRequest,
    TransportEventPublishRequest,
    TransportEventReplayRequest,
    TransportLeaseRenewRequest,
)


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ExecutionTransportError(RuntimeError):
    pass


class RemoteSessionNotFound(ExecutionTransportError):
    pass


class RemoteSessionUnauthorized(ExecutionTransportError):
    pass


class RemoteProtocolUnsupported(ExecutionTransportError):
    pass


class TransportEventNotFound(ExecutionTransportError):
    pass


class TransportLeaseLost(ExecutionTransportError):
    pass


class RemoteEventTypeRejected(ExecutionTransportError):
    pass


class ExecutionEventTransport:
    """Durable DB-backed event transport for remote execution nodes.

    Delivery is at-least-once. Receipt records make acknowledgement
    idempotent for a given consumer and delivery attempt.
    """

    ACTIVE_SESSION_STATUSES = {"active"}
    ACTIVE_EVENT_STATUSES = {"pending", "leased"}
    REMOTE_EVENT_PREFIXES = (
        "remote_executor.",
        "execution_remote.",
        "execution_plan.remote.",
        "execution_worker.remote.",
    )

    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
        node_id: str | None = None,
        reconcile_interval_seconds: int = 15,
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._node_id = node_id or f"{socket.gethostname()}:{uuid.uuid4().hex[:12]}"
        self._reconcile_interval_seconds = max(5, reconcile_interval_seconds)
        self._reconcile_task: asyncio.Task[None] | None = None
        self._shutdown_event = asyncio.Event()
        self._published = 0
        self._claimed = 0
        self._acknowledged = 0
        self._retried = 0
        self._dead_lettered = 0

    @property
    def node_id(self) -> str:
        return self._node_id

    async def start(self) -> None:
        if self._reconcile_task is not None and not self._reconcile_task.done():
            return
        self._shutdown_event = asyncio.Event()
        self._reconcile_task = asyncio.create_task(
            self._reconcile_loop(),
            name="execution-event-transport-reconcile",
        )

    async def shutdown(self) -> None:
        self._shutdown_event.set()
        task = self._reconcile_task
        self._reconcile_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _reconcile_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                await self.reconcile()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Reconciliation is self-healing and must not terminate runtime.
                pass
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self._reconcile_interval_seconds,
                )
            except TimeoutError:
                continue

    def protocol_info(self) -> dict[str, Any]:
        return {
            "node_id": self._node_id,
            "protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "features": list(SUPPORTED_PROTOCOL_FEATURES),
            "delivery_semantics": "at-least-once",
            "acknowledgement": "idempotent-per-consumer-attempt",
        }

    def stats(self) -> dict[str, Any]:
        now = utc_now()
        with self._session_factory() as session:
            session_counts = dict(
                session.execute(
                    select(
                        ExecutionRemoteSessionModel.status,
                        func.count(ExecutionRemoteSessionModel.id),
                    ).group_by(ExecutionRemoteSessionModel.status)
                ).all()
            )
            event_counts = dict(
                session.execute(
                    select(
                        ExecutionEventEnvelopeModel.status,
                        func.count(ExecutionEventEnvelopeModel.id),
                    ).group_by(ExecutionEventEnvelopeModel.status)
                ).all()
            )
            stale_sessions = session.scalar(
                select(func.count(ExecutionRemoteSessionModel.id)).where(
                    ExecutionRemoteSessionModel.status == "active",
                    ExecutionRemoteSessionModel.expires_at <= now,
                )
            ) or 0
            expired_leases = session.scalar(
                select(func.count(ExecutionEventEnvelopeModel.id)).where(
                    ExecutionEventEnvelopeModel.status == "leased",
                    ExecutionEventEnvelopeModel.lease_expires_at <= now,
                )
            ) or 0
            pending_available = session.scalar(
                select(func.count(ExecutionEventEnvelopeModel.id)).where(
                    ExecutionEventEnvelopeModel.status == "pending",
                    ExecutionEventEnvelopeModel.available_at <= now,
                )
            ) or 0

        return {
            "node_id": self._node_id,
            "running": self._reconcile_task is not None
            and not self._reconcile_task.done(),
            "sessions": session_counts,
            "events": event_counts,
            "stale_sessions": int(stale_sessions),
            "expired_event_leases": int(expired_leases),
            "pending_available": int(pending_available),
            "counters": {
                "published": self._published,
                "claimed": self._claimed,
                "acknowledged": self._acknowledged,
                "retried": self._retried,
                "dead_lettered": self._dead_lettered,
            },
        }

    async def open_session(
        self,
        request: RemoteSessionOpenRequest,
    ) -> dict[str, Any]:
        if request.protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise RemoteProtocolUnsupported(
                f"Unsupported protocol version: {request.protocol_version}."
            )

        now = utc_now()
        raw_token = secrets.token_urlsafe(48)
        requested_features = set(request.features)
        negotiated_features = sorted(
            requested_features.intersection(SUPPORTED_PROTOCOL_FEATURES)
        )
        with self._session_factory() as session:
            worker = session.get(ExecutionWorkerModel, request.worker_id)
            if worker is None:
                raise RemoteSessionNotFound("Execution Worker not found.")
            if not worker.enabled or worker.status not in {"active", "draining"}:
                raise RemoteSessionUnauthorized(
                    f"Worker status {worker.status} does not allow remote session."
                )
            if worker.instance_id != request.instance_id:
                raise RemoteSessionUnauthorized(
                    "Worker instance_id does not match the registered worker."
                )
            expires_at = as_utc(worker.expires_at) or now
            if expires_at <= now:
                raise RemoteSessionUnauthorized("Worker heartbeat has expired.")

            existing = session.scalar(
                select(ExecutionRemoteSessionModel).where(
                    ExecutionRemoteSessionModel.worker_id == worker.id,
                    ExecutionRemoteSessionModel.instance_id == request.instance_id,
                    ExecutionRemoteSessionModel.status == "active",
                )
            )
            if existing is not None:
                existing.status = "revoked"
                existing.closed_at = now
                existing.close_reason = "replaced_by_new_session"

            remote_session = ExecutionRemoteSessionModel(
                worker_id=worker.id,
                instance_id=request.instance_id,
                protocol_version=request.protocol_version,
                features_json=negotiated_features,
                token_hash=token_hash(raw_token),
                status="active",
                last_seen_at=now,
                expires_at=now + timedelta(seconds=request.ttl_seconds),
                metadata_json=dict(request.metadata),
            )
            session.add(remote_session)
            session.flush()
            result = self._serialize_session(remote_session)

        await self._event_bus.publish(
            Event(
                event_type="execution_transport.session.opened",
                source="execution_transport",
                payload={
                    "session_id": result["id"],
                    "worker_id": result["worker_id"],
                    "protocol_version": result["protocol_version"],
                },
            )
        )
        result["session_token"] = raw_token
        result["protocol"] = self.protocol_info()
        return result

    def authenticate_session(
        self,
        session_id: str,
        session_token: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._session_factory() as session:
            remote_session = self._authorize_session(
                session,
                session_id,
                session_token,
                now=now,
            )
            remote_session.last_seen_at = now
            return self._serialize_session(remote_session)

    async def heartbeat_session(
        self,
        session_id: str,
        request: RemoteSessionHeartbeatRequest,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._session_factory() as session:
            remote_session = self._authorize_session(
                session,
                session_id,
                request.session_token,
                now=now,
            )
            ttl = request.ttl_seconds or 120
            remote_session.last_seen_at = now
            remote_session.expires_at = now + timedelta(seconds=ttl)
            if request.metadata is not None:
                remote_session.metadata_json = dict(request.metadata)
            result = self._serialize_session(remote_session)

        await self._event_bus.publish(
            Event(
                event_type="execution_transport.session.heartbeat",
                source="execution_transport",
                payload={"session_id": session_id},
            )
        )
        return result

    async def close_session(
        self,
        session_id: str,
        request: RemoteSessionCloseRequest,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._session_factory() as session:
            remote_session = self._authorize_session(
                session,
                session_id,
                request.session_token,
                now=now,
            )
            remote_session.status = "closed"
            remote_session.closed_at = now
            remote_session.close_reason = request.reason
            result = self._serialize_session(remote_session)

        await self._event_bus.publish(
            Event(
                event_type="execution_transport.session.closed",
                source="execution_transport",
                payload={
                    "session_id": session_id,
                    "reason": request.reason,
                },
            )
        )
        return result

    async def publish(
        self,
        request: TransportEventPublishRequest,
        *,
        trusted_internal: bool = False,
    ) -> dict[str, Any]:
        source_node = self._node_id
        now = utc_now()

        with self._session_factory() as session:
            if request.session_id is not None or request.session_token is not None:
                if not request.session_id or not request.session_token:
                    raise RemoteSessionUnauthorized(
                        "Both session_id and session_token are required."
                    )
                remote_session = self._authorize_session(
                    session,
                    request.session_id,
                    request.session_token,
                    now=now,
                )
                source_node = f"worker:{remote_session.worker_id}"
                if not trusted_internal and not request.event_type.startswith(
                    self.REMOTE_EVENT_PREFIXES
                ):
                    raise RemoteEventTypeRejected(
                        "Remote publishers may only use remote execution event prefixes."
                    )

            existing = None
            if request.idempotency_key:
                existing = session.scalar(
                    select(ExecutionEventEnvelopeModel).where(
                        ExecutionEventEnvelopeModel.idempotency_key
                        == request.idempotency_key
                    )
                )
            if existing is None and request.event_id:
                existing = session.scalar(
                    select(ExecutionEventEnvelopeModel).where(
                        ExecutionEventEnvelopeModel.event_id == request.event_id
                    )
                )
            if existing is not None:
                return self._serialize_envelope(existing)

            if request.target_worker_id is not None:
                worker = session.get(
                    ExecutionWorkerModel,
                    request.target_worker_id,
                )
                if worker is None:
                    raise RemoteSessionNotFound("Target Execution Worker not found.")

            envelope = ExecutionEventEnvelopeModel(
                event_id=request.event_id or f"event_{uuid.uuid4().hex}",
                topic=request.topic,
                event_type=request.event_type,
                source_node=source_node,
                target_worker_id=request.target_worker_id,
                status="pending",
                priority=request.priority,
                payload_json=dict(request.payload),
                headers_json=dict(request.headers),
                idempotency_key=request.idempotency_key,
                max_attempts=request.max_attempts,
                available_at=request.available_at or now,
            )
            session.add(envelope)
            session.flush()
            result = self._serialize_envelope(envelope)

        self._published += 1
        await self._event_bus.publish(
            Event(
                event_type="execution_transport.event.published",
                source="execution_transport",
                payload={
                    "envelope_id": result["id"],
                    "event_id": result["event_id"],
                    "topic": result["topic"],
                    "target_worker_id": result["target_worker_id"],
                },
            )
        )
        return result

    async def mirror_event(self, event: Event) -> dict[str, Any] | None:
        if event.event_type.startswith("execution_transport."):
            return None
        target_worker_id = None
        for key in ("worker_id", "target_worker_id"):
            value = event.payload.get(key)
            if isinstance(value, str) and value:
                target_worker_id = value
                break
        topic = event.event_type.split(".", maxsplit=1)[0]
        return await self.publish(
            TransportEventPublishRequest(
                event_id=event.id,
                topic=topic,
                event_type=event.event_type,
                payload=event.to_dict(),
                headers={
                    "schema_version": event.schema_version,
                    "correlation_id": event.correlation_id,
                    "causation_id": event.causation_id,
                    "workspace_id": event.workspace_id,
                    "project_id": event.project_id,
                },
                target_worker_id=target_worker_id,
                idempotency_key=f"eventbus:{event.id}",
                priority=self._event_priority(event.priority),
                max_attempts=10,
            ),
            trusted_internal=True,
        )

    async def claim(
        self,
        request: TransportEventClaimRequest,
    ) -> list[dict[str, Any]]:
        now = utc_now()
        claims: list[dict[str, Any]] = []
        with self._session_factory() as session:
            remote_session = self._authorize_session(
                session,
                request.session_id,
                request.session_token,
                now=now,
            )
            self._release_expired_event_leases(session, now)

            query = select(ExecutionEventEnvelopeModel).where(
                ExecutionEventEnvelopeModel.status == "pending",
                ExecutionEventEnvelopeModel.available_at <= now,
                or_(
                    ExecutionEventEnvelopeModel.target_worker_id.is_(None),
                    ExecutionEventEnvelopeModel.target_worker_id
                    == remote_session.worker_id,
                ),
            )
            if request.topics:
                query = query.where(
                    ExecutionEventEnvelopeModel.topic.in_(tuple(request.topics))
                )
            query = query.order_by(
                ExecutionEventEnvelopeModel.priority.desc(),
                ExecutionEventEnvelopeModel.available_at.asc(),
                ExecutionEventEnvelopeModel.created_at.asc(),
            ).limit(request.limit)

            envelopes = list(session.scalars(query).all())
            for envelope in envelopes:
                lease_token = secrets.token_urlsafe(32)
                envelope.status = "leased"
                envelope.attempt_count += 1
                envelope.lease_session_id = remote_session.id
                envelope.lease_token = lease_token
                envelope.lease_expires_at = now + timedelta(
                    seconds=request.lease_seconds
                )
                envelope.delivered_at = now
                claims.append(
                    {
                        "event": self._serialize_envelope(envelope),
                        "lease_token": lease_token,
                        "consumer_key": request.consumer_key,
                        "lease_expires_at": envelope.lease_expires_at.isoformat(),
                        "delivery_attempt": envelope.attempt_count,
                    }
                )
            remote_session.last_seen_at = now

        self._claimed += len(claims)
        if claims:
            await self._event_bus.publish(
                Event(
                    event_type="execution_transport.events.claimed",
                    source="execution_transport",
                    payload={
                        "session_id": request.session_id,
                        "count": len(claims),
                        "event_ids": [item["event"]["event_id"] for item in claims],
                    },
                )
            )
        return claims

    async def renew_event_lease(
        self,
        envelope_id: str,
        request: TransportLeaseRenewRequest,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._session_factory() as session:
            self._authorize_session(
                session,
                request.session_id,
                request.session_token,
                now=now,
            )
            envelope = self._get_envelope(session, envelope_id)
            self._assert_event_lease(
                envelope,
                request.session_id,
                request.lease_token,
                now,
            )
            envelope.lease_expires_at = now + timedelta(
                seconds=request.lease_seconds
            )
            result = self._serialize_envelope(envelope)

        await self._event_bus.publish(
            Event(
                event_type="execution_transport.event.lease_renewed",
                source="execution_transport",
                payload={"envelope_id": envelope_id},
            )
        )
        return result

    async def acknowledge(
        self,
        envelope_id: str,
        request: TransportEventAckRequest,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._session_factory() as session:
            self._authorize_session(
                session,
                request.session_id,
                request.session_token,
                now=now,
            )
            envelope = self._get_envelope(session, envelope_id)
            existing = session.scalar(
                select(ExecutionEventReceiptModel).where(
                    ExecutionEventReceiptModel.envelope_id == envelope.id,
                    ExecutionEventReceiptModel.consumer_key == request.consumer_key,
                    ExecutionEventReceiptModel.delivery_attempt
                    == envelope.attempt_count,
                    ExecutionEventReceiptModel.status == "processed",
                )
            )
            if existing is not None and envelope.status == "acknowledged":
                return {
                    "event": self._serialize_envelope(envelope),
                    "receipt": self._serialize_receipt(existing),
                    "idempotent": True,
                }

            self._assert_event_lease(
                envelope,
                request.session_id,
                request.lease_token,
                now,
            )
            receipt = ExecutionEventReceiptModel(
                envelope_id=envelope.id,
                session_id=request.session_id,
                consumer_key=request.consumer_key,
                delivery_attempt=envelope.attempt_count,
                status="processed",
                result_json=dict(request.result),
                received_at=envelope.delivered_at or now,
                processed_at=now,
            )
            session.add(receipt)
            envelope.status = "acknowledged"
            envelope.ack_result_json = dict(request.result)
            envelope.acknowledged_at = now
            envelope.lease_session_id = None
            envelope.lease_token = None
            envelope.lease_expires_at = None
            session.flush()
            result = {
                "event": self._serialize_envelope(envelope),
                "receipt": self._serialize_receipt(receipt),
                "idempotent": False,
            }

        self._acknowledged += 1
        await self._event_bus.publish(
            Event(
                event_type="execution_transport.event.acknowledged",
                source="execution_transport",
                payload={
                    "envelope_id": envelope_id,
                    "consumer_key": request.consumer_key,
                },
            )
        )
        return result

    async def reject(
        self,
        envelope_id: str,
        request: TransportEventNackRequest,
    ) -> dict[str, Any]:
        now = utc_now()
        dead = False
        with self._session_factory() as session:
            self._authorize_session(
                session,
                request.session_id,
                request.session_token,
                now=now,
            )
            envelope = self._get_envelope(session, envelope_id)
            self._assert_event_lease(
                envelope,
                request.session_id,
                request.lease_token,
                now,
            )
            receipt = ExecutionEventReceiptModel(
                envelope_id=envelope.id,
                session_id=request.session_id,
                consumer_key=request.consumer_key,
                delivery_attempt=envelope.attempt_count,
                status="failed",
                error=request.error,
                result_json=dict(request.metadata),
                received_at=envelope.delivered_at or now,
                processed_at=now,
            )
            session.add(receipt)
            envelope.last_error = request.error
            dead = request.dead_letter or envelope.attempt_count >= envelope.max_attempts
            if dead:
                envelope.status = "dead"
                envelope.dead_lettered_at = now
            else:
                envelope.status = "pending"
                envelope.available_at = now + timedelta(
                    seconds=request.retry_delay_seconds
                )
            envelope.lease_session_id = None
            envelope.lease_token = None
            envelope.lease_expires_at = None
            session.flush()
            result = {
                "event": self._serialize_envelope(envelope),
                "receipt": self._serialize_receipt(receipt),
                "dead_lettered": dead,
            }

        if dead:
            self._dead_lettered += 1
        else:
            self._retried += 1
        await self._event_bus.publish(
            Event(
                event_type=(
                    "execution_transport.event.dead_lettered"
                    if dead
                    else "execution_transport.event.requeued"
                ),
                source="execution_transport",
                payload={
                    "envelope_id": envelope_id,
                    "error": request.error,
                },
            )
        )
        return result

    async def replay_dead_letter(
        self,
        envelope_id: str,
        request: TransportEventReplayRequest,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._session_factory() as session:
            envelope = self._get_envelope(session, envelope_id)
            if envelope.status != "dead":
                raise ExecutionTransportError(
                    "Only dead-lettered events can be replayed."
                )
            envelope.status = "pending"
            envelope.available_at = now + timedelta(seconds=request.delay_seconds)
            envelope.last_error = None
            envelope.dead_lettered_at = None
            envelope.lease_session_id = None
            envelope.lease_token = None
            envelope.lease_expires_at = None
            if request.reset_attempts:
                envelope.attempt_count = 0
            headers = dict(envelope.headers_json or {})
            headers["replay"] = {
                "actor_id": request.actor_id,
                "reason": request.reason,
                "at": now.isoformat(),
            }
            envelope.headers_json = headers
            result = self._serialize_envelope(envelope)

        await self._event_bus.publish(
            Event(
                event_type="execution_transport.event.replayed",
                source="execution_transport",
                payload={
                    "envelope_id": envelope_id,
                    "actor_id": request.actor_id,
                    "reason": request.reason,
                },
            )
        )
        return result

    async def reconcile(self) -> dict[str, int]:
        now = utc_now()
        with self._session_factory() as session:
            expired_sessions = 0
            for remote_session in session.scalars(
                select(ExecutionRemoteSessionModel).where(
                    ExecutionRemoteSessionModel.status == "active",
                    ExecutionRemoteSessionModel.expires_at <= now,
                )
            ).all():
                remote_session.status = "expired"
                remote_session.closed_at = now
                remote_session.close_reason = "session_ttl_expired"
                expired_sessions += 1

            released, dead = self._release_expired_event_leases(session, now)

        if expired_sessions or released or dead:
            await self._event_bus.publish(
                Event(
                    event_type="execution_transport.reconciled",
                    source="execution_transport",
                    payload={
                        "expired_sessions": expired_sessions,
                        "released_event_leases": released,
                        "dead_lettered_events": dead,
                    },
                )
            )
        self._retried += released
        self._dead_lettered += dead
        return {
            "expired_sessions": expired_sessions,
            "released_event_leases": released,
            "dead_lettered_events": dead,
        }

    def verify(self) -> dict[str, Any]:
        now = utc_now()
        issues: list[dict[str, Any]] = []
        with self._session_factory() as session:
            leased = session.scalars(
                select(ExecutionEventEnvelopeModel).where(
                    ExecutionEventEnvelopeModel.status == "leased"
                )
            ).all()
            for envelope in leased:
                if not (
                    envelope.lease_session_id
                    and envelope.lease_token
                    and envelope.lease_expires_at
                ):
                    issues.append(
                        {
                            "code": "leased_event_missing_lease_fields",
                            "envelope_id": envelope.id,
                        }
                    )
                elif as_utc(envelope.lease_expires_at) <= now:
                    issues.append(
                        {
                            "code": "expired_event_lease",
                            "envelope_id": envelope.id,
                        }
                    )

            acknowledged = session.scalars(
                select(ExecutionEventEnvelopeModel).where(
                    ExecutionEventEnvelopeModel.status == "acknowledged"
                )
            ).all()
            for envelope in acknowledged:
                receipt_count = session.scalar(
                    select(func.count(ExecutionEventReceiptModel.id)).where(
                        ExecutionEventReceiptModel.envelope_id == envelope.id,
                        ExecutionEventReceiptModel.status == "processed",
                    )
                ) or 0
                if receipt_count < 1:
                    issues.append(
                        {
                            "code": "acknowledged_event_without_receipt",
                            "envelope_id": envelope.id,
                        }
                    )

        return {
            "ok": not issues,
            "issue_count": len(issues),
            "issues": issues,
        }

    def list_sessions(
        self,
        *,
        status: str | None = None,
        worker_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            query = select(ExecutionRemoteSessionModel)
            if status:
                query = query.where(ExecutionRemoteSessionModel.status == status)
            if worker_id:
                query = query.where(
                    ExecutionRemoteSessionModel.worker_id == worker_id
                )
            rows = session.scalars(
                query.order_by(
                    ExecutionRemoteSessionModel.created_at.desc()
                ).offset(offset).limit(limit)
            ).all()
            return [self._serialize_session(row) for row in rows]

    def list_events(
        self,
        *,
        status: str | None = None,
        topic: str | None = None,
        target_worker_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            query = select(ExecutionEventEnvelopeModel)
            if status:
                query = query.where(ExecutionEventEnvelopeModel.status == status)
            if topic:
                query = query.where(ExecutionEventEnvelopeModel.topic == topic)
            if target_worker_id:
                query = query.where(
                    ExecutionEventEnvelopeModel.target_worker_id
                    == target_worker_id
                )
            rows = session.scalars(
                query.order_by(
                    ExecutionEventEnvelopeModel.created_at.desc()
                ).offset(offset).limit(limit)
            ).all()
            return [self._serialize_envelope(row) for row in rows]

    def get_event(self, envelope_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            envelope = session.get(ExecutionEventEnvelopeModel, envelope_id)
            if envelope is None:
                return None
            result = self._serialize_envelope(envelope)
            receipts = session.scalars(
                select(ExecutionEventReceiptModel).where(
                    ExecutionEventReceiptModel.envelope_id == envelope.id
                ).order_by(ExecutionEventReceiptModel.created_at.asc())
            ).all()
            result["receipts"] = [
                self._serialize_receipt(receipt) for receipt in receipts
            ]
            return result

    def _authorize_session(
        self,
        session: Session,
        session_id: str,
        raw_token: str,
        *,
        now: datetime,
    ) -> ExecutionRemoteSessionModel:
        remote_session = session.get(ExecutionRemoteSessionModel, session_id)
        if remote_session is None:
            raise RemoteSessionNotFound("Remote executor session not found.")
        if remote_session.status != "active":
            raise RemoteSessionUnauthorized(
                f"Remote executor session is {remote_session.status}."
            )
        if as_utc(remote_session.expires_at) <= now:
            remote_session.status = "expired"
            remote_session.closed_at = now
            remote_session.close_reason = "session_ttl_expired"
            raise RemoteSessionUnauthorized("Remote executor session expired.")
        if not hmac.compare_digest(
            remote_session.token_hash,
            token_hash(raw_token),
        ):
            raise RemoteSessionUnauthorized("Invalid remote session token.")
        worker = (
            session.get(ExecutionWorkerModel, remote_session.worker_id)
            if remote_session.worker_id
            else None
        )
        if worker is None or not worker.enabled:
            raise RemoteSessionUnauthorized(
                "Registered Execution Worker is unavailable."
            )
        if worker.instance_id != remote_session.instance_id:
            raise RemoteSessionUnauthorized(
                "Remote session no longer matches Worker instance_id."
            )
        if worker.status not in {"active", "draining"}:
            raise RemoteSessionUnauthorized(
                f"Worker status {worker.status} does not allow remote execution."
            )
        if as_utc(worker.expires_at) <= now:
            raise RemoteSessionUnauthorized("Execution Worker heartbeat expired.")
        return remote_session

    def _release_expired_event_leases(
        self,
        session: Session,
        now: datetime,
    ) -> tuple[int, int]:
        released = 0
        dead = 0
        rows = session.scalars(
            select(ExecutionEventEnvelopeModel).where(
                ExecutionEventEnvelopeModel.status == "leased"
            )
        ).all()
        for envelope in rows:
            remote_session = (
                session.get(
                    ExecutionRemoteSessionModel,
                    envelope.lease_session_id,
                )
                if envelope.lease_session_id
                else None
            )
            lease_expired = (
                envelope.lease_expires_at is None
                or as_utc(envelope.lease_expires_at) <= now
            )
            session_unavailable = (
                remote_session is None
                or remote_session.status != "active"
                or as_utc(remote_session.expires_at) <= now
            )
            if not lease_expired and not session_unavailable:
                continue
            envelope.last_error = "event_lease_expired"
            if envelope.attempt_count >= envelope.max_attempts:
                envelope.status = "dead"
                envelope.dead_lettered_at = now
                dead += 1
            else:
                envelope.status = "pending"
                envelope.available_at = now
                released += 1
            envelope.lease_session_id = None
            envelope.lease_token = None
            envelope.lease_expires_at = None
        return released, dead

    @staticmethod
    def _assert_event_lease(
        envelope: ExecutionEventEnvelopeModel,
        session_id: str,
        lease_token: str,
        now: datetime,
    ) -> None:
        if envelope.status != "leased":
            raise TransportLeaseLost(
                f"Event is not leased; current status is {envelope.status}."
            )
        if envelope.lease_session_id != session_id:
            raise TransportLeaseLost("Event lease belongs to another session.")
        if not envelope.lease_token or not hmac.compare_digest(
            envelope.lease_token,
            lease_token,
        ):
            raise TransportLeaseLost("Event lease token does not match.")
        if as_utc(envelope.lease_expires_at) <= now:
            raise TransportLeaseLost("Event lease has expired.")

    @staticmethod
    def _get_envelope(
        session: Session,
        envelope_id: str,
    ) -> ExecutionEventEnvelopeModel:
        envelope = session.get(ExecutionEventEnvelopeModel, envelope_id)
        if envelope is None:
            raise TransportEventNotFound("Transport event not found.")
        return envelope

    @staticmethod
    def _event_priority(value: str) -> int:
        return {
            "low": -100,
            "normal": 0,
            "high": 100,
            "critical": 500,
        }.get(value, 0)

    @staticmethod
    def _serialize_session(
        item: ExecutionRemoteSessionModel,
    ) -> dict[str, Any]:
        return {
            "id": item.id,
            "worker_id": item.worker_id,
            "instance_id": item.instance_id,
            "protocol_version": item.protocol_version,
            "features": list(item.features_json or []),
            "status": item.status,
            "last_seen_at": item.last_seen_at.isoformat(),
            "expires_at": item.expires_at.isoformat(),
            "metadata": dict(item.metadata_json or {}),
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
            "closed_at": item.closed_at.isoformat() if item.closed_at else None,
            "close_reason": item.close_reason,
        }

    @staticmethod
    def _serialize_envelope(
        item: ExecutionEventEnvelopeModel,
    ) -> dict[str, Any]:
        return {
            "id": item.id,
            "event_id": item.event_id,
            "topic": item.topic,
            "event_type": item.event_type,
            "source_node": item.source_node,
            "target_worker_id": item.target_worker_id,
            "status": item.status,
            "priority": item.priority,
            "payload": dict(item.payload_json or {}),
            "headers": dict(item.headers_json or {}),
            "idempotency_key": item.idempotency_key,
            "attempt_count": item.attempt_count,
            "max_attempts": item.max_attempts,
            "available_at": item.available_at.isoformat(),
            "lease_session_id": item.lease_session_id,
            "lease_expires_at": (
                item.lease_expires_at.isoformat()
                if item.lease_expires_at
                else None
            ),
            "last_error": item.last_error,
            "ack_result": (
                dict(item.ack_result_json)
                if item.ack_result_json is not None
                else None
            ),
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
            "delivered_at": (
                item.delivered_at.isoformat() if item.delivered_at else None
            ),
            "acknowledged_at": (
                item.acknowledged_at.isoformat()
                if item.acknowledged_at
                else None
            ),
            "dead_lettered_at": (
                item.dead_lettered_at.isoformat()
                if item.dead_lettered_at
                else None
            ),
        }

    @staticmethod
    def _serialize_receipt(
        item: ExecutionEventReceiptModel,
    ) -> dict[str, Any]:
        return {
            "id": item.id,
            "envelope_id": item.envelope_id,
            "session_id": item.session_id,
            "consumer_key": item.consumer_key,
            "delivery_attempt": item.delivery_attempt,
            "status": item.status,
            "result": dict(item.result_json or {}),
            "error": item.error,
            "received_at": item.received_at.isoformat(),
            "processed_at": (
                item.processed_at.isoformat() if item.processed_at else None
            ),
            "created_at": item.created_at.isoformat(),
        }
