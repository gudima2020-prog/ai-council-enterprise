from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from backend.api.dependencies import get_container
from backend.control_center.auth import HumanControlAuthService
from backend.control_center.auth_schemas import (
    HumanControlApiTokenCreate,
    HumanControlApiTokenRevoke,
    HumanControlAuthPolicyUpsert,
    HumanControlBootstrapIdentityRequest,
    HumanControlBreakGlassActivate,
    HumanControlBreakGlassDecision,
    HumanControlBreakGlassRequestCreate,
    HumanControlBreakGlassRevoke,
    HumanControlIdentityCreate,
    HumanControlIdentityUpdate,
    HumanControlLoginRequest,
    HumanControlSessionRevokeRequest,
)
from backend.control_center.governance_schemas import HumanControlPermission
from backend.control_center.security import (
    HumanControlPrincipal,
    HumanControlSecurityError,
    bind_actor,
    bind_identity,
    bind_workspace,
    require_authenticated,
    require_permission,
    security_http_exception,
)
from backend.control_center.service import HumanControlConflict, HumanControlError, HumanControlNotFound
from backend.core.container import AppContainer

router = APIRouter(tags=["human-control-auth"])


def get_service(container: AppContainer = Depends(get_container)) -> HumanControlAuthService:
    service = container.human_control_auth_service
    if service is None:
        raise HTTPException(status_code=503, detail="Human Control Auth не запущен.")
    return service


def translate(exc: HumanControlError) -> HTTPException:
    if isinstance(exc, HumanControlSecurityError):
        return security_http_exception(exc)
    if isinstance(exc, HumanControlNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, HumanControlConflict):
        return HTTPException(status_code=409, detail=str(exc))
    lowered = str(exc).lower()
    return HTTPException(status_code=401 if any(value in lowered for value in ("credential", "token", "session")) else 400, detail=str(exc))


@router.get("/human-control/auth/status")
def status(service: HumanControlAuthService = Depends(get_service)) -> dict[str, Any]:
    return service.status()


@router.get("/human-control/auth/policy")
def policy(
    workspace_id: str | None = None,
    service: HumanControlAuthService = Depends(get_service),
    _: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUTH_VIEW.value)),
) -> dict[str, Any]:
    return service.effective_policy(workspace_id)


@router.put("/human-control/auth/policy")
async def upsert_policy(
    request: HumanControlAuthPolicyUpsert,
    service: HumanControlAuthService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUTH_MANAGE.value)),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        request = bind_workspace(request, principal)
        return await service.upsert_policy(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/auth/bootstrap-identity")
async def bootstrap(request: HumanControlBootstrapIdentityRequest, service: HumanControlAuthService = Depends(get_service)) -> dict[str, Any]:
    try:
        return await service.bootstrap_identity(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/auth/identities")
async def create_identity(
    request: HumanControlIdentityCreate,
    service: HumanControlAuthService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUTH_MANAGE.value)),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal, field_name="created_by")
        return await service.create_identity(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/auth/identities")
def list_identities(
    status: str | None = None,
    service: HumanControlAuthService = Depends(get_service),
    _: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUTH_VIEW.value)),
) -> dict[str, Any]:
    return {"identities": service.list_identities(status)}


@router.patch("/human-control/auth/identities/{identity_id}")
async def update_identity(
    identity_id: str,
    request: HumanControlIdentityUpdate,
    service: HumanControlAuthService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUTH_MANAGE.value)),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.update_identity(identity_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/auth/login")
async def login(request: HumanControlLoginRequest, service: HumanControlAuthService = Depends(get_service)) -> dict[str, Any]:
    try:
        return await service.login(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/auth/whoami")
def whoami(
    principal: HumanControlPrincipal = Depends(require_authenticated()),
) -> dict[str, Any]:
    return principal.as_dict()


@router.get("/human-control/auth/sessions")
def sessions(
    identity_id: str | None = None,
    service: HumanControlAuthService = Depends(get_service),
    _: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUTH_VIEW.value)),
) -> dict[str, Any]:
    return {"sessions": service.list_sessions(identity_id)}


@router.post("/human-control/auth/sessions/{session_id}/revoke")
async def revoke_session(
    session_id: str,
    request: HumanControlSessionRevokeRequest,
    service: HumanControlAuthService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUTH_MANAGE.value)),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.revoke_session(session_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/auth/api-tokens")
async def create_api_token(
    request: HumanControlApiTokenCreate,
    service: HumanControlAuthService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUTH_TOKEN_MANAGE.value)),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal, field_name="created_by")
        request = bind_workspace(request, principal)
        return await service.create_api_token(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/auth/api-tokens")
def api_tokens(
    identity_id: str | None = None,
    service: HumanControlAuthService = Depends(get_service),
    _: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUTH_VIEW.value)),
) -> dict[str, Any]:
    return {"api_tokens": service.list_api_tokens(identity_id)}


@router.post("/human-control/auth/api-tokens/{token_id}/revoke")
async def revoke_api_token(
    token_id: str,
    request: HumanControlApiTokenRevoke,
    service: HumanControlAuthService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUTH_TOKEN_MANAGE.value)),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.revoke_api_token(token_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/auth/break-glass")
async def request_break_glass(
    request: HumanControlBreakGlassRequestCreate,
    service: HumanControlAuthService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(require_authenticated()),
) -> dict[str, Any]:
    try:
        request = bind_identity(request, principal, field_name="requested_by_identity_id")
        request = bind_workspace(request, principal)
        return await service.request_break_glass(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/auth/break-glass")
def list_break_glass(
    status: str | None = None,
    service: HumanControlAuthService = Depends(get_service),
    _: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUTH_BREAK_GLASS.value)),
) -> dict[str, Any]:
    return {"requests": service.list_break_glass(status)}


@router.post("/human-control/auth/break-glass/{request_id}/decision")
async def decide_break_glass(
    request_id: str,
    request: HumanControlBreakGlassDecision,
    service: HumanControlAuthService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUTH_BREAK_GLASS.value)),
) -> dict[str, Any]:
    try:
        request = bind_identity(request, principal, field_name="approver_identity_id")
        return await service.decide_break_glass(request_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/auth/break-glass/activate")
async def activate_break_glass(request: HumanControlBreakGlassActivate, service: HumanControlAuthService = Depends(get_service)) -> dict[str, Any]:
    try:
        return await service.activate_break_glass(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/auth/break-glass/{request_id}/revoke")
async def revoke_break_glass(
    request_id: str,
    request: HumanControlBreakGlassRevoke,
    service: HumanControlAuthService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUTH_BREAK_GLASS.value)),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.revoke_break_glass(request_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/auth/security-events")
def security_events(
    limit: int = Query(default=100, ge=1, le=500),
    service: HumanControlAuthService = Depends(get_service),
    _: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUDIT.value)),
) -> dict[str, Any]:
    return {"events": service.security_events(limit)}


@router.post("/human-control/auth/reconcile")
async def reconcile(
    service: HumanControlAuthService = Depends(get_service),
    _: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUTH_MANAGE.value)),
) -> dict[str, Any]:
    return await service.reconcile()
