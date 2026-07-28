from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_container
from backend.core.container import AppContainer
from backend.orchestration.runtime import (
    ExecutionPlanRuntime,
    ExecutionRuntimeError,
)
from backend.orchestration.runtime_schemas import (
    ExecutionPlanCancelRequest,
    ExecutionPlanRunRequest,
)

router = APIRouter(tags=["execution-runtime"])


def get_runtime(
    container: AppContainer = Depends(get_container),
) -> ExecutionPlanRuntime:
    runtime = container.execution_plan_runtime
    if runtime is None:
        raise HTTPException(
            status_code=503,
            detail="Execution Plan Runtime не запущен.",
        )
    return runtime


@router.get("/execution-runtime/status")
def runtime_status(
    runtime: ExecutionPlanRuntime = Depends(get_runtime),
) -> dict[str, Any]:
    return runtime.stats()


@router.post("/execution-runtime/recover")
async def recover_runtime(
    runtime: ExecutionPlanRuntime = Depends(get_runtime),
) -> dict[str, int]:
    return await runtime.recover_interrupted()


@router.post("/execution-plans/{plan_id}/run")
async def run_plan(
    plan_id: str,
    request: ExecutionPlanRunRequest,
    runtime: ExecutionPlanRuntime = Depends(get_runtime),
) -> dict[str, Any]:
    try:
        result = await runtime.start(plan_id, request)
    except ExecutionRuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="Ожидание завершения Execution Plan превысило timeout.",
        ) from exc

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return result


@router.post("/execution-plans/{plan_id}/cancel")
async def cancel_plan(
    plan_id: str,
    request: ExecutionPlanCancelRequest,
    runtime: ExecutionPlanRuntime = Depends(get_runtime),
) -> dict[str, Any]:
    try:
        result = await runtime.cancel(plan_id, request.reason)
    except ExecutionRuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return result


@router.get("/execution-plans/{plan_id}/runtime")
def plan_runtime_status(
    plan_id: str,
    runtime: ExecutionPlanRuntime = Depends(get_runtime),
) -> dict[str, Any]:
    result = runtime.status(plan_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return result


@router.get("/execution-plans/{plan_id}/step-runs")
def list_step_runs(
    plan_id: str,
    step_id: str | None = Query(default=None),
    runtime: ExecutionPlanRuntime = Depends(get_runtime),
) -> dict[str, Any]:
    result = runtime.list_runs(plan_id, step_id=step_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return {
        "plan_id": plan_id,
        "step_id": step_id,
        "runs": result,
    }
