from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.api.dependencies import get_container
from backend.autonomy.forecast import (
    MissionForecastNotFound,
    MissionForecastService,
    MissionScenarioNotFound,
    WorkspacePortfolioSimulationNotFound,
)
from backend.autonomy.forecast_schemas import (
    MissionForecastGenerateRequest,
    MissionForecastInvalidateRequest,
    MissionForecastPolicyUpsert,
    MissionScenarioArchiveRequest,
    MissionScenarioCreate,
    MissionScenarioEvaluateRequest,
    MissionScenarioSelectionRequest,
    MissionScenarioUpdate,
    WorkspacePortfolioSimulationCompareRequest,
    WorkspacePortfolioSimulationRequest,
)
from backend.autonomy.service import AutonomousMissionError, MissionNotFound, MissionStateError
from backend.core.container import AppContainer


router = APIRouter(tags=["Mission Forecasting"])


def get_service(
    container: AppContainer = Depends(get_container),
) -> MissionForecastService:
    service = container.mission_forecast_service
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Mission Forecast Service не запущен.",
        )
    return service


def translate_error(exc: AutonomousMissionError) -> HTTPException:
    if isinstance(
        exc,
        (
            MissionNotFound,
            MissionForecastNotFound,
            MissionScenarioNotFound,
            WorkspacePortfolioSimulationNotFound,
        ),
    ):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, MissionStateError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/mission-forecasting/status")
def forecasting_status(
    service: MissionForecastService = Depends(get_service),
) -> dict[str, Any]:
    return service.status()


@router.get("/missions/{mission_id}/forecast-policy")
def get_policy(
    mission_id: str,
    service: MissionForecastService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.get_policy(mission_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.put("/missions/{mission_id}/forecast-policy")
async def upsert_policy(
    mission_id: str,
    request: MissionForecastPolicyUpsert,
    service: MissionForecastService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.upsert_policy(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/forecast-dashboard")
def forecast_dashboard(
    mission_id: str,
    service: MissionForecastService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.dashboard(mission_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/forecast-context")
def forecast_context(
    mission_id: str,
    cycle_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    service: MissionForecastService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.build_context(mission_id, cycle_id=cycle_id, limit=limit)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post(
    "/missions/{mission_id}/forecasts",
    status_code=status.HTTP_201_CREATED,
)
async def generate_forecast(
    mission_id: str,
    request: MissionForecastGenerateRequest,
    service: MissionForecastService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.generate_forecast(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/forecasts")
def list_forecasts(
    mission_id: str,
    forecast_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(current|superseded|invalidated)$",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionForecastService = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_forecasts(
            mission_id,
            status=forecast_status,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"forecasts": rows}


@router.get("/mission-forecasts/{forecast_id}")
def get_forecast(
    forecast_id: str,
    service: MissionForecastService = Depends(get_service),
) -> dict[str, Any]:
    row = service.get_forecast(forecast_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Mission Forecast не найден.")
    return row


@router.post("/mission-forecasts/{forecast_id}/invalidate")
async def invalidate_forecast(
    forecast_id: str,
    request: MissionForecastInvalidateRequest,
    service: MissionForecastService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.invalidate_forecast(
            forecast_id,
            actor_id=request.actor_id,
            reason=request.reason,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post(
    "/missions/{mission_id}/scenarios",
    status_code=status.HTTP_201_CREATED,
)
async def create_scenario(
    mission_id: str,
    request: MissionScenarioCreate,
    service: MissionForecastService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.create_scenario(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/scenarios")
def list_scenarios(
    mission_id: str,
    scenario_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(draft|evaluated|selected|archived)$",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionForecastService = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_scenarios(
            mission_id,
            status=scenario_status,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"scenarios": rows}


@router.get("/mission-scenarios/{scenario_id}")
def get_scenario(
    scenario_id: str,
    service: MissionForecastService = Depends(get_service),
) -> dict[str, Any]:
    row = service.get_scenario(scenario_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Mission Scenario не найден.")
    return row


@router.patch("/mission-scenarios/{scenario_id}")
async def update_scenario(
    scenario_id: str,
    request: MissionScenarioUpdate,
    service: MissionForecastService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.update_scenario(scenario_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-scenarios/{scenario_id}/evaluate")
async def evaluate_scenario(
    scenario_id: str,
    request: MissionScenarioEvaluateRequest,
    service: MissionForecastService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.evaluate_scenario(scenario_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-scenarios/{scenario_id}/select")
async def select_scenario(
    scenario_id: str,
    request: MissionScenarioSelectionRequest,
    service: MissionForecastService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.select_scenario(scenario_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-scenarios/{scenario_id}/archive")
async def archive_scenario(
    scenario_id: str,
    request: MissionScenarioArchiveRequest,
    service: MissionForecastService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.archive_scenario(scenario_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post(
    "/workspaces/{workspace_id}/portfolio-simulations",
    status_code=status.HTTP_201_CREATED,
)
async def simulate_workspace(
    workspace_id: str,
    request: WorkspacePortfolioSimulationRequest,
    service: MissionForecastService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.simulate_workspace(workspace_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/workspaces/{workspace_id}/portfolio-simulations")
def list_simulations(
    workspace_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionForecastService = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_simulations(
            workspace_id,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"simulations": rows}


@router.get("/workspace-portfolio-simulations/{simulation_id}")
def get_simulation(
    simulation_id: str,
    service: MissionForecastService = Depends(get_service),
) -> dict[str, Any]:
    row = service.get_simulation(simulation_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Workspace Portfolio Simulation не найдена.",
        )
    return row


@router.post("/workspaces/{workspace_id}/portfolio-simulations/compare")
def compare_simulations(
    workspace_id: str,
    request: WorkspacePortfolioSimulationCompareRequest,
    service: MissionForecastService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.compare_simulations(workspace_id, request.simulation_ids)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
