from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_container
from backend.core.container import AppContainer
from backend.task_engine.admission import (
    AdmissionError,
    CostEntryType,
    TaskAdmissionManager,
)
from backend.task_engine.budget_schemas import (
    AdmissionEvaluateRequest,
    AdmissionOverrideRequest,
    BudgetPolicyCreate,
    BudgetPolicyUpdate,
    TaskCostChargeRequest,
)

router = APIRouter(tags=["task-budgets"])


def get_manager(
    container: AppContainer = Depends(get_container),
) -> TaskAdmissionManager:
    manager = container.task_admission_manager
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail="Task Admission Manager не запущен.",
        )
    return manager


@router.get("/task-admission/status")
def admission_status(
    manager: TaskAdmissionManager = Depends(get_manager),
) -> dict[str, int]:
    return manager.stats()


@router.post("/task-budget-policies")
async def create_policy(
    request: BudgetPolicyCreate,
    manager: TaskAdmissionManager = Depends(get_manager),
) -> dict[str, Any]:
    try:
        return await manager.create_policy(request)
    except AdmissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/task-budget-policies")
def list_policies(
    workspace_id: str | None = None,
    enabled: bool | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    manager: TaskAdmissionManager = Depends(get_manager),
) -> dict[str, list[dict[str, Any]]]:
    return {
        "policies": manager.list_policies(
            workspace_id=workspace_id,
            enabled=enabled,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/task-budget-policies/{policy_id}")
def get_policy(
    policy_id: str,
    manager: TaskAdmissionManager = Depends(get_manager),
) -> dict[str, Any]:
    result = manager.get_policy(policy_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Budget policy не найдена.")
    return result


@router.patch("/task-budget-policies/{policy_id}")
async def update_policy(
    policy_id: str,
    request: BudgetPolicyUpdate,
    manager: TaskAdmissionManager = Depends(get_manager),
) -> dict[str, Any]:
    try:
        result = await manager.update_policy(policy_id, request)
    except AdmissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Budget policy не найдена.")
    return result


@router.delete("/task-budget-policies/{policy_id}")
async def delete_policy(
    policy_id: str,
    manager: TaskAdmissionManager = Depends(get_manager),
) -> dict[str, bool]:
    if not await manager.delete_policy(policy_id):
        raise HTTPException(status_code=404, detail="Budget policy не найдена.")
    return {"deleted": True}


@router.get("/task-budgets/status")
def budget_status(
    workspace_id: str | None = None,
    policy_id: str | None = None,
    manager: TaskAdmissionManager = Depends(get_manager),
) -> dict[str, Any]:
    return manager.budget_status(
        workspace_id=workspace_id,
        policy_id=policy_id,
    )


@router.get("/task-cost-ledger")
def list_cost_ledger(
    task_id: str | None = None,
    workspace_id: str | None = None,
    entry_type: CostEntryType | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    manager: TaskAdmissionManager = Depends(get_manager),
) -> dict[str, list[dict[str, Any]]]:
    return {
        "entries": manager.list_ledger(
            task_id=task_id,
            workspace_id=workspace_id,
            entry_type=entry_type,
            limit=limit,
            offset=offset,
        )
    }


@router.post("/tasks/{task_id}/admission/evaluate")
async def evaluate_task(
    task_id: str,
    request: AdmissionEvaluateRequest,
    manager: TaskAdmissionManager = Depends(get_manager),
) -> dict[str, Any]:
    result = await manager.evaluate_task(
        task_id,
        reserve=request.reserve,
        actor_id=request.actor_id,
        source=request.source,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    return result


@router.post("/tasks/{task_id}/admission/override")
async def override_task(
    task_id: str,
    request: AdmissionOverrideRequest,
    manager: TaskAdmissionManager = Depends(get_manager),
) -> dict[str, Any]:
    result = await manager.override_task(
        task_id,
        actor_id=request.actor_id,
        reason=request.reason,
        metadata=request.metadata,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    return result


@router.get("/tasks/{task_id}/cost")
def task_cost(
    task_id: str,
    manager: TaskAdmissionManager = Depends(get_manager),
) -> dict[str, Any]:
    result = manager.cost_status(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    return result


@router.post("/tasks/{task_id}/cost/charge")
async def charge_task(
    task_id: str,
    request: TaskCostChargeRequest,
    manager: TaskAdmissionManager = Depends(get_manager),
) -> dict[str, Any]:
    result = await manager.manual_charge(
        task_id,
        amount_usd=request.amount_usd,
        actor_id=request.actor_id,
        reason=request.reason,
        metadata=request.metadata,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    return result
