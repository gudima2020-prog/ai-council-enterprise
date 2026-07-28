from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_container
from backend.core.container import AppContainer
from backend.orchestration.planner import (
    ExecutionPlannerError,
    ExecutionPlannerService,
    PlannerAdapterNotRegistered,
)
from backend.orchestration.planner_schemas import (
    ExecutionPlanGenerationRequest,
    ExecutionPlanReplanRequest,
)

router = APIRouter(tags=["execution-planner"])


def get_planner(
    container: AppContainer = Depends(get_container),
) -> ExecutionPlannerService:
    planner = container.execution_planner_service
    if planner is None:
        raise HTTPException(
            status_code=503,
            detail="Execution Planner не запущен.",
        )
    return planner


@router.get("/execution-planner/status")
def planner_status(
    planner: ExecutionPlannerService = Depends(get_planner),
) -> dict[str, Any]:
    return planner.stats()


@router.post("/execution-planner/generate")
async def generate_plan(
    request: ExecutionPlanGenerationRequest,
    planner: ExecutionPlannerService = Depends(get_planner),
) -> dict[str, Any]:
    try:
        return await planner.generate(request)
    except PlannerAdapterNotRegistered as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ExecutionPlannerError as exc:
        message = str(exc)
        status_code = 404 if "не найдена" in message else 409
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/execution-plans/{plan_id}/replan")
async def replan(
    plan_id: str,
    request: ExecutionPlanReplanRequest,
    planner: ExecutionPlannerService = Depends(get_planner),
) -> dict[str, Any]:
    try:
        result = await planner.replan(plan_id, request)
    except PlannerAdapterNotRegistered as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ExecutionPlannerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return result


@router.get("/execution-planner/runs")
def list_planner_runs(
    workspace_id: str | None = None,
    source_task_id: str | None = None,
    source_plan_id: str | None = None,
    run_type: str | None = Query(default=None, pattern="^(generate|replan)$"),
    status: str | None = Query(
        default=None,
        pattern="^(running|completed|failed)$",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    planner: ExecutionPlannerService = Depends(get_planner),
) -> dict[str, Any]:
    return {
        "runs": planner.list_runs(
            workspace_id=workspace_id,
            source_task_id=source_task_id,
            source_plan_id=source_plan_id,
            run_type=run_type,
            status=status,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/execution-planner/runs/{run_id}")
def get_planner_run(
    run_id: str,
    planner: ExecutionPlannerService = Depends(get_planner),
) -> dict[str, Any]:
    result = planner.get_run(run_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Planner Run не найден.",
        )
    return result
