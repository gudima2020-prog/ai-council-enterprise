from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_container
from backend.core.container import AppContainer
from backend.task_engine.audit import TaskAuditManager

router = APIRouter(tags=["task-audit"])


def get_audit_manager(
    container: AppContainer = Depends(get_container),
) -> TaskAuditManager:
    manager = container.task_audit_manager

    if manager is None:
        raise HTTPException(
            status_code=503,
            detail="Task Audit Manager не запущен.",
        )

    return manager


@router.get("/task-audit/status")
def audit_status(
    manager: TaskAuditManager = Depends(get_audit_manager),
) -> dict[str, Any]:
    return {
        **manager.stats(),
        "integrity": manager.verify_chain(),
    }


@router.get("/task-audit/events")
def list_audit_events(
    workspace_id: str | None = None,
    task_id: str | None = None,
    approval_id: str | None = None,
    workflow_instance_id: str | None = None,
    event_type: str | None = None,
    actor_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    manager: TaskAuditManager = Depends(get_audit_manager),
) -> dict[str, Any]:
    events = manager.list(
        workspace_id=workspace_id,
        task_id=task_id,
        approval_id=approval_id,
        workflow_instance_id=workflow_instance_id,
        event_type=event_type,
        actor_id=actor_id,
        limit=limit,
        offset=offset,
    )
    return {
        "events": events,
        "count": len(events),
        "limit": limit,
        "offset": offset,
    }


@router.get("/task-audit/events/{audit_id}")
def get_audit_event(
    audit_id: str,
    manager: TaskAuditManager = Depends(get_audit_manager),
) -> dict[str, Any]:
    result = manager.get(audit_id)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Audit event не найден.",
        )

    return result


@router.get("/task-audit/verify")
def verify_audit_chain(
    manager: TaskAuditManager = Depends(get_audit_manager),
) -> dict[str, Any]:
    return manager.verify_chain()


@router.get("/tasks/{task_id}/audit-trace")
def task_audit_trace(
    task_id: str,
    manager: TaskAuditManager = Depends(get_audit_manager),
) -> dict[str, Any]:
    result = manager.task_trace(task_id)

    if not result["task_exists"] and not result["audit_events"]:
        raise HTTPException(status_code=404, detail="Task не найдена.")

    return result


@router.get("/task-approvals/{approval_id}/evidence")
def approval_evidence(
    approval_id: str,
    manager: TaskAuditManager = Depends(get_audit_manager),
) -> dict[str, Any]:
    result = manager.approval_evidence(approval_id)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Approval не найден.",
        )

    return result
