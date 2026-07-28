from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.api.dependencies import get_container
from backend.autonomy.resources import (
    MissionCapacityPlanNotFound,
    MissionResourceAllocationNotFound,
    MissionResourceService,
)
from backend.autonomy.resources_schemas import (
    MissionCapacityPlanDecision,
    MissionCapacityPlanRequest,
    MissionResourceAllocationApprove,
    MissionResourceAllocationCancel,
    MissionResourceAllocationCreate,
    MissionResourcePolicyUpsert,
    MissionResourceUsageCreate,
)
from backend.autonomy.service import (
    AutonomousMissionError,
    MissionCycleNotFound,
    MissionNotFound,
    MissionStateError,
)
from backend.core.container import AppContainer


router = APIRouter(tags=["Mission Resources"])


def get_service(
    container: AppContainer = Depends(get_container),
) -> MissionResourceService:
    service = container.mission_resource_service
    if service is None:
        raise HTTPException(status_code=503, detail="Mission Resource Service не запущен.")
    return service


def translate_error(exc: AutonomousMissionError) -> HTTPException:
    if isinstance(
        exc,
        (
            MissionNotFound,
            MissionCycleNotFound,
            MissionResourceAllocationNotFound,
            MissionCapacityPlanNotFound,
        ),
    ):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, MissionStateError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/mission-resources/status")
def resource_status(
    service: MissionResourceService = Depends(get_service),
) -> dict[str, Any]:
    return service.status()


@router.get("/missions/{mission_id}/resource-policy")
def get_resource_policy(
    mission_id: str,
    service: MissionResourceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.get_policy(mission_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.put("/missions/{mission_id}/resource-policy")
async def upsert_resource_policy(
    mission_id: str,
    request: MissionResourcePolicyUpsert,
    service: MissionResourceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.upsert_policy(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/resource-dashboard")
def resource_dashboard(
    mission_id: str,
    service: MissionResourceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.dashboard(mission_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/resource-context")
def resource_context(
    mission_id: str,
    cycle_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    service: MissionResourceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.build_context(mission_id, cycle_id=cycle_id, limit=limit)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post(
    "/mission-cycles/{cycle_id}/resource-allocation",
    status_code=status.HTTP_201_CREATED,
)
async def create_allocation(
    cycle_id: str,
    request: MissionResourceAllocationCreate,
    service: MissionResourceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.create_allocation(cycle_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/resource-allocations")
def list_allocations(
    mission_id: str,
    allocation_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(pending_approval|approved|reserved|active|released|consumed|exceeded|cancelled)$",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionResourceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_allocations(
            mission_id,
            status=allocation_status,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"allocations": rows}


@router.get("/mission-resource-allocations/{allocation_id}")
def get_allocation(
    allocation_id: str,
    service: MissionResourceService = Depends(get_service),
) -> dict[str, Any]:
    row = service.get_allocation(allocation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Mission Resource Allocation не найден.")
    return row


@router.post("/mission-resource-allocations/{allocation_id}/approve")
async def approve_allocation(
    allocation_id: str,
    request: MissionResourceAllocationApprove,
    service: MissionResourceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.approve_allocation(allocation_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-resource-allocations/{allocation_id}/cancel")
async def cancel_allocation(
    allocation_id: str,
    request: MissionResourceAllocationCancel,
    service: MissionResourceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.cancel_allocation(allocation_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-resource-allocations/{allocation_id}/usage")
async def record_usage(
    allocation_id: str,
    request: MissionResourceUsageCreate,
    service: MissionResourceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.record_usage(allocation_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/resource-usage")
def list_usage(
    mission_id: str,
    allocation_id: str | None = None,
    category: str | None = Query(
        default=None,
        pattern="^(llm|tool|compute|storage|network|human|other)$",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionResourceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_usage(
            mission_id,
            allocation_id=allocation_id,
            category=category,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"usage": rows}


@router.post("/missions/{mission_id}/capacity-plans")
async def create_capacity_plan(
    mission_id: str,
    request: MissionCapacityPlanRequest,
    service: MissionResourceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.create_capacity_plan(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/capacity-plans")
def list_capacity_plans(
    mission_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionResourceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_capacity_plans(mission_id, limit=limit, offset=offset)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"capacity_plans": rows}


@router.post("/mission-capacity-plans/{plan_id}/apply")
async def apply_capacity_plan(
    plan_id: str,
    request: MissionCapacityPlanDecision,
    service: MissionResourceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.apply_capacity_plan(plan_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-capacity-plans/{plan_id}/reject")
async def reject_capacity_plan(
    plan_id: str,
    request: MissionCapacityPlanDecision,
    service: MissionResourceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.reject_capacity_plan(plan_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
