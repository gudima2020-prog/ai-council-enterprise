from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_container
from backend.control_center.governance import HumanControlGovernanceService
from backend.control_center.governance_schemas import HumanControlPermission
from backend.control_center.schemas import (
    HumanControlBulkDecisionEntry,
    HumanControlBulkDecisionRequest,
    HumanControlClaimRequest,
    HumanControlDecisionRequest,
    HumanControlItemStatus,
    HumanControlReleaseRequest,
    HumanControlRiskLevel,
    HumanControlSnoozeRequest,
    HumanControlSourceType,
    HumanControlSyncRequest,
)
from backend.control_center.security import (
    HumanControlPrincipal,
    HumanControlSecurityError,
    bind_actor,
    enforce_workspace_value,
    require_permission,
    security_http_exception,
)
from backend.control_center.service import (
    HumanControlCenterService,
    HumanControlConflict,
    HumanControlError,
    HumanControlNotFound,
)
from backend.core.container import AppContainer


router = APIRouter(tags=["human-control-center"])


def get_governance_service(
    container: AppContainer = Depends(get_container),
) -> HumanControlGovernanceService | None:
    return container.human_control_governance_service


def get_service(
    container: AppContainer = Depends(get_container),
) -> HumanControlCenterService:
    service = container.human_control_center_service
    if service is None:
        raise HTTPException(status_code=503, detail="Human Control Center не запущен.")
    return service


def translate_error(exc: HumanControlError) -> HTTPException:
    if isinstance(exc, HumanControlNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, HumanControlConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, HumanControlSecurityError):
        return security_http_exception(exc)
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/human-control/status")
def status(
    service: HumanControlCenterService = Depends(get_service),
    _: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.VIEW.value)),
) -> dict[str, Any]:
    return service.status()


@router.post("/human-control/sync")
async def sync(
    request: HumanControlSyncRequest,
    service: HumanControlCenterService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.MANAGE_POLICIES.value)),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        workspace_id = enforce_workspace_value(request.workspace_id, principal)
        return await service.sync(workspace_id=workspace_id, actor_id=request.actor_id)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.get("/human-control/dashboard")
async def dashboard(
    workspace_id: str | None = None,
    refresh: bool = True,
    service: HumanControlCenterService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.VIEW.value)),
) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return await service.dashboard(workspace_id=workspace_id, refresh=refresh)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.get("/human-control/items")
def list_items(
    workspace_id: str | None = None,
    status: HumanControlItemStatus | None = None,
    source_type: HumanControlSourceType | None = None,
    risk_level: HumanControlRiskLevel | None = None,
    assigned_to: str | None = None,
    overdue_only: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: HumanControlCenterService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.VIEW.value)),
) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return {"items": service.list_items(workspace_id=workspace_id, status=status.value if status else None, source_type=source_type.value if source_type else None, risk_level=risk_level.value if risk_level else None, assigned_to=assigned_to, overdue_only=overdue_only, limit=limit, offset=offset)}
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.post("/human-control/items/bulk-decision")
async def bulk_decision(
    request: HumanControlBulkDecisionRequest,
    service: HumanControlCenterService = Depends(get_service),
    governance: HumanControlGovernanceService | None = Depends(get_governance_service),
    principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.DECIDE.value)),
) -> dict[str, Any]:
    try:
        request = request.model_copy(update={"decisions": [HumanControlBulkDecisionEntry(item_id=entry.item_id, decision=bind_actor(entry.decision, principal)) for entry in request.decisions]})
        if governance is not None:
            return await governance.bulk_submit_decisions(request)
        return await service.bulk_decide(request)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.get("/human-control/items/{item_id}")
def get_item(
    item_id: str,
    service: HumanControlCenterService = Depends(get_service),
    _: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.VIEW.value)),
) -> dict[str, Any]:
    result = service.get_item(item_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Human Control Item не найден.")
    return result


@router.post("/human-control/items/{item_id}/claim")
async def claim(
    item_id: str,
    request: HumanControlClaimRequest,
    service: HumanControlCenterService = Depends(get_service),
    governance: HumanControlGovernanceService | None = Depends(get_governance_service),
    principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.CLAIM.value)),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        if governance is not None:
            await governance.authorize_operator_action(item_id=item_id, actor_id=request.actor_id, permission=HumanControlPermission.CLAIM.value, force=request.force)
        return await service.claim(item_id, request)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.post("/human-control/items/{item_id}/release")
async def release(
    item_id: str,
    request: HumanControlReleaseRequest,
    service: HumanControlCenterService = Depends(get_service),
    governance: HumanControlGovernanceService | None = Depends(get_governance_service),
    principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.CLAIM.value)),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        if governance is not None:
            await governance.authorize_operator_action(item_id=item_id, actor_id=request.actor_id, permission=HumanControlPermission.CLAIM.value, force=request.force)
        return await service.release_claim(item_id, request)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.post("/human-control/items/{item_id}/snooze")
async def snooze(
    item_id: str,
    request: HumanControlSnoozeRequest,
    service: HumanControlCenterService = Depends(get_service),
    governance: HumanControlGovernanceService | None = Depends(get_governance_service),
    principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.SNOOZE.value)),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        if governance is not None:
            await governance.authorize_operator_action(item_id=item_id, actor_id=request.actor_id, permission=HumanControlPermission.SNOOZE.value)
        return await service.snooze(item_id, request)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.post("/human-control/items/{item_id}/decision")
async def decide(
    item_id: str,
    request: HumanControlDecisionRequest,
    service: HumanControlCenterService = Depends(get_service),
    governance: HumanControlGovernanceService | None = Depends(get_governance_service),
    principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.DECIDE.value)),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        if governance is not None:
            return await governance.submit_decision(item_id, request)
        return await service.decide(item_id, request)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.get("/human-control/actions")
def list_actions(
    workspace_id: str | None = None,
    item_id: str | None = None,
    actor_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: HumanControlCenterService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUDIT.value)),
) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return {"actions": service.list_actions(workspace_id=workspace_id, item_id=item_id, actor_id=actor_id, limit=limit, offset=offset)}
    except HumanControlError as exc:
        raise translate_error(exc) from exc
