from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_container
from backend.control_center.compliance import HumanControlComplianceService
from backend.control_center.compliance_schemas import (
    HumanControlAccessFindingDecisionRequest,
    HumanControlAccessReviewCompleteRequest,
    HumanControlAccessReviewCreate,
    HumanControlAuditExportRequest,
    HumanControlComplianceReportCreate,
)
from backend.control_center.governance_schemas import HumanControlPermission
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


router = APIRouter(tags=["human-control-compliance"])


def get_service(
    container: AppContainer = Depends(get_container),
) -> HumanControlComplianceService:
    service = container.human_control_compliance_service
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Human Control Compliance Service не запущен.",
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


@router.get("/human-control/compliance/status")
def status(
    service: HumanControlComplianceService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.COMPLIANCE_VIEW.value)
    ),
) -> dict[str, Any]:
    return service.status()


@router.get("/human-control/compliance/dashboard")
def dashboard(
    workspace_id: str | None = None,
    service: HumanControlComplianceService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.COMPLIANCE_VIEW.value)
    ),
) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return service.dashboard(workspace_id=workspace_id)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/operator-audit/events")
def list_audit_events(
    workspace_id: str | None = None,
    actor_id: str | None = None,
    event_type: str | None = None,
    outcome: str | None = Query(default=None, pattern=r"^(success|failure|unknown)$"),
    risk_level: str | None = Query(
        default=None, pattern=r"^(low|medium|high|critical)$"
    ),
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: HumanControlComplianceService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.COMPLIANCE_VIEW.value)
    ),
) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return {
            "events": service.list_audit_events(
                workspace_id=workspace_id,
                actor_id=actor_id,
                event_type=event_type,
                outcome=outcome,
                risk_level=risk_level,
                period_start=period_start,
                period_end=period_end,
                limit=limit,
                offset=offset,
            )
        }
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/operator-audit/events/{audit_id}")
def get_audit_event(
    audit_id: str,
    service: HumanControlComplianceService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.COMPLIANCE_VIEW.value)
    ),
) -> dict[str, Any]:
    result = service.get_audit_event(audit_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Запись операторского аудита не найдена.")
    return result


@router.get("/human-control/operator-audit/verify")
def verify_audit_chain(
    service: HumanControlComplianceService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.COMPLIANCE_VIEW.value)
    ),
) -> dict[str, Any]:
    return service.verify_chain()


@router.post("/human-control/operator-audit/export")
def export_audit(
    request: HumanControlAuditExportRequest,
    service: HumanControlComplianceService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.COMPLIANCE_VIEW.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_workspace(request, principal)
        return service.export_audit(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/compliance/reports")
async def generate_report(
    request: HumanControlComplianceReportCreate,
    service: HumanControlComplianceService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.COMPLIANCE_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal, field_name="generated_by")
        request = bind_workspace(request, principal)
        return await service.generate_report(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/compliance/reports")
def list_reports(
    workspace_id: str | None = None,
    report_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: HumanControlComplianceService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.COMPLIANCE_VIEW.value)
    ),
) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return {
            "reports": service.list_reports(
                workspace_id=workspace_id,
                report_type=report_type,
                limit=limit,
                offset=offset,
            )
        }
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/compliance/reports/{report_id}")
def get_report(
    report_id: str,
    service: HumanControlComplianceService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.COMPLIANCE_VIEW.value)
    ),
) -> dict[str, Any]:
    result = service.get_report(report_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Compliance-отчёт не найден.")
    return result


@router.post("/human-control/access-reviews")
async def start_access_review(
    request: HumanControlAccessReviewCreate,
    service: HumanControlComplianceService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.ACCESS_REVIEW.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal, field_name="initiated_by")
        request = bind_workspace(request, principal)
        return await service.start_access_review(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/access-reviews")
def list_access_reviews(
    workspace_id: str | None = None,
    status: str | None = Query(default=None, pattern=r"^(open|completed|cancelled)$"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: HumanControlComplianceService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.COMPLIANCE_VIEW.value)
    ),
) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return {
            "reviews": service.list_access_reviews(
                workspace_id=workspace_id,
                status=status,
                limit=limit,
                offset=offset,
            )
        }
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/access-reviews/{review_id}")
def get_access_review(
    review_id: str,
    service: HumanControlComplianceService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.COMPLIANCE_VIEW.value)
    ),
) -> dict[str, Any]:
    review = service.get_access_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Проверка доступа не найдена.")
    review["findings"] = service.list_findings(review_id=review_id)
    return review


@router.get("/human-control/access-findings")
def list_access_findings(
    review_id: str | None = None,
    workspace_id: str | None = None,
    status: str | None = Query(
        default=None, pattern=r"^(open|accepted|remediated|dismissed)$"
    ),
    severity: str | None = Query(
        default=None, pattern=r"^(low|medium|high|critical)$"
    ),
    actor_id: str | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    service: HumanControlComplianceService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.COMPLIANCE_VIEW.value)
    ),
) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return {
            "findings": service.list_findings(
                review_id=review_id,
                workspace_id=workspace_id,
                status=status,
                severity=severity,
                actor_id=actor_id,
                limit=limit,
                offset=offset,
            )
        }
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/access-findings/{finding_id}/decision")
async def decide_access_finding(
    finding_id: str,
    request: HumanControlAccessFindingDecisionRequest,
    service: HumanControlComplianceService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.ACCESS_REVIEW.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.decide_finding(finding_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/access-reviews/{review_id}/complete")
async def complete_access_review(
    review_id: str,
    request: HumanControlAccessReviewCompleteRequest,
    service: HumanControlComplianceService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.ACCESS_REVIEW.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.complete_access_review(review_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc
