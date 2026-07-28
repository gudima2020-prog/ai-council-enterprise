from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_container
from backend.control_center.governance_schemas import HumanControlPermission
from backend.control_center.retention import HumanControlRetentionService
from backend.control_center.retention_schemas import (
    HumanControlEvidenceArchiveCreate,
    HumanControlEvidenceArchiveRevokeRequest,
    HumanControlExternalAuditPackageCreate,
    HumanControlExternalAuditPackageRevokeRequest,
    HumanControlLegalHoldCreate,
    HumanControlLegalHoldReleaseRequest,
    HumanControlRetentionPolicyUpsert,
    HumanControlRetentionRunRequest,
)
from backend.control_center.security import (
    HumanControlPrincipal,
    HumanControlSecurityError,
    bind_actor,
    bind_workspace,
    enforce_workspace_value,
    require_permission,
    security_http_exception,
)
from backend.control_center.service import (
    HumanControlConflict,
    HumanControlError,
    HumanControlNotFound,
)
from backend.core.container import AppContainer


router = APIRouter(tags=["human-control-retention"])


def get_service(
    container: AppContainer = Depends(get_container),
) -> HumanControlRetentionService:
    service = container.human_control_retention_service
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Human Control Retention Service не запущен.",
        )
    return service


def translate(exc: HumanControlError) -> HTTPException:
    if isinstance(exc, HumanControlSecurityError):
        return security_http_exception(exc)
    if isinstance(exc, HumanControlNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, HumanControlConflict):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/human-control/retention/status")
def status(
    service: HumanControlRetentionService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.RETENTION_VIEW.value)
    ),
) -> dict[str, Any]:
    return service.status()


@router.get("/human-control/retention/dashboard")
def dashboard(
    workspace_id: str | None = None,
    service: HumanControlRetentionService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.RETENTION_VIEW.value)
    ),
) -> dict[str, Any]:
    workspace_id = enforce_workspace_value(workspace_id, principal)
    return service.dashboard(workspace_id)


@router.get("/human-control/retention/policy")
def get_policy(
    workspace_id: str | None = None,
    service: HumanControlRetentionService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.RETENTION_VIEW.value)
    ),
) -> dict[str, Any]:
    workspace_id = enforce_workspace_value(workspace_id, principal)
    return service.get_policy(workspace_id)


@router.put("/human-control/retention/policy")
async def upsert_policy(
    request: HumanControlRetentionPolicyUpsert,
    service: HumanControlRetentionService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.RETENTION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        request = bind_workspace(request, principal)
        return await service.upsert_policy(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/retention/policies")
def list_policies(
    service: HumanControlRetentionService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.RETENTION_VIEW.value)
    ),
) -> dict[str, Any]:
    return {"policies": service.list_policies()}


@router.post("/human-control/legal-holds")
async def create_legal_hold(
    request: HumanControlLegalHoldCreate,
    service: HumanControlRetentionService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.LEGAL_HOLD_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal, field_name="created_by")
        request = bind_workspace(request, principal)
        return await service.create_legal_hold(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/legal-holds")
def list_legal_holds(
    workspace_id: str | None = None,
    status: str | None = Query(default=None, pattern=r"^(active|released|expired)$"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: HumanControlRetentionService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.RETENTION_VIEW.value)
    ),
) -> dict[str, Any]:
    workspace_id = enforce_workspace_value(workspace_id, principal)
    return {
        "legal_holds": service.list_legal_holds(
            workspace_id=workspace_id,
            status=status,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/human-control/legal-holds/{hold_id}")
def get_legal_hold(
    hold_id: str,
    service: HumanControlRetentionService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.RETENTION_VIEW.value)
    ),
) -> dict[str, Any]:
    result = service.get_legal_hold(hold_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Legal hold не найден.")
    return result


@router.post("/human-control/legal-holds/{hold_id}/release")
async def release_legal_hold(
    hold_id: str,
    request: HumanControlLegalHoldReleaseRequest,
    service: HumanControlRetentionService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.LEGAL_HOLD_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.release_legal_hold(hold_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/evidence-archives")
async def create_archive(
    request: HumanControlEvidenceArchiveCreate,
    service: HumanControlRetentionService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.RETENTION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal, field_name="created_by")
        request = bind_workspace(request, principal)
        return await service.create_archive(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/evidence-archives")
def list_archives(
    workspace_id: str | None = None,
    status: str | None = Query(default=None, pattern=r"^(building|sealed|revoked)$"),
    archive_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: HumanControlRetentionService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.RETENTION_VIEW.value)
    ),
) -> dict[str, Any]:
    workspace_id = enforce_workspace_value(workspace_id, principal)
    return {
        "archives": service.list_archives(
            workspace_id=workspace_id,
            status=status,
            archive_type=archive_type,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/human-control/evidence-archives/{archive_id}")
def get_archive(
    archive_id: str,
    include_items: bool = False,
    item_limit: int = Query(default=1000, ge=1, le=5000),
    item_offset: int = Query(default=0, ge=0),
    service: HumanControlRetentionService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.RETENTION_VIEW.value)
    ),
) -> dict[str, Any]:
    result = service.get_archive(
        archive_id,
        include_items=include_items,
        item_limit=item_limit,
        item_offset=item_offset,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Evidence archive не найден.")
    return result


@router.get("/human-control/evidence-archives/{archive_id}/verify")
def verify_archive(
    archive_id: str,
    service: HumanControlRetentionService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.RETENTION_VIEW.value)
    ),
) -> dict[str, Any]:
    try:
        return service.verify_archive(archive_id)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/evidence-archives/{archive_id}/revoke")
async def revoke_archive(
    archive_id: str,
    request: HumanControlEvidenceArchiveRevokeRequest,
    service: HumanControlRetentionService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.RETENTION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.revoke_archive(archive_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/retention/runs")
async def run_retention(
    request: HumanControlRetentionRunRequest,
    service: HumanControlRetentionService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.RETENTION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        request = bind_workspace(request, principal)
        return await service.run_retention(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/retention/runs")
def list_retention_runs(
    workspace_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: HumanControlRetentionService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.RETENTION_VIEW.value)
    ),
) -> dict[str, Any]:
    workspace_id = enforce_workspace_value(workspace_id, principal)
    return {
        "runs": service.list_retention_runs(
            workspace_id=workspace_id,
            limit=limit,
            offset=offset,
        )
    }


@router.post("/human-control/external-audit-packages")
async def create_external_package(
    request: HumanControlExternalAuditPackageCreate,
    service: HumanControlRetentionService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.EVIDENCE_EXPORT.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal, field_name="generated_by")
        request = bind_workspace(request, principal)
        return await service.create_external_package(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/external-audit-packages")
def list_external_packages(
    workspace_id: str | None = None,
    status: str | None = Query(default=None, pattern=r"^(draft|sealed|revoked|expired)$"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: HumanControlRetentionService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.EVIDENCE_EXPORT.value)
    ),
) -> dict[str, Any]:
    workspace_id = enforce_workspace_value(workspace_id, principal)
    return {
        "packages": service.list_external_packages(
            workspace_id=workspace_id,
            status=status,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/human-control/external-audit-packages/{package_id}")
def get_external_package(
    package_id: str,
    service: HumanControlRetentionService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.EVIDENCE_EXPORT.value)
    ),
) -> dict[str, Any]:
    result = service.get_external_package(package_id)
    if result is None:
        raise HTTPException(status_code=404, detail="External audit package не найден.")
    return result


@router.get("/human-control/external-audit-packages/{package_id}/export")
def export_external_package(
    package_id: str,
    include_archive_items: bool = False,
    max_archive_items: int = Query(default=5000, ge=1, le=20000),
    service: HumanControlRetentionService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.EVIDENCE_EXPORT.value)
    ),
) -> dict[str, Any]:
    try:
        return service.export_external_package(
            package_id,
            include_archive_items=include_archive_items,
            max_archive_items=max_archive_items,
        )
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/external-audit-packages/{package_id}/revoke")
async def revoke_external_package(
    package_id: str,
    request: HumanControlExternalAuditPackageRevokeRequest,
    service: HumanControlRetentionService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.EVIDENCE_EXPORT.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.revoke_external_package(package_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc
