from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.api.dependencies import get_container
from backend.autonomy.schedule import (
    MissionDeadlineEventNotFound,
    MissionScheduleService,
    MissionScheduleWindowNotFound,
)
from backend.autonomy.schedule_schemas import (
    MissionDeadlineResolveRequest,
    MissionDeadlineScanRequest,
    MissionScheduleEvaluationRequest,
    MissionSchedulePolicyUpsert,
    MissionScheduleWindowCreate,
    MissionScheduleWindowUpdate,
)
from backend.autonomy.service import (
    AutonomousMissionError,
    MissionCycleNotFound,
    MissionNotFound,
    MissionStateError,
)
from backend.core.container import AppContainer


router = APIRouter(tags=["Mission Schedules"])


def get_service(
    container: AppContainer = Depends(get_container),
) -> MissionScheduleService:
    service = container.mission_schedule_service
    if service is None:
        raise HTTPException(status_code=503, detail="Mission Schedule Service is not running.")
    return service


def translate_error(exc: AutonomousMissionError) -> HTTPException:
    if isinstance(
        exc,
        (
            MissionNotFound,
            MissionCycleNotFound,
            MissionScheduleWindowNotFound,
            MissionDeadlineEventNotFound,
        ),
    ):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, MissionStateError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/mission-schedules/status")
def schedule_status(
    service: MissionScheduleService = Depends(get_service),
) -> dict[str, Any]:
    return service.status()


@router.get("/missions/{mission_id}/schedule-policy")
def get_schedule_policy(
    mission_id: str,
    service: MissionScheduleService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.get_policy(mission_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.put("/missions/{mission_id}/schedule-policy")
async def upsert_schedule_policy(
    mission_id: str,
    request: MissionSchedulePolicyUpsert,
    service: MissionScheduleService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.upsert_policy(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/schedule-dashboard")
def schedule_dashboard(
    mission_id: str,
    service: MissionScheduleService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.dashboard(mission_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/schedule-context")
def schedule_context(
    mission_id: str,
    cycle_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    service: MissionScheduleService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.build_context(mission_id, cycle_id=cycle_id, limit=limit)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/missions/{mission_id}/schedule-evaluate")
async def evaluate_schedule(
    mission_id: str,
    request: MissionScheduleEvaluationRequest,
    service: MissionScheduleService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.evaluate(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post(
    "/missions/{mission_id}/schedule-windows",
    status_code=status.HTTP_201_CREATED,
)
async def create_schedule_window(
    mission_id: str,
    request: MissionScheduleWindowCreate,
    service: MissionScheduleService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.create_window(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/schedule-windows")
def list_schedule_windows(
    mission_id: str,
    service: MissionScheduleService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return {"windows": service.list_windows(mission_id)}
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.patch("/mission-schedule-windows/{window_id}")
async def update_schedule_window(
    window_id: str,
    request: MissionScheduleWindowUpdate,
    service: MissionScheduleService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.update_window(window_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.delete("/mission-schedule-windows/{window_id}")
async def delete_schedule_window(
    window_id: str,
    service: MissionScheduleService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.delete_window(window_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/schedule-evaluations")
def list_schedule_evaluations(
    mission_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionScheduleService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return {
            "evaluations": service.list_evaluations(
                mission_id,
                limit=limit,
                offset=offset,
            )
        }
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-schedules/deadlines/scan")
async def scan_deadlines(
    request: MissionDeadlineScanRequest,
    service: MissionScheduleService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.scan_deadlines(
            workspace_id=request.workspace_id,
            limit=request.limit,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/deadline-events")
def list_deadline_events(
    mission_id: str,
    open_only: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionScheduleService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return {
            "deadline_events": service.list_deadline_events(
                mission_id,
                open_only=open_only,
                limit=limit,
                offset=offset,
            )
        }
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-deadline-events/{event_id}/resolve")
async def resolve_deadline_event(
    event_id: str,
    request: MissionDeadlineResolveRequest,
    service: MissionScheduleService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.resolve_deadline_event(event_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
