from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_container
from backend.autonomy.memory import MissionMemoryService
from backend.autonomy.memory_schemas import (
    MissionEvidenceEvaluateRequest,
    MissionEvidencePolicyUpsert,
    MissionEvidenceReviewRequest,
    MissionEvidenceSubmit,
    MissionGoalConfirmRequest,
    MissionGoalEvidenceEvaluateRequest,
    MissionGoalReopenRequest,
    MissionMemoryUpsert,
)
from backend.autonomy.schemas import MissionDecisionRequest
from backend.autonomy.service import (
    AutonomousMissionError,
    MissionCycleNotFound,
    MissionGoalNotFound,
    MissionNotFound,
    MissionStateError,
)
from backend.core.container import AppContainer


router = APIRouter(tags=["mission-memory"])


def get_service(
    container: AppContainer = Depends(get_container),
) -> MissionMemoryService:
    service = container.mission_memory_service
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Mission Memory Service не запущен.",
        )
    return service


def translate_error(exc: AutonomousMissionError) -> HTTPException:
    if isinstance(exc, (MissionNotFound, MissionGoalNotFound, MissionCycleNotFound)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, MissionStateError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/mission-memory/status")
def status(service: MissionMemoryService = Depends(get_service)) -> dict[str, Any]:
    return service.status()


@router.put("/missions/{mission_id}/memory/{memory_key}")
async def upsert_memory(
    mission_id: str,
    memory_key: str,
    request: MissionMemoryUpsert,
    service: MissionMemoryService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.upsert_memory(mission_id, memory_key, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/memory")
def list_memory(
    mission_id: str,
    goal_id: str | None = None,
    category: str | None = Query(
        default=None,
        pattern="^(fact|decision|artifact|lesson|constraint|summary)$",
    ),
    query: str | None = None,
    include_archived: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionMemoryService = Depends(get_service),
) -> dict[str, Any]:
    try:
        entries = service.list_memory(
            mission_id,
            goal_id=goal_id,
            category=category,
            query=query,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"memory_entries": entries}


@router.get("/missions/{mission_id}/memory-context")
def memory_context(
    mission_id: str,
    goal_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    service: MissionMemoryService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.build_context(mission_id, goal_id=goal_id, limit=limit)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.delete("/missions/{mission_id}/memory/{memory_id}")
async def archive_memory(
    mission_id: str,
    memory_id: str,
    request: MissionDecisionRequest,
    service: MissionMemoryService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.archive_memory(
            mission_id,
            memory_id,
            actor_id=request.actor_id,
            reason=request.reason,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/goals/{goal_id}/evidence-policy")
def get_evidence_policy(
    mission_id: str,
    goal_id: str,
    service: MissionMemoryService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.get_evidence_policy(mission_id, goal_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.put("/missions/{mission_id}/goals/{goal_id}/evidence-policy")
async def upsert_evidence_policy(
    mission_id: str,
    goal_id: str,
    request: MissionEvidencePolicyUpsert,
    service: MissionMemoryService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.upsert_evidence_policy(mission_id, goal_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/missions/{mission_id}/goals/{goal_id}/evidence")
async def submit_evidence(
    mission_id: str,
    goal_id: str,
    request: MissionEvidenceSubmit,
    service: MissionMemoryService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.submit_evidence(mission_id, goal_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/evidence")
def list_evidence(
    mission_id: str,
    goal_id: str | None = None,
    evidence_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(submitted|evaluated|accepted|rejected|needs_review)$",
    ),
    evidence_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionMemoryService = Depends(get_service),
) -> dict[str, Any]:
    try:
        evidence = service.list_evidence(
            mission_id,
            goal_id=goal_id,
            status=evidence_status,
            evidence_type=evidence_type,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"evidence": evidence}


@router.get("/mission-evidence/{evidence_id}")
def get_evidence(
    evidence_id: str,
    service: MissionMemoryService = Depends(get_service),
) -> dict[str, Any]:
    result = service.get_evidence(evidence_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Evidence не найдено.")
    return result


@router.post("/mission-evidence/{evidence_id}/evaluate")
async def evaluate_evidence(
    evidence_id: str,
    request: MissionEvidenceEvaluateRequest,
    service: MissionMemoryService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.evaluate_evidence(evidence_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-evidence/{evidence_id}/review")
async def review_evidence(
    evidence_id: str,
    request: MissionEvidenceReviewRequest,
    service: MissionMemoryService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.review_evidence(evidence_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/missions/{mission_id}/goals/{goal_id}/evaluate-evidence")
async def evaluate_goal_evidence(
    mission_id: str,
    goal_id: str,
    request: MissionGoalEvidenceEvaluateRequest,
    service: MissionMemoryService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.evaluate_goal(mission_id, goal_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/missions/{mission_id}/goals/{goal_id}/confirm")
async def confirm_goal(
    mission_id: str,
    goal_id: str,
    request: MissionGoalConfirmRequest,
    service: MissionMemoryService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.confirm_goal(mission_id, goal_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/missions/{mission_id}/goals/{goal_id}/reopen")
async def reopen_goal(
    mission_id: str,
    goal_id: str,
    request: MissionGoalReopenRequest,
    service: MissionMemoryService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.reopen_goal(mission_id, goal_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/goal-confirmations")
def list_confirmations(
    mission_id: str,
    goal_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionMemoryService = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_confirmations(
            mission_id,
            goal_id=goal_id,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"confirmations": rows}
