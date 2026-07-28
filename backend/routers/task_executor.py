from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from backend.api.dependencies import get_container
from backend.core.container import AppContainer
from backend.task_engine.parallel_executor import ParallelTaskExecutor

router = APIRouter(tags=["task-executor"])


def get_executor(
    container: AppContainer = Depends(get_container),
) -> ParallelTaskExecutor:
    executor = container.task_executor

    if executor is None:
        raise HTTPException(
            status_code=503,
            detail="Task Executor не запущен.",
        )

    return executor


@router.get("/task-executor/status")
def executor_status(
    executor: ParallelTaskExecutor = Depends(get_executor),
) -> dict[str, Any]:
    return executor.stats()


@router.get("/task-executor/resources")
def executor_resources(
    executor: ParallelTaskExecutor = Depends(get_executor),
) -> dict[str, Any]:
    return executor.resource_locks.stats()


@router.post("/task-executor/recover")
async def recover_tasks(
    executor: ParallelTaskExecutor = Depends(get_executor),
) -> dict[str, int]:
    return await executor.recover_persistent_queue()
