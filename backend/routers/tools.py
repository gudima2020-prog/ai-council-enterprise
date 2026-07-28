from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.api.dependencies import get_container, get_db_session
from backend.core.container import AppContainer
from backend.orchestration.tool_runtime import (
    ToolExecutionError,
    ToolExecutionRuntime,
)
from backend.orchestration.tool_schemas import (
    DirectToolExecutionRequest,
    ToolCreate,
    ToolPermissionCreate,
    ToolPermissionEvaluationRequest,
    ToolUpdate,
)
from backend.orchestration.tools import (
    ToolPermissionDenied,
    ToolRegistryError,
    ToolRegistryService,
    ToolRepository,
)

router = APIRouter(tags=["tools"])


def get_tool_service(
    session: Session = Depends(get_db_session),
    container: AppContainer = Depends(get_container),
) -> ToolRegistryService:
    return container.tool_registry_service(session)


def get_tool_runtime(
    container: AppContainer = Depends(get_container),
) -> ToolExecutionRuntime:
    runtime = container.tool_execution_runtime
    if runtime is None:
        raise HTTPException(
            status_code=503,
            detail="Tool Execution Runtime не запущен.",
        )
    return runtime


@router.get("/tool-runtime/status")
def tool_runtime_status(
    runtime: ToolExecutionRuntime = Depends(get_tool_runtime),
) -> dict[str, Any]:
    return runtime.stats()


@router.post("/tools")
async def create_tool(
    request: ToolCreate,
    service: ToolRegistryService = Depends(get_tool_service),
) -> dict[str, Any]:
    try:
        return await service.create(request)
    except ToolRegistryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/tools")
def list_tools(
    workspace_id: str | None = None,
    include_global: bool = True,
    enabled: bool | None = None,
    risk_level: str | None = None,
    kind: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: ToolRegistryService = Depends(get_tool_service),
) -> dict[str, Any]:
    return {
        "tools": service.list(
            workspace_id=workspace_id,
            include_global=include_global,
            enabled=enabled,
            risk_level=risk_level,
            kind=kind,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/tools/{tool_id}")
def get_tool(
    tool_id: str,
    service: ToolRegistryService = Depends(get_tool_service),
) -> dict[str, Any]:
    result = service.get(tool_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Tool не найден.")
    return result


@router.patch("/tools/{tool_id}")
async def update_tool(
    tool_id: str,
    request: ToolUpdate,
    service: ToolRegistryService = Depends(get_tool_service),
) -> dict[str, Any]:
    result = await service.update(tool_id, request)
    if result is None:
        raise HTTPException(status_code=404, detail="Tool не найден.")
    return result


@router.delete("/tools/{tool_id}")
async def delete_tool(
    tool_id: str,
    service: ToolRegistryService = Depends(get_tool_service),
) -> dict[str, bool]:
    try:
        deleted = await service.delete(tool_id)
    except ToolRegistryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Tool не найден.")
    return {"deleted": True}


@router.post("/tools/{tool_id}/permissions")
async def add_tool_permission(
    tool_id: str,
    request: ToolPermissionCreate,
    service: ToolRegistryService = Depends(get_tool_service),
) -> dict[str, Any]:
    try:
        result = await service.add_permission(tool_id, request)
    except ToolRegistryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Tool не найден.")
    return result


@router.get("/tools/{tool_id}/permissions")
def list_tool_permissions(
    tool_id: str,
    service: ToolRegistryService = Depends(get_tool_service),
) -> dict[str, Any]:
    result = service.list_permissions(tool_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Tool не найден.")
    return {"tool_id": tool_id, "permissions": result}


@router.delete("/tools/{tool_id}/permissions/{permission_id}")
async def delete_tool_permission(
    tool_id: str,
    permission_id: str,
    service: ToolRegistryService = Depends(get_tool_service),
) -> dict[str, bool]:
    result = await service.delete_permission(tool_id, permission_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Tool не найден.")
    if not result:
        raise HTTPException(status_code=404, detail="Permission не найдено.")
    return {"deleted": True}


@router.post("/tools/{tool_id}/permission/evaluate")
def evaluate_tool_permission(
    tool_id: str,
    request: ToolPermissionEvaluationRequest,
    session: Session = Depends(get_db_session),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    repository = ToolRepository(session)
    tool = repository.get(tool_id)
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool не найден.")
    service = container.tool_registry_service(session)
    return service.evaluate(
        tool=tool,
        workspace_id=request.workspace_id,
        agent_id=request.agent_id,
        input_data=request.input,
    )


@router.post("/tools/{tool_id}/execute")
async def execute_tool(
    tool_id: str,
    request: DirectToolExecutionRequest,
    runtime: ToolExecutionRuntime = Depends(get_tool_runtime),
) -> dict[str, Any]:
    try:
        result = await runtime.execute_direct(tool_id, request)
    except ToolPermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ToolExecutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Tool не найден.")
    return result


@router.get("/tool-invocations")
def list_tool_invocations(
    tool_id: str | None = None,
    workspace_id: str | None = None,
    agent_id: str | None = None,
    plan_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: ToolRegistryService = Depends(get_tool_service),
) -> dict[str, Any]:
    return {
        "invocations": service.list_invocations(
            tool_id=tool_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            plan_id=plan_id,
            status=status,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/tool-invocations/{invocation_id}")
def get_tool_invocation(
    invocation_id: str,
    service: ToolRegistryService = Depends(get_tool_service),
) -> dict[str, Any]:
    result = service.get_invocation(invocation_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Tool Invocation не найден.",
        )
    return result
