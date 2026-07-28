from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_container
from backend.core.container import AppContainer
from backend.orchestration.distributed import (
    ExecutionDistributionCoordinator,
    ExecutionDistributionError,
    LeaseLost,
    LeaseNotFound,
    WorkerConflict,
    WorkerNotFound,
    WorkItemNotFound,
)
from backend.orchestration.distributed_schemas import (
    LeaseCompleteRequest,
    LeaseFailRequest,
    LeaseRenewRequest,
    WorkItemCancelRequest,
    WorkItemDispatchRequest,
    WorkerClaimRequest,
    WorkerHeartbeatRequest,
    WorkerRegisterRequest,
    WorkerStateRequest,
)

router = APIRouter(tags=["execution-distribution"])


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


def translate_error(exc: ExecutionDistributionError) -> HTTPException:
    if isinstance(
        exc,
        (WorkerNotFound, WorkItemNotFound, LeaseNotFound),
    ):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (WorkerConflict, LeaseLost)):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/execution-distribution/status")
def distribution_status(
    container: AppContainer = Depends(get_container),
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    result = coordinator.stats()
    result["local_worker"] = (
        container.execution_distributed_worker.stats()
        if container.execution_distributed_worker is not None
        else {"running": False, "enabled": False}
    )
    return result


@router.post("/execution-workers/register")
async def register_worker(
    request: WorkerRegisterRequest,
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    try:
        return await coordinator.register_worker(request)
    except ExecutionDistributionError as exc:
        raise translate_error(exc) from exc


@router.get("/execution-workers")
def list_workers(
    status: str | None = Query(
        default=None,
        pattern="^(active|draining|offline|unhealthy)$",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    return {
        "workers": coordinator.list_workers(
            status=status,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/execution-workers/{worker_id}")
def get_worker(
    worker_id: str,
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    worker = coordinator.get_worker(worker_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="Execution Worker not found.")
    return worker


@router.post("/execution-workers/{worker_id}/heartbeat")
async def heartbeat_worker(
    worker_id: str,
    request: WorkerHeartbeatRequest,
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    try:
        return await coordinator.heartbeat(worker_id, request)
    except ExecutionDistributionError as exc:
        raise translate_error(exc) from exc


@router.post("/execution-workers/{worker_id}/drain")
async def drain_worker(
    worker_id: str,
    request: WorkerStateRequest,
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    try:
        return await coordinator.set_worker_state(
            worker_id,
            instance_id=request.instance_id,
            state="draining",
            reason=request.reason,
        )
    except ExecutionDistributionError as exc:
        raise translate_error(exc) from exc


@router.post("/execution-workers/{worker_id}/offline")
async def offline_worker(
    worker_id: str,
    request: WorkerStateRequest,
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    try:
        return await coordinator.set_worker_state(
            worker_id,
            instance_id=request.instance_id,
            state="offline",
            reason=request.reason,
        )
    except ExecutionDistributionError as exc:
        raise translate_error(exc) from exc


@router.post("/execution-workers/{worker_id}/claim")
async def claim_work(
    worker_id: str,
    request: WorkerClaimRequest,
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    try:
        claim = await coordinator.claim_next(worker_id, request)
    except ExecutionDistributionError as exc:
        raise translate_error(exc) from exc
    return {"claim": claim}


@router.post("/execution-plans/{plan_id}/dispatch")
async def dispatch_plan(
    plan_id: str,
    request: WorkItemDispatchRequest,
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    if request.plan_id != plan_id:
        raise HTTPException(
            status_code=422,
            detail="plan_id in path and request body must match.",
        )
    try:
        return await coordinator.dispatch_plan(request)
    except ExecutionDistributionError as exc:
        raise translate_error(exc) from exc


@router.post("/execution-work-items")
async def create_work_item(
    request: WorkItemDispatchRequest,
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    try:
        return await coordinator.dispatch_plan(request)
    except ExecutionDistributionError as exc:
        raise translate_error(exc) from exc


@router.get("/execution-work-items")
def list_work_items(
    status: str | None = Query(
        default=None,
        pattern="^(pending|leased|completed|failed|cancelled)$",
    ),
    worker_id: str | None = None,
    plan_id: str | None = None,
    queue_name: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    return {
        "work_items": coordinator.list_work_items(
            status=status,
            worker_id=worker_id,
            plan_id=plan_id,
            queue_name=queue_name,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/execution-work-items/{work_item_id}")
def get_work_item(
    work_item_id: str,
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    item = coordinator.get_work_item(work_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Work item not found.")
    return item


@router.post("/execution-work-items/{work_item_id}/cancel")
async def cancel_work_item(
    work_item_id: str,
    request: WorkItemCancelRequest,
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    try:
        return await coordinator.cancel_work_item(
            work_item_id,
            actor_id=request.actor_id,
            reason=request.reason,
        )
    except ExecutionDistributionError as exc:
        raise translate_error(exc) from exc


@router.get("/execution-leases")
def list_leases(
    worker_id: str | None = None,
    work_item_id: str | None = None,
    status: str | None = Query(
        default=None,
        pattern="^(active|renewed|completed|failed|released|expired|lost)$",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    return {
        "leases": coordinator.list_leases(
            worker_id=worker_id,
            work_item_id=work_item_id,
            status=status,
            limit=limit,
            offset=offset,
        )
    }


@router.post("/execution-leases/{lease_token}/renew")
async def renew_lease(
    lease_token: str,
    request: LeaseRenewRequest,
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    try:
        return await coordinator.renew_lease(lease_token, request)
    except ExecutionDistributionError as exc:
        raise translate_error(exc) from exc


@router.post("/execution-leases/{lease_token}/complete")
async def complete_lease(
    lease_token: str,
    request: LeaseCompleteRequest,
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    try:
        return await coordinator.complete_lease(lease_token, request)
    except ExecutionDistributionError as exc:
        raise translate_error(exc) from exc


@router.post("/execution-leases/{lease_token}/fail")
async def fail_lease(
    lease_token: str,
    request: LeaseFailRequest,
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, Any]:
    try:
        return await coordinator.fail_lease(lease_token, request)
    except ExecutionDistributionError as exc:
        raise translate_error(exc) from exc


@router.post("/execution-distribution/reconcile")
async def reconcile_distribution(
    coordinator: ExecutionDistributionCoordinator = Depends(get_coordinator),
) -> dict[str, int]:
    return await coordinator.reconcile()
