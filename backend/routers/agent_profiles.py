from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from backend.agent_governance.repository import (
    AgentProfileVersionConflictError,
)
from backend.agent_governance.schemas import (
    AgentPolicyProfileCreateRequest,
    AgentPolicyProfileSelectionRequest,
)
from backend.agent_governance.service import (
    AgentProfileFingerprintMismatchError,
    AgentProfileImmutableBuiltInError,
    AgentProfileIntegrityError,
    AgentProfileNotFoundError,
    AgentProfileSelectionNotFoundError,
    AgentProfileService,
    AgentProfileServiceError,
    AgentProfileToolBindingError,
    AgentProfileWorkspaceError,
)
from backend.api.dependencies import get_agent_profile_service
from backend.control_center.governance_schemas import HumanControlPermission
from backend.control_center.security import (
    HumanControlPrincipal,
    HumanControlSecurityError,
    enforce_workspace_value,
    require_permission,
    security_http_exception,
)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/agent-profiles",
    tags=["agent-profiles"],
)


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(
        exc,
        (
            AgentProfileNotFoundError,
            AgentProfileSelectionNotFoundError,
            AgentProfileWorkspaceError,
        ),
    ):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(
        exc,
        (
            AgentProfileFingerprintMismatchError,
            AgentProfileImmutableBuiltInError,
            AgentProfileIntegrityError,
            AgentProfileVersionConflictError,
        ),
    ):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (AgentProfileToolBindingError, AgentProfileServiceError)):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(
        status_code=500,
        detail="Agent profile operation failed.",
    )


def _bound_workspace(
    workspace_id: str,
    principal: HumanControlPrincipal,
) -> str:
    try:
        resolved = enforce_workspace_value(workspace_id, principal)
    except HumanControlSecurityError as exc:
        raise security_http_exception(exc) from exc
    if not resolved:
        raise HTTPException(
            status_code=400,
            detail="A Workspace is required.",
        )
    return resolved


def _required_actor(principal: HumanControlPrincipal) -> str:
    actor_id = principal.actor_id or principal.identity_id
    if not principal.authenticated or not actor_id:
        raise HTTPException(
            status_code=401,
            detail=(
                "Agent profile mutations require an authenticated "
                "Human Control operator."
            ),
        )
    return actor_id


@router.get("/built-ins")
def list_built_in_agent_profiles(
    workspace_id: str,
    service: AgentProfileService = Depends(get_agent_profile_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.VIEW.value)
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(workspace_id, principal)
    try:
        # Workspace existence is deliberately checked even though built-ins
        # are global audited code constants.
        service.list_custom_versions(
            workspace_id=workspace_id,
            limit=1,
            offset=0,
        )
        profiles = service.built_in_profiles()
    except Exception as exc:
        raise _translate_error(exc) from exc
    return {
        "workspace_id": workspace_id,
        "items": [
            {
                "source": "built_in",
                "profile": profile.to_dict(),
                "profile_fingerprint": profile.fingerprint,
            }
            for profile in profiles
        ],
    }


@router.get("/custom")
def list_custom_agent_profiles(
    workspace_id: str,
    profile_id: str | None = Query(
        default=None,
        min_length=1,
        max_length=64,
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: AgentProfileService = Depends(get_agent_profile_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.VIEW.value)
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(workspace_id, principal)
    try:
        records = service.list_custom_versions(
            workspace_id=workspace_id,
            profile_id=profile_id,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    return {
        "workspace_id": workspace_id,
        "items": [record.to_public_dict() for record in records],
        "limit": limit,
        "offset": offset,
    }


@router.post("/custom")
def create_custom_agent_profile(
    workspace_id: str,
    request: AgentPolicyProfileCreateRequest,
    response: Response,
    service: AgentProfileService = Depends(get_agent_profile_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.MANAGE_POLICIES.value)
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(workspace_id, principal)
    actor_id = _required_actor(principal)
    try:
        record, created = service.create_custom_version(
            workspace_id=workspace_id,
            profile=request.to_domain(),
            actor_id=actor_id,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    response.status_code = (
        status.HTTP_201_CREATED if created else status.HTTP_200_OK
    )
    return {
        "created": created,
        "profile": record.to_public_dict(),
    }


@router.get("/custom/{profile_id}/{version}")
def get_custom_agent_profile(
    workspace_id: str,
    profile_id: str,
    version: str,
    service: AgentProfileService = Depends(get_agent_profile_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.VIEW.value)
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(workspace_id, principal)
    try:
        record = service.get_custom_version(
            workspace_id=workspace_id,
            profile_id=profile_id,
            version=version,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    return record.to_public_dict()


@router.get("/active")
def get_active_agent_profile(
    workspace_id: str,
    service: AgentProfileService = Depends(get_agent_profile_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.VIEW.value)
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(workspace_id, principal)
    try:
        record = service.get_active(workspace_id=workspace_id)
    except Exception as exc:
        raise _translate_error(exc) from exc
    return record.to_public_dict()


@router.put("/active")
def select_active_agent_profile(
    workspace_id: str,
    request: AgentPolicyProfileSelectionRequest,
    service: AgentProfileService = Depends(get_agent_profile_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.MANAGE_POLICIES.value)
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(workspace_id, principal)
    actor_id = _required_actor(principal)
    try:
        record = service.select_active(
            workspace_id=workspace_id,
            profile_id=request.profile_id,
            version=request.version,
            expected_fingerprint=request.expected_fingerprint,
            actor_id=actor_id,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    return record.to_public_dict()
