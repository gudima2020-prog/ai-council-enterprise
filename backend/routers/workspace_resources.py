from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.api.dependencies import get_container
from backend.autonomy.service import (
    AutonomousMissionError,
    MissionCycleNotFound,
    MissionNotFound,
    MissionStateError,
)
from backend.autonomy.workspace_resources import (
    WorkspaceResourceConflictNotFound,
    WorkspaceResourceCoordinator,
    WorkspaceResourceRebalanceNotFound,
    WorkspaceResourceReservationNotFound,
)
from backend.autonomy.workspace_resources_schemas import (
    WorkspaceResourceConflictResolution,
    WorkspaceResourcePolicyUpsert,
    WorkspaceResourceRebalanceDecision,
    WorkspaceResourceRebalanceRequest,
    WorkspaceResourceReservationCreate,
    WorkspaceResourceReservationDecision,
)
from backend.core.container import AppContainer


router = APIRouter(tags=["Workspace Mission Resources"])


def get_service(
    container: AppContainer = Depends(get_container),
) -> WorkspaceResourceCoordinator:
    service = container.workspace_resource_coordinator
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Workspace Resource Coordinator не запущен.",
        )
    return service


def translate_error(exc: AutonomousMissionError) -> HTTPException:
    if isinstance(
        exc,
        (
            MissionNotFound,
            MissionCycleNotFound,
            WorkspaceResourceReservationNotFound,
            WorkspaceResourceConflictNotFound,
            WorkspaceResourceRebalanceNotFound,
        ),
    ):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, MissionStateError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/workspace-resources/status")
def workspace_resource_status(
    service: WorkspaceResourceCoordinator = Depends(get_service),
) -> dict[str, Any]:
    return service.status()


@router.get("/workspaces/{workspace_id}/resource-pool/policy")
def get_policy(
    workspace_id: str,
    service: WorkspaceResourceCoordinator = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.get_policy(workspace_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.put("/workspaces/{workspace_id}/resource-pool/policy")
async def upsert_policy(
    workspace_id: str,
    request: WorkspaceResourcePolicyUpsert,
    service: WorkspaceResourceCoordinator = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.upsert_policy(workspace_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/workspaces/{workspace_id}/resource-pool/dashboard")
def dashboard(
    workspace_id: str,
    service: WorkspaceResourceCoordinator = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.dashboard(workspace_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/workspace-resource-context")
def mission_context(
    mission_id: str,
    cycle_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    service: WorkspaceResourceCoordinator = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.build_context(mission_id, cycle_id=cycle_id, limit=limit)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post(
    "/mission-cycles/{cycle_id}/workspace-resource-reservation",
    status_code=status.HTTP_201_CREATED,
)
async def create_reservation(
    cycle_id: str,
    request: WorkspaceResourceReservationCreate,
    service: WorkspaceResourceCoordinator = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.create_reservation(cycle_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/workspaces/{workspace_id}/resource-reservations")
def list_reservations(
    workspace_id: str,
    mission_id: str | None = None,
    reservation_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(pending_approval|reserved|active|released|consumed|exceeded|cancelled)$",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: WorkspaceResourceCoordinator = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_reservations(
            workspace_id,
            mission_id=mission_id,
            status=reservation_status,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"reservations": rows}


@router.get("/workspace-resource-reservations/{reservation_id}")
def get_reservation(
    reservation_id: str,
    service: WorkspaceResourceCoordinator = Depends(get_service),
) -> dict[str, Any]:
    row = service.get_reservation(reservation_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Workspace Resource Reservation не найден.",
        )
    return row


@router.post("/workspace-resource-reservations/{reservation_id}/approve")
async def approve_reservation(
    reservation_id: str,
    request: WorkspaceResourceReservationDecision,
    service: WorkspaceResourceCoordinator = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.approve_reservation(reservation_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/workspace-resource-reservations/{reservation_id}/release")
async def release_reservation(
    reservation_id: str,
    request: WorkspaceResourceReservationDecision,
    service: WorkspaceResourceCoordinator = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.release_reservation(reservation_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/workspaces/{workspace_id}/resource-conflicts")
def list_conflicts(
    workspace_id: str,
    conflict_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(open|resolved|waived|cancelled)$",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: WorkspaceResourceCoordinator = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_conflicts(
            workspace_id,
            status=conflict_status,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"conflicts": rows}


@router.get("/workspace-resource-conflicts/{conflict_id}")
def get_conflict(
    conflict_id: str,
    service: WorkspaceResourceCoordinator = Depends(get_service),
) -> dict[str, Any]:
    row = service.get_conflict(conflict_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Workspace Resource Conflict не найден.",
        )
    return row


@router.post("/workspace-resource-conflicts/{conflict_id}/resolve")
async def resolve_conflict(
    conflict_id: str,
    request: WorkspaceResourceConflictResolution,
    service: WorkspaceResourceCoordinator = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.resolve_conflict(conflict_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/workspaces/{workspace_id}/resource-pool/rebalance")
async def rebalance(
    workspace_id: str,
    request: WorkspaceResourceRebalanceRequest,
    service: WorkspaceResourceCoordinator = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.rebalance(workspace_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/workspaces/{workspace_id}/resource-pool/rebalances")
def list_rebalances(
    workspace_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: WorkspaceResourceCoordinator = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_rebalances(
            workspace_id,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"rebalances": rows}


@router.post("/workspace-resource-rebalances/{rebalance_id}/apply")
async def apply_rebalance(
    rebalance_id: str,
    request: WorkspaceResourceRebalanceDecision,
    service: WorkspaceResourceCoordinator = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.decide_rebalance(
            rebalance_id,
            request,
            apply=True,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/workspace-resource-rebalances/{rebalance_id}/reject")
async def reject_rebalance(
    rebalance_id: str,
    request: WorkspaceResourceRebalanceDecision,
    service: WorkspaceResourceCoordinator = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.decide_rebalance(
            rebalance_id,
            request,
            apply=False,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
