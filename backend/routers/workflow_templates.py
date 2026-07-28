from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.api.dependencies import get_container, get_db_session
from backend.core.container import AppContainer
from backend.task_engine.dependencies import DependencyCycleError
from backend.task_engine.workflow_templates import (
    WorkflowInstantiateRequest,
    WorkflowTemplateCreate,
    WorkflowTemplateError,
    WorkflowTemplateService,
)

router = APIRouter(tags=["workflow-templates"])


def get_template_service(
    session: Session = Depends(get_db_session),
    container: AppContainer = Depends(get_container),
) -> WorkflowTemplateService:
    return WorkflowTemplateService(session, container.event_bus)


@router.post("/workflow-templates")
async def create_template(
    request: WorkflowTemplateCreate,
    service: WorkflowTemplateService = Depends(get_template_service),
) -> dict[str, Any]:
    try:
        return await service.create_template(request)
    except (WorkflowTemplateError, DependencyCycleError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/workflow-templates")
def list_templates(
    workspace_id: str | None = None,
    enabled: bool | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: WorkflowTemplateService = Depends(get_template_service),
) -> dict[str, Any]:
    return {
        "templates": service.list_templates(
            workspace_id=workspace_id,
            enabled=enabled,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/workflow-templates/{template_id}")
def get_template(
    template_id: str,
    service: WorkflowTemplateService = Depends(get_template_service),
) -> dict[str, Any]:
    result = service.get_template(template_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow Template не найден.")
    return result


@router.post("/workflow-templates/{template_id}/validate")
def validate_template(
    template_id: str,
    service: WorkflowTemplateService = Depends(get_template_service),
) -> dict[str, Any]:
    template = service.get_template(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Workflow Template не найден.")
    return template["validation"]


@router.post("/workflow-templates/{template_id}/enable")
async def enable_template(
    template_id: str,
    service: WorkflowTemplateService = Depends(get_template_service),
) -> dict[str, Any]:
    result = await service.set_enabled(template_id, True)
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow Template не найден.")
    return result


@router.post("/workflow-templates/{template_id}/disable")
async def disable_template(
    template_id: str,
    service: WorkflowTemplateService = Depends(get_template_service),
) -> dict[str, Any]:
    result = await service.set_enabled(template_id, False)
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow Template не найден.")
    return result


@router.post("/workflow-templates/{template_id}/instantiate")
async def instantiate_template(
    template_id: str,
    request: WorkflowInstantiateRequest,
    service: WorkflowTemplateService = Depends(get_template_service),
) -> dict[str, Any]:
    try:
        result = await service.instantiate(template_id, request)
    except WorkflowTemplateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow Template не найден.")
    return result


@router.get("/workflow-instances/{instance_id}")
def get_instance(
    instance_id: str,
    service: WorkflowTemplateService = Depends(get_template_service),
) -> dict[str, Any]:
    result = service.get_instance(instance_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow Instance не найден.")
    return result


@router.post("/workflow-instances/{instance_id}/start")
async def start_instance(
    instance_id: str,
    service: WorkflowTemplateService = Depends(get_template_service),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    instance = service.get_instance(instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="Workflow Instance не найден.")
    engine = container.task_workflow_engine
    if engine is None:
        raise HTTPException(status_code=503, detail="Task Workflow Engine не запущен.")
    result = await engine.start_workflow(instance["root_task_id"])
    if result is None:
        raise HTTPException(status_code=404, detail="Root Task не найдена.")
    return {"instance_id": instance_id, **result}
