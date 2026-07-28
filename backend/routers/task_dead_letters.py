from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_container
from backend.core.container import AppContainer
from backend.task_engine.dead_letter import (
    DeadLetterError,
    DeadLetterStatus,
    TaskDeadLetterManager,
)
from backend.task_engine.dead_letter_schemas import (
    DeadLetterCaptureRequest,
    DeadLetterDiscardRequest,
    DeadLetterReplayRequest,
)

router = APIRouter(tags=["task-dead-letter"])


def get_manager(
    container: AppContainer = Depends(get_container),
) -> TaskDeadLetterManager:
    manager = container.task_dead_letter_manager
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail="Task Dead Letter Manager не запущен.",
        )
    return manager


@router.get("/task-dead-letter/status")
def dead_letter_status(
    manager: TaskDeadLetterManager = Depends(get_manager),
) -> dict[str, Any]:
    return manager.stats()


@router.get("/task-dead-letter/verify")
def verify_dead_letters(
    manager: TaskDeadLetterManager = Depends(get_manager),
) -> dict[str, Any]:
    return manager.verify_integrity()


@router.get("/task-dead-letter")
def list_dead_letters(
    status: DeadLetterStatus | None = None,
    task_id: str | None = None,
    workspace_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    manager: TaskDeadLetterManager = Depends(get_manager),
) -> dict[str, list[dict[str, Any]]]:
    return {
        "entries": manager.list_entries(
            status=status,
            task_id=task_id,
            workspace_id=workspace_id,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/task-dead-letter/{entry_id}")
def get_dead_letter(
    entry_id: str,
    manager: TaskDeadLetterManager = Depends(get_manager),
) -> dict[str, Any]:
    result = manager.get_entry(entry_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Dead-letter entry не найдена.",
        )
    return result


@router.post("/tasks/{task_id}/dead-letter")
async def capture_task(
    task_id: str,
    request: DeadLetterCaptureRequest,
    manager: TaskDeadLetterManager = Depends(get_manager),
) -> dict[str, Any]:
    result = await manager.capture_task(
        task_id=task_id,
        reason_code=request.reason_code,
        error_type=request.error_type,
        error_message=request.error_message,
        source_event_type="task.dead_letter.manual_capture",
        source_payload=request.metadata,
        actor_id=request.actor_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    if not result.get("captured") and not result.get("duplicate"):
        raise HTTPException(
            status_code=409,
            detail="В DLQ можно поместить только Task со статусом failed.",
        )
    return result


@router.post("/task-dead-letter/{entry_id}/replay")
async def replay_dead_letter(
    entry_id: str,
    request: DeadLetterReplayRequest,
    manager: TaskDeadLetterManager = Depends(get_manager),
) -> dict[str, Any]:
    try:
        result = await manager.replay(entry_id, request)
    except DeadLetterError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Dead-letter entry не найдена.",
        )
    return result


@router.post("/task-dead-letter/{entry_id}/discard")
async def discard_dead_letter(
    entry_id: str,
    request: DeadLetterDiscardRequest,
    manager: TaskDeadLetterManager = Depends(get_manager),
) -> dict[str, Any]:
    try:
        result = await manager.discard(
            entry_id=entry_id,
            actor_id=request.actor_id,
            reason=request.reason,
        )
    except DeadLetterError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Dead-letter entry не найдена.",
        )
    return result


@router.post("/task-dead-letter/reconcile")
async def reconcile_dead_letters(
    manager: TaskDeadLetterManager = Depends(get_manager),
) -> dict[str, int]:
    return await manager.reconcile()
