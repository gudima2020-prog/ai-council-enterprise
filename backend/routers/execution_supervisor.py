from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_container
from backend.core.container import AppContainer
from backend.orchestration.supervisor import (
    ExecutionSupervisorError,
    ExecutionSupervisorService,
    SupervisorIncidentNotFound,
    SupervisorPolicyNotFound,
)
from backend.orchestration.supervisor_schemas import (
    SupervisorIncidentDecision,
    SupervisorInterventionRequest,
    SupervisorPolicyCreate,
    SupervisorPolicyUpdate,
)

router = APIRouter(tags=["execution-supervisor"])


def get_supervisor(
    container: AppContainer = Depends(get_container),
) -> ExecutionSupervisorService:
    service = container.execution_supervisor_service
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Execution Supervisor is not running.",
        )
    return service


@router.get("/execution-supervisor/status")
def supervisor_status(
    service: ExecutionSupervisorService = Depends(get_supervisor),
) -> dict[str, Any]:
    return service.stats()


@router.post("/execution-supervisor/scan")
async def scan_now(
    service: ExecutionSupervisorService = Depends(get_supervisor),
) -> dict[str, Any]:
    return await service.scan_once()


@router.post("/execution-supervisor/policies")
def create_policy(
    request: SupervisorPolicyCreate,
    service: ExecutionSupervisorService = Depends(get_supervisor),
) -> dict[str, Any]:
    try:
        return service.create_policy(request)
    except ExecutionSupervisorError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/execution-supervisor/policies")
def list_policies(
    service: ExecutionSupervisorService = Depends(get_supervisor),
) -> dict[str, Any]:
    return {"policies": service.list_policies()}


@router.patch("/execution-supervisor/policies/{policy_id}")
def update_policy(
    policy_id: str,
    request: SupervisorPolicyUpdate,
    service: ExecutionSupervisorService = Depends(get_supervisor),
) -> dict[str, Any]:
    try:
        return service.update_policy(policy_id, request)
    except SupervisorPolicyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExecutionSupervisorError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/execution-supervisor/effective-policy")
def effective_policy(
    workspace_id: str | None = None,
    service: ExecutionSupervisorService = Depends(get_supervisor),
) -> dict[str, Any]:
    policy = service.get_effective_policy(workspace_id)
    if policy is None:
        raise HTTPException(
            status_code=404,
            detail="No effective supervisor policy was found.",
        )
    return policy


@router.get("/execution-supervisor/incidents")
def list_incidents(
    plan_id: str | None = None,
    workspace_id: str | None = None,
    status: str | None = Query(
        default=None,
        pattern="^(open|acknowledged|resolved|dismissed)$",
    ),
    severity: str | None = Query(
        default=None,
        pattern="^(info|warning|high|critical)$",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: ExecutionSupervisorService = Depends(get_supervisor),
) -> dict[str, Any]:
    return {
        "incidents": service.list_incidents(
            plan_id=plan_id,
            workspace_id=workspace_id,
            status=status,
            severity=severity,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/execution-supervisor/incidents/{incident_id}")
def get_incident(
    incident_id: str,
    service: ExecutionSupervisorService = Depends(get_supervisor),
) -> dict[str, Any]:
    incident = service.get_incident(incident_id)
    if incident is None:
        raise HTTPException(
            status_code=404,
            detail="Supervisor incident not found.",
        )
    return incident


@router.post(
    "/execution-supervisor/incidents/{incident_id}/acknowledge"
)
async def acknowledge_incident(
    incident_id: str,
    request: SupervisorIncidentDecision,
    service: ExecutionSupervisorService = Depends(get_supervisor),
) -> dict[str, Any]:
    try:
        return await service.acknowledge_incident(
            incident_id,
            actor_id=request.actor_id,
            reason=request.reason,
        )
    except SupervisorIncidentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExecutionSupervisorError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/execution-supervisor/incidents/{incident_id}/resolve")
async def resolve_incident(
    incident_id: str,
    request: SupervisorIncidentDecision,
    dismissed: bool = False,
    service: ExecutionSupervisorService = Depends(get_supervisor),
) -> dict[str, Any]:
    try:
        return await service.resolve_incident(
            incident_id,
            actor_id=request.actor_id,
            reason=request.reason,
            dismissed=dismissed,
        )
    except SupervisorIncidentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/execution-supervisor/actions")
def list_actions(
    plan_id: str | None = None,
    incident_id: str | None = None,
    status: str | None = Query(
        default=None,
        pattern="^(pending|running|completed|failed|skipped)$",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: ExecutionSupervisorService = Depends(get_supervisor),
) -> dict[str, Any]:
    return {
        "actions": service.list_actions(
            plan_id=plan_id,
            incident_id=incident_id,
            status=status,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/execution-supervisor/actions/{action_id}")
def get_action(
    action_id: str,
    service: ExecutionSupervisorService = Depends(get_supervisor),
) -> dict[str, Any]:
    action = service.get_action(action_id)
    if action is None:
        raise HTTPException(
            status_code=404,
            detail="Supervisor action not found.",
        )
    return action


@router.post("/execution-plans/{plan_id}/supervisor/intervene")
async def intervene(
    plan_id: str,
    request: SupervisorInterventionRequest,
    service: ExecutionSupervisorService = Depends(get_supervisor),
) -> dict[str, Any]:
    try:
        result = await service.intervene(plan_id, request)
    except ExecutionSupervisorError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan not found.",
        )
    return result


@router.get("/execution-plans/{plan_id}/supervisor")
def plan_supervisor_overview(
    plan_id: str,
    service: ExecutionSupervisorService = Depends(get_supervisor),
) -> dict[str, Any]:
    result = service.plan_overview(plan_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan not found.",
        )
    return result
