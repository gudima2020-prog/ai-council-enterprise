from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from backend.api.dependencies import get_container
from backend.core.container import AppContainer
from backend.task_engine.dependencies import (
    DependencyCreateRequest,
    DependencyCycleError,
    DependencyError,
)
from backend.task_engine.state_machine import InvalidTaskTransition
from backend.task_engine.workflow import TaskWorkflowEngine

router = APIRouter(tags=["task-workflows"])


def get_workflow_engine(
    container: AppContainer = Depends(get_container),
) -> TaskWorkflowEngine:
    engine = container.task_workflow_engine
    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="Task Workflow Engine не запущен.",
        )
    return engine


@router.get("/task-workflows/status")
def workflow_status(
    engine: TaskWorkflowEngine = Depends(get_workflow_engine),
) -> dict[str, Any]:
    return {
        **engine.stats(),
        "dag": engine.validate(),
    }


@router.post("/tasks/{task_id}/dependencies")
async def add_dependency(
    task_id: str,
    request: DependencyCreateRequest,
    engine: TaskWorkflowEngine = Depends(get_workflow_engine),
) -> dict[str, Any]:
    try:
        return await engine.add_dependency(
            task_id=task_id,
            request=request,
        )
    except DependencyCycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DependencyError as exc:
        message = str(exc)
        status_code = 404 if "не найдена" in message else 409
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.get("/tasks/{task_id}/dependencies")
def list_dependencies(
    task_id: str,
    engine: TaskWorkflowEngine = Depends(get_workflow_engine),
) -> dict[str, Any]:
    result = engine.list_dependencies(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    return result


@router.delete(
    "/tasks/{task_id}/dependencies/{depends_on_task_id}"
)
async def remove_dependency(
    task_id: str,
    depends_on_task_id: str,
    engine: TaskWorkflowEngine = Depends(get_workflow_engine),
) -> dict[str, bool]:
    try:
        removed = await engine.remove_dependency(
            task_id=task_id,
            depends_on_task_id=depends_on_task_id,
        )
    except DependencyError as exc:
        message = str(exc)
        status_code = 404 if "не найдена" in message else 409
        raise HTTPException(status_code=status_code, detail=message) from exc

    if not removed:
        raise HTTPException(
            status_code=404,
            detail="Зависимость не найдена.",
        )
    return {"deleted": True}


@router.get("/task-workflows/{task_id}/graph")
def workflow_graph(
    task_id: str,
    engine: TaskWorkflowEngine = Depends(get_workflow_engine),
) -> dict[str, Any]:
    try:
        result = engine.graph(task_id)
    except DependencyCycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    return result


@router.post("/task-workflows/{task_id}/validate")
def validate_workflow(
    task_id: str,
    engine: TaskWorkflowEngine = Depends(get_workflow_engine),
) -> dict[str, Any]:
    graph = engine.graph(task_id)
    if graph is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    return {
        "root_task_id": task_id,
        "valid": graph["valid"],
        "cycle": graph["cycle"],
        "topological_order": graph["topological_order"],
    }


@router.post("/task-workflows/{task_id}/start")
async def start_workflow(
    task_id: str,
    engine: TaskWorkflowEngine = Depends(get_workflow_engine),
) -> dict[str, Any]:
    try:
        result = await engine.start_workflow(task_id)
    except (DependencyCycleError, InvalidTaskTransition) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Task не найдена.")
    return result


@router.post("/task-workflows/reconcile")
async def reconcile_workflows(
    engine: TaskWorkflowEngine = Depends(get_workflow_engine),
) -> dict[str, int]:
    return await engine.reconcile_all()
