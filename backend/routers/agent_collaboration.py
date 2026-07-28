from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_container
from backend.core.container import AppContainer
from backend.orchestration.collaboration import (
    AgentCollaborationManager,
    CollaborationError,
    CollaborationNotFound,
    ContextVersionConflict,
)
from backend.orchestration.collaboration_schemas import (
    AgentMessageCreateRequest,
    ContextEntryUpsertRequest,
    ConversationCreateRequest,
    DelegationCreateRequest,
    DelegationDecisionRequest,
    DelegationRunRequest,
)
from backend.orchestration.runtime import (
    ExecutionPlanRuntime,
    ExecutionRuntimeError,
)

router = APIRouter(tags=["agent-collaboration"])


def get_collaboration_manager(
    container: AppContainer = Depends(get_container),
) -> AgentCollaborationManager:
    manager = container.agent_collaboration_manager
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail="Agent Collaboration Manager не запущен.",
        )
    return manager


def get_execution_runtime(
    container: AppContainer = Depends(get_container),
) -> ExecutionPlanRuntime:
    runtime = container.execution_plan_runtime
    if runtime is None:
        raise HTTPException(
            status_code=503,
            detail="Execution Plan Runtime не запущен.",
        )
    return runtime


def translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, CollaborationNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ContextVersionConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (CollaborationError, ExecutionRuntimeError)):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@router.get("/agent-collaboration/status")
def collaboration_status(
    manager: AgentCollaborationManager = Depends(get_collaboration_manager),
    runtime: ExecutionPlanRuntime = Depends(get_execution_runtime),
) -> dict[str, Any]:
    return {
        **manager.stats(),
        "runtime": {
            "active_delegation_ids": runtime.stats()[
                "active_delegation_ids"
            ],
            "active_delegation_count": runtime.stats()[
                "active_delegation_count"
            ],
        },
    }


@router.post("/execution-plans/{plan_id}/conversations")
async def create_conversation(
    plan_id: str,
    request: ConversationCreateRequest,
    manager: AgentCollaborationManager = Depends(get_collaboration_manager),
) -> dict[str, Any]:
    try:
        return await manager.create_conversation(plan_id, request)
    except Exception as exc:
        raise translate_error(exc) from exc


@router.get("/execution-plans/{plan_id}/conversations")
def list_conversations(
    plan_id: str,
    manager: AgentCollaborationManager = Depends(get_collaboration_manager),
) -> dict[str, Any]:
    result = manager.list_conversations(plan_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Execution Plan не найден.")
    return {"plan_id": plan_id, "conversations": result}


@router.get("/agent-conversations/{conversation_id}")
def get_conversation(
    conversation_id: str,
    message_limit: int = Query(default=100, ge=1, le=1000),
    manager: AgentCollaborationManager = Depends(get_collaboration_manager),
) -> dict[str, Any]:
    result = manager.get_conversation(
        conversation_id,
        message_limit=message_limit,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Conversation не найдена.")
    return result


@router.post("/agent-conversations/{conversation_id}/close")
async def close_conversation(
    conversation_id: str,
    manager: AgentCollaborationManager = Depends(get_collaboration_manager),
) -> dict[str, Any]:
    result = await manager.close_conversation(conversation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Conversation не найдена.")
    return result


@router.post("/agent-conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    request: AgentMessageCreateRequest,
    manager: AgentCollaborationManager = Depends(get_collaboration_manager),
) -> dict[str, Any]:
    try:
        return await manager.send_message(conversation_id, request)
    except Exception as exc:
        raise translate_error(exc) from exc


@router.get("/agent-conversations/{conversation_id}/messages")
def list_messages(
    conversation_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    after_sequence: int | None = Query(default=None, ge=0),
    manager: AgentCollaborationManager = Depends(get_collaboration_manager),
) -> dict[str, Any]:
    result = manager.list_messages(
        conversation_id,
        limit=limit,
        after_sequence=after_sequence,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Conversation не найдена.")
    return {"conversation_id": conversation_id, "messages": result}


@router.post("/agent-messages/{message_id}/read")
async def mark_message_read(
    message_id: str,
    handled: bool = False,
    manager: AgentCollaborationManager = Depends(get_collaboration_manager),
) -> dict[str, Any]:
    result = await manager.mark_message_read(
        message_id,
        handled=handled,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Message не найдено.")
    return result


@router.put("/execution-plans/{plan_id}/shared-context/{key:path}")
async def upsert_context(
    plan_id: str,
    key: str,
    request: ContextEntryUpsertRequest,
    manager: AgentCollaborationManager = Depends(get_collaboration_manager),
) -> dict[str, Any]:
    try:
        return await manager.upsert_context(plan_id, key, request)
    except Exception as exc:
        raise translate_error(exc) from exc


@router.get("/execution-plans/{plan_id}/shared-context")
def context_snapshot(
    plan_id: str,
    agent_id: str | None = None,
    step_key: str | None = None,
    delegation_id: str | None = None,
    manager: AgentCollaborationManager = Depends(get_collaboration_manager),
) -> dict[str, Any]:
    result = manager.context_snapshot(
        plan_id,
        agent_id=agent_id,
        step_key=step_key,
        delegation_id=delegation_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Execution Plan не найден.")
    return result


@router.get("/execution-plans/{plan_id}/shared-context/entries")
def list_context_entries(
    plan_id: str,
    scope_type: str | None = None,
    scope_id: str | None = None,
    manager: AgentCollaborationManager = Depends(get_collaboration_manager),
) -> dict[str, Any]:
    result = manager.list_context(
        plan_id,
        scope_type=scope_type,
        scope_id=scope_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Execution Plan не найден.")
    return {"plan_id": plan_id, "entries": result}


@router.delete("/execution-plans/{plan_id}/shared-context/{key:path}")
async def delete_context(
    plan_id: str,
    key: str,
    scope_type: str = "plan",
    scope_id: str = "",
    manager: AgentCollaborationManager = Depends(get_collaboration_manager),
) -> dict[str, bool]:
    try:
        removed = await manager.delete_context(
            plan_id,
            scope_type=scope_type,
            scope_id=scope_id,
            key=key,
        )
    except Exception as exc:
        raise translate_error(exc) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="Context entry не найден.")
    return {"deleted": True}


@router.post("/execution-plans/{plan_id}/delegations")
async def create_delegation(
    plan_id: str,
    request: DelegationCreateRequest,
    manager: AgentCollaborationManager = Depends(get_collaboration_manager),
) -> dict[str, Any]:
    try:
        return await manager.create_delegation(plan_id, request)
    except Exception as exc:
        raise translate_error(exc) from exc


@router.get("/execution-plans/{plan_id}/delegations")
def list_delegations(
    plan_id: str,
    status: str | None = None,
    manager: AgentCollaborationManager = Depends(get_collaboration_manager),
) -> dict[str, Any]:
    result = manager.list_delegations(plan_id, status=status)
    if result is None:
        raise HTTPException(status_code=404, detail="Execution Plan не найден.")
    return {"plan_id": plan_id, "delegations": result}


@router.get("/agent-delegations/{delegation_id}")
def get_delegation(
    delegation_id: str,
    manager: AgentCollaborationManager = Depends(get_collaboration_manager),
) -> dict[str, Any]:
    result = manager.get_delegation(delegation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Delegation не найдена.")
    return result


@router.post("/agent-delegations/{delegation_id}/accept")
async def accept_delegation(
    delegation_id: str,
    request: DelegationDecisionRequest,
    manager: AgentCollaborationManager = Depends(get_collaboration_manager),
) -> dict[str, Any]:
    try:
        result = await manager.accept_delegation(
            delegation_id,
            actor_agent_id=request.actor_agent_id,
            note=request.note,
        )
    except Exception as exc:
        raise translate_error(exc) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Delegation не найдена.")
    return result


@router.post("/agent-delegations/{delegation_id}/reject")
async def reject_delegation(
    delegation_id: str,
    request: DelegationDecisionRequest,
    manager: AgentCollaborationManager = Depends(get_collaboration_manager),
) -> dict[str, Any]:
    try:
        result = await manager.reject_delegation(
            delegation_id,
            actor_agent_id=request.actor_agent_id,
            note=request.note,
        )
    except Exception as exc:
        raise translate_error(exc) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Delegation не найдена.")
    return result


@router.post("/agent-delegations/{delegation_id}/run")
async def run_delegation(
    delegation_id: str,
    request: DelegationRunRequest,
    runtime: ExecutionPlanRuntime = Depends(get_execution_runtime),
) -> dict[str, Any]:
    try:
        result = await runtime.run_delegation(
            delegation_id,
            wait=request.wait,
            wait_timeout_seconds=request.wait_timeout_seconds,
        )
    except Exception as exc:
        raise translate_error(exc) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Delegation не найдена.")
    return result


@router.post("/agent-delegations/{delegation_id}/cancel")
async def cancel_delegation(
    delegation_id: str,
    request: DelegationDecisionRequest,
    runtime: ExecutionPlanRuntime = Depends(get_execution_runtime),
) -> dict[str, Any]:
    try:
        result = await runtime.cancel_delegation(
            delegation_id,
            actor_agent_id=request.actor_agent_id,
            note=request.note,
        )
    except Exception as exc:
        raise translate_error(exc) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Delegation не найдена.")
    return result
