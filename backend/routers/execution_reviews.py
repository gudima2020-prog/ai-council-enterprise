from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_container
from backend.core.container import AppContainer
from backend.orchestration.critic import (
    ExecutionPlanCriticError,
    ExecutionPlanCriticService,
    PlanReviewRejected,
)
from backend.orchestration.critic_schemas import (
    ExecutionPlanFixRequest,
    ExecutionPlanReviewRequest,
)

router = APIRouter(tags=["execution-plan-reviews"])


def get_critic(
    container: AppContainer = Depends(get_container),
) -> ExecutionPlanCriticService:
    service = container.execution_plan_critic_service
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Execution Plan Critic не запущен.",
        )
    return service


@router.get("/execution-critic/status")
def critic_status(
    service: ExecutionPlanCriticService = Depends(get_critic),
) -> dict[str, Any]:
    return service.stats()


@router.post("/execution-plans/{plan_id}/review")
async def review_plan(
    plan_id: str,
    request: ExecutionPlanReviewRequest,
    service: ExecutionPlanCriticService = Depends(get_critic),
) -> dict[str, Any]:
    try:
        result = await service.review(plan_id, request)
    except ExecutionPlanCriticError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return result


@router.post("/execution-plans/{plan_id}/review-and-fix")
async def review_and_fix_plan(
    plan_id: str,
    request: ExecutionPlanReviewRequest,
    service: ExecutionPlanCriticService = Depends(get_critic),
) -> dict[str, Any]:
    try:
        result = await service.review_and_fix(plan_id, request)
    except PlanReviewRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ExecutionPlanCriticError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution Plan не найден.",
        )
    return result


@router.post("/execution-plans/{plan_id}/apply-review-fixes")
async def apply_review_fixes(
    plan_id: str,
    request: ExecutionPlanFixRequest,
    service: ExecutionPlanCriticService = Depends(get_critic),
) -> dict[str, Any]:
    try:
        return await service.apply_fixes(plan_id, request)
    except ExecutionPlanCriticError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/execution-plan-reviews")
def list_reviews(
    plan_id: str | None = None,
    workspace_id: str | None = None,
    decision: str | None = Query(
        default=None,
        pattern="^(pass|revise|reject)$",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: ExecutionPlanCriticService = Depends(get_critic),
) -> dict[str, Any]:
    return {
        "reviews": service.list_reviews(
            plan_id=plan_id,
            workspace_id=workspace_id,
            decision=decision,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/execution-plan-reviews/{review_id}")
def get_review(
    review_id: str,
    service: ExecutionPlanCriticService = Depends(get_critic),
) -> dict[str, Any]:
    result = service.get_review(review_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Plan Review не найден.")
    return result
