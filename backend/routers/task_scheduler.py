from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.api.dependencies import get_container
from backend.core.container import AppContainer
from backend.task_engine.scheduler import TaskScheduler

router = APIRouter(tags=["task-scheduler"])


class EnqueueRequest(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=64)
    priority: str = "normal"
    metadata: dict[str, Any] = Field(default_factory=dict)


def get_scheduler(
    container: AppContainer = Depends(get_container),
) -> TaskScheduler:
    scheduler = container.task_scheduler

    if scheduler is None or not scheduler.running:
        raise HTTPException(
            status_code=503,
            detail="Task Scheduler не запущен.",
        )

    return scheduler


@router.get("/task-scheduler/status")
def scheduler_status(
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    scheduler = container.task_scheduler

    if scheduler is None:
        return {
            "running": False,
            "queue_size": 0,
            "processed": 0,
            "failed": 0,
        }

    return scheduler.stats()


@router.post("/task-scheduler/enqueue")
async def enqueue_task(
    request: EnqueueRequest,
    scheduler: TaskScheduler = Depends(get_scheduler),
) -> dict[str, Any]:
    added = await scheduler.enqueue(
        task_id=request.task_id,
        priority=request.priority,
        metadata=request.metadata,
    )

    if not added:
        raise HTTPException(
            status_code=409,
            detail="Task уже находится в очереди.",
        )

    return {
        "enqueued": True,
        "task_id": request.task_id,
        "queue_size": scheduler.stats()["queue_size"],
    }
