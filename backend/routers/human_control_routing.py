from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_container
from backend.control_center.governance_schemas import HumanControlPermission
from backend.control_center.routing import HumanControlRoutingService
from backend.control_center.routing_schemas import (
    HumanControlAvailabilityHeartbeat,
    HumanControlAvailabilityUpsert,
    HumanControlEscalationManualRequest,
    HumanControlEscalationResolveRequest,
    HumanControlEscalationRuleCreate,
    HumanControlEscalationRuleUpdate,
    HumanControlEscalationScanRequest,
    HumanControlOnCallMemberCreate,
    HumanControlOnCallMemberUpdate,
    HumanControlOnCallScheduleCreate,
    HumanControlOnCallScheduleUpdate,
    HumanControlRoutingActorRequest,
    HumanControlRoutingEvaluateRequest,
    HumanControlRoutingRuleCreate,
    HumanControlRoutingRuleUpdate,
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

router = APIRouter(tags=["human-control-routing"])


def get_service(
    container: AppContainer = Depends(get_container),
) -> HumanControlRoutingService:
    service = container.human_control_routing_service
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Human Control Routing Service не запущен.",
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


@router.get("/human-control/routing/status")
def status(
    service: HumanControlRoutingService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    return service.status()


@router.get("/human-control/routing/dashboard")
def dashboard(
    workspace_id: str | None = None,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    try:
        return service.dashboard(
            workspace_id=enforce_workspace_value(workspace_id, principal)
        )
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/on-call-schedules")
async def create_schedule(
    request: HumanControlOnCallScheduleCreate,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal, field_name="created_by")
        request = bind_workspace(request, principal)
        return await service.create_schedule(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/on-call-schedules")
def list_schedules(
    workspace_id: str | None = None,
    enabled: bool | None = None,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return {
            "schedules": service.list_schedules(
                workspace_id=workspace_id, enabled=enabled
            )
        }
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/on-call-schedules/{schedule_id}")
def get_schedule(
    schedule_id: str,
    service: HumanControlRoutingService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    result = service.get_schedule(schedule_id)
    if result is None:
        raise HTTPException(status_code=404, detail="On-call расписание не найдено.")
    result["members"] = service.list_members(schedule_id)
    return result


@router.patch("/human-control/on-call-schedules/{schedule_id}")
async def update_schedule(
    schedule_id: str,
    request: HumanControlOnCallScheduleUpdate,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.update_schedule(schedule_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/on-call-schedules/{schedule_id}/members")
async def add_member(
    schedule_id: str,
    request: HumanControlOnCallMemberCreate,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal, field_name="created_by")
        return await service.add_member(schedule_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/on-call-schedules/{schedule_id}/members")
def list_members(
    schedule_id: str,
    active_only: bool = False,
    service: HumanControlRoutingService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    return {"members": service.list_members(schedule_id, active_only=active_only)}


@router.patch("/human-control/on-call-members/{member_id}")
async def update_member(
    member_id: str,
    request: HumanControlOnCallMemberUpdate,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(
            request, principal, field_name="actor_id_updated_by"
        )
        return await service.update_member(member_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.delete("/human-control/on-call-members/{member_id}")
async def remove_member(
    member_id: str,
    request: HumanControlRoutingActorRequest,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.remove_member(member_id, actor_id=request.actor_id)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.put("/human-control/operator-availability")
async def upsert_availability(
    request: HumanControlAvailabilityUpsert,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal, field_name="updated_by")
        request = bind_workspace(request, principal)
        return await service.upsert_availability(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/operator-availability/heartbeat")
async def availability_heartbeat(
    request: HumanControlAvailabilityHeartbeat,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_ACK.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        request = bind_workspace(request, principal)
        return await service.heartbeat(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/operator-availability")
def list_availability(
    workspace_id: str | None = None,
    actor_id: str | None = None,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return {
            "availability": service.list_availability(
                workspace_id=workspace_id,
                actor_id=actor_id,
            )
        }
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/notification-routing-rules")
async def create_routing_rule(
    request: HumanControlRoutingRuleCreate,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal, field_name="created_by")
        request = bind_workspace(request, principal)
        return await service.create_routing_rule(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/notification-routing-rules")
def list_routing_rules(
    workspace_id: str | None = None,
    enabled: bool | None = None,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return {
            "routing_rules": service.list_routing_rules(
                workspace_id=workspace_id, enabled=enabled
            )
        }
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.patch("/human-control/notification-routing-rules/{rule_id}")
async def update_routing_rule(
    rule_id: str,
    request: HumanControlRoutingRuleUpdate,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.update_routing_rule(rule_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/notification-routing/evaluate")
def evaluate_route(
    request: HumanControlRoutingEvaluateRequest,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_workspace(request, principal)
        return service.evaluate_route(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/notification-escalation-rules")
async def create_escalation_rule(
    request: HumanControlEscalationRuleCreate,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal, field_name="created_by")
        request = bind_workspace(request, principal)
        return await service.create_escalation_rule(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/notification-escalation-rules")
def list_escalation_rules(
    workspace_id: str | None = None,
    enabled: bool | None = None,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return {
            "escalation_rules": service.list_escalation_rules(
                workspace_id=workspace_id, enabled=enabled
            )
        }
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.patch("/human-control/notification-escalation-rules/{rule_id}")
async def update_escalation_rule(
    rule_id: str,
    request: HumanControlEscalationRuleUpdate,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.update_escalation_rule(rule_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/notifications/{notification_id}/escalate")
async def manual_escalate(
    notification_id: str,
    request: HumanControlEscalationManualRequest,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.manual_escalate(notification_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/notification-escalations/scan")
async def scan_escalations(
    request: HumanControlEscalationScanRequest,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    request = bind_actor(request, principal)
    return await service.scan_escalations(
        actor_id=request.actor_id, limit=request.limit
    )


@router.get("/human-control/notification-escalations")
def list_escalations(
    workspace_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return {
            "escalations": service.list_escalations(
                workspace_id=workspace_id, status=status, limit=limit
            )
        }
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/notification-escalations/{escalation_id}")
def get_escalation(
    escalation_id: str,
    service: HumanControlRoutingService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    result = service.get_escalation(escalation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Эскалация не найдена.")
    result["attempts"] = service.list_escalation_attempts(
        escalation_id=escalation_id
    )
    return result


@router.post("/human-control/notification-escalations/{escalation_id}/resolve")
async def resolve_escalation(
    escalation_id: str,
    request: HumanControlEscalationResolveRequest,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.resolve_escalation(escalation_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/notification-escalations/{escalation_id}/cancel")
async def cancel_escalation(
    escalation_id: str,
    request: HumanControlEscalationResolveRequest,
    service: HumanControlRoutingService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.resolve_escalation(
            escalation_id, request, cancel=True
        )
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/notification-escalation-attempts")
def list_escalation_attempts(
    escalation_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    service: HumanControlRoutingService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    return {
        "attempts": service.list_escalation_attempts(
            escalation_id=escalation_id, limit=limit
        )
    }
