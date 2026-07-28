from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.api.dependencies import get_container, get_db_session
from backend.core.container import AppContainer
from backend.orchestration.schemas import (
    ExecutionPlanCreate,
    ExecutionPlanStepCreate,
    ExecutionPlanStepUpdate,
    ExecutionPlanTransitionRequest,
    ExecutionPlanUpdate,
)
from backend.orchestration.service import (
    ExecutionPlanError,
    ExecutionPlanNotFound,
    ExecutionPlanService,
    InvalidExecutionPlanTransition,
)

router = APIRouter(tags=["execution-plans"])


def get_execution_plan_service(
    session: Session = Depends(get_db_session),
    container: AppContainer = Depends(get_container),
) -> ExecutionPlanService:
    return container.execution_plan_service(session)


def conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.post("/execution-plans")
async def create_plan(
    request: ExecutionPlanCreate,
    service: ExecutionPlanService = Depends(get_execution_plan_service),
) -> dict[str, Any]:
    try:
        return await service.create_plan(request)
    except ExecutionPlanError as exc:
        raise conflict(exc) from exc


@router.get("/execution-plans")
def list_plans(
    workspace_id: str | None = None,
    source_task_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: ExecutionPlanService = Depends(get_execution_plan_service),
) -> dict[str, list[dict[str, Any]]]:
    return {
        "execution_plans": service.list_plans(
            workspace_id=workspace_id,
            source_task_id=source_task_id,
            status=status,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/execution-plans/{plan_id}")
def get_plan(
    plan_id: str,
    service: ExecutionPlanService = Depends(get_execution_plan_service),
) -> dict[str, Any]:
    result = service.get_plan(plan_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return result


@router.patch("/execution-plans/{plan_id}")
async def update_plan(
    plan_id: str,
    request: ExecutionPlanUpdate,
    service: ExecutionPlanService = Depends(get_execution_plan_service),
) -> dict[str, Any]:
    try:
        result = await service.update_plan(plan_id, request)
    except ExecutionPlanError as exc:
        raise conflict(exc) from exc

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return result


@router.delete("/execution-plans/{plan_id}")
async def delete_plan(
    plan_id: str,
    service: ExecutionPlanService = Depends(get_execution_plan_service),
) -> dict[str, bool]:
    try:
        deleted = await service.delete_plan(plan_id)
    except ExecutionPlanError as exc:
        raise conflict(exc) from exc

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return {"deleted": True}


@router.post("/execution-plans/{plan_id}/steps")
async def add_step(
    plan_id: str,
    request: ExecutionPlanStepCreate,
    service: ExecutionPlanService = Depends(get_execution_plan_service),
) -> dict[str, Any]:
    try:
        result = await service.add_step(plan_id, request)
    except ExecutionPlanError as exc:
        raise conflict(exc) from exc

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return result


@router.patch("/execution-plans/{plan_id}/steps/{step_id}")
async def update_step(
    plan_id: str,
    step_id: str,
    request: ExecutionPlanStepUpdate,
    service: ExecutionPlanService = Depends(get_execution_plan_service),
) -> dict[str, Any]:
    try:
        result = await service.update_step(plan_id, step_id, request)
    except ExecutionPlanNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExecutionPlanError as exc:
        raise conflict(exc) from exc

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return result


@router.delete("/execution-plans/{plan_id}/steps/{step_id}")
async def delete_step(
    plan_id: str,
    step_id: str,
    service: ExecutionPlanService = Depends(get_execution_plan_service),
) -> dict[str, Any]:
    try:
        result = await service.delete_step(plan_id, step_id)
    except ExecutionPlanNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExecutionPlanError as exc:
        raise conflict(exc) from exc

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return result


@router.post("/execution-plans/{plan_id}/validate")
async def validate_plan(
    plan_id: str,
    service: ExecutionPlanService = Depends(get_execution_plan_service),
) -> dict[str, Any]:
    try:
        result = await service.validate_plan(plan_id)
    except ExecutionPlanError as exc:
        raise conflict(exc) from exc

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return result


@router.post("/execution-plans/{plan_id}/transition")
async def transition_plan(
    plan_id: str,
    request: ExecutionPlanTransitionRequest,
    service: ExecutionPlanService = Depends(get_execution_plan_service),
) -> dict[str, Any]:
    try:
        result = await service.transition_plan(plan_id, request)
    except (
        ExecutionPlanError,
        InvalidExecutionPlanTransition,
    ) as exc:
        raise conflict(exc) from exc

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return result


@router.get("/execution-plans/{plan_id}/graph")
def get_plan_graph(
    plan_id: str,
    service: ExecutionPlanService = Depends(get_execution_plan_service),
) -> dict[str, Any]:
    result = service.graph(plan_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return result
