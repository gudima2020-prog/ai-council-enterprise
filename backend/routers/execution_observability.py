from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from backend.api.dependencies import get_container
from backend.core.container import AppContainer
from backend.orchestration.observability import (
    ExecutionObservabilityError,
    ExecutionObservabilityService,
    ExecutionSLOBreachNotFound,
    ExecutionSLOPolicyNotFound,
)
from backend.orchestration.observability_schemas import (
    ExecutionSLOPolicyCreate,
    ExecutionSLOPolicyUpdate,
    MetricsCollectRequest,
    SLOBreachDecision,
    SLOEvaluationRequest,
)

router = APIRouter(tags=["execution-observability"])


def get_observability(
    container: AppContainer = Depends(get_container),
) -> ExecutionObservabilityService:
    service = container.execution_observability_service
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Execution Observability service is not running.",
        )
    return service


@router.get("/execution-observability/status")
def observability_status(
    service: ExecutionObservabilityService = Depends(get_observability),
) -> dict[str, Any]:
    return service.stats()


@router.get("/execution-observability/dashboard")
def dashboard(
    workspace_id: str | None = None,
    window_minutes: int = Query(default=60, ge=1, le=43200),
    service: ExecutionObservabilityService = Depends(get_observability),
) -> dict[str, Any]:
    return service.dashboard(
        workspace_id=workspace_id,
        window_minutes=window_minutes,
    )


@router.post("/execution-observability/collect")
async def collect_metrics(
    request: MetricsCollectRequest,
    service: ExecutionObservabilityService = Depends(get_observability),
) -> dict[str, Any]:
    return await service.collect_snapshot(
        workspace_id=request.workspace_id,
        window_minutes=request.window_minutes,
        persist=request.persist,
    )


@router.post("/execution-observability/evaluate")
async def evaluate_slos(
    request: SLOEvaluationRequest,
    service: ExecutionObservabilityService = Depends(get_observability),
) -> dict[str, Any]:
    return await service.evaluate_slos(
        workspace_id=request.workspace_id,
        policy_id=request.policy_id,
        persist_snapshot=request.persist_snapshot,
    )


@router.get("/execution-observability/prometheus")
def prometheus_metrics(
    workspace_id: str | None = None,
    window_minutes: int = Query(default=60, ge=1, le=43200),
    service: ExecutionObservabilityService = Depends(get_observability),
) -> Response:
    return Response(
        content=service.prometheus_metrics(
            workspace_id=workspace_id,
            window_minutes=window_minutes,
        ),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.post("/execution-observability/slo-policies")
def create_policy(
    request: ExecutionSLOPolicyCreate,
    service: ExecutionObservabilityService = Depends(get_observability),
) -> dict[str, Any]:
    try:
        return service.create_policy(request)
    except ExecutionObservabilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/execution-observability/slo-policies")
def list_policies(
    service: ExecutionObservabilityService = Depends(get_observability),
) -> dict[str, Any]:
    return {"policies": service.list_policies()}


@router.get("/execution-observability/slo-policies/effective")
def effective_policy(
    workspace_id: str | None = None,
    service: ExecutionObservabilityService = Depends(get_observability),
) -> dict[str, Any]:
    policy = service.get_effective_policy(workspace_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="SLO policy not found.")
    return policy


@router.get("/execution-observability/slo-policies/{policy_id}")
def get_policy(
    policy_id: str,
    service: ExecutionObservabilityService = Depends(get_observability),
) -> dict[str, Any]:
    policy = service.get_policy(policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="SLO policy not found.")
    return policy


@router.patch("/execution-observability/slo-policies/{policy_id}")
def update_policy(
    policy_id: str,
    request: ExecutionSLOPolicyUpdate,
    service: ExecutionObservabilityService = Depends(get_observability),
) -> dict[str, Any]:
    try:
        return service.update_policy(policy_id, request)
    except ExecutionSLOPolicyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/execution-observability/snapshots")
def list_snapshots(
    workspace_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: ExecutionObservabilityService = Depends(get_observability),
) -> dict[str, Any]:
    return {
        "snapshots": service.list_snapshots(
            workspace_id=workspace_id,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/execution-observability/slo-breaches")
def list_breaches(
    workspace_id: str | None = None,
    policy_id: str | None = None,
    status: str | None = Query(
        default=None,
        pattern="^(open|resolved|dismissed)$",
    ),
    severity: str | None = Query(
        default=None,
        pattern="^(warning|high|critical)$",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: ExecutionObservabilityService = Depends(get_observability),
) -> dict[str, Any]:
    return {
        "breaches": service.list_breaches(
            workspace_id=workspace_id,
            policy_id=policy_id,
            status=status,
            severity=severity,
            limit=limit,
            offset=offset,
        )
    }


@router.post("/execution-observability/slo-breaches/{breach_id}/resolve")
async def resolve_breach(
    breach_id: str,
    request: SLOBreachDecision,
    dismissed: bool = False,
    service: ExecutionObservabilityService = Depends(get_observability),
) -> dict[str, Any]:
    try:
        return await service.resolve_breach(
            breach_id,
            actor_id=request.actor_id,
            reason=request.reason,
            dismissed=dismissed,
        )
    except ExecutionSLOBreachNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
