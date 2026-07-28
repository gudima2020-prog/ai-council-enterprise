from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.api.dependencies import get_container, get_db_session
from backend.core.container import AppContainer
from backend.orchestration.agent_schemas import (
    AgentAssignmentRequest,
    AgentCapabilityCreate,
    AgentCapabilityUpdate,
    AgentCreate,
    AgentManualAssignmentRequest,
    AgentMatchRequest,
    AgentUpdate,
)
from backend.orchestration.agents import (
    AgentAssignmentService,
    AgentNotFound,
    AgentRegistryError,
    AgentRegistryService,
)

router = APIRouter(tags=["agents"])


def get_registry(
    session: Session = Depends(get_db_session),
    container: AppContainer = Depends(get_container),
) -> AgentRegistryService:
    return container.agent_registry_service(session)


def get_assignment_service(
    session: Session = Depends(get_db_session),
    container: AppContainer = Depends(get_container),
) -> AgentAssignmentService:
    return container.agent_assignment_service(session)


def conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.post("/agents")
async def create_agent(
    request: AgentCreate,
    service: AgentRegistryService = Depends(get_registry),
) -> dict[str, Any]:
    try:
        return await service.create(request)
    except AgentRegistryError as exc:
        raise conflict(exc) from exc


@router.get("/agents")
def list_agents(
    workspace_id: str | None = None,
    enabled: bool | None = None,
    status: str | None = None,
    role: str | None = None,
    capability: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: AgentRegistryService = Depends(get_registry),
) -> dict[str, list[dict[str, Any]]]:
    return {
        "agents": service.list(
            workspace_id=workspace_id,
            enabled=enabled,
            status=status,
            role=role.strip().lower() if role else None,
            capability=(
                capability.strip().lower() if capability else None
            ),
            limit=limit,
            offset=offset,
        )
    }


@router.post("/agents/match")
def match_agents(
    request: AgentMatchRequest,
    service: AgentRegistryService = Depends(get_registry),
) -> dict[str, Any]:
    matches = service.match(request)
    return {"matches": matches, "count": len(matches)}


@router.get("/agents/{agent_id}")
def get_agent(
    agent_id: str,
    service: AgentRegistryService = Depends(get_registry),
) -> dict[str, Any]:
    result = service.get(agent_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Agent не найден.")
    return result


@router.patch("/agents/{agent_id}")
async def update_agent(
    agent_id: str,
    request: AgentUpdate,
    service: AgentRegistryService = Depends(get_registry),
) -> dict[str, Any]:
    result = await service.update(agent_id, request)
    if result is None:
        raise HTTPException(status_code=404, detail="Agent не найден.")
    return result


@router.delete("/agents/{agent_id}")
async def delete_agent(
    agent_id: str,
    service: AgentRegistryService = Depends(get_registry),
) -> dict[str, bool]:
    try:
        deleted = await service.delete(agent_id)
    except AgentRegistryError as exc:
        raise conflict(exc) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Agent не найден.")
    return {"deleted": True}


@router.post("/agents/{agent_id}/capabilities")
async def add_capability(
    agent_id: str,
    request: AgentCapabilityCreate,
    service: AgentRegistryService = Depends(get_registry),
) -> dict[str, Any]:
    try:
        result = await service.add_capability(agent_id, request)
    except AgentRegistryError as exc:
        raise conflict(exc) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Agent не найден.")
    return result


@router.patch("/agents/{agent_id}/capabilities/{capability_id}")
async def update_capability(
    agent_id: str,
    capability_id: str,
    request: AgentCapabilityUpdate,
    service: AgentRegistryService = Depends(get_registry),
) -> dict[str, Any]:
    try:
        result = await service.update_capability(
            agent_id,
            capability_id,
            request,
        )
    except AgentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Agent не найден.")
    return result


@router.delete("/agents/{agent_id}/capabilities/{capability_id}")
async def delete_capability(
    agent_id: str,
    capability_id: str,
    service: AgentRegistryService = Depends(get_registry),
) -> dict[str, Any]:
    try:
        result = await service.delete_capability(agent_id, capability_id)
    except AgentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Agent не найден.")
    return result


@router.get("/execution-plans/{plan_id}/agent-assignments")
def assignment_status(
    plan_id: str,
    service: AgentAssignmentService = Depends(get_assignment_service),
) -> dict[str, Any]:
    result = service.assignment_status(plan_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return result


@router.post("/execution-plans/{plan_id}/assign-agents")
async def assign_plan_agents(
    plan_id: str,
    request: AgentAssignmentRequest,
    service: AgentAssignmentService = Depends(get_assignment_service),
) -> dict[str, Any]:
    try:
        result = await service.assign_plan(plan_id, request)
    except AgentRegistryError as exc:
        raise conflict(exc) from exc
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return result


@router.get(
    "/execution-plans/{plan_id}/steps/{step_id}/agent-selection"
)
def preview_step_agent_selection(
    plan_id: str,
    step_id: str,
    limit: int = Query(default=20, ge=1, le=200),
    service: AgentAssignmentService = Depends(get_assignment_service),
) -> dict[str, Any]:
    try:
        result = service.preview_step(plan_id, step_id, limit=limit)
    except AgentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AgentRegistryError as exc:
        raise conflict(exc) from exc
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return result


@router.post(
    "/execution-plans/{plan_id}/steps/{step_id}/assign-agent"
)
async def assign_step_agent(
    plan_id: str,
    step_id: str,
    request: AgentManualAssignmentRequest | None = None,
    service: AgentAssignmentService = Depends(get_assignment_service),
) -> dict[str, Any]:
    try:
        result = await service.assign_step(plan_id, step_id, request)
    except AgentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AgentRegistryError as exc:
        raise conflict(exc) from exc
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return result


@router.delete(
    "/execution-plans/{plan_id}/steps/{step_id}/assigned-agent"
)
async def clear_step_agent(
    plan_id: str,
    step_id: str,
    service: AgentAssignmentService = Depends(get_assignment_service),
) -> dict[str, Any]:
    try:
        result = await service.clear_assignment(plan_id, step_id)
    except AgentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AgentRegistryError as exc:
        raise conflict(exc) from exc
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return result
