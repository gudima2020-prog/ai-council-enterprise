from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from backend.api.dependencies import get_container
from backend.control_center.browser_schemas import (
    HumanControlBrowserLoginRequest,
    HumanControlBrowserLogoutRequest,
    HumanControlBrowserPolicyUpsert,
    HumanControlCsrfRotateRequest,
    HumanControlTrustedClientCreate,
    HumanControlTrustedClientUpdate,
)
from backend.control_center.browser_security import HumanControlBrowserSecurityService
from backend.control_center.governance_schemas import HumanControlPermission
from backend.control_center.security import (
    HumanControlPrincipal,
    bind_actor,
    bind_workspace,
    require_authenticated,
    require_permission,
)
from backend.control_center.service import HumanControlConflict, HumanControlError, HumanControlNotFound
from backend.core.container import AppContainer

router = APIRouter(tags=["human-control-browser-security"])


def get_service(container: AppContainer = Depends(get_container)) -> HumanControlBrowserSecurityService:
    service = container.human_control_browser_security_service
    if service is None:
        raise HTTPException(status_code=503, detail="Browser Security service is not running.")
    return service


def translate(exc: HumanControlError) -> HTTPException:
    if isinstance(exc, HumanControlNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, HumanControlConflict):
        return HTTPException(status_code=409, detail=str(exc))
    lowered = str(exc).lower()
    status = 401 if any(value in lowered for value in ("credential", "password", "session")) else 403 if any(value in lowered for value in ("csrf", "origin", "trusted", "binding")) else 400
    return HTTPException(status_code=status, detail=str(exc))


def _set_browser_cookies(response: Response, result: dict[str, Any]) -> None:
    cookie = result["cookie"]
    common = {
        "secure": cookie["secure"],
        "samesite": cookie["samesite"],
        "domain": cookie["domain"],
        "max_age": cookie["max_age"],
        "path": "/",
    }
    response.set_cookie(
        cookie["session_cookie_name"],
        result["session_token"],
        httponly=True,
        **common,
    )
    response.set_cookie(
        cookie["csrf_cookie_name"],
        result["csrf_token"],
        httponly=False,
        **common,
    )


def _clear_browser_cookies(response: Response, policy: dict[str, Any]) -> None:
    for name in (
        policy.get("session_cookie_name", "hc_browser_session"),
        policy.get("csrf_cookie_name", "hc_csrf"),
    ):
        response.delete_cookie(
            name,
            path="/",
            domain=policy.get("cookie_domain"),
            secure=bool(policy.get("cookie_secure", True)),
            samesite=str(policy.get("cookie_samesite") or "strict"),
        )


@router.get("/human-control/browser/status")
def status(service: HumanControlBrowserSecurityService = Depends(get_service)) -> dict[str, Any]:
    return service.status()


@router.get("/human-control/browser/policy")
def policy(
    workspace_id: str | None = None,
    service: HumanControlBrowserSecurityService = Depends(get_service),
    _: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUTH_VIEW.value)),
) -> dict[str, Any]:
    return service.effective_policy(workspace_id)


@router.put("/human-control/browser/policy")
async def upsert_policy(
    body: HumanControlBrowserPolicyUpsert,
    service: HumanControlBrowserSecurityService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUTH_MANAGE.value)),
) -> dict[str, Any]:
    try:
        body = bind_actor(body, principal)
        body = bind_workspace(body, principal)
        return await service.upsert_policy(body)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/browser/trusted-clients")
async def create_trusted_client(
    body: HumanControlTrustedClientCreate,
    service: HumanControlBrowserSecurityService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUTH_MANAGE.value)),
) -> dict[str, Any]:
    try:
        body = bind_actor(body, principal, field_name="created_by")
        body = bind_workspace(body, principal)
        return await service.create_trusted_client(body)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/browser/trusted-clients")
def trusted_clients(
    workspace_id: str | None = None,
    include_disabled: bool = False,
    service: HumanControlBrowserSecurityService = Depends(get_service),
    _: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUTH_VIEW.value)),
) -> dict[str, Any]:
    return {"trusted_clients": service.list_trusted_clients(workspace_id, include_disabled)}


@router.patch("/human-control/browser/trusted-clients/{client_id}")
async def update_trusted_client(
    client_id: str,
    body: HumanControlTrustedClientUpdate,
    service: HumanControlBrowserSecurityService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(require_permission(HumanControlPermission.AUTH_MANAGE.value)),
) -> dict[str, Any]:
    try:
        body = bind_actor(body, principal)
        return await service.update_trusted_client(client_id, body)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/browser/login")
async def browser_login(
    body: HumanControlBrowserLoginRequest,
    request: Request,
    response: Response,
    service: HumanControlBrowserSecurityService = Depends(get_service),
) -> dict[str, Any]:
    try:
        result = await service.login(
            body,
            origin=request.headers.get("Origin"),
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("User-Agent"),
        )
        _set_browser_cookies(response, result)
        return {
            "session": result["session"],
            "identity": result["identity"],
            "csrf_header_name": result["cookie"]["csrf_header_name"],
            "cookie_authentication": True,
        }
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/browser/session")
def browser_session(
    principal: HumanControlPrincipal = Depends(require_authenticated()),
) -> dict[str, Any]:
    return principal.as_dict()


@router.post("/human-control/browser/csrf/rotate")
async def rotate_csrf(
    body: HumanControlCsrfRotateRequest,
    response: Response,
    service: HumanControlBrowserSecurityService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(require_authenticated()),
) -> dict[str, Any]:
    if principal.auth_method != "browser":
        raise HTTPException(status_code=409, detail="CSRF rotation is available only for browser sessions.")
    try:
        result = await service.rotate_csrf(principal.credential_id or "", principal.actor_id)
        policy = service.effective_policy(principal.workspace_id)
        cookie = service.cookie_settings(policy)
        response.set_cookie(
            cookie["csrf_cookie_name"],
            result["csrf_token"],
            httponly=False,
            secure=cookie["secure"],
            samesite=cookie["samesite"],
            domain=cookie["domain"],
            max_age=cookie["max_age"],
            path="/",
        )
        return {"rotated": True, "csrf_header_name": cookie["csrf_header_name"]}
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/browser/logout")
async def browser_logout(
    body: HumanControlBrowserLogoutRequest,
    response: Response,
    service: HumanControlBrowserSecurityService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(require_authenticated()),
) -> dict[str, Any]:
    if principal.auth_method != "browser":
        raise HTTPException(status_code=409, detail="Current credential is not a browser session.")
    try:
        result = await service.logout(principal.credential_id or "", principal.actor_id, body.reason)
        _clear_browser_cookies(response, service.effective_policy(principal.workspace_id))
        return {"logged_out": True, "session": result}
    except HumanControlError as exc:
        raise translate(exc) from exc
