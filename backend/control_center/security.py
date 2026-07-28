from __future__ import annotations

from dataclasses import dataclass, replace
import hmac
from typing import Any, Callable, Iterable, TypeVar

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from backend.api.dependencies import get_container
from backend.control_center.auth import HumanControlAuthService
from backend.control_center.service import HumanControlError
from backend.core.container import AppContainer


@dataclass(frozen=True, slots=True)
class HumanControlPrincipal:
    identity_id: str | None
    actor_id: str | None
    username: str | None
    display_name: str | None
    identity_type: str | None
    workspace_id: str | None
    auth_method: str
    scopes: frozenset[str]
    credential_id: str | None
    authenticated: bool
    governed: bool
    role_keys: tuple[str, ...]
    permissions: frozenset[str]

    @classmethod
    def anonymous(cls, workspace_id: str | None = None) -> "HumanControlPrincipal":
        return cls(
            identity_id=None,
            actor_id=None,
            username=None,
            display_name=None,
            identity_type=None,
            workspace_id=workspace_id,
            auth_method="anonymous",
            scopes=frozenset(),
            credential_id=None,
            authenticated=False,
            governed=False,
            role_keys=(),
            permissions=frozenset(),
        )

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        governed: bool = False,
        role_keys: Iterable[str] = (),
        permissions: Iterable[str] = (),
    ) -> "HumanControlPrincipal":
        return cls(
            identity_id=payload.get("identity_id"),
            actor_id=payload.get("actor_id"),
            username=payload.get("username"),
            display_name=payload.get("display_name"),
            identity_type=payload.get("identity_type"),
            workspace_id=payload.get("workspace_id"),
            auth_method=str(payload.get("auth_method") or "unknown"),
            scopes=frozenset(str(value) for value in payload.get("scopes") or []),
            credential_id=payload.get("credential_id"),
            authenticated=True,
            governed=governed,
            role_keys=tuple(sorted({str(value) for value in role_keys})),
            permissions=frozenset(str(value) for value in permissions),
        )

    @property
    def break_glass(self) -> bool:
        return self.auth_method == "break_glass"

    def as_dict(self) -> dict[str, Any]:
        return {
            "identity_id": self.identity_id,
            "actor_id": self.actor_id,
            "username": self.username,
            "display_name": self.display_name,
            "identity_type": self.identity_type,
            "workspace_id": self.workspace_id,
            "auth_method": self.auth_method,
            "scopes": sorted(self.scopes),
            "credential_id": self.credential_id,
            "authenticated": self.authenticated,
            "governed": self.governed,
            "role_keys": list(self.role_keys),
            "permissions": sorted(self.permissions),
            "break_glass": self.break_glass,
        }


class HumanControlSecurityError(HumanControlError):
    status_code = 403


class HumanControlAuthenticationRequired(HumanControlSecurityError):
    status_code = 401


class HumanControlPermissionDenied(HumanControlSecurityError):
    status_code = 403


class HumanControlActorMismatch(HumanControlSecurityError):
    status_code = 403


class HumanControlWorkspaceMismatch(HumanControlSecurityError):
    status_code = 403


def security_http_exception(exc: HumanControlSecurityError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))




def _browser_origin(request: Request) -> str | None:
    origin = request.headers.get("Origin")
    if origin:
        return origin
    if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return f"{request.url.scheme}://{request.url.netloc}"
    return None

def _request_workspace_id(request: Request) -> str | None:
    context = getattr(request.state, "workspace_context", None)
    if isinstance(context, dict):
        value = context.get("workspace_id")
        if value:
            return str(value)
    value = request.headers.get("X-Workspace-ID")
    return value.strip() if value and value.strip() else None


def _path_matches(path: str, patterns: Iterable[str]) -> bool:
    for raw_pattern in patterns:
        pattern = str(raw_pattern or "").strip()
        if not pattern:
            continue
        if pattern == "*":
            return True
        if pattern.endswith("*") and path.startswith(pattern[:-1]):
            return True
        if path == pattern:
            return True
    return False


def _scope_matches(granted: Iterable[str], required: str) -> bool:
    values = {str(value) for value in granted}
    if "*" in values or required in values:
        return True
    parts = required.split(".")
    for index in range(len(parts), 0, -1):
        if ".".join(parts[:index]) + ".*" in values:
            return True
    return False


def _policy_mode(policy: dict[str, Any]) -> str:
    if not policy.get("enabled", False):
        return "legacy"
    return str(policy.get("enforcement_mode") or "legacy")


def _is_protected(path: str, policy: dict[str, Any]) -> bool:
    prefixes = policy.get("protected_path_prefixes") or ["/api/human-control"]
    return any(path.startswith(str(prefix)) for prefix in prefixes if str(prefix))


def _is_public(path: str, method: str, policy: dict[str, Any]) -> bool:
    if method.upper() == "OPTIONS":
        return True
    if path in {
        "/api/human-control/browser/status",
        "/api/human-control/browser/login",
    }:
        return True
    patterns = policy.get("public_paths") or [
        "/",
        "/docs*",
        "/redoc*",
        "/openapi.json",
        "/api/health*",
        "/api/human-control/auth/status",
        "/api/human-control/auth/login",
        "/api/human-control/auth/bootstrap-identity",
        "/api/human-control/auth/break-glass/activate",
        "/api/human-control/governance/bootstrap-owner",
    ]
    return _path_matches(path, patterns)


def _principal_allowed(
    principal: HumanControlPrincipal,
    permission: str,
    policy: dict[str, Any],
) -> bool:
    if not principal.authenticated:
        return _policy_mode(policy) != "enforce"
    if principal.break_glass:
        return _scope_matches(principal.scopes, permission) or _scope_matches(
            principal.scopes,
            "human_control.override",
        )
    rbac_allowed = (
        not principal.governed
        or _scope_matches(principal.permissions, permission)
    )
    if principal.auth_method == "api_token":
        if policy.get("reject_unscoped_api_tokens", True) and not principal.scopes:
            return False
        return rbac_allowed and _scope_matches(principal.scopes, permission)
    return rbac_allowed


async def resolve_request_principal(
    request: Request,
    container: AppContainer,
) -> tuple[HumanControlPrincipal, dict[str, Any]]:
    service = container.human_control_auth_service
    workspace_id = _request_workspace_id(request)
    if service is None:
        return HumanControlPrincipal.anonymous(workspace_id), {
            "enabled": False,
            "enforcement_mode": "legacy",
        }

    policy = service.effective_policy(workspace_id)
    path = request.url.path
    public = _is_public(path, request.method, policy)
    protected = _is_protected(path, policy)
    authorization = request.headers.get("Authorization")
    browser_service = getattr(
        container,
        "human_control_browser_security_service",
        None,
    )
    browser_policy = (
        browser_service.effective_policy(workspace_id)
        if browser_service is not None
        else {"enabled": False}
    )
    browser_cookie_name = str(
        browser_policy.get("session_cookie_name") or "hc_browser_session"
    )
    browser_token = request.cookies.get(browser_cookie_name)

    if not authorization and not browser_token:
        principal = HumanControlPrincipal.anonymous(workspace_id)
        if protected and not public and _policy_mode(policy) == "enforce":
            await service.record_access_event(
                event_type="request.authentication_required",
                success=False,
                workspace_id=workspace_id,
                identity_id=None,
                actor_id=None,
                client_ip=request.client.host if request.client else None,
                user_agent=request.headers.get("User-Agent"),
                details={"method": request.method, "path": path},
            )
            raise HumanControlAuthenticationRequired(
                "Для этого маршрута требуется аутентификация."
            )
        return principal, policy

    try:
        if authorization:
            payload = service.authenticate(authorization)
        elif browser_service is not None and browser_token:
            payload = browser_service.authenticate_cookie(
                browser_token,
                workspace_id=workspace_id,
                origin=_browser_origin(request),
                client_ip=request.client.host if request.client else None,
                user_agent=request.headers.get("User-Agent"),
            )
            request.state.human_control_browser_policy = browser_policy
        else:
            raise HumanControlError("Browser authentication is unavailable.")
    except HumanControlError as exc:
        await service.record_access_event(
            event_type="request.authentication_failed",
            success=False,
            workspace_id=workspace_id,
            identity_id=None,
            actor_id=None,
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("User-Agent"),
            details={"method": request.method, "path": path, "reason": str(exc)},
        )
        raise HumanControlAuthenticationRequired(str(exc)) from exc

    credential_workspace = payload.get("workspace_id")
    if (
        policy.get("require_workspace_binding", True)
        and credential_workspace
        and workspace_id
        and credential_workspace != workspace_id
    ):
        await service.record_access_event(
            event_type="request.workspace_mismatch",
            success=False,
            workspace_id=workspace_id,
            identity_id=payload.get("identity_id"),
            actor_id=payload.get("actor_id"),
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("User-Agent"),
            details={
                "method": request.method,
                "path": path,
                "credential_workspace_id": credential_workspace,
                "request_workspace_id": workspace_id,
            },
        )
        raise HumanControlWorkspaceMismatch(
            "Аутентификационные данные привязаны к другому Workspace."
        )

    access_workspace = workspace_id or credential_workspace
    governed = False
    roles: list[str] = []
    permissions: list[str] = []
    governance = container.human_control_governance_service
    if governance is not None and payload.get("actor_id"):
        access = governance.effective_access(
            actor_id=str(payload["actor_id"]),
            workspace_id=access_workspace,
        )
        governed = bool(access.get("governed"))
        roles = list(access.get("role_keys") or [])
        permissions = list(access.get("permissions") or [])

    principal = HumanControlPrincipal.from_payload(
        payload,
        governed=governed,
        role_keys=roles,
        permissions=permissions,
    )
    if principal.workspace_id is None and access_workspace is not None:
        principal = replace(principal, workspace_id=access_workspace)
    return principal, policy


async def get_request_principal(
    request: Request,
    container: AppContainer = Depends(get_container),
) -> HumanControlPrincipal:
    principal = getattr(request.state, "human_control_principal", None)
    if isinstance(principal, HumanControlPrincipal):
        return principal
    try:
        principal, policy = await resolve_request_principal(request, container)
    except HumanControlSecurityError as exc:
        raise security_http_exception(exc) from exc
    request.state.human_control_principal = principal
    request.state.human_control_auth_policy = policy
    return principal


def require_permission(permission: str) -> Callable[..., Any]:
    async def dependency(
        request: Request,
        container: AppContainer = Depends(get_container),
        principal: HumanControlPrincipal = Depends(get_request_principal),
    ) -> HumanControlPrincipal:
        policy = getattr(request.state, "human_control_auth_policy", None)
        if not isinstance(policy, dict):
            service = container.human_control_auth_service
            policy = (
                service.effective_policy(_request_workspace_id(request))
                if service is not None
                else {"enabled": False, "enforcement_mode": "legacy"}
            )
        if not _principal_allowed(principal, permission, policy):
            service = container.human_control_auth_service
            if service is not None:
                await service.record_access_event(
                    event_type="request.permission_denied",
                    success=False,
                    workspace_id=_request_workspace_id(request),
                    identity_id=principal.identity_id,
                    actor_id=principal.actor_id,
                    client_ip=request.client.host if request.client else None,
                    user_agent=request.headers.get("User-Agent"),
                    details={
                        "method": request.method,
                        "path": request.url.path,
                        "required_permission": permission,
                        "auth_method": principal.auth_method,
                    },
                )
            if not principal.authenticated:
                raise HTTPException(
                    status_code=401,
                    detail="Для этого маршрута требуется Bearer-аутентификация.",
                )
            raise HTTPException(
                status_code=403,
                detail=f"Недостаточно полномочий: требуется {permission}.",
            )
        return principal

    return dependency


def require_authenticated() -> Callable[..., Any]:
    async def dependency(
        request: Request,
        principal: HumanControlPrincipal = Depends(get_request_principal),
    ) -> HumanControlPrincipal:
        policy = getattr(request.state, "human_control_auth_policy", {})
        if not principal.authenticated and _policy_mode(policy) == "enforce":
            raise HTTPException(
                status_code=401,
                detail="Для этого маршрута требуется Bearer-аутентификация.",
            )
        return principal

    return dependency


ModelT = TypeVar("ModelT", bound=BaseModel)


def bind_actor(
    model: ModelT,
    principal: HumanControlPrincipal,
    *,
    field_name: str = "actor_id",
) -> ModelT:
    if not principal.authenticated or not principal.actor_id:
        return model
    current = getattr(model, field_name, None)
    if current and str(current) != principal.actor_id:
        raise HumanControlActorMismatch(
            f"Поле {field_name} не совпадает с аутентифицированным оператором."
        )
    return model.model_copy(update={field_name: principal.actor_id})


def bind_identity(
    model: ModelT,
    principal: HumanControlPrincipal,
    *,
    field_name: str,
) -> ModelT:
    if not principal.authenticated or not principal.identity_id:
        return model
    current = getattr(model, field_name, None)
    if current and str(current) != principal.identity_id:
        raise HumanControlActorMismatch(
            f"Поле {field_name} не совпадает с аутентифицированной личностью."
        )
    return model.model_copy(update={field_name: principal.identity_id})


def bind_workspace(
    model: ModelT,
    principal: HumanControlPrincipal,
    *,
    field_name: str = "workspace_id",
) -> ModelT:
    if not principal.authenticated or not principal.workspace_id:
        return model
    current = getattr(model, field_name, None)
    if current and str(current) != principal.workspace_id:
        raise HumanControlWorkspaceMismatch(
            "Запрос пытается использовать Workspace, отличный от Workspace токена."
        )
    return model.model_copy(update={field_name: principal.workspace_id})


def enforce_workspace_value(
    workspace_id: str | None,
    principal: HumanControlPrincipal,
) -> str | None:
    if not principal.authenticated or not principal.workspace_id:
        return workspace_id
    if workspace_id and workspace_id != principal.workspace_id:
        raise HumanControlWorkspaceMismatch(
            "Запрос пытается использовать Workspace, отличный от Workspace токена."
        )
    return principal.workspace_id


class HumanControlAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Any],
    ) -> Response:
        container = getattr(request.app.state, "container", None)
        if container is None:
            return await call_next(request)
        try:
            principal, policy = await resolve_request_principal(request, container)
        except HumanControlSecurityError as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": str(exc)},
                headers={"WWW-Authenticate": "Bearer"}
                if exc.status_code == 401
                else None,
            )
        request.state.human_control_principal = principal
        request.state.human_control_auth_policy = policy
        browser_service = getattr(
            container,
            "human_control_browser_security_service",
            None,
        )
        if principal.auth_method == "browser" and browser_service is not None:
            browser_policy = getattr(
                request.state,
                "human_control_browser_policy",
                browser_service.effective_policy(principal.workspace_id),
            )
            csrf_cookie_name = str(
                browser_policy.get("csrf_cookie_name") or "hc_csrf"
            )
            csrf_header_name = str(
                browser_policy.get("csrf_header_name") or "X-CSRF-Token"
            )
            csrf_cookie = request.cookies.get(csrf_cookie_name)
            csrf_header = request.headers.get(csrf_header_name)
            try:
                if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
                    if not csrf_cookie or not csrf_header or not hmac.compare_digest(
                        csrf_cookie,
                        csrf_header,
                    ):
                        raise HumanControlPermissionDenied(
                            "CSRF cookie and request header must match."
                        )
                browser_service.validate_csrf(
                    session_id=principal.credential_id or "",
                    csrf_token=csrf_header,
                    method=request.method,
                    policy=browser_policy,
                )
            except HumanControlError as exc:
                auth_service = container.human_control_auth_service
                if auth_service is not None:
                    await auth_service.record_access_event(
                        event_type="request.csrf_rejected",
                        success=False,
                        workspace_id=principal.workspace_id,
                        identity_id=principal.identity_id,
                        actor_id=principal.actor_id,
                        client_ip=request.client.host if request.client else None,
                        user_agent=request.headers.get("User-Agent"),
                        details={
                            "method": request.method,
                            "path": request.url.path,
                            "reason": str(exc),
                        },
                    )
                return JSONResponse(status_code=403, content={"detail": str(exc)})
        response = await call_next(request)
        if principal.authenticated:
            response.headers["X-Authenticated-Actor"] = principal.actor_id or ""
            response.headers["X-Authentication-Method"] = principal.auth_method
        return response
