from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_container
from backend.core.container import AppContainer
from backend.task_engine.approval_schemas import (
    ApprovalDecisionRequest,
    ApprovalStatus,
    TaskApprovalRequest,
)
from backend.task_engine.approvals import (
    ApprovalError,
    TaskApprovalManager,
)

router = APIRouter(tags=["task-approvals"])


def get_approval_manager(
    container: AppContainer = Depends(get_container),
) -> TaskApprovalManager:
    manager = container.task_approval_manager

    if manager is None:
        raise HTTPException(
            status_code=503,
            detail="Task Approval Manager не запущен.",
        )

    return manager


@router.get("/task-approvals/status")
def approval_status(
    manager: TaskApprovalManager = Depends(get_approval_manager),
) -> dict[str, int]:
    return manager.stats()


@router.get("/task-approvals")
def list_approvals(
    status: ApprovalStatus | None = None,
    workspace_id: str | None = None,
    task_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    manager: TaskApprovalManager = Depends(get_approval_manager),
) -> dict[str, Any]:
    return {
        "approvals": manager.list(
            status=status,
            workspace_id=workspace_id,
            task_id=task_id,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/task-approvals/{approval_id}")
def get_approval(
    approval_id: str,
    manager: TaskApprovalManager = Depends(get_approval_manager),
) -> dict[str, Any]:
    result = manager.get(approval_id)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Approval не найден.",
        )

    return result


@router.post("/tasks/{task_id}/approval-request")
async def request_task_approval(
    task_id: str,
    request: TaskApprovalRequest,
    manager: TaskApprovalManager = Depends(get_approval_manager),
) -> dict[str, Any]:
    try:
        result = await manager.request(
            task_id=task_id,
            request=request,
        )
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")

    return result


@router.post("/task-approvals/{approval_id}/decision")
async def decide_approval(
    approval_id: str,
    request: ApprovalDecisionRequest,
    manager: TaskApprovalManager = Depends(get_approval_manager),
) -> dict[str, Any]:
    try:
        result = await manager.decide(
            approval_id=approval_id,
            request=request,
        )
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Approval не найден.",
        )

    return result


@router.post("/task-approvals/reconcile")
async def reconcile_expired_approvals(
    manager: TaskApprovalManager = Depends(get_approval_manager),
) -> dict[str, Any]:
    return await manager.reconcile_expired()
