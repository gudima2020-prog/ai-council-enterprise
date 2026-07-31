from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_policy_approval_service
from backend.control_center.governance_schemas import HumanControlPermission
from backend.control_center.security import (
    HumanControlPrincipal,
    HumanControlSecurityError,
    enforce_workspace_value,
    require_permission,
    security_http_exception,
)
from backend.policy_approvals.core import (
    PolicyApprovalExpiredError,
    PolicyApprovalStateError,
    PolicyApprovalStatus,
    PolicyApprovalTokenError,
)
from backend.policy_approvals.schemas import (
    PolicyApprovalCreateRequest,
    PolicyApprovalDecisionRequest,
)
from backend.policy_approvals.service import (
    PolicyApprovalNotFoundError,
    PolicyApprovalService,
    PolicyApprovalServiceError,
    PolicyApprovalWorkspaceError,
)
from backend.runtime_policy import PolicyOperation


router = APIRouter(
    prefix="/workspaces/{workspace_id}/policy-approvals",
    tags=["policy-approvals"],
)


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PolicyApprovalNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, PolicyApprovalWorkspaceError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(
        exc,
        (
            PolicyApprovalExpiredError,
            PolicyApprovalStateError,
        ),
    ):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, PolicyApprovalTokenError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, PolicyApprovalServiceError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(
        status_code=500,
        detail="Policy approval operation failed.",
    )


def _bound_workspace(
    workspace_id: str,
    principal: HumanControlPrincipal,
) -> str:
    try:
        resolved = enforce_workspace_value(
            workspace_id,
            principal,
        )
    except HumanControlSecurityError as exc:
        raise security_http_exception(exc) from exc

    if not resolved:
        raise HTTPException(
            status_code=400,
            detail="A Workspace is required.",
        )
    return resolved


def _bound_actor(
    provided_actor: str | None,
    principal: HumanControlPrincipal,
    *,
    required: bool,
) -> str | None:
    normalized = (
        provided_actor.strip()
        if provided_actor and provided_actor.strip()
        else None
    )
    principal_actor = principal.actor_id or principal.identity_id

    if principal.authenticated and principal_actor:
        if normalized and normalized != principal_actor:
            raise HTTPException(
                status_code=403,
                detail=(
                    "actor_id does not match the authenticated "
                    "Human Control principal."
                ),
            )
        return principal_actor

    if normalized:
        return normalized

    if required:
        raise HTTPException(
            status_code=400,
            detail=(
                "actor_id is required when the request is not "
                "bound to an authenticated operator."
            ),
        )
    return None


@router.post("")
async def request_policy_approval(
    workspace_id: str,
    request: PolicyApprovalCreateRequest,
    service: PolicyApprovalService = Depends(
        get_policy_approval_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(
            HumanControlPermission.APPROVAL_INITIATE.value
        )
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(workspace_id, principal)
    requested_by = _bound_actor(
        request.requested_by,
        principal,
        required=False,
    )

    try:
        result = await service.request(
            scope=request.scope.to_domain(
                workspace_id=workspace_id
            ),
            reason_codes=request.reason_codes,
            requested_by=requested_by,
            request_note=request.request_note,
            ttl_seconds=request.ttl_seconds,
            metadata=request.metadata,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc

    return {
        "created": result.created,
        "approval": result.record.to_public_dict(),
    }


@router.get("")
def list_policy_approvals(
    workspace_id: str,
    status: PolicyApprovalStatus | None = None,
    operation: PolicyOperation | None = None,
    subject_type: str | None = Query(
        default=None,
        max_length=64,
    ),
    subject_id: str | None = Query(
        default=None,
        max_length=255,
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: PolicyApprovalService = Depends(
        get_policy_approval_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.VIEW.value)
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(workspace_id, principal)

    try:
        records = service.list(
            workspace_id=workspace_id,
            status=status,
            operation=operation,
            subject_type=subject_type,
            subject_id=subject_id,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc

    return {
        "workspace_id": workspace_id,
        "items": [
            record.to_public_dict()
            for record in records
        ],
        "limit": limit,
        "offset": offset,
    }


@router.post("/reconcile-expired")
async def reconcile_expired_policy_approvals(
    workspace_id: str,
    service: PolicyApprovalService = Depends(
        get_policy_approval_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.OVERRIDE.value)
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(workspace_id, principal)

    try:
        return await service.reconcile_expired(
            workspace_id=workspace_id
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/{approval_id}/evidence")
def get_policy_approval_evidence(
    workspace_id: str,
    approval_id: str,
    service: PolicyApprovalService = Depends(
        get_policy_approval_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.AUDIT.value)
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(workspace_id, principal)

    try:
        items = service.evidence(
            approval_id=approval_id,
            workspace_id=workspace_id,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc

    return {
        "workspace_id": workspace_id,
        "approval_id": approval_id,
        "items": items,
    }


@router.get("/{approval_id}")
def get_policy_approval(
    workspace_id: str,
    approval_id: str,
    service: PolicyApprovalService = Depends(
        get_policy_approval_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.VIEW.value)
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(workspace_id, principal)

    try:
        record = service.get(
            approval_id=approval_id,
            workspace_id=workspace_id,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc

    return record.to_public_dict()


@router.post("/{approval_id}/approve")
async def approve_policy_approval(
    workspace_id: str,
    approval_id: str,
    request: PolicyApprovalDecisionRequest,
    service: PolicyApprovalService = Depends(
        get_policy_approval_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(
            HumanControlPermission.APPROVAL_VOTE.value
        )
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(workspace_id, principal)
    actor_id = _bound_actor(
        request.actor_id,
        principal,
        required=True,
    )

    try:
        grant = await service.approve(
            approval_id=approval_id,
            workspace_id=workspace_id,
            decided_by=actor_id,
            note=request.note,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc

    return {
        "approval": grant.record.to_public_dict(),
        "token": grant.token,
        "token_delivery": "one_time",
    }


@router.post("/{approval_id}/deny")
async def deny_policy_approval(
    workspace_id: str,
    approval_id: str,
    request: PolicyApprovalDecisionRequest,
    service: PolicyApprovalService = Depends(
        get_policy_approval_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(
            HumanControlPermission.APPROVAL_VOTE.value
        )
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(workspace_id, principal)
    actor_id = _bound_actor(
        request.actor_id,
        principal,
        required=True,
    )

    try:
        record = await service.deny(
            approval_id=approval_id,
            workspace_id=workspace_id,
            decided_by=actor_id,
            note=request.note,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc

    return record.to_public_dict()


@router.post("/{approval_id}/revoke")
async def revoke_policy_approval(
    workspace_id: str,
    approval_id: str,
    request: PolicyApprovalDecisionRequest,
    service: PolicyApprovalService = Depends(
        get_policy_approval_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.OVERRIDE.value)
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(workspace_id, principal)
    actor_id = _bound_actor(
        request.actor_id,
        principal,
        required=True,
    )

    try:
        record = await service.revoke(
            approval_id=approval_id,
            workspace_id=workspace_id,
            revoked_by=actor_id,
            note=request.note,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc

    return record.to_public_dict()
