from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from backend.api.dependencies import get_container
from backend.autonomy.schemas import (
    AutonomousWorkspacePolicyUpsert,
    MissionActivateRequest,
    MissionCreate,
    MissionCycleCreateRequest,
    MissionCycleRunRequest,
    MissionDecisionRequest,
    MissionGoalCreate,
    MissionGoalUpdate,
    MissionPauseRequest,
    MissionProgressRequest,
    MissionTickRequest,
    MissionUpdate,
)
from backend.autonomy.service import (
    AutonomousMissionError,
    AutonomousMissionService,
    MissionCycleNotFound,
    MissionGoalNotFound,
    MissionNotFound,
    MissionStateError,
)
from backend.core.container import AppContainer


router = APIRouter(tags=["autonomous-missions"])


def get_service(
    container: AppContainer = Depends(get_container),
) -> AutonomousMissionService:
    service = container.autonomous_mission_service
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Autonomous Mission Service не запущен.",
        )
    return service


def translate_error(exc: AutonomousMissionError) -> HTTPException:
    if isinstance(exc, (MissionNotFound, MissionGoalNotFound, MissionCycleNotFound)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, MissionStateError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/autonomous-workspaces/status")
def runtime_status(
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    return service.status()


@router.get("/autonomous-workspaces/{workspace_id}/policy")
def get_policy(
    workspace_id: str,
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    return service.get_policy(workspace_id)


@router.put("/autonomous-workspaces/{workspace_id}/policy")
async def upsert_policy(
    workspace_id: str,
    request: AutonomousWorkspacePolicyUpsert,
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.upsert_policy(workspace_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/autonomous-workspaces/{workspace_id}/dashboard")
def dashboard(
    workspace_id: str,
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    return service.dashboard(workspace_id)


@router.post("/autonomous-workspaces/tick")
async def tick(
    request: MissionTickRequest,
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    return await service.tick_once(
        workspace_id=request.workspace_id,
        limit=request.limit,
    )


@router.post("/missions", status_code=status.HTTP_201_CREATED)
async def create_mission(
    request: MissionCreate,
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.create_mission(request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions")
def list_missions(
    workspace_id: str | None = None,
    mission_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(draft|active|paused|completed|failed|cancelled)$",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    return {
        "missions": service.list_missions(
            workspace_id=workspace_id,
            status=mission_status,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/missions/{mission_id}")
def get_mission(
    mission_id: str,
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    result = service.get_mission(mission_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Mission не найдена.")
    return result


@router.patch("/missions/{mission_id}")
async def update_mission(
    mission_id: str,
    request: MissionUpdate,
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.update_mission(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.delete("/missions/{mission_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mission(
    mission_id: str,
    service: AutonomousMissionService = Depends(get_service),
) -> Response:
    try:
        deleted = await service.delete_mission(mission_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Mission не найдена.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/missions/{mission_id}/validate")
def validate_mission(
    mission_id: str,
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.validate_mission(mission_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/missions/{mission_id}/activate")
async def activate_mission(
    mission_id: str,
    request: MissionActivateRequest,
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.activate_mission(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/missions/{mission_id}/pause")
async def pause_mission(
    mission_id: str,
    request: MissionPauseRequest,
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.pause_mission(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/missions/{mission_id}/resume")
async def resume_mission(
    mission_id: str,
    request: MissionActivateRequest,
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.resume_mission(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/missions/{mission_id}/complete")
async def complete_mission(
    mission_id: str,
    request: MissionDecisionRequest,
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.complete_mission(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/missions/{mission_id}/fail")
async def fail_mission(
    mission_id: str,
    request: MissionDecisionRequest,
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.fail_mission(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/missions/{mission_id}/cancel")
async def cancel_mission(
    mission_id: str,
    request: MissionDecisionRequest,
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.cancel_mission(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.post("/missions/{mission_id}/goals", status_code=status.HTTP_201_CREATED)
async def add_goal(
    mission_id: str,
    request: MissionGoalCreate,
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.add_goal(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.patch("/missions/{mission_id}/goals/{goal_id}")
async def update_goal(
    mission_id: str,
    goal_id: str,
    request: MissionGoalUpdate,
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.update_goal(mission_id, goal_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.delete(
    "/missions/{mission_id}/goals/{goal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_goal(
    mission_id: str,
    goal_id: str,
    service: AutonomousMissionService = Depends(get_service),
) -> Response:
    try:
        await service.delete_goal(mission_id, goal_id)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/missions/{mission_id}/progress")
async def record_progress(
    mission_id: str,
    request: MissionProgressRequest,
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.record_progress(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/progress")
def list_progress(
    mission_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    try:
        updates = service.list_progress(
            mission_id,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"progress_updates": updates}


@router.post("/missions/{mission_id}/cycles", status_code=status.HTTP_201_CREATED)
async def create_cycle(
    mission_id: str,
    request: MissionCycleCreateRequest,
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.create_cycle(mission_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc


@router.get("/missions/{mission_id}/cycles")
def list_cycles(
    mission_id: str,
    cycle_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(queued|planning|ready|running|completed|failed|cancelled)$",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    try:
        cycles = service.list_cycles(
            mission_id,
            status=cycle_status,
            limit=limit,
            offset=offset,
        )
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
    return {"cycles": cycles}


@router.get("/mission-cycles/{cycle_id}")
def get_cycle(
    cycle_id: str,
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    result = service.get_cycle(cycle_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Mission Cycle не найден.")
    return result


@router.post("/mission-cycles/{cycle_id}/run")
async def run_cycle(
    cycle_id: str,
    request: MissionCycleRunRequest,
    service: AutonomousMissionService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return await service.run_cycle(cycle_id, request)
    except AutonomousMissionError as exc:
        raise translate_error(exc) from exc
