from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse

from backend.api.dependencies import (
    get_container,
    get_council_control_service,
    get_council_history_service,
    get_council_live_manager,
)
from backend.core.container import AppContainer
from backend.council.control import (
    CouncilControlService,
    CouncilCostApprovalInvalidError,
    CouncilCostApprovalRequiredError,
    CouncilCostBlockedError,
)
from backend.council.history import CouncilHistoryService
from backend.council.live import CouncilLiveManager
from backend.council.schemas import (
    CouncilBudgetPolicy,
    CouncilBudgetPolicyUpdate,
    CouncilCostApproval,
    CouncilCostEstimate,
    CouncilCostUsage,
    CouncilLiveCancelResponse,
    CouncilLiveStartResponse,
    CouncilLiveStatusResponse,
    CouncilMemberResult,
    CouncilRole,
    CouncilRetentionPolicy,
    CouncilRetentionPolicyUpdate,
    CouncilRetentionPurgeResponse,
    CouncilPreset,
    CouncilPresetCreate,
    CouncilPresetDeleteResponse,
    CouncilPresetList,
    CouncilPresetUpdate,
    CouncilRunDeleteResponse,
    CouncilRunHistoryDetail,
    CouncilRunHistoryPage,
    CouncilRunRequest,
    CouncilRunResponse,
    CouncilRoutingPlan,
)
from backend.council.service import CouncilExecutionError, CouncilService
from backend.gateway.factory import build_ai_gateway


router = APIRouter(tags=["council"])


@router.get("/council/status")
def council_status() -> dict:
    return {
        "status": "ready",
        "min_members": 1,
        "max_members": 6,
        "roles": [role.value for role in CouncilRole],
        "modes": ["universal", "crypto", "code", "documents"],
        "execution_modes": [
            "solo", "council", "best_of_n", "review", "arbitration", "delegate"
        ],
        "supports_partial_results": True,
        "supports_history": True,
        "supports_replay": True,
        "supports_retention_policy": True,
        "supports_live_progress": True,
        "supports_cancellation": True,
        "supports_failed_member_retry": True,
        "supports_presets": True,
        "supports_cost_preflight": True,
        "supports_cost_approval": True,
        "supports_workspace_cost_policy": True,
        "supports_actual_cost_ledger": True,
        "supports_cost_reservations": True,
        "supports_workspace_quotas": True,
        "supports_budget_aware_routing": True,
        "supports_separate_chair": True,
        "supports_best_of_n": True,
        "supports_independent_review": True,
        "supports_independent_arbitration": True,
        "supports_bounded_delegation": True,
        "max_delegation_depth": 1,
        "max_delegation_calls": 8,
        "live_transport": "sse",
        "synthesis_format": "structured_json_with_safe_fallback",
    }


@router.post("/council/run", response_model=CouncilRunResponse)
async def run_council(
    payload: CouncilRunRequest,
    http_request: Request,
    container: AppContainer = Depends(get_container),
    history: CouncilHistoryService = Depends(get_council_history_service),
    control: CouncilControlService = Depends(get_council_control_service),
) -> CouncilRunResponse | JSONResponse:
    request = payload.model_copy(
        update={
            "workspace_id": _effective_workspace_id(
                http_request,
                payload.workspace_id,
            )
        }
    )
    request = await _authorize_cost(request, control)
    return await _execute_council(
        request=request,
        container=container,
        history=history,
    )


@router.post(
    "/council/live",
    response_model=CouncilLiveStartResponse,
    status_code=202,
)
async def start_live_council(
    payload: CouncilRunRequest,
    http_request: Request,
    container: AppContainer = Depends(get_container),
    live: CouncilLiveManager = Depends(get_council_live_manager),
    control: CouncilControlService = Depends(get_council_control_service),
) -> CouncilLiveStartResponse:
    request = payload.model_copy(
        update={
            "workspace_id": _effective_workspace_id(
                http_request,
                payload.workspace_id,
            )
        }
    )
    request = await _authorize_cost(request, control)
    return await _start_live(
        request=request,
        container=container,
        live=live,
    )


@router.get(
    "/council/live/{run_id}",
    response_model=CouncilLiveStatusResponse,
)
async def get_live_council_status(
    run_id: str,
    http_request: Request,
    live: CouncilLiveManager = Depends(get_council_live_manager),
) -> CouncilLiveStatusResponse:
    result = await live.status(
        run_id=run_id,
        workspace_id=_effective_workspace_id(http_request),
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Live-запуск Совета не найден в текущем Workspace.",
        )
    return result


@router.get("/council/live/{run_id}/events")
async def stream_live_council_events(
    run_id: str,
    http_request: Request,
    live: CouncilLiveManager = Depends(get_council_live_manager),
) -> StreamingResponse:
    workspace_id = _effective_workspace_id(http_request)
    status = await live.status(
        run_id=run_id,
        workspace_id=workspace_id,
    )
    if status is None:
        raise HTTPException(
            status_code=404,
            detail="Live-запуск Совета не найден в текущем Workspace.",
        )

    raw_last_event_id = http_request.headers.get("Last-Event-ID", "0")
    try:
        last_event_id = int(raw_last_event_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Last-Event-ID должен быть целым числом.",
        ) from exc

    return StreamingResponse(
        live.stream(
            run_id=run_id,
            workspace_id=workspace_id,
            last_event_id=last_event_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete(
    "/council/live/{run_id}",
    response_model=CouncilLiveCancelResponse,
)
async def cancel_live_council(
    run_id: str,
    http_request: Request,
    live: CouncilLiveManager = Depends(get_council_live_manager),
) -> CouncilLiveCancelResponse:
    result = await live.cancel(
        run_id=run_id,
        workspace_id=_effective_workspace_id(http_request),
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Live-запуск Совета не найден в текущем Workspace.",
        )
    return result


@router.get("/council/presets", response_model=CouncilPresetList)
def list_council_presets(
    http_request: Request,
    control: CouncilControlService = Depends(get_council_control_service),
) -> CouncilPresetList:
    return control.list_presets(_effective_workspace_id(http_request))


@router.post("/council/presets", response_model=CouncilPreset, status_code=201)
async def create_council_preset(
    payload: CouncilPresetCreate,
    http_request: Request,
    control: CouncilControlService = Depends(get_council_control_service),
) -> CouncilPreset:
    try:
        return await control.create_preset(payload, _effective_workspace_id(http_request))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/council/presets/{preset_id}", response_model=CouncilPreset)
async def update_council_preset(
    preset_id: str,
    payload: CouncilPresetUpdate,
    http_request: Request,
    control: CouncilControlService = Depends(get_council_control_service),
) -> CouncilPreset:
    try:
        result = await control.update_preset(
            preset_id, payload, _effective_workspace_id(http_request)
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Preset Совета не найден.")
    return result


@router.delete(
    "/council/presets/{preset_id}",
    response_model=CouncilPresetDeleteResponse,
)
async def delete_council_preset(
    preset_id: str,
    http_request: Request,
    control: CouncilControlService = Depends(get_council_control_service),
) -> CouncilPresetDeleteResponse:
    result = await control.delete_preset(preset_id, _effective_workspace_id(http_request))
    if result is None:
        raise HTTPException(status_code=404, detail="Preset Совета не найден.")
    return result


@router.get("/council/cost/policy", response_model=CouncilBudgetPolicy)
def get_council_cost_policy(
    http_request: Request,
    control: CouncilControlService = Depends(get_council_control_service),
) -> CouncilBudgetPolicy:
    return control.get_budget_policy(_effective_workspace_id(http_request))


@router.put("/council/cost/policy", response_model=CouncilBudgetPolicy)
async def update_council_cost_policy(
    payload: CouncilBudgetPolicyUpdate,
    http_request: Request,
    control: CouncilControlService = Depends(get_council_control_service),
) -> CouncilBudgetPolicy:
    return await control.update_budget_policy(
        _effective_workspace_id(http_request), payload
    )


@router.post("/council/cost/estimate", response_model=CouncilCostEstimate)
def estimate_council_cost(
    payload: CouncilRunRequest,
    http_request: Request,
    control: CouncilControlService = Depends(get_council_control_service),
) -> CouncilCostEstimate:
    workspace_id = _effective_workspace_id(http_request, payload.workspace_id)
    request = payload.model_copy(update={"workspace_id": workspace_id})
    return control.estimate(request, workspace_id)


@router.get("/council/cost/usage", response_model=CouncilCostUsage)
def get_council_cost_usage(
    http_request: Request,
    control: CouncilControlService = Depends(get_council_control_service),
) -> CouncilCostUsage:
    return control.get_usage(_effective_workspace_id(http_request))


@router.post("/council/cost/route", response_model=CouncilRoutingPlan)
def route_council_cost(
    payload: CouncilRunRequest,
    http_request: Request,
    control: CouncilControlService = Depends(get_council_control_service),
) -> CouncilRoutingPlan:
    workspace_id = _effective_workspace_id(http_request, payload.workspace_id)
    request = payload.model_copy(update={"workspace_id": workspace_id})
    return control.route(request, workspace_id)


@router.post("/council/cost/approve", response_model=CouncilCostApproval)
async def approve_council_cost(
    payload: CouncilRunRequest,
    http_request: Request,
    control: CouncilControlService = Depends(get_council_control_service),
) -> CouncilCostApproval:
    workspace_id = _effective_workspace_id(http_request, payload.workspace_id)
    request = payload.model_copy(update={"workspace_id": workspace_id})
    try:
        return await control.approve(request, workspace_id)
    except CouncilCostBlockedError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "COUNCIL_COST_BLOCKED",
                "message": str(exc),
                "estimate": exc.estimate.model_dump(mode="json"),
            },
        ) from exc


@router.get(
    "/council/retention",
    response_model=CouncilRetentionPolicy,
)
def get_council_retention_policy(
    http_request: Request,
    history: CouncilHistoryService = Depends(get_council_history_service),
) -> CouncilRetentionPolicy:
    return history.get_retention_policy(
        _effective_workspace_id(http_request)
    )


@router.put(
    "/council/retention",
    response_model=CouncilRetentionPolicy,
)
async def update_council_retention_policy(
    payload: CouncilRetentionPolicyUpdate,
    http_request: Request,
    history: CouncilHistoryService = Depends(get_council_history_service),
) -> CouncilRetentionPolicy:
    return await history.update_retention_policy(
        workspace_id=_effective_workspace_id(http_request),
        update=payload,
    )


@router.post(
    "/council/retention/purge",
    response_model=CouncilRetentionPurgeResponse,
)
async def purge_council_history(
    http_request: Request,
    history: CouncilHistoryService = Depends(get_council_history_service),
) -> CouncilRetentionPurgeResponse:
    return await history.purge_expired(
        workspace_id=_effective_workspace_id(http_request)
    )


@router.get(
    "/council/runs",
    response_model=CouncilRunHistoryPage,
)
def list_council_runs(
    http_request: Request,
    status: Literal[
        "completed",
        "partial",
        "failed",
        "cancelled",
    ]
    | None = Query(default=None),
    mode: Literal[
        "universal",
        "crypto",
        "code",
        "documents",
    ]
    | None = Query(default=None),
    query: str | None = Query(default=None, max_length=500),
    started_from: datetime | None = Query(default=None),
    started_to: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    history: CouncilHistoryService = Depends(get_council_history_service),
) -> CouncilRunHistoryPage:
    try:
        return history.list_runs(
            workspace_id=_effective_workspace_id(http_request),
            status=status,
            mode=mode,
            query=query,
            started_from=started_from,
            started_to=started_to,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/council/runs/{run_id}",
    response_model=CouncilRunHistoryDetail,
)
def get_council_run(
    run_id: str,
    http_request: Request,
    history: CouncilHistoryService = Depends(get_council_history_service),
) -> CouncilRunHistoryDetail:
    result = history.get_run(
        run_id=run_id,
        workspace_id=_effective_workspace_id(http_request),
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Запуск Совета не найден в текущем Workspace.",
        )
    return result


@router.post(
    "/council/runs/{run_id}/cost/approve-replay",
    response_model=CouncilCostApproval,
)
async def approve_replay_council_cost(
    run_id: str,
    http_request: Request,
    history: CouncilHistoryService = Depends(get_council_history_service),
    control: CouncilControlService = Depends(get_council_control_service),
) -> CouncilCostApproval:
    workspace_id = _effective_workspace_id(http_request)
    request = history.build_replay_request(
        run_id=run_id,
        workspace_id=workspace_id,
    )
    if request is None:
        raise HTTPException(
            status_code=404,
            detail="Запуск Совета не найден в текущем Workspace.",
        )
    try:
        return await control.approve(request, workspace_id)
    except CouncilCostBlockedError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "COUNCIL_COST_BLOCKED",
                "message": str(exc),
                "estimate": exc.estimate.model_dump(mode="json"),
            },
        ) from exc


@router.post(
    "/council/runs/{run_id}/cost/approve-retry-failed",
    response_model=CouncilCostApproval,
)
async def approve_retry_failed_council_cost(
    run_id: str,
    http_request: Request,
    history: CouncilHistoryService = Depends(get_council_history_service),
    control: CouncilControlService = Depends(get_council_control_service),
) -> CouncilCostApproval:
    workspace_id = _effective_workspace_id(http_request)
    try:
        plan = history.build_failed_retry_plan(
            run_id=run_id,
            workspace_id=workspace_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail="Запуск Совета не найден в текущем Workspace.",
        )
    try:
        return await control.approve(plan.request, workspace_id)
    except CouncilCostBlockedError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "COUNCIL_COST_BLOCKED",
                "message": str(exc),
                "estimate": exc.estimate.model_dump(mode="json"),
            },
        ) from exc


@router.post(
    "/council/runs/{run_id}/replay",
    response_model=CouncilRunResponse,
)
async def replay_council_run(
    run_id: str,
    http_request: Request,
    approval_token: str | None = Query(default=None, max_length=96),
    container: AppContainer = Depends(get_container),
    history: CouncilHistoryService = Depends(get_council_history_service),
    control: CouncilControlService = Depends(get_council_control_service),
) -> CouncilRunResponse | JSONResponse:
    request = history.build_replay_request(
        run_id=run_id,
        workspace_id=_effective_workspace_id(http_request),
    )
    if request is None:
        raise HTTPException(
            status_code=404,
            detail="Запуск Совета не найден в текущем Workspace.",
        )
    request = request.model_copy(update={"cost_approval_token": approval_token})
    request = await _authorize_cost(request, control)
    return await _execute_council(
        request=request,
        container=container,
        history=history,
        replay_of_run_id=run_id,
    )


@router.post(
    "/council/runs/{run_id}/replay/live",
    response_model=CouncilLiveStartResponse,
    status_code=202,
)
async def replay_council_run_live(
    run_id: str,
    http_request: Request,
    approval_token: str | None = Query(default=None, max_length=96),
    container: AppContainer = Depends(get_container),
    history: CouncilHistoryService = Depends(get_council_history_service),
    live: CouncilLiveManager = Depends(get_council_live_manager),
    control: CouncilControlService = Depends(get_council_control_service),
) -> CouncilLiveStartResponse:
    workspace_id = _effective_workspace_id(http_request)
    request = history.build_replay_request(
        run_id=run_id,
        workspace_id=workspace_id,
    )
    if request is None:
        raise HTTPException(
            status_code=404,
            detail="Запуск Совета не найден в текущем Workspace.",
        )
    request = request.model_copy(update={"cost_approval_token": approval_token})
    request = await _authorize_cost(request, control)
    return await _start_live(
        request=request,
        container=container,
        live=live,
        kind="replay",
        replay_of_run_id=run_id,
    )


@router.post(
    "/council/runs/{run_id}/retry-failed",
    response_model=CouncilLiveStartResponse,
    status_code=202,
)
async def retry_failed_council_members(
    run_id: str,
    http_request: Request,
    approval_token: str | None = Query(default=None, max_length=96),
    container: AppContainer = Depends(get_container),
    history: CouncilHistoryService = Depends(get_council_history_service),
    live: CouncilLiveManager = Depends(get_council_live_manager),
    control: CouncilControlService = Depends(get_council_control_service),
) -> CouncilLiveStartResponse:
    workspace_id = _effective_workspace_id(http_request)
    try:
        plan = history.build_failed_retry_plan(
            run_id=run_id,
            workspace_id=workspace_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail="Запуск Совета не найден в текущем Workspace.",
        )
    retry_request = plan.request.model_copy(
        update={"cost_approval_token": approval_token}
    )
    retry_request = await _authorize_cost(retry_request, control)
    return await _start_live(
        request=retry_request,
        container=container,
        live=live,
        kind="retry_failed",
        replay_of_run_id=run_id,
        reused_member_results=plan.reused_member_results,
    )


@router.delete(
    "/council/runs/{run_id}",
    response_model=CouncilRunDeleteResponse,
)
async def delete_council_run(
    run_id: str,
    http_request: Request,
    history: CouncilHistoryService = Depends(get_council_history_service),
) -> CouncilRunDeleteResponse:
    result = await history.delete_run(
        run_id=run_id,
        workspace_id=_effective_workspace_id(http_request),
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Запуск Совета не найден в текущем Workspace.",
        )
    return result


async def _execute_council(
    *,
    request: CouncilRunRequest,
    container: AppContainer,
    history: CouncilHistoryService,
    replay_of_run_id: str | None = None,
) -> CouncilRunResponse | JSONResponse:
    gateway = build_ai_gateway(
        settings=container.settings,
        event_bus=container.event_bus,
        secret_manager=container.secret_manager_service,
    )
    service = CouncilService(
        gateway=gateway,
        event_bus=container.event_bus,
    )

    try:
        result = await service.run(request)
    except CouncilExecutionError as exc:
        await history.record_failure(
            request=request,
            error=exc,
            replay_of_run_id=replay_of_run_id,
        )
        return JSONResponse(
            status_code=502,
            content=jsonable_encoder(
                {
                    "detail": {
                        "code": "COUNCIL_ALL_MEMBERS_FAILED",
                        "message": str(exc),
                        "run_id": exc.run_id,
                        "history_saved": True,
                        "members": [
                            member.model_dump(mode="json")
                            for member in exc.member_results
                        ],
                    }
                }
            ),
        )

    saved = await history.record_success(
        request=request,
        result=result,
        replay_of_run_id=replay_of_run_id,
    )
    return result.model_copy(
        update={
            "history_saved": True,
            "replay_of_run_id": replay_of_run_id,
            "actual_cost_usd": saved.actual_cost_usd,
            "actual_cost_status": saved.actual_cost_status,
            "actual_total_tokens": saved.actual_total_tokens,
        }
    )


async def _start_live(
    *,
    request: CouncilRunRequest,
    container: AppContainer,
    live: CouncilLiveManager,
    kind: Literal["run", "replay", "retry_failed"] = "run",
    replay_of_run_id: str | None = None,
    reused_member_results: dict[int, CouncilMemberResult] | None = None,
) -> CouncilLiveStartResponse:
    gateway = build_ai_gateway(
        settings=container.settings,
        event_bus=container.event_bus,
        secret_manager=container.secret_manager_service,
    )
    return await live.start(
        request=request,
        gateway=gateway,
        kind=kind,
        replay_of_run_id=replay_of_run_id,
        reused_member_results=reused_member_results,
    )


async def _authorize_cost(
    request: CouncilRunRequest,
    control: CouncilControlService,
) -> CouncilRunRequest:
    try:
        authorized, _estimate = await control.authorize(
            request, request.workspace_id
        )
        return authorized
    except CouncilCostBlockedError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "COUNCIL_COST_BLOCKED",
                "message": str(exc),
                "estimate": exc.estimate.model_dump(mode="json"),
            },
        ) from exc
    except CouncilCostApprovalRequiredError as exc:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "COUNCIL_COST_APPROVAL_REQUIRED",
                "message": str(exc),
                "estimate": exc.estimate.model_dump(mode="json"),
            },
        ) from exc
    except CouncilCostApprovalInvalidError as exc:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "COUNCIL_COST_APPROVAL_INVALID",
                "message": str(exc),
                "estimate": exc.estimate.model_dump(mode="json"),
            },
        ) from exc


def _effective_workspace_id(
    request: Request,
    requested_workspace_id: str | None = None,
) -> str | None:
    resolved = getattr(request.state, "workspace_context", None)
    context_workspace_id = (
        resolved.get("workspace_id")
        if isinstance(resolved, dict)
        else None
    )
    if (
        requested_workspace_id is not None
        and context_workspace_id is not None
        and requested_workspace_id != context_workspace_id
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "workspace_id запроса не совпадает с текущим "
                "X-Workspace-ID."
            ),
        )
    return context_workspace_id or requested_workspace_id
