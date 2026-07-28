from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.api.dependencies import get_container
from backend.autonomy.portfolio import (
    MissionDependencyNotFound,
    MissionPortfolioService,
)
from backend.autonomy.portfolio_schemas import (
    MissionDependencyCreate,
    MissionDependencyUpdate,
    MissionDependencyWaiveRequest,
    MissionPortfolioAssignmentOverrideRequest,
    MissionPortfolioPolicyUpsert,
    MissionPortfolioRankRequest,
    MissionPortfolioRebalanceRequest,
)
from backend.autonomy.service import (
    AutonomousMissionError,
    MissionCycleNotFound,
    MissionNotFound,
    MissionStateError,
)
from backend.core.container import AppContainer


router = APIRouter(tags=["Mission Portfolio"])


def get_service(
    container: AppContainer = Depends(get_container),
) -> MissionPortfolioService:
    service = container.mission_portfolio_service
    if service is None:
        raise HTTPException(status_code=503, detail="Mission Portfolio Service is not running.")
    return service


def translate_error(exc: AutonomousMissionError) -> HTTPException:
    if isinstance(exc, (MissionNotFound, MissionCycleNotFound, MissionDependencyNotFound)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, MissionStateError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/mission-portfolio/status")
def portfolio_status(
    service: MissionPortfolioService = Depends(get_service),
) -> dict[str, Any]:
    return service.status()


@router.get("/workspaces/{workspace_id}/mission-portfolio/policy")
def get_portfolio_policy(
    workspace_id: str,
    service: MissionPortfolioService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.get_policy(workspace_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.put("/workspaces/{workspace_id}/mission-portfolio/policy")
async def upsert_portfolio_policy(
    workspace_id: str,
    request: MissionPortfolioPolicyUpsert,
    service: MissionPortfolioService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.upsert_policy(workspace_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/workspaces/{workspace_id}/mission-portfolio/dashboard")
def portfolio_dashboard(
    workspace_id: str,
    service: MissionPortfolioService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.dashboard(workspace_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/workspaces/{workspace_id}/mission-portfolio/rank")
async def rank_portfolio(
    workspace_id: str,
    request: MissionPortfolioRankRequest,
    service: MissionPortfolioService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.rank_workspace(workspace_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/workspaces/{workspace_id}/mission-portfolio/rebalance")
async def rebalance_portfolio(
    workspace_id: str,
    request: MissionPortfolioRebalanceRequest,
    service: MissionPortfolioService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.rebalance(workspace_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/workspaces/{workspace_id}/mission-portfolio/assignments")
def list_portfolio_assignments(
    workspace_id: str,
    assignment_status: str | None = Query(default=None, alias="status"),
    service: MissionPortfolioService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return {
            "assignments": service.list_assignments(
                workspace_id,
                status=assignment_status,
            )
        }
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/workspaces/{workspace_id}/mission-portfolio/evaluations")
def list_portfolio_evaluations(
    workspace_id: str,
    mission_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionPortfolioService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return {
            "evaluations": service.list_evaluations(
                workspace_id,
                mission_id=mission_id,
                limit=limit,
                offset=offset,
            )
        }
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/missions/{mission_id}/portfolio-assignment")
async def override_portfolio_assignment(
    mission_id: str,
    request: MissionPortfolioAssignmentOverrideRequest,
    service: MissionPortfolioService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.override_assignment(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/portfolio-context")
def portfolio_context(
    mission_id: str,
    cycle_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    service: MissionPortfolioService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.build_context(mission_id, cycle_id=cycle_id, limit=limit)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/portfolio-admission")
def portfolio_admission(
    mission_id: str,
    force: bool = False,
    service: MissionPortfolioService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.evaluate_mission_admission(mission_id, force=force)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post(
    "/missions/{mission_id}/dependencies",
    status_code=status.HTTP_201_CREATED,
)
async def create_dependency(
    mission_id: str,
    request: MissionDependencyCreate,
    service: MissionPortfolioService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.create_dependency(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/dependencies")
def list_dependencies(
    mission_id: str,
    incoming: bool = False,
    include_waived: bool = True,
    service: MissionPortfolioService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return {
            "dependencies": service.list_dependencies(
                mission_id,
                incoming=incoming,
                include_waived=include_waived,
            )
        }
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.patch("/mission-dependencies/{dependency_id}")
async def update_dependency(
    dependency_id: str,
    request: MissionDependencyUpdate,
    service: MissionPortfolioService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.update_dependency(dependency_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-dependencies/{dependency_id}/waive")
async def waive_dependency(
    dependency_id: str,
    request: MissionDependencyWaiveRequest,
    service: MissionPortfolioService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.waive_dependency(dependency_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.delete("/mission-dependencies/{dependency_id}")
async def delete_dependency(
    dependency_id: str,
    service: MissionPortfolioService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.delete_dependency(dependency_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/workspaces/{workspace_id}/mission-dependencies/validate")
def validate_dependencies(
    workspace_id: str,
    service: MissionPortfolioService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.validate_graph(workspace_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-dependencies/refresh")
async def refresh_dependencies(
    workspace_id: str | None = None,
    service: MissionPortfolioService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.refresh_dependencies(workspace_id=workspace_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
