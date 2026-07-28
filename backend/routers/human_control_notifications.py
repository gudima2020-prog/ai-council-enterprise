from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_container
from backend.control_center.governance_schemas import HumanControlPermission
from backend.control_center.notification_schemas import (
    HumanControlNotificationAcknowledgeRequest,
    HumanControlNotificationCancelRequest,
    HumanControlNotificationChannelCreate,
    HumanControlNotificationChannelTestRequest,
    HumanControlNotificationChannelUpdate,
    HumanControlNotificationDispatchRequest,
    HumanControlNotificationManualCreate,
    HumanControlNotificationReadRequest,
    HumanControlNotificationRetryRequest,
    HumanControlNotificationSubscriptionCreate,
    HumanControlNotificationSubscriptionUpdate,
)
from backend.control_center.notifications import HumanControlNotificationService
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

router = APIRouter(tags=["human-control-notifications"])


def get_service(
    container: AppContainer = Depends(get_container),
) -> HumanControlNotificationService:
    service = container.human_control_notification_service
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Human Control Notification Service не запущен.",
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


@router.get("/human-control/notifications/status")
def status(
    service: HumanControlNotificationService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    return service.status()


@router.get("/human-control/notifications/dashboard")
def dashboard(
    workspace_id: str | None = None,
    service: HumanControlNotificationService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return service.dashboard(workspace_id=workspace_id)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/notification-channels")
async def create_channel(
    request: HumanControlNotificationChannelCreate,
    service: HumanControlNotificationService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal, field_name="created_by")
        request = bind_workspace(request, principal)
        return await service.create_channel(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/notification-channels")
def list_channels(
    workspace_id: str | None = None,
    enabled: bool | None = None,
    service: HumanControlNotificationService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return {
            "channels": service.list_channels(
                workspace_id=workspace_id,
                enabled=enabled,
            )
        }
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/notification-channels/{channel_id}")
def get_channel(
    channel_id: str,
    service: HumanControlNotificationService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    result = service.get_channel(channel_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Канал уведомлений не найден.")
    return result


@router.patch("/human-control/notification-channels/{channel_id}")
async def update_channel(
    channel_id: str,
    request: HumanControlNotificationChannelUpdate,
    service: HumanControlNotificationService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.update_channel(channel_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/notification-channels/{channel_id}/test")
async def test_channel(
    channel_id: str,
    request: HumanControlNotificationChannelTestRequest,
    service: HumanControlNotificationService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.test_channel(channel_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/notification-subscriptions")
async def create_subscription(
    request: HumanControlNotificationSubscriptionCreate,
    service: HumanControlNotificationService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal, field_name="created_by")
        request = bind_workspace(request, principal)
        return await service.create_subscription(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/notification-subscriptions")
def list_subscriptions(
    workspace_id: str | None = None,
    actor_id: str | None = None,
    enabled: bool | None = None,
    service: HumanControlNotificationService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return {
            "subscriptions": service.list_subscriptions(
                workspace_id=workspace_id,
                actor_id=actor_id,
                enabled=enabled,
            )
        }
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.patch("/human-control/notification-subscriptions/{subscription_id}")
async def update_subscription(
    subscription_id: str,
    request: HumanControlNotificationSubscriptionUpdate,
    service: HumanControlNotificationService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(
            request,
            principal,
            field_name="actor_id_updated_by",
        )
        return await service.update_subscription(subscription_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/notifications")
async def create_manual_notification(
    request: HumanControlNotificationManualCreate,
    service: HumanControlNotificationService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal, field_name="created_by")
        request = bind_workspace(request, principal)
        return await service.create_manual(request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/notifications")
def list_notifications(
    workspace_id: str | None = None,
    recipient_actor_id: str | None = None,
    status: str | None = None,
    ack_status: str | None = None,
    severity: str | None = None,
    channel_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: HumanControlNotificationService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return {
            "notifications": service.list_notifications(
                workspace_id=workspace_id,
                recipient_actor_id=recipient_actor_id,
                status=status,
                ack_status=ack_status,
                severity=severity,
                channel_id=channel_id,
                limit=limit,
                offset=offset,
            )
        }
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/notifications/{notification_id}")
def get_notification(
    notification_id: str,
    service: HumanControlNotificationService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    result = service.get_notification(notification_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Уведомление не найдено.")
    return result


@router.post("/human-control/notifications/{notification_id}/read")
async def mark_read(
    notification_id: str,
    request: HumanControlNotificationReadRequest,
    service: HumanControlNotificationService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_ACK.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.mark_read(notification_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/notifications/{notification_id}/acknowledge")
async def acknowledge(
    notification_id: str,
    request: HumanControlNotificationAcknowledgeRequest,
    service: HumanControlNotificationService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_ACK.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.acknowledge(notification_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/notifications/{notification_id}/retry")
async def retry_notification(
    notification_id: str,
    request: HumanControlNotificationRetryRequest,
    service: HumanControlNotificationService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.retry_notification(notification_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/notifications/{notification_id}/cancel")
async def cancel_notification(
    notification_id: str,
    request: HumanControlNotificationCancelRequest,
    service: HumanControlNotificationService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        request = bind_actor(request, principal)
        return await service.cancel_notification(notification_id, request)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.post("/human-control/notifications/dispatch")
async def dispatch(
    request: HumanControlNotificationDispatchRequest,
    service: HumanControlNotificationService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    request = bind_actor(request, principal)
    return await service.dispatch_due(limit=request.limit)


@router.post("/human-control/notifications/reconcile")
async def reconcile(
    request: HumanControlNotificationDispatchRequest,
    service: HumanControlNotificationService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    request = bind_actor(request, principal)
    result = await service.reconcile(actor_id=request.actor_id)
    result["ack_deadlines"] = await service.scan_ack_deadlines(
        actor_id=request.actor_id
    )
    return result


@router.post("/human-control/notifications/sync")
async def sync_notifications(
    workspace_id: str | None = None,
    service: HumanControlNotificationService = Depends(get_service),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_MANAGE.value)
    ),
) -> dict[str, Any]:
    try:
        workspace_id = enforce_workspace_value(workspace_id, principal)
        return await service.sync_open_items(workspace_id=workspace_id)
    except HumanControlError as exc:
        raise translate(exc) from exc


@router.get("/human-control/notification-attempts")
def list_attempts(
    notification_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    service: HumanControlNotificationService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    return {
        "attempts": service.list_attempts(
            notification_id=notification_id,
            limit=limit,
        )
    }


@router.get("/human-control/notification-receipts")
def list_receipts(
    notification_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    service: HumanControlNotificationService = Depends(get_service),
    _: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.NOTIFICATION_VIEW.value)
    ),
) -> dict[str, Any]:
    return {
        "receipts": service.list_receipts(
            notification_id=notification_id,
            limit=limit,
        )
    }
