from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.api.dependencies import get_container
from backend.autonomy.governance import MissionGovernanceService
from backend.autonomy.governance_schemas import (
    MissionCheckpointCancelRequest,
    MissionCheckpointCreate,
    MissionCheckpointDecisionRequest,
    MissionCheckpointEvaluateRequest,
    MissionCheckpointScanRequest,
    MissionHypothesisCreate,
    MissionHypothesisEvaluateRequest,
    MissionHypothesisReopenRequest,
    MissionHypothesisUpdate,
    MissionRiskAssessRequest,
    MissionRiskCloseRequest,
    MissionRiskCreate,
    MissionRiskMaterializeRequest,
    MissionRiskUpdate,
)
from backend.autonomy.service import (
    AutonomousMissionError,
    MissionCycleNotFound,
    MissionGoalNotFound,
    MissionNotFound,
    MissionStateError,
)
from backend.core.container import AppContainer


router = APIRouter(tags=["mission-governance"])


def get_service(
    container: AppContainer = Depends(get_container),
) -> MissionGovernanceService:
    service = container.mission_governance_service
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Mission Governance Service не запущен.",
        )
    return service


def translate_error(exc: AutonomousMissionError) -> HTTPException:
    if isinstance(exc, (MissionNotFound, MissionGoalNotFound, MissionCycleNotFound)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, MissionStateError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/mission-governance/status")
def governance_status(
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    return service.status()


@router.get("/missions/{mission_id}/governance-context")
def governance_context(
    mission_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.build_context(mission_id, limit=limit)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/governance-dashboard")
def governance_dashboard(
    mission_id: str,
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.dashboard(mission_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post(
    "/missions/{mission_id}/risks",
    status_code=status.HTTP_201_CREATED,
)
async def create_risk(
    mission_id: str,
    request: MissionRiskCreate,
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.create_risk(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/risks")
def list_risks(
    mission_id: str,
    risk_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(identified|monitoring|mitigated|accepted|materialized|closed)$",
    ),
    severity: str | None = Query(
        default=None,
        pattern="^(low|medium|high|critical)$",
    ),
    goal_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_risks(
            mission_id,
            status=risk_status,
            severity=severity,
            goal_id=goal_id,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"risks": rows}


@router.get("/mission-risks/{risk_id}")
def get_risk(
    risk_id: str,
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    row = service.get_risk(risk_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Mission Risk не найден.")
    return row


@router.patch("/mission-risks/{risk_id}")
async def update_risk(
    risk_id: str,
    request: MissionRiskUpdate,
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.update_risk(risk_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-risks/{risk_id}/assess")
async def assess_risk(
    risk_id: str,
    request: MissionRiskAssessRequest,
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.assess_risk(risk_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-risks/{risk_id}/materialize")
async def materialize_risk(
    risk_id: str,
    request: MissionRiskMaterializeRequest,
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.materialize_risk(risk_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-risks/{risk_id}/close")
async def close_risk(
    risk_id: str,
    request: MissionRiskCloseRequest,
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.close_risk(risk_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/mission-risks/{risk_id}/assessments")
def risk_assessments(
    risk_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_risk_assessments(
            risk_id,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"assessments": rows}


@router.post(
    "/missions/{mission_id}/hypotheses",
    status_code=status.HTTP_201_CREATED,
)
async def create_hypothesis(
    mission_id: str,
    request: MissionHypothesisCreate,
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.create_hypothesis(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/hypotheses")
def list_hypotheses(
    mission_id: str,
    hypothesis_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(proposed|testing|supported|rejected|inconclusive|invalidated)$",
    ),
    goal_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_hypotheses(
            mission_id,
            status=hypothesis_status,
            goal_id=goal_id,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"hypotheses": rows}


@router.get("/mission-hypotheses/{hypothesis_id}")
def get_hypothesis(
    hypothesis_id: str,
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    row = service.get_hypothesis(hypothesis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Mission Hypothesis не найдена.")
    return row


@router.patch("/mission-hypotheses/{hypothesis_id}")
async def update_hypothesis(
    hypothesis_id: str,
    request: MissionHypothesisUpdate,
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.update_hypothesis(hypothesis_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-hypotheses/{hypothesis_id}/evaluate")
async def evaluate_hypothesis(
    hypothesis_id: str,
    request: MissionHypothesisEvaluateRequest,
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.evaluate_hypothesis(hypothesis_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-hypotheses/{hypothesis_id}/reopen")
async def reopen_hypothesis(
    hypothesis_id: str,
    request: MissionHypothesisReopenRequest,
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.reopen_hypothesis(hypothesis_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/mission-hypotheses/{hypothesis_id}/evaluations")
def hypothesis_evaluations(
    hypothesis_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_hypothesis_evaluations(
            hypothesis_id,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"evaluations": rows}


@router.post(
    "/missions/{mission_id}/checkpoints",
    status_code=status.HTTP_201_CREATED,
)
async def create_checkpoint(
    mission_id: str,
    request: MissionCheckpointCreate,
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.create_checkpoint(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/checkpoints")
def list_checkpoints(
    mission_id: str,
    checkpoint_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(pending|ready|resolved|deferred|expired|cancelled)$",
    ),
    checkpoint_type: str | None = Query(
        default=None,
        pattern="^(manual|scheduled|risk|hypothesis|deadline|budget|phase_gate)$",
    ),
    blocking: bool | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_checkpoints(
            mission_id,
            status=checkpoint_status,
            checkpoint_type=checkpoint_type,
            blocking=blocking,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"checkpoints": rows}


@router.post("/missions/{mission_id}/checkpoints/scan")
async def scan_checkpoints(
    mission_id: str,
    request: MissionCheckpointScanRequest,
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.scan_checkpoints(
            mission_id,
            auto_decide=request.auto_decide,
            actor_id=request.actor_id,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/mission-checkpoints/{checkpoint_id}")
def get_checkpoint(
    checkpoint_id: str,
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    row = service.get_checkpoint(checkpoint_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Mission Checkpoint не найден.")
    return row


@router.post("/mission-checkpoints/{checkpoint_id}/evaluate")
async def evaluate_checkpoint(
    checkpoint_id: str,
    request: MissionCheckpointEvaluateRequest,
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.evaluate_checkpoint(checkpoint_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-checkpoints/{checkpoint_id}/decide")
async def decide_checkpoint(
    checkpoint_id: str,
    request: MissionCheckpointDecisionRequest,
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.decide_checkpoint(checkpoint_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/mission-checkpoints/{checkpoint_id}/cancel")
async def cancel_checkpoint(
    checkpoint_id: str,
    request: MissionCheckpointCancelRequest,
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.cancel_checkpoint(checkpoint_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/mission-checkpoints/{checkpoint_id}/decisions")
def checkpoint_decisions(
    checkpoint_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MissionGovernanceService = Depends(get_service),
) -> dict[str, Any]:
    try:
        rows = service.list_checkpoint_decisions(
            checkpoint_id,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"decisions": rows}
