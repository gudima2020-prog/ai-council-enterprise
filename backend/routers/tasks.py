from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.api.dependencies import get_container, get_db_session
from backend.core.container import AppContainer
from backend.task_engine.schemas import (
    TaskArtifactCreate,
    TaskCancelRequest,
    TaskCreate,
    TaskLogCreate,
    TaskRunCreate,
    TaskTransitionRequest,
    TaskUpdate,
)
from backend.task_engine.service import (
    InvalidTaskTransition,
    TaskService,
)

router = APIRouter(tags=["tasks"])


def get_task_service(
    session: Session = Depends(get_db_session),
    container: AppContainer = Depends(get_container),
) -> TaskService:
    return container.task_service(session)


@router.post("/tasks")
async def create_task(
    request: TaskCreate,
    service: TaskService = Depends(get_task_service),
) -> dict[str, Any]:
    return await service.create_task(request)


@router.get("/tasks")
def list_tasks(
    workspace_id: str | None = None,
    status: str | None = None,
    task_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: TaskService = Depends(get_task_service),
) -> dict[str, list[dict[str, Any]]]:
    return {
        "tasks": service.list_tasks(
            workspace_id=workspace_id,
            status=status,
            task_type=task_type,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/tasks/{task_id}")
def get_task(
    task_id: str,
    service: TaskService = Depends(get_task_service),
) -> dict[str, Any]:
    task = service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    return task


@router.patch("/tasks/{task_id}")
async def update_task(
    task_id: str,
    request: TaskUpdate,
    service: TaskService = Depends(get_task_service),
) -> dict[str, Any]:
    task = await service.update_task(task_id, request)
    if task is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    return task


@router.post("/tasks/{task_id}/enqueue")
async def enqueue_task(
    task_id: str,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    workflow = container.task_workflow_engine
    scheduler = container.task_scheduler
    admission = container.task_admission_manager

    if (
        workflow is None
        or scheduler is None
        or not scheduler.running
        or admission is None
    ):
        raise HTTPException(
            status_code=503,
            detail="Task runtime не запущен.",
        )

    preview = await admission.evaluate_task(
        task_id,
        reserve=False,
        actor_id="api",
        source="enqueue_preview",
    )
    if preview is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    if not preview["allowed"]:
        raise HTTPException(status_code=429, detail=preview)

    try:
        result = await workflow.enqueue_task(
            task_id,
            source="task_api",
        )
    except InvalidTaskTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    result["admission_preview"] = preview
    return result


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    request: TaskCancelRequest,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    executor = container.task_executor
    if executor is None:
        raise HTTPException(
            status_code=503,
            detail="Task Executor не запущен.",
        )

    try:
        result = await executor.request_cancel(
            task_id,
            request.reason,
        )
    except InvalidTaskTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    return result


@router.post("/tasks/{task_id}/retry")
async def retry_task(
    task_id: str,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    executor = container.task_executor
    if executor is None:
        raise HTTPException(
            status_code=503,
            detail="Task Executor не запущен.",
        )

    try:
        result = await executor.retry_task(task_id)
    except InvalidTaskTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    return result


@router.get("/tasks/{task_id}/transitions")
def get_allowed_transitions(
    task_id: str,
    service: TaskService = Depends(get_task_service),
) -> dict[str, Any]:
    result = service.get_allowed_transitions(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    return result


@router.post("/tasks/{task_id}/transition")
async def transition_task(
    task_id: str,
    request: TaskTransitionRequest,
    service: TaskService = Depends(get_task_service),
) -> dict[str, Any]:
    try:
        task = await service.transition_task(task_id, request)
    except InvalidTaskTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if task is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    return task


@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: str,
    service: TaskService = Depends(get_task_service),
) -> dict[str, bool]:
    deleted = await service.delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    return {"deleted": True}


@router.post("/tasks/{task_id}/runs")
def append_run(
    task_id: str,
    request: TaskRunCreate,
    service: TaskService = Depends(get_task_service),
) -> dict[str, Any]:
    row = service.append_run(task_id, request)
    if row is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    return row


@router.post("/tasks/{task_id}/logs")
def append_log(
    task_id: str,
    request: TaskLogCreate,
    service: TaskService = Depends(get_task_service),
) -> dict[str, Any]:
    row = service.append_log(task_id, request)
    if row is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    return row


@router.post("/tasks/{task_id}/artifacts")
def append_artifact(
    task_id: str,
    request: TaskArtifactCreate,
    service: TaskService = Depends(get_task_service),
) -> dict[str, Any]:
    row = service.append_artifact(task_id, request)
    if row is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    return row
