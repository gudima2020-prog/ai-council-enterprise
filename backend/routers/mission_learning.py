from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.api.dependencies import get_container
from backend.autonomy.learning import (
    MissionLearningNotFound,
    MissionLearningService,
    MissionLearningStateError,
)
from backend.autonomy.learning_schemas import (
    MissionForecastOutcomeResolveRequest,
    MissionLearningPolicyUpsert,
    MissionLearningReconcileRequest,
    MissionLearningRunDecisionRequest,
    MissionLearningRunRequest,
)
from backend.autonomy.service import AutonomousMissionError
from backend.core.container import AppContainer


router = APIRouter(tags=["Mission Learning"])


def get_service(
    container: AppContainer = Depends(get_container),
) -> MissionLearningService:
    service = container.mission_learning_service
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Mission Learning Service is not running.",
        )
    return service


def translate_error(exc: AutonomousMissionError) -> HTTPException:
    if isinstance(exc, MissionLearningNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, MissionLearningStateError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/mission-learning/status")
def learning_status(
    service: MissionLearningService = Depends(get_service),
) -> dict[str, Any]:
    return service.status()


@router.get("/mission-learning/verify")
def verify_learning_integrity(
    service: MissionLearningService = Depends(get_service),
) -> dict[str, Any]:
    return service.verify_integrity()


@router.get("/missions/{mission_id}/learning-policy")
def get_learning_policy(
    mission_id: str,
    service: MissionLearningService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.get_policy(mission_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.put("/missions/{mission_id}/learning-policy")
async def upsert_learning_policy(
    mission_id: str,
    request: MissionLearningPolicyUpsert,
    service: MissionLearningService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.upsert_policy(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/learning-dashboard")
def learning_dashboard(
    mission_id: str,
    service: MissionLearningService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.dashboard(mission_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/learning-context")
def learning_context(
    mission_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    service: MissionLearningService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.build_context(mission_id, limit=limit)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post(
    "/missions/{mission_id}/forecast-outcomes/resolve",
    status_code=status.HTTP_201_CREATED,
)
async def resolve_forecast_outcome(
    mission_id: str,
    request: MissionForecastOutcomeResolveRequest,
    service: MissionLearningService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.resolve_manual_outcome(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/forecast-outcomes")
def list_forecast_outcomes(
    mission_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: MissionLearningService = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_outcomes(
            mission_id,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"outcomes": rows}


@router.post(
    "/missions/{mission_id}/learning-runs",
    status_code=status.HTTP_201_CREATED,
)
async def create_learning_run(
    mission_id: str,
    request: MissionLearningRunRequest,
    service: MissionLearningService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.run_learning(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/learning-runs")
def list_learning_runs(
    mission_id: str,
    run_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(proposed|applied|rejected|failed)$",
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: MissionLearningService = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_runs(
            mission_id,
            status=run_status,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"learning_runs": rows}


@router.get("/mission-learning-runs/{run_id}")
def get_learning_run(
    run_id: str,
    service: MissionLearningService = Depends(get_service),
) -> dict[str, Any]:
    row = service.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Mission Learning Run not found.")
    return row


@router.post("/mission-learning-runs/{run_id}/apply")
async def apply_learning_run(
    run_id: str,
    request: MissionLearningRunDecisionRequest,
    service: MissionLearningService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.apply_run(run_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-learning-runs/{run_id}/reject")
async def reject_learning_run(
    run_id: str,
    request: MissionLearningRunDecisionRequest,
    service: MissionLearningService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.reject_run(run_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/forecast-calibrations")
def list_forecast_calibrations(
    mission_id: str,
    calibration_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(proposed|current|superseded|rejected)$",
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: MissionLearningService = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_calibrations(
            mission_id,
            status=calibration_status,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"calibrations": rows}


@router.get("/mission-forecast-calibrations/{calibration_id}")
def get_forecast_calibration(
    calibration_id: str,
    service: MissionLearningService = Depends(get_service),
) -> dict[str, Any]:
    row = service.get_calibration(calibration_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Mission Forecast Calibration not found.",
        )
    return row


@router.post("/mission-learning/reconcile")
async def reconcile_learning(
    request: MissionLearningReconcileRequest,
    service: MissionLearningService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.reconcile(request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
