from __future__ import annotations

import asyncio
import os
import secrets
import socket
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.core.events import Event, EventBus
from backend.database.session import session_scope
from backend.orchestration.distributed_schemas import (
    LeaseCompleteRequest,
    LeaseFailRequest,
    LeaseRenewRequest,
    WorkItemDispatchRequest,
    WorkerClaimRequest,
    WorkerHeartbeatRequest,
    WorkerRegisterRequest,
)
from backend.orchestration.models import (
    ExecutionLeaseModel,
    ExecutionPlanModel,
    ExecutionWorkerModel,
    ExecutionWorkItemModel,
)
from backend.orchestration.runtime import ExecutionPlanRuntime
from backend.orchestration.runtime_schemas import ExecutionPlanRunRequest


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


class ExecutionDistributionError(RuntimeError):
    pass


class WorkerNotFound(ExecutionDistributionError):
    pass


class WorkerConflict(ExecutionDistributionError):
    pass


class WorkItemNotFound(ExecutionDistributionError):
    pass


class LeaseNotFound(ExecutionDistributionError):
    pass


class LeaseLost(ExecutionDistributionError):
    pass


class ExecutionDistributionCoordinator:
    ACTIVE_LEASE_STATUSES = {"active", "renewed"}

    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._claimed = 0
        self._completed = 0
        self._failed = 0
        self._requeued = 0
        self._expired = 0

    def stats(self) -> dict[str, Any]:
        now = utc_now()
        with self._session_factory() as session:
            worker_counts = dict(
                session.execute(
                    select(
                        ExecutionWorkerModel.status,
                        func.count(ExecutionWorkerModel.id),
                    ).group_by(ExecutionWorkerModel.status)
                ).all()
            )
            work_counts = dict(
                session.execute(
                    select(
                        ExecutionWorkItemModel.status,
                        func.count(ExecutionWorkItemModel.id),
                    ).group_by(ExecutionWorkItemModel.status)
                ).all()
            )
            active_leases = session.scalar(
                select(func.count(ExecutionLeaseModel.id)).where(
                    ExecutionLeaseModel.status.in_(
                        tuple(self.ACTIVE_LEASE_STATUSES)
                    ),
                    ExecutionLeaseModel.expires_at > now,
                )
            ) or 0
            stale_workers = session.scalar(
                select(func.count(ExecutionWorkerModel.id)).where(
                    ExecutionWorkerModel.enabled.is_(True),
                    ExecutionWorkerModel.status.in_(("active", "draining")),
                    ExecutionWorkerModel.expires_at <= now,
                )
            ) or 0

        return {
            "workers": worker_counts,
            "work_items": work_counts,
            "active_leases": int(active_leases),
            "stale_workers": int(stale_workers),
            "counters": {
                "claimed": self._claimed,
                "completed": self._completed,
                "failed": self._failed,
                "requeued": self._requeued,
                "expired": self._expired,
            },
        }

    def active_plan_ids(self) -> set[str]:
        now = utc_now()
        with self._session_factory() as session:
            rows = session.scalars(
                select(ExecutionWorkItemModel.plan_id).where(
                    ExecutionWorkItemModel.status == "leased",
                    ExecutionWorkItemModel.lease_expires_at > now,
                    ExecutionWorkItemModel.plan_id.is_not(None),
                )
            ).all()
        return {str(plan_id) for plan_id in rows if plan_id}

    async def register_worker(
        self,
        request: WorkerRegisterRequest,
    ) -> dict[str, Any]:
        now = utc_now()
        takeover = False
        with self._session_factory() as session:
            worker = session.scalar(
                select(ExecutionWorkerModel).where(
                    ExecutionWorkerModel.worker_key == request.worker_key
                )
            )
            if worker is not None:
                alive = (
                    worker.enabled
                    and worker.status in {"active", "draining"}
                    and (as_utc(worker.expires_at) or now) > now
                )
                if (
                    alive
                    and worker.instance_id != request.instance_id
                    and not request.force_takeover
                ):
                    raise WorkerConflict(
                        "Worker key уже принадлежит активному instance_id. "
                        "Используйте force_takeover только после проверки."
                    )
                takeover = worker.instance_id != request.instance_id
            else:
                worker = ExecutionWorkerModel(
                    worker_key=request.worker_key,
                    instance_id=request.instance_id,
                    expires_at=now,
                )
                session.add(worker)

            worker.instance_id = request.instance_id
            worker.hostname = request.hostname
            worker.process_id = request.process_id
            worker.status = "active"
            worker.enabled = True
            worker.queues_json = list(request.queues)
            worker.capabilities_json = list(request.capabilities)
            worker.max_concurrency = request.max_concurrency
            worker.heartbeat_ttl_seconds = request.heartbeat_ttl_seconds
            worker.default_lease_seconds = request.default_lease_seconds
            worker.last_heartbeat_at = now
            worker.expires_at = now + timedelta(
                seconds=request.heartbeat_ttl_seconds
            )
            worker.draining_at = None
            worker.metadata_json = dict(request.metadata)
            worker.active_leases = self._active_lease_count(
                session,
                worker.id,
                now,
            ) if worker.id else 0
            session.flush()
            result = self._serialize_worker(worker)

        await self._event_bus.publish(
            Event(
                event_type=(
                    "execution_worker.taken_over"
                    if takeover
                    else "execution_worker.registered"
                ),
                source="execution_distribution",
                payload={
                    "worker_id": result["id"],
                    "worker_key": result["worker_key"],
                    "instance_id": result["instance_id"],
                },
            )
        )
        return result

    async def heartbeat(
        self,
        worker_id: str,
        request: WorkerHeartbeatRequest,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._session_factory() as session:
            worker = self._get_worker(session, worker_id)
            self._assert_instance(worker, request.instance_id)
            if not worker.enabled or worker.status in {"offline", "unhealthy"}:
                raise WorkerConflict(
                    f"Worker имеет статус {worker.status} и не принимает heartbeat."
                )
            worker.status = request.status
            worker.last_heartbeat_at = now
            worker.expires_at = now + timedelta(
                seconds=worker.heartbeat_ttl_seconds
            )
            if request.metadata is not None:
                worker.metadata_json = dict(request.metadata)
            worker.active_leases = self._active_lease_count(
                session,
                worker.id,
                now,
            )
            result = self._serialize_worker(worker)

        await self._event_bus.publish(
            Event(
                event_type="execution_worker.heartbeat",
                source="execution_distribution",
                payload={
                    "worker_id": worker_id,
                    "status": result["status"],
                    "active_leases": result["active_leases"],
                },
            )
        )
        return result

    async def set_worker_state(
        self,
        worker_id: str,
        *,
        instance_id: str,
        state: str,
        reason: str,
    ) -> dict[str, Any]:
        if state not in {"draining", "offline", "unhealthy", "active"}:
            raise ExecutionDistributionError("Недопустимый статус Worker.")
        now = utc_now()
        with self._session_factory() as session:
            worker = self._get_worker(session, worker_id)
            self._assert_instance(worker, instance_id)
            worker.status = state
            worker.draining_at = now if state == "draining" else None
            if state == "active":
                worker.expires_at = now + timedelta(
                    seconds=worker.heartbeat_ttl_seconds
                )
            metadata = dict(worker.metadata_json or {})
            metadata["last_state_reason"] = reason
            worker.metadata_json = metadata
            result = self._serialize_worker(worker)

        await self._event_bus.publish(
            Event(
                event_type=f"execution_worker.{state}",
                source="execution_distribution",
                payload={
                    "worker_id": worker_id,
                    "instance_id": instance_id,
                    "reason": reason,
                },
            )
        )
        return result

    async def dispatch_plan(
        self,
        request: WorkItemDispatchRequest,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._session_factory() as session:
            plan = session.get(ExecutionPlanModel, request.plan_id)
            if plan is None:
                raise WorkItemNotFound("Execution Plan не найден.")

            if request.idempotency_key:
                existing = session.scalar(
                    select(ExecutionWorkItemModel).where(
                        ExecutionWorkItemModel.idempotency_key
                        == request.idempotency_key
                    )
                )
                if existing is not None:
                    return self._serialize_work_item(existing)

            payload = {
                "run_request": dict(request.run_request),
                "metadata": dict(request.metadata),
            }
            row = ExecutionWorkItemModel(
                workspace_id=plan.workspace_id,
                plan_id=plan.id,
                work_type="execution_plan",
                queue_name=request.queue_name,
                status="pending",
                priority=request.priority,
                payload_json=payload,
                required_capabilities_json=list(
                    request.required_capabilities
                ),
                idempotency_key=request.idempotency_key,
                max_attempts=request.max_attempts,
                available_at=request.available_at or now,
            )
            session.add(row)
            session.flush()
            result = self._serialize_work_item(row)

        await self._event_bus.publish(
            Event(
                event_type="execution_dispatch.enqueued",
                source="execution_distribution",
                workspace_id=result["workspace_id"],
                correlation_id=result["plan_id"],
                payload={
                    "work_item_id": result["id"],
                    "plan_id": result["plan_id"],
                    "queue_name": result["queue_name"],
                    "priority": result["priority"],
                },
            )
        )
        return result

    async def claim_next(
        self,
        worker_id: str,
        request: WorkerClaimRequest,
    ) -> dict[str, Any] | None:
        now = utc_now()
        claimed: dict[str, Any] | None = None
        with self._session_factory() as session:
            worker = self._get_worker(session, worker_id)
            self._assert_instance(worker, request.instance_id)
            self._assert_worker_can_claim(worker, now)

            active_count = self._active_lease_count(
                session,
                worker.id,
                now,
            )
            worker.active_leases = active_count
            if active_count >= worker.max_concurrency:
                return None

            allowed_queues = set(request.queue_names or worker.queues_json or [])
            if not allowed_queues:
                allowed_queues = {"default"}

            candidates = list(
                session.scalars(
                    select(ExecutionWorkItemModel)
                    .where(
                        ExecutionWorkItemModel.status == "pending",
                        ExecutionWorkItemModel.available_at <= now,
                        ExecutionWorkItemModel.queue_name.in_(
                            tuple(sorted(allowed_queues))
                        ),
                    )
                    .order_by(
                        ExecutionWorkItemModel.priority.desc(),
                        ExecutionWorkItemModel.available_at.asc(),
                        ExecutionWorkItemModel.created_at.asc(),
                    )
                    .limit(50)
                ).all()
            )

            worker_capabilities = set(worker.capabilities_json or [])
            for candidate in candidates:
                required = set(candidate.required_capabilities_json or [])
                if not required.issubset(worker_capabilities):
                    continue

                token = secrets.token_urlsafe(32)
                fence = int(candidate.fencing_token or 0) + 1
                lease_seconds = (
                    request.lease_seconds
                    or worker.default_lease_seconds
                )
                expires_at = now + timedelta(seconds=lease_seconds)
                result = session.execute(
                    update(ExecutionWorkItemModel)
                    .where(
                        ExecutionWorkItemModel.id == candidate.id,
                        ExecutionWorkItemModel.status == "pending",
                    )
                    .values(
                        status="leased",
                        worker_id=worker.id,
                        last_lease_token=token,
                        fencing_token=fence,
                        lease_expires_at=expires_at,
                        attempt_count=ExecutionWorkItemModel.attempt_count + 1,
                        started_at=func.coalesce(
                            ExecutionWorkItemModel.started_at,
                            now,
                        ),
                        updated_at=now,
                    )
                )
                if result.rowcount != 1:
                    continue

                lease = ExecutionLeaseModel(
                    work_item_id=candidate.id,
                    worker_id=worker.id,
                    lease_token=token,
                    fencing_token=fence,
                    status="active",
                    acquired_at=now,
                    heartbeat_at=now,
                    expires_at=expires_at,
                    metadata_json={
                        "worker_key": worker.worker_key,
                        "instance_id": worker.instance_id,
                        "queue_name": candidate.queue_name,
                    },
                )
                session.add(lease)
                worker.active_leases = active_count + 1
                session.flush()
                session.refresh(candidate)
                claimed = {
                    "work_item": self._serialize_work_item(candidate),
                    "lease": self._serialize_lease(lease),
                    "worker": self._serialize_worker(worker),
                }
                break

        if claimed is None:
            return None

        self._claimed += 1
        await self._event_bus.publish(
            Event(
                event_type="execution_lease.acquired",
                source="execution_distribution",
                workspace_id=claimed["work_item"]["workspace_id"],
                correlation_id=claimed["work_item"]["plan_id"],
                payload={
                    "worker_id": worker_id,
                    "work_item_id": claimed["work_item"]["id"],
                    "lease_id": claimed["lease"]["id"],
                    "fencing_token": claimed["lease"]["fencing_token"],
                    "expires_at": claimed["lease"]["expires_at"],
                },
            )
        )
        return claimed

    async def renew_lease(
        self,
        lease_token: str,
        request: LeaseRenewRequest,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._session_factory() as session:
            worker = self._get_worker(session, request.worker_id)
            self._assert_instance(worker, request.instance_id)
            lease, work = self._get_owned_lease(
                session,
                lease_token,
                worker,
                request.fencing_token,
                now,
            )
            lease_seconds = (
                request.lease_seconds
                or worker.default_lease_seconds
            )
            expires_at = now + timedelta(seconds=lease_seconds)
            lease.status = "renewed"
            lease.heartbeat_at = now
            lease.expires_at = expires_at
            work.lease_expires_at = expires_at
            worker.last_heartbeat_at = now
            worker.expires_at = now + timedelta(
                seconds=worker.heartbeat_ttl_seconds
            )
            plan_id = work.plan_id
            result = self._serialize_lease(lease)

        await self._event_bus.publish(
            Event(
                event_type="execution_lease.renewed",
                source="execution_distribution",
                correlation_id=plan_id,
                payload={
                    "lease_id": result["id"],
                    "work_item_id": result["work_item_id"],
                    "worker_id": request.worker_id,
                    "fencing_token": request.fencing_token,
                    "expires_at": result["expires_at"],
                },
            )
        )
        return result

    async def complete_lease(
        self,
        lease_token: str,
        request: LeaseCompleteRequest,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._session_factory() as session:
            worker = self._get_worker(session, request.worker_id)
            self._assert_instance(worker, request.instance_id)
            lease, work = self._get_owned_lease(
                session,
                lease_token,
                worker,
                request.fencing_token,
                now,
            )
            work.status = "completed"
            work.result_json = dict(request.result)
            work.error = None
            work.finished_at = now
            work.lease_expires_at = None
            lease.status = "completed"
            lease.released_at = now
            lease.release_reason = "Work item completed."
            worker.active_leases = max(0, worker.active_leases - 1)
            result = self._serialize_work_item(work)

        self._completed += 1
        await self._event_bus.publish(
            Event(
                event_type="execution_dispatch.completed",
                source="execution_distribution",
                workspace_id=result["workspace_id"],
                correlation_id=result["plan_id"],
                payload={
                    "work_item_id": result["id"],
                    "worker_id": request.worker_id,
                    "fencing_token": request.fencing_token,
                },
            )
        )
        return result

    async def fail_lease(
        self,
        lease_token: str,
        request: LeaseFailRequest,
    ) -> dict[str, Any]:
        now = utc_now()
        event_type = "execution_dispatch.failed"
        with self._session_factory() as session:
            worker = self._get_worker(session, request.worker_id)
            self._assert_instance(worker, request.instance_id)
            lease, work = self._get_owned_lease(
                session,
                lease_token,
                worker,
                request.fencing_token,
                now,
            )
            retry = request.retryable and work.attempt_count < work.max_attempts
            lease.status = "failed"
            lease.released_at = now
            lease.release_reason = request.error
            work.worker_id = None
            work.last_lease_token = None
            work.lease_expires_at = None
            work.error = request.error
            if retry:
                work.status = "pending"
                work.available_at = now + timedelta(
                    seconds=request.retry_delay_seconds
                )
                work.finished_at = None
                event_type = "execution_dispatch.requeued"
                self._requeued += 1
            else:
                work.status = "failed"
                work.finished_at = now
                self._failed += 1
            worker.active_leases = max(0, worker.active_leases - 1)
            result = self._serialize_work_item(work)

        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="execution_distribution",
                workspace_id=result["workspace_id"],
                correlation_id=result["plan_id"],
                payload={
                    "work_item_id": result["id"],
                    "worker_id": request.worker_id,
                    "fencing_token": request.fencing_token,
                    "attempt_count": result["attempt_count"],
                    "max_attempts": result["max_attempts"],
                    "error": request.error,
                },
            )
        )
        return result

    async def cancel_work_item(
        self,
        work_item_id: str,
        *,
        actor_id: str,
        reason: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._session_factory() as session:
            work = session.get(ExecutionWorkItemModel, work_item_id)
            if work is None:
                raise WorkItemNotFound("Work item не найден.")
            if work.status in {"completed", "failed", "cancelled"}:
                return self._serialize_work_item(work)
            if work.last_lease_token:
                lease = session.scalar(
                    select(ExecutionLeaseModel).where(
                        ExecutionLeaseModel.lease_token
                        == work.last_lease_token
                    )
                )
                if lease is not None and lease.status in self.ACTIVE_LEASE_STATUSES:
                    lease.status = "released"
                    lease.released_at = now
                    lease.release_reason = reason
            if work.worker_id:
                worker = session.get(ExecutionWorkerModel, work.worker_id)
                if worker is not None:
                    worker.active_leases = max(0, worker.active_leases - 1)
            work.status = "cancelled"
            work.error = reason
            work.finished_at = now
            work.worker_id = None
            work.last_lease_token = None
            work.lease_expires_at = None
            result = self._serialize_work_item(work)

        await self._event_bus.publish(
            Event(
                event_type="execution_dispatch.cancelled",
                source="execution_distribution",
                workspace_id=result["workspace_id"],
                correlation_id=result["plan_id"],
                payload={
                    "work_item_id": work_item_id,
                    "actor_id": actor_id,
                    "reason": reason,
                },
            )
        )
        return result

    async def reconcile(self) -> dict[str, int]:
        now = utc_now()
        stale_workers = 0
        expired_leases = 0
        requeued = 0
        failed = 0
        with self._session_factory() as session:
            workers = list(
                session.scalars(
                    select(ExecutionWorkerModel).where(
                        ExecutionWorkerModel.enabled.is_(True),
                        ExecutionWorkerModel.status.in_(("active", "draining")),
                        ExecutionWorkerModel.expires_at <= now,
                    )
                ).all()
            )
            for worker in workers:
                worker.status = "offline"
                stale_workers += 1

            leases = list(
                session.scalars(
                    select(ExecutionLeaseModel).where(
                        ExecutionLeaseModel.status.in_(
                            tuple(self.ACTIVE_LEASE_STATUSES)
                        ),
                        ExecutionLeaseModel.expires_at <= now,
                    )
                ).all()
            )
            for lease in leases:
                lease.status = "expired"
                lease.released_at = now
                lease.release_reason = "Lease expired."
                expired_leases += 1
                work = session.get(
                    ExecutionWorkItemModel,
                    lease.work_item_id,
                )
                if (
                    work is None
                    or work.status != "leased"
                    or work.last_lease_token != lease.lease_token
                    or work.fencing_token != lease.fencing_token
                ):
                    lease.status = "lost"
                    continue
                work.worker_id = None
                work.last_lease_token = None
                work.lease_expires_at = None
                work.error = "Lease expired before completion."
                if work.attempt_count < work.max_attempts:
                    delay = min(2 ** max(work.attempt_count - 1, 0), 300)
                    work.status = "pending"
                    work.available_at = now + timedelta(seconds=delay)
                    work.finished_at = None
                    requeued += 1
                else:
                    work.status = "failed"
                    work.finished_at = now
                    failed += 1

            for worker in session.scalars(
                select(ExecutionWorkerModel)
            ).all():
                worker.active_leases = self._active_lease_count(
                    session,
                    worker.id,
                    now,
                )

        self._expired += expired_leases
        self._requeued += requeued
        self._failed += failed
        if stale_workers or expired_leases:
            await self._event_bus.publish(
                Event(
                    event_type="execution_distribution.reconciled",
                    source="execution_distribution",
                    payload={
                        "stale_workers": stale_workers,
                        "expired_leases": expired_leases,
                        "requeued": requeued,
                        "failed": failed,
                    },
                )
            )
        return {
            "stale_workers": stale_workers,
            "expired_leases": expired_leases,
            "requeued": requeued,
            "failed": failed,
        }

    def list_workers(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            query = select(ExecutionWorkerModel)
            if status:
                query = query.where(ExecutionWorkerModel.status == status)
            rows = session.scalars(
                query.order_by(ExecutionWorkerModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return [self._serialize_worker(row) for row in rows]

    def get_worker(self, worker_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(ExecutionWorkerModel, worker_id)
            return self._serialize_worker(row) if row else None

    def list_work_items(
        self,
        *,
        status: str | None = None,
        worker_id: str | None = None,
        plan_id: str | None = None,
        queue_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            query = select(ExecutionWorkItemModel)
            if status:
                query = query.where(ExecutionWorkItemModel.status == status)
            if worker_id:
                query = query.where(ExecutionWorkItemModel.worker_id == worker_id)
            if plan_id:
                query = query.where(ExecutionWorkItemModel.plan_id == plan_id)
            if queue_name:
                query = query.where(
                    ExecutionWorkItemModel.queue_name == queue_name
                )
            rows = session.scalars(
                query.order_by(
                    ExecutionWorkItemModel.priority.desc(),
                    ExecutionWorkItemModel.created_at.desc(),
                )
                .offset(offset)
                .limit(limit)
            ).all()
            return [self._serialize_work_item(row) for row in rows]

    def get_work_item(self, work_item_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(ExecutionWorkItemModel, work_item_id)
            return self._serialize_work_item(row) if row else None

    def list_leases(
        self,
        *,
        worker_id: str | None = None,
        work_item_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            query = select(ExecutionLeaseModel)
            if worker_id:
                query = query.where(ExecutionLeaseModel.worker_id == worker_id)
            if work_item_id:
                query = query.where(
                    ExecutionLeaseModel.work_item_id == work_item_id
                )
            if status:
                query = query.where(ExecutionLeaseModel.status == status)
            rows = session.scalars(
                query.order_by(ExecutionLeaseModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return [self._serialize_lease(row) for row in rows]

    def _get_worker(
        self,
        session: Session,
        worker_id: str,
    ) -> ExecutionWorkerModel:
        worker = session.get(ExecutionWorkerModel, worker_id)
        if worker is None:
            raise WorkerNotFound("Execution Worker не найден.")
        return worker

    @staticmethod
    def _assert_instance(
        worker: ExecutionWorkerModel,
        instance_id: str,
    ) -> None:
        if worker.instance_id != instance_id:
            raise WorkerConflict(
                "instance_id не соответствует зарегистрированному Worker."
            )

    @staticmethod
    def _assert_worker_can_claim(
        worker: ExecutionWorkerModel,
        now: datetime,
    ) -> None:
        if not worker.enabled:
            raise WorkerConflict("Worker отключён.")
        if worker.status != "active":
            raise WorkerConflict(
                f"Worker в статусе {worker.status} не получает новую работу."
            )
        if (as_utc(worker.expires_at) or now) <= now:
            raise WorkerConflict(
                "Heartbeat Worker просрочен. Выполните регистрацию или heartbeat."
            )

    def _get_owned_lease(
        self,
        session: Session,
        lease_token: str,
        worker: ExecutionWorkerModel,
        fencing_token: int,
        now: datetime,
    ) -> tuple[ExecutionLeaseModel, ExecutionWorkItemModel]:
        lease = session.scalar(
            select(ExecutionLeaseModel).where(
                ExecutionLeaseModel.lease_token == lease_token
            )
        )
        if lease is None:
            raise LeaseNotFound("Lease не найден.")
        work = session.get(ExecutionWorkItemModel, lease.work_item_id)
        if work is None:
            raise WorkItemNotFound("Work item для Lease не найден.")
        if lease.worker_id != worker.id:
            raise LeaseLost("Lease принадлежит другому Worker.")
        lease_instance_id = str(
            (lease.metadata_json or {}).get("instance_id") or ""
        )
        if lease_instance_id and lease_instance_id != worker.instance_id:
            raise LeaseLost(
                "Lease принадлежит предыдущему instance_id Worker."
            )
        if lease.status not in self.ACTIVE_LEASE_STATUSES:
            raise LeaseLost(f"Lease уже закрыт со статусом {lease.status}.")
        if (as_utc(lease.expires_at) or now) <= now:
            raise LeaseLost("Lease истёк.")
        if lease.fencing_token != fencing_token:
            raise LeaseLost("Fencing token Lease не совпадает.")
        if (
            work.status != "leased"
            or work.worker_id != worker.id
            or work.last_lease_token != lease_token
            or work.fencing_token != fencing_token
        ):
            raise LeaseLost(
                "Worker потерял право завершать Work item: "
                "обнаружен более новый владелец или статус."
            )
        return lease, work

    @staticmethod
    def _active_lease_count(
        session: Session,
        worker_id: str,
        now: datetime,
    ) -> int:
        return int(
            session.scalar(
                select(func.count(ExecutionLeaseModel.id)).where(
                    ExecutionLeaseModel.worker_id == worker_id,
                    ExecutionLeaseModel.status.in_(("active", "renewed")),
                    ExecutionLeaseModel.expires_at > now,
                )
            )
            or 0
        )

    @staticmethod
    def _serialize_worker(row: ExecutionWorkerModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "worker_key": row.worker_key,
            "instance_id": row.instance_id,
            "hostname": row.hostname,
            "process_id": row.process_id,
            "status": row.status,
            "enabled": row.enabled,
            "queues": list(row.queues_json or []),
            "capabilities": list(row.capabilities_json or []),
            "max_concurrency": row.max_concurrency,
            "active_leases": row.active_leases,
            "heartbeat_ttl_seconds": row.heartbeat_ttl_seconds,
            "default_lease_seconds": row.default_lease_seconds,
            "last_heartbeat_at": row.last_heartbeat_at,
            "expires_at": row.expires_at,
            "draining_at": row.draining_at,
            "metadata": dict(row.metadata_json or {}),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _serialize_work_item(row: ExecutionWorkItemModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "plan_id": row.plan_id,
            "work_type": row.work_type,
            "queue_name": row.queue_name,
            "status": row.status,
            "priority": row.priority,
            "payload": dict(row.payload_json or {}),
            "required_capabilities": list(
                row.required_capabilities_json or []
            ),
            "idempotency_key": row.idempotency_key,
            "max_attempts": row.max_attempts,
            "attempt_count": row.attempt_count,
            "available_at": row.available_at,
            "worker_id": row.worker_id,
            "fencing_token": row.fencing_token,
            "lease_expires_at": row.lease_expires_at,
            "result": row.result_json,
            "error": row.error,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
        }

    @staticmethod
    def _serialize_lease(row: ExecutionLeaseModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "work_item_id": row.work_item_id,
            "worker_id": row.worker_id,
            "lease_token": row.lease_token,
            "fencing_token": row.fencing_token,
            "status": row.status,
            "acquired_at": row.acquired_at,
            "heartbeat_at": row.heartbeat_at,
            "expires_at": row.expires_at,
            "released_at": row.released_at,
            "release_reason": row.release_reason,
            "metadata": dict(row.metadata_json or {}),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }


class DistributedExecutionWorker:
    """Optional in-process Worker using the persistent coordinator queue.

    Enable it with AI_STUDIO_DISTRIBUTED_WORKER_ENABLED=1. Separate backend
    processes may use the same database and a unique worker key.
    """

    def __init__(
        self,
        *,
        coordinator: ExecutionDistributionCoordinator,
        runtime: ExecutionPlanRuntime,
        event_bus: EventBus,
        worker_key: str | None = None,
        instance_id: str | None = None,
        queues: list[str] | None = None,
        capabilities: list[str] | None = None,
        max_concurrency: int = 1,
        poll_interval_seconds: float = 1.0,
        heartbeat_ttl_seconds: int = 45,
        lease_seconds: int = 90,
    ) -> None:
        self._coordinator = coordinator
        self._runtime = runtime
        self._event_bus = event_bus
        self.worker_key = worker_key or (
            f"{socket.gethostname()}:{os.getpid()}"
        )
        self.instance_id = instance_id or uuid.uuid4().hex
        self.queues = queues or ["default"]
        self.capabilities = capabilities or ["execution_plan"]
        self.max_concurrency = max(1, max_concurrency)
        self.poll_interval_seconds = max(0.1, poll_interval_seconds)
        self.heartbeat_ttl_seconds = max(5, heartbeat_ttl_seconds)
        self.lease_seconds = max(5, lease_seconds)
        self.worker_id: str | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._active: dict[str, asyncio.Task[None]] = {}
        self._stopping = False

    @property
    def running(self) -> bool:
        return self._loop_task is not None and not self._loop_task.done()

    def stats(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "worker_id": self.worker_id,
            "worker_key": self.worker_key,
            "instance_id": self.instance_id,
            "queues": list(self.queues),
            "capabilities": list(self.capabilities),
            "max_concurrency": self.max_concurrency,
            "active_work_item_ids": sorted(self._active),
        }

    async def start(self) -> None:
        if self.running:
            return
        registered = await self._coordinator.register_worker(
            WorkerRegisterRequest(
                worker_key=self.worker_key,
                instance_id=self.instance_id,
                hostname=socket.gethostname(),
                process_id=os.getpid(),
                queues=self.queues,
                capabilities=self.capabilities,
                max_concurrency=self.max_concurrency,
                heartbeat_ttl_seconds=self.heartbeat_ttl_seconds,
                default_lease_seconds=self.lease_seconds,
                metadata={"mode": "in_process"},
                force_takeover=False,
            )
        )
        self.worker_id = registered["id"]
        self._stopping = False
        self._loop_task = asyncio.create_task(
            self._run_loop(),
            name=f"distributed-worker-{self.worker_key}",
        )

    async def shutdown(self, grace_seconds: float = 10.0) -> None:
        self._stopping = True
        if self.worker_id is not None:
            try:
                await self._coordinator.set_worker_state(
                    self.worker_id,
                    instance_id=self.instance_id,
                    state="draining",
                    reason="Application shutdown.",
                )
            except ExecutionDistributionError:
                pass
        if self._loop_task is not None:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
            self._loop_task = None
        active = list(self._active.values())
        if active:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*active, return_exceptions=True),
                    timeout=max(0.1, grace_seconds),
                )
            except asyncio.TimeoutError:
                for task in active:
                    task.cancel()
                await asyncio.gather(*active, return_exceptions=True)
        self._active.clear()
        if self.worker_id is not None:
            try:
                await self._coordinator.set_worker_state(
                    self.worker_id,
                    instance_id=self.instance_id,
                    state="offline",
                    reason="Application stopped.",
                )
            except ExecutionDistributionError:
                pass

    async def _run_loop(self) -> None:
        assert self.worker_id is not None
        heartbeat_every = max(1.0, self.heartbeat_ttl_seconds / 3)
        next_heartbeat = 0.0
        loop = asyncio.get_running_loop()
        while not self._stopping:
            now = loop.time()
            if now >= next_heartbeat:
                await self._coordinator.heartbeat(
                    self.worker_id,
                    WorkerHeartbeatRequest(
                        instance_id=self.instance_id,
                        status="active",
                    ),
                )
                await self._coordinator.reconcile()
                next_heartbeat = now + heartbeat_every

            self._active = {
                key: task
                for key, task in self._active.items()
                if not task.done()
            }
            while len(self._active) < self.max_concurrency:
                claim = await self._coordinator.claim_next(
                    self.worker_id,
                    WorkerClaimRequest(
                        instance_id=self.instance_id,
                        queue_names=self.queues,
                        lease_seconds=self.lease_seconds,
                    ),
                )
                if claim is None:
                    break
                work_id = claim["work_item"]["id"]
                task = asyncio.create_task(
                    self._execute_claim(claim),
                    name=f"distributed-work-{work_id}",
                )
                self._active[work_id] = task
            await asyncio.sleep(self.poll_interval_seconds)

    async def _execute_claim(self, claim: dict[str, Any]) -> None:
        assert self.worker_id is not None
        work = claim["work_item"]
        lease = claim["lease"]
        plan_id = work.get("plan_id")
        if not plan_id:
            await self._coordinator.fail_lease(
                lease["lease_token"],
                LeaseFailRequest(
                    worker_id=self.worker_id,
                    instance_id=self.instance_id,
                    fencing_token=lease["fencing_token"],
                    error="Work item does not contain plan_id.",
                    retryable=False,
                ),
            )
            return

        run_payload = dict(work.get("payload", {}).get("run_request", {}))
        run_payload["wait"] = False
        run_payload.setdefault("wait_timeout_seconds", 30.0)
        try:
            await self._runtime.recover_interrupted(
                include_plan_ids={plan_id}
            )
            await self._runtime.start(
                plan_id,
                ExecutionPlanRunRequest(**run_payload),
            )
            renew_every = max(1.0, self.lease_seconds / 3)
            while True:
                status = self._runtime.status(plan_id)
                if status is None:
                    raise RuntimeError("Execution Plan disappeared during execution.")
                if status["status"] in {
                    "completed",
                    "failed",
                    "cancelled",
                    "superseded",
                }:
                    break
                await asyncio.sleep(renew_every)
                await self._coordinator.renew_lease(
                    lease["lease_token"],
                    LeaseRenewRequest(
                        worker_id=self.worker_id,
                        instance_id=self.instance_id,
                        fencing_token=lease["fencing_token"],
                        lease_seconds=self.lease_seconds,
                    ),
                )

            status = self._runtime.status(plan_id) or {"status": "unknown"}
            if status["status"] == "completed":
                await self._coordinator.complete_lease(
                    lease["lease_token"],
                    LeaseCompleteRequest(
                        worker_id=self.worker_id,
                        instance_id=self.instance_id,
                        fencing_token=lease["fencing_token"],
                        result={"execution_plan": status},
                    ),
                )
            else:
                await self._coordinator.fail_lease(
                    lease["lease_token"],
                    LeaseFailRequest(
                        worker_id=self.worker_id,
                        instance_id=self.instance_id,
                        fencing_token=lease["fencing_token"],
                        error=(
                            "Execution Plan finished with status "
                            f"{status['status']}."
                        ),
                        retryable=status["status"] == "failed",
                    ),
                )
        except LeaseLost:
            try:
                await self._runtime.cancel(
                    plan_id,
                    "Distributed lease lost; stale Worker execution fenced.",
                )
            except Exception:
                pass
        except asyncio.CancelledError:
            try:
                await self._runtime.cancel(
                    plan_id,
                    "Distributed Worker shutdown.",
                )
            except Exception:
                pass
            raise
        except Exception as exc:
            try:
                await self._coordinator.fail_lease(
                    lease["lease_token"],
                    LeaseFailRequest(
                        worker_id=self.worker_id,
                        instance_id=self.instance_id,
                        fencing_token=lease["fencing_token"],
                        error=str(exc),
                        retryable=True,
                    ),
                )
            except ExecutionDistributionError:
                pass
