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
from backend.autonomy.strategy import (
    MissionStrategyAssignmentNotFound,
    MissionStrategyNotFound,
    MissionStrategyService,
)
from backend.autonomy.strategy_schemas import (
    MissionStrategyAutoSelectRequest,
    MissionStrategyCreate,
    MissionStrategyEvaluateRequest,
    MissionStrategyFeedbackRequest,
    MissionStrategyPolicyUpsert,
    MissionStrategyRankRequest,
    MissionStrategyRetireRequest,
    MissionStrategySelectRequest,
    MissionStrategyUpdate,
)
from backend.core.container import AppContainer


router = APIRouter(tags=["Mission Strategies"])


def get_service(
    container: AppContainer = Depends(get_container),
) -> MissionStrategyService:
    service = container.mission_strategy_service
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Mission Strategy Service не запущен.",
        )
    return service


def translate_error(exc: AutonomousMissionError) -> HTTPException:
    if isinstance(
        exc,
        (
            MissionNotFound,
            MissionCycleNotFound,
            MissionStrategyNotFound,
            MissionStrategyAssignmentNotFound,
        ),
    ):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, MissionStateError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/mission-strategies/status")
def strategy_status(
    service: MissionStrategyService = Depends(get_service),
) -> dict[str, Any]:
    return service.status()


@router.get("/missions/{mission_id}/strategy-policy")
def get_strategy_policy(
    mission_id: str,
    service: MissionStrategyService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.get_policy(mission_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.put("/missions/{mission_id}/strategy-policy")
async def upsert_strategy_policy(
    mission_id: str,
    request: MissionStrategyPolicyUpsert,
    service: MissionStrategyService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.upsert_policy(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/strategy-dashboard")
def strategy_dashboard(
    mission_id: str,
    service: MissionStrategyService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.dashboard(mission_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/strategy-context")
def strategy_context(
    mission_id: str,
    cycle_id: str | None = None,
    limit: int = Query(default=10, ge=1, le=100),
    service: MissionStrategyService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.build_context(
            mission_id,
            cycle_id=cycle_id,
            limit=limit,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post(
    "/missions/{mission_id}/strategies",
    status_code=status.HTTP_201_CREATED,
)
async def create_strategy(
    mission_id: str,
    request: MissionStrategyCreate,
    service: MissionStrategyService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.create_strategy(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/strategies")
def list_strategies(
    mission_id: str,
    strategy_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(draft|candidate|selected|paused|retired|rejected)$",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionStrategyService = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_strategies(
            mission_id,
            status=strategy_status,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"strategies": rows}


@router.get("/mission-strategies/{strategy_id}")
def get_strategy(
    strategy_id: str,
    service: MissionStrategyService = Depends(get_service),
) -> dict[str, Any]:
    row = service.get_strategy(strategy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Mission Strategy не найдена.")
    return row


@router.patch("/mission-strategies/{strategy_id}")
async def update_strategy(
    strategy_id: str,
    request: MissionStrategyUpdate,
    service: MissionStrategyService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.update_strategy(strategy_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-strategies/{strategy_id}/evaluate")
async def evaluate_strategy(
    strategy_id: str,
    request: MissionStrategyEvaluateRequest,
    service: MissionStrategyService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.evaluate_strategy(strategy_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/mission-strategies/{strategy_id}/evaluations")
def list_strategy_evaluations(
    strategy_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionStrategyService = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_evaluations(
            strategy_id,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"evaluations": rows}


@router.post("/missions/{mission_id}/strategies/rank")
async def rank_strategies(
    mission_id: str,
    request: MissionStrategyRankRequest,
    service: MissionStrategyService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.rank_portfolio(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-strategies/{strategy_id}/select")
async def select_strategy(
    strategy_id: str,
    request: MissionStrategySelectRequest,
    service: MissionStrategyService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.select_strategy(strategy_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/missions/{mission_id}/strategies/auto-select")
async def auto_select_strategy(
    mission_id: str,
    request: MissionStrategyAutoSelectRequest,
    service: MissionStrategyService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.auto_select(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-strategies/{strategy_id}/retire")
async def retire_strategy(
    strategy_id: str,
    request: MissionStrategyRetireRequest,
    service: MissionStrategyService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.retire_strategy(strategy_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/strategy-selections")
def list_strategy_selections(
    mission_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionStrategyService = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_selections(
            mission_id,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"selections": rows}


@router.get("/missions/{mission_id}/strategy-assignments")
def list_strategy_assignments(
    mission_id: str,
    assignment_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(planned|running|succeeded|failed|cancelled)$",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionStrategyService = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_assignments(
            mission_id,
            status=assignment_status,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"assignments": rows}


@router.post("/mission-strategy-assignments/{assignment_id}/feedback")
async def record_strategy_feedback(
    assignment_id: str,
    request: MissionStrategyFeedbackRequest,
    service: MissionStrategyService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.record_feedback(assignment_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
