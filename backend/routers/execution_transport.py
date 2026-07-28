from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_container
from backend.core.container import AppContainer
from backend.orchestration.distributed import (
    ExecutionDistributionCoordinator,
    ExecutionDistributionError,
)
from backend.orchestration.distributed_schemas import (
    LeaseCompleteRequest,
    LeaseFailRequest,
    LeaseRenewRequest,
    WorkerClaimRequest,
)
from backend.orchestration.transport import (
    ExecutionEventTransport,
    ExecutionTransportError,
    RemoteProtocolUnsupported,
    RemoteSessionNotFound,
    RemoteSessionUnauthorized,
    TransportEventNotFound,
    TransportLeaseLost,
)
from backend.orchestration.transport_schemas import (
    RemoteSessionCloseRequest,
    RemoteSessionHeartbeatRequest,
    RemoteSessionOpenRequest,
    RemoteWorkClaimRequest,
    RemoteWorkLeaseCompleteRequest,
    RemoteWorkLeaseFailRequest,
    RemoteWorkLeaseRenewRequest,
    TransportEventAckRequest,
    TransportEventClaimRequest,
    TransportEventNackRequest,
    TransportEventPublishRequest,
    TransportEventReplayRequest,
    TransportLeaseRenewRequest,
)

router = APIRouter(tags=["execution-transport"])


def get_transport(
    container: AppContainer = Depends(get_container),
) -> ExecutionEventTransport:
    transport = container.execution_event_transport
    if transport is None:
        raise HTTPException(
            status_code=503,
            detail="Execution Event Transport is not running.",
        )
    return transport


def get_coordinator(
    container: AppContainer = Depends(get_container),
) -> ExecutionDistributionCoordinator:
    coordinator = container.execution_distribution_coordinator
    if coordinator is None:
        raise HTTPException(
            status_code=503,
            detail="Execution Distribution service is not running.",
        )
    return coordinator


def translate_error(exc: ExecutionTransportError) -> HTTPException:
    if isinstance(exc, (RemoteSessionNotFound, TransportEventNotFound)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(
        exc,
        (
            RemoteSessionUnauthorized,
            RemoteProtocolUnsupported,
            TransportLeaseLost,
        ),
    ):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/execution-transport/status")
def transport_status(
    transport: ExecutionEventTransport = Depends(get_transport),
) -> dict[str, Any]:
    return {
        **transport.stats(),
        "protocol": transport.protocol_info(),
    }


@router.get("/execution-transport/protocol")
def transport_protocol(
    transport: ExecutionEventTransport = Depends(get_transport),
) -> dict[str, Any]:
    return transport.protocol_info()


@router.get("/execution-transport/verify")
def verify_transport(
    transport: ExecutionEventTransport = Depends(get_transport),
) -> dict[str, Any]:
    return transport.verify()


@router.post("/execution-transport/reconcile")
async def reconcile_transport(
    transport: ExecutionEventTransport = Depends(get_transport),
) -> dict[str, int]:
    return await transport.reconcile()


@router.post("/execution-transport/sessions/open")
async def open_remote_session(
    request: RemoteSessionOpenRequest,
    transport: ExecutionEventTransport = Depends(get_transport),
) -> dict[str, Any]:
    try:
        return await transport.open_session(request)
    except ExecutionTransportError as exc:
        raise translate_error(exc) from exc


@router.post("/execution-transport/sessions/{session_id}/heartbeat")
async def heartbeat_remote_session(
    session_id: str,
    request: RemoteSessionHeartbeatRequest,
    transport: ExecutionEventTransport = Depends(get_transport),
) -> dict[str, Any]:
    try:
        return await transport.heartbeat_session(session_id, request)
    except ExecutionTransportError as exc:
        raise translate_error(exc) from exc


@router.post("/execution-transport/sessions/{session_id}/close")
async def close_remote_session(
    session_id: str,
    request: RemoteSessionCloseRequest,
    transport: ExecutionEventTransport = Depends(get_transport),
) -> dict[str, Any]:
    try:
        return await transport.close_session(session_id, request)
    except ExecutionTransportError as exc:
        raise translate_error(exc) from exc


@router.post(
    "/execution-transport/sessions/{session_id}/claim-work"
)
async def claim_remote_work(
    session_id: str,
    request: RemoteWorkClaimRequest,
    transport: ExecutionEventTransport = Depends(get_transport),
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    try:
        identity = transport.authenticate_session(
            session_id,
            request.session_token,
        )
        claim = await coordinator.claim_next(
            identity["worker_id"],
            WorkerClaimRequest(
                instance_id=identity["instance_id"],
                queue_names=request.queues or None,
                lease_seconds=request.lease_seconds,
            ),
        )
        if claim is None:
            return {"claim": None}
        await transport.publish(
            TransportEventPublishRequest(
                topic="remote-work",
                event_type="execution_remote.work.assigned",
                payload=claim,
                target_worker_id=identity["worker_id"],
                idempotency_key=(
                    f"remote-work-assignment:{claim['lease']['id']}"
                ),
                priority=100,
                max_attempts=10,
            ),
            trusted_internal=True,
        )
        return {"claim": claim}
    except ExecutionTransportError as exc:
        raise translate_error(exc) from exc
    except ExecutionDistributionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/execution-transport/work-leases/{lease_token}/renew")
async def renew_remote_work_lease(
    lease_token: str,
    request: RemoteWorkLeaseRenewRequest,
    transport: ExecutionEventTransport = Depends(get_transport),
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    try:
        identity = transport.authenticate_session(
            request.session_id,
            request.session_token,
        )
        return await coordinator.renew_lease(
            lease_token,
            LeaseRenewRequest(
                worker_id=identity["worker_id"],
                instance_id=identity["instance_id"],
                fencing_token=request.fencing_token,
                lease_seconds=request.lease_seconds,
            ),
        )
    except ExecutionTransportError as exc:
        raise translate_error(exc) from exc
    except ExecutionDistributionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/execution-transport/work-leases/{lease_token}/complete")
async def complete_remote_work_lease(
    lease_token: str,
    request: RemoteWorkLeaseCompleteRequest,
    transport: ExecutionEventTransport = Depends(get_transport),
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    try:
        identity = transport.authenticate_session(
            request.session_id,
            request.session_token,
        )
        return await coordinator.complete_lease(
            lease_token,
            LeaseCompleteRequest(
                worker_id=identity["worker_id"],
                instance_id=identity["instance_id"],
                fencing_token=request.fencing_token,
                result=request.result,
            ),
        )
    except ExecutionTransportError as exc:
        raise translate_error(exc) from exc
    except ExecutionDistributionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/execution-transport/work-leases/{lease_token}/fail")
async def fail_remote_work_lease(
    lease_token: str,
    request: RemoteWorkLeaseFailRequest,
    transport: ExecutionEventTransport = Depends(get_transport),
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    try:
        identity = transport.authenticate_session(
            request.session_id,
            request.session_token,
        )
        return await coordinator.fail_lease(
            lease_token,
            LeaseFailRequest(
                worker_id=identity["worker_id"],
                instance_id=identity["instance_id"],
                fencing_token=request.fencing_token,
                error=request.error,
                retryable=request.retryable,
                retry_delay_seconds=request.retry_delay_seconds,
            ),
        )
    except ExecutionTransportError as exc:
        raise translate_error(exc) from exc
    except ExecutionDistributionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/execution-transport/sessions")
def list_remote_sessions(
    status: str | None = Query(
        default=None,
        pattern="^(active|closed|expired|revoked)$",
    ),
    worker_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    transport: ExecutionEventTransport = Depends(get_transport),
) -> dict[str, Any]:
    return {
        "sessions": transport.list_sessions(
            status=status,
            worker_id=worker_id,
            limit=limit,
            offset=offset,
        )
    }


@router.post("/execution-transport/events")
async def publish_transport_event(
    request: TransportEventPublishRequest,
    transport: ExecutionEventTransport = Depends(get_transport),
) -> dict[str, Any]:
    if not request.session_id or not request.session_token:
        raise HTTPException(
            status_code=403,
            detail="Remote session credentials are required.",
        )
    try:
        return await transport.publish(request)
    except ExecutionTransportError as exc:
        raise translate_error(exc) from exc


@router.post("/execution-transport/events/claim")
async def claim_transport_events(
    request: TransportEventClaimRequest,
    transport: ExecutionEventTransport = Depends(get_transport),
) -> dict[str, Any]:
    try:
        return {"claims": await transport.claim(request)}
    except ExecutionTransportError as exc:
        raise translate_error(exc) from exc


@router.get("/execution-transport/events")
def list_transport_events(
    status: str | None = Query(
        default=None,
        pattern="^(pending|leased|acknowledged|dead|cancelled)$",
    ),
    topic: str | None = None,
    target_worker_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    transport: ExecutionEventTransport = Depends(get_transport),
) -> dict[str, Any]:
    return {
        "events": transport.list_events(
            status=status,
            topic=topic,
            target_worker_id=target_worker_id,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/execution-transport/events/{envelope_id}")
def get_transport_event(
    envelope_id: str,
    transport: ExecutionEventTransport = Depends(get_transport),
) -> dict[str, Any]:
    result = transport.get_event(envelope_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Transport event not found.")
    return result


@router.post("/execution-transport/events/{envelope_id}/lease/renew")
async def renew_transport_event_lease(
    envelope_id: str,
    request: TransportLeaseRenewRequest,
    transport: ExecutionEventTransport = Depends(get_transport),
) -> dict[str, Any]:
    try:
        return await transport.renew_event_lease(envelope_id, request)
    except ExecutionTransportError as exc:
        raise translate_error(exc) from exc


@router.post("/execution-transport/events/{envelope_id}/ack")
async def acknowledge_transport_event(
    envelope_id: str,
    request: TransportEventAckRequest,
    transport: ExecutionEventTransport = Depends(get_transport),
) -> dict[str, Any]:
    try:
        return await transport.acknowledge(envelope_id, request)
    except ExecutionTransportError as exc:
        raise translate_error(exc) from exc


@router.post("/execution-transport/events/{envelope_id}/nack")
async def reject_transport_event(
    envelope_id: str,
    request: TransportEventNackRequest,
    transport: ExecutionEventTransport = Depends(get_transport),
) -> dict[str, Any]:
    try:
        return await transport.reject(envelope_id, request)
    except ExecutionTransportError as exc:
        raise translate_error(exc) from exc


@router.post("/execution-transport/events/{envelope_id}/replay")
async def replay_transport_event(
    envelope_id: str,
    request: TransportEventReplayRequest,
    transport: ExecutionEventTransport = Depends(get_transport),
) -> dict[str, Any]:
    try:
        return await transport.replay_dead_letter(envelope_id, request)
    except ExecutionTransportError as exc:
        raise translate_error(exc) from exc
