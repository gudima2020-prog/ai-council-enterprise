from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_container
from backend.control_center.governance import HumanControlGovernanceService
from backend.control_center.governance_schemas import (
    HumanControlApprovalCaseCancelRequest,
    HumanControlApprovalCaseRetryRequest,
    HumanControlApprovalPolicyCreate,
    HumanControlApprovalPolicyUpdate,
    HumanControlApprovalVoteRequest,
    HumanControlBootstrapOwnerRequest,
    HumanControlEscalationAcknowledgeRequest,
    HumanControlEscalationResolveRequest,
    HumanControlEscalationScanRequest,
    HumanControlManualEscalationRequest,
    HumanControlPermission,
    HumanControlRoleBindingCreate,
    HumanControlRoleBindingRevoke,
    HumanControlRoleCreate,
    HumanControlRoleUpdate,
)
from backend.control_center.schemas import HumanControlDecisionAction
from backend.control_center.security import (
    HumanControlPrincipal,
    HumanControlSecurityError,
    bind_actor,
    bind_workspace,
    enforce_workspace_value,
    require_permission,
    security_http_exception,
)
from backend.control_center.service import HumanControlConflict, HumanControlError, HumanControlNotFound
from backend.core.container import AppContainer

router = APIRouter(tags=["human-control-governance"])


def get_service(container: AppContainer = Depends(get_container)) -> HumanControlGovernanceService:
    service = container.human_control_governance_service
    if service is None:
        raise HTTPException(status_code=503, detail="Human Control Governance не запущен.")
    return service


def translate_error(exc: HumanControlError) -> HTTPException:
    if isinstance(exc, HumanControlSecurityError):
        return security_http_exception(exc)
    if isinstance(exc, HumanControlNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, HumanControlConflict):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/human-control/governance/status")
def status(service: HumanControlGovernanceService = Depends(get_service), _: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.VIEW.value))) -> dict[str, Any]:
    return service.status()


@router.post("/human-control/governance/bootstrap-owner")
async def bootstrap_owner(request: HumanControlBootstrapOwnerRequest, service: HumanControlGovernanceService = Depends(get_service)) -> dict[str, Any]:
    try:
        return await service.bootstrap_owner(request)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.post("/human-control/governance/roles")
async def create_role(request: HumanControlRoleCreate, service: HumanControlGovernanceService = Depends(get_service), principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.MANAGE_ROLES.value))) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        request = bind_workspace(request, principal)
        return await service.create_role(request)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.get("/human-control/governance/roles")
def list_roles(workspace_id: str | None = None, enabled: bool | None = None, service: HumanControlGovernanceService = Depends(get_service), principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.VIEW.value))) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return {"roles": service.list_roles(workspace_id=workspace_id, enabled=enabled)}
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.patch("/human-control/governance/roles/{role_id}")
async def update_role(role_id: str, request: HumanControlRoleUpdate, service: HumanControlGovernanceService = Depends(get_service), principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.MANAGE_ROLES.value))) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.update_role(role_id, request)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.post("/human-control/governance/role-bindings")
async def grant_binding(request: HumanControlRoleBindingCreate, service: HumanControlGovernanceService = Depends(get_service), principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.MANAGE_ROLES.value))) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal, field_name="granted_by")
        request = bind_workspace(request, principal)
        return await service.grant_binding(request)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.get("/human-control/governance/role-bindings")
def list_bindings(workspace_id: str | None = None, actor_id: str | None = None, enabled: bool | None = None, service: HumanControlGovernanceService = Depends(get_service), principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.VIEW.value))) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return {"bindings": service.list_bindings(workspace_id=workspace_id, actor_id=actor_id, enabled=enabled)}
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.post("/human-control/governance/role-bindings/{binding_id}/revoke")
async def revoke_binding(binding_id: str, request: HumanControlRoleBindingRevoke, service: HumanControlGovernanceService = Depends(get_service), principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.MANAGE_ROLES.value))) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.revoke_binding(binding_id, request)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.get("/human-control/governance/effective-access")
def effective_access(actor_id: str, workspace_id: str | None = None, service: HumanControlGovernanceService = Depends(get_service), principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUDIT.value))) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return service.effective_access(actor_id=actor_id, workspace_id=workspace_id)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.post("/human-control/governance/approval-policies")
async def create_policy(request: HumanControlApprovalPolicyCreate, service: HumanControlGovernanceService = Depends(get_service), principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.MANAGE_POLICIES.value))) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        request = bind_workspace(request, principal)
        return await service.create_policy(request)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.get("/human-control/governance/approval-policies")
def list_policies(workspace_id: str | None = None, enabled: bool | None = None, service: HumanControlGovernanceService = Depends(get_service), principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.VIEW.value))) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return {"policies": service.list_policies(workspace_id=workspace_id, enabled=enabled)}
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.patch("/human-control/governance/approval-policies/{policy_id}")
async def update_policy(policy_id: str, request: HumanControlApprovalPolicyUpdate, service: HumanControlGovernanceService = Depends(get_service), principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.MANAGE_POLICIES.value))) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.update_policy(policy_id, request)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.get("/human-control/items/{item_id}/approval-policy")
def match_policy(item_id: str, action: HumanControlDecisionAction, service: HumanControlGovernanceService = Depends(get_service), _: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.VIEW.value))) -> dict[str, Any]:
    try:
        return {"policy": service.match_policy(item_id=item_id, action=action)}
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.get("/human-control/approval-cases")
def list_cases(workspace_id: str | None = None, item_id: str | None = None, status: str | None = None, limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0), service: HumanControlGovernanceService = Depends(get_service), principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.VIEW.value))) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return {"cases": service.list_cases(workspace_id=workspace_id, item_id=item_id, status=status, limit=limit, offset=offset)}
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.get("/human-control/approval-cases/{case_id}")
def get_case(case_id: str, service: HumanControlGovernanceService = Depends(get_service), _: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.VIEW.value))) -> dict[str, Any]:
    result = service.get_case(case_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Approval Case не найден.")
    return result


@router.post("/human-control/approval-cases/{case_id}/vote")
async def cast_vote(case_id: str, request: HumanControlApprovalVoteRequest, service: HumanControlGovernanceService = Depends(get_service), principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.APPROVAL_VOTE.value))) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.cast_vote(case_id, request)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.post("/human-control/approval-cases/{case_id}/retry-execution")
async def retry_case_execution(case_id: str, request: HumanControlApprovalCaseRetryRequest, service: HumanControlGovernanceService = Depends(get_service), principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.OVERRIDE.value))) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.retry_case_execution(case_id, request)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.post("/human-control/approval-cases/{case_id}/cancel")
async def cancel_case(case_id: str, request: HumanControlApprovalCaseCancelRequest, service: HumanControlGovernanceService = Depends(get_service), principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.OVERRIDE.value))) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.cancel_case(case_id, request)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.post("/human-control/escalations/scan")
async def scan_escalations(request: HumanControlEscalationScanRequest, service: HumanControlGovernanceService = Depends(get_service), principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.ESCALATE.value))) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        workspace_id = enforce_workspace_value(request.workspace_id, principal)
        return await service.scan_escalations(workspace_id=workspace_id, actor_id=request.actor_id)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.post("/human-control/approval-cases/{case_id}/escalate")
async def create_manual_escalation(case_id: str, request: HumanControlManualEscalationRequest, service: HumanControlGovernanceService = Depends(get_service), principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.ESCALATE.value))) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.create_manual_escalation(case_id, request)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.get("/human-control/escalations")
def list_escalations(workspace_id: str | None = None, case_id: str | None = None, status: str | None = None, limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0), service: HumanControlGovernanceService = Depends(get_service), principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.VIEW.value))) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return {"escalations": service.list_escalations(workspace_id=workspace_id, case_id=case_id, status=status, limit=limit, offset=offset)}
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.post("/human-control/escalations/{escalation_id}/acknowledge")
async def acknowledge_escalation(escalation_id: str, request: HumanControlEscalationAcknowledgeRequest, service: HumanControlGovernanceService = Depends(get_service), principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.ESCALATE.value))) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.acknowledge_escalation(escalation_id, request)
    except HumanControlError as exc:
        raise translate_error(exc) from exc


@router.post("/human-control/escalations/{escalation_id}/resolve")
async def resolve_escalation(escalation_id: str, request: HumanControlEscalationResolveRequest, service: HumanControlGovernanceService = Depends(get_service), principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.ESCALATE.value))) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.resolve_escalation(escalation_id, request)
    except HumanControlError as exc:
        raise translate_error(exc) from exc
