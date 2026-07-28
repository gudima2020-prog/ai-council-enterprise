from __future__ import annotations

import asyncio
import ast
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from inspect import isawaitable
import json
import re
from threading import Event as ThreadEvent
from time import perf_counter
from typing import Any
import uuid

from backend.core.events import Event, EventBus
from backend.council.prompts import (
    ARBITER_SYSTEM_PROMPT,
    BEST_OF_N_SYSTEM_PROMPT,
    DELEGATE_SYSTEM_PROMPT,
    DELEGATION_PLANNER_SYSTEM_PROMPT,
    REVIEWER_SYSTEM_PROMPT,
    REVISION_SYSTEM_PROMPT,
    SYNTHESIS_SYSTEM_PROMPT,
    build_member_system_prompt,
)
from backend.council.schemas import (
    CouncilAuxiliaryResult,
    CouncilExecutionMode,
    CouncilMemberRequest,
    CouncilMemberResult,
    CouncilOrchestrationTrace,
    CouncilRunRequest,
    CouncilRunResponse,
    CouncilSynthesis,
    CouncilUsage,
)
from backend.gateway.schemas import GatewayResponse
from backend.gateway.providers.base import ProviderStreamCancelled
from backend.gateway.service import AIGateway


FREE_FALLBACK_MODEL = "openrouter/free"
TRANSIENT_FREE_MODEL_ERRORS = frozenset(
    {
        "MODEL_UNAVAILABLE",
        "NETWORK_ERROR",
        "PROVIDER_UNAVAILABLE",
        "RATE_LIMIT",
        "TIMEOUT",
    }
)
ProgressCallback = Callable[
    [str, dict[str, Any]],
    Awaitable[None] | None,
]


class _CouncilCancellationRequested(RuntimeError):
    pass


class CouncilExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        run_id: str,
        started_at: datetime,
        finished_at: datetime,
        duration_ms: float,
        member_results: list[CouncilMemberResult] | None = None,
        auxiliary_results: list[CouncilAuxiliaryResult] | None = None,
    ) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.started_at = started_at
        self.finished_at = finished_at
        self.duration_ms = duration_ms
        self.member_results = list(member_results or [])
        self.auxiliary_results = list(auxiliary_results or [])


class CouncilCancelledError(CouncilExecutionError):
    pass


class CouncilService:
    def __init__(
        self,
        *,
        gateway: AIGateway,
        event_bus: EventBus,
    ) -> None:
        self._gateway = gateway
        self._event_bus = event_bus

    async def run(
        self,
        request: CouncilRunRequest,
        *,
        run_id: str | None = None,
        progress: ProgressCallback | None = None,
        cancel_event: asyncio.Event | None = None,
        cancellation_check: Callable[[], bool] | None = None,
        reused_member_results: dict[int, CouncilMemberResult] | None = None,
    ) -> CouncilRunResponse:
        run_id = run_id or f"council_{uuid.uuid4().hex}"
        started_at = datetime.now(timezone.utc)
        started = perf_counter()
        reused = dict(reused_member_results or {})
        member_results: list[CouncilMemberResult | None] = [
            None
        ] * len(request.members)
        spent_auxiliary: list[CouncilAuxiliaryResult] = []

        for index, result in reused.items():
            if index < 0 or index >= len(request.members):
                raise ValueError("Reused Council member index is out of range.")
            if result.status != "success":
                raise ValueError(
                    "Only successful Council member results can be reused."
                )
            member_results[index] = result

        await self._event_bus.publish(
            Event(
                event_type="council.run.started",
                source="council",
                correlation_id=request.correlation_id,
                payload={
                    "run_id": run_id,
                    "mode": request.mode,
                    "execution_mode": request.execution_mode.value,
                    "member_count": len(request.members),
                    "workspace_id": request.workspace_id,
                    "member_timeout_seconds": (
                        request.member_timeout_seconds
                    ),
                    "reused_members": len(reused),
                },
            )
        )
        await self._emit_progress(
            progress,
            "run.started",
            {
                "run_id": run_id,
                "mode": request.mode,
                "execution_mode": request.execution_mode.value,
                "member_count": len(request.members),
                "member_timeout_seconds": request.member_timeout_seconds,
                "reused_members": len(reused),
            },
        )

        for index, result in sorted(reused.items()):
            await self._emit_progress(
                progress,
                "member.reused",
                {
                    "run_id": run_id,
                    "member_index": index,
                    "member": result.model_dump(mode="json"),
                },
            )

        if self._is_cancellation_requested(
            cancel_event=cancel_event,
            cancellation_check=cancellation_check,
        ):
            raise await self._cancelled_error(
                request=request,
                run_id=run_id,
                started_at=started_at,
                started=started,
                member_results=member_results,
                auxiliary_results=spent_auxiliary,
            )

        async def execute_member(
            index: int,
            member: CouncilMemberRequest,
        ) -> None:
            member_results[index] = await self._run_member(
                run_id=run_id,
                request=request,
                member=member,
                member_index=index,
                progress=progress,
                cancel_event=cancel_event,
                cancellation_check=cancellation_check,
            )

        member_tasks = [
            asyncio.create_task(
                execute_member(index, member),
                name=f"{run_id}:member:{index}",
            )
            for index, member in enumerate(request.members)
            if index not in reused
        ]

        try:
            if member_tasks:
                await asyncio.gather(*member_tasks)
        except _CouncilCancellationRequested:
            for task in member_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*member_tasks, return_exceptions=True)
            raise await self._cancelled_error(
                request=request,
                run_id=run_id,
                started_at=started_at,
                started=started,
                member_results=member_results,
                auxiliary_results=spent_auxiliary,
            )

        completed_results = [
            result
            for result in member_results
            if result is not None
        ]
        successful = [
            result
            for result in completed_results
            if result.status == "success"
        ]

        if not successful:
            finished_at = datetime.now(timezone.utc)
            duration_ms = round((perf_counter() - started) * 1000, 2)
            await self._event_bus.publish(
                Event(
                    event_type="council.run.failed",
                    source="council",
                    correlation_id=request.correlation_id,
                    payload={
                        "run_id": run_id,
                        "reason": "all_members_failed",
                        "duration_ms": duration_ms,
                    },
                )
            )
            raise CouncilExecutionError(
                "Все участники Совета ИИ завершились с ошибкой.",
                run_id=run_id,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
                member_results=completed_results,
            )

        if self._is_cancellation_requested(
            cancel_event=cancel_event,
            cancellation_check=cancellation_check,
        ):
            raise await self._cancelled_error(
                request=request,
                run_id=run_id,
                started_at=started_at,
                started=started,
                member_results=member_results,
                auxiliary_results=spent_auxiliary,
            )

        try:
            synthesis, orchestration = await self._orchestrate(
                run_id=run_id,
                request=request,
                successful=successful,
                progress=progress,
                cancel_event=cancel_event,
                cancellation_check=cancellation_check,
                spent_auxiliary=spent_auxiliary,
            )
        except _CouncilCancellationRequested:
            raise await self._cancelled_error(
                request=request,
                run_id=run_id,
                started_at=started_at,
                started=started,
                member_results=member_results,
                auxiliary_results=spent_auxiliary,
            )
        auxiliary_failed = any(
            item.status == "error" for item in orchestration.auxiliary_calls
        )
        status = (
            "completed"
            if len(successful) == len(completed_results)
            and synthesis.status != "fallback"
            and not auxiliary_failed
            else "partial"
        )
        finished_at = datetime.now(timezone.utc)
        duration_ms = round((perf_counter() - started) * 1000, 2)

        await self._event_bus.publish(
            Event(
                event_type="council.run.completed",
                source="council",
                correlation_id=request.correlation_id,
                payload={
                    "run_id": run_id,
                    "status": status,
                    "execution_mode": request.execution_mode.value,
                    "successful_members": len(successful),
                    "failed_members": (
                        len(completed_results) - len(successful)
                    ),
                    "confidence": synthesis.confidence,
                    "duration_ms": duration_ms,
                },
            )
        )

        cost_values = [
            member.provider_reported_cost_usd
            for member in completed_results
        ]
        if orchestration.finalizer_stage != "solo":
            cost_values.append(synthesis.provider_reported_cost_usd)
        cost_values.extend(
            item.provider_reported_cost_usd
            for item in orchestration.auxiliary_calls
        )
        known_cost_values = [value for value in cost_values if value is not None]
        if len(known_cost_values) == len(cost_values):
            actual_cost_status = "known"
        elif known_cost_values:
            actual_cost_status = "partial"
        else:
            actual_cost_status = "unknown"
        actual_cost_usd = (
            round(sum(known_cost_values), 8) if known_cost_values else None
        )
        token_values = [
            member.usage.total_tokens
            for member in completed_results
            if member.usage.total_tokens is not None
        ]
        if orchestration.finalizer_stage != "solo" and synthesis.usage.total_tokens is not None:
            token_values.append(synthesis.usage.total_tokens)
        token_values.extend(
            item.usage.total_tokens
            for item in orchestration.auxiliary_calls
            if item.usage.total_tokens is not None
        )

        return CouncilRunResponse(
            run_id=run_id,
            execution_mode=request.execution_mode,
            status=status,
            question=request.question,
            mode=request.mode,
            members=completed_results,
            synthesis=synthesis,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            estimated_cost_usd=request.estimated_cost_usd,
            cost_estimate_status=request.cost_estimate_status,
            cost_approval_id=request.cost_approval_id,
            cost_reservation_id=request.cost_reservation_id,
            actual_cost_usd=actual_cost_usd,
            actual_cost_status=actual_cost_status,
            actual_total_tokens=(sum(token_values) if token_values else None),
            orchestration=orchestration,
        )

    async def _run_member(
        self,
        *,
        run_id: str,
        request: CouncilRunRequest,
        member: CouncilMemberRequest,
        member_index: int,
        progress: ProgressCallback | None,
        cancel_event: asyncio.Event | None,
        cancellation_check: Callable[[], bool] | None,
    ) -> CouncilMemberResult:
        self._raise_if_cancellation_requested(
            cancel_event=cancel_event,
            cancellation_check=cancellation_check,
        )
        label = member.label or f"{member.role.value} · {member.model}"
        await self._emit_progress(
            progress,
            "member.started",
            {
                "run_id": run_id,
                "member_index": member_index,
                "provider": member.provider,
                "model": member.model,
                "role": member.role.value,
                "label": label,
            },
        )
        self._raise_if_cancellation_requested(
            cancel_event=cancel_event,
            cancellation_check=cancellation_check,
        )
        provider_cancel = ThreadEvent()

        def provider_cancelled() -> bool:
            return provider_cancel.is_set() or (
                cancellation_check is not None
                and cancellation_check()
            )

        try:
            (
                response,
                fallback_used,
                primary_response,
                fallback_exception,
            ) = await self._await_operation(
                self._ask_with_free_fallback(
                    user_prompt=request.question,
                    system_prompt=build_member_system_prompt(
                        mode=request.mode,
                        role=member.role,
                    ),
                    model=member.model,
                    provider=member.provider,
                    mode=f"council:{request.mode}:{member.role.value}",
                    source="council_member",
                    correlation_id=request.correlation_id or run_id,
                    workspace_id=request.workspace_id,
                    actor_id=request.actor_id or "council",
                    timeout_seconds=request.member_timeout_seconds,
                    cancellation_check=(
                        provider_cancelled
                        if cancellation_check is not None
                        else None
                    ),
                ),
                timeout_seconds=request.member_timeout_seconds,
                cancel_event=cancel_event,
                on_abort=provider_cancel.set,
            )
        except _CouncilCancellationRequested:
            raise
        except ProviderStreamCancelled as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise _CouncilCancellationRequested() from exc
            result = CouncilMemberResult(
                provider=member.provider,
                model=member.model,
                requested_model=member.model,
                role=member.role,
                label=label,
                status="error",
                error_code="COUNCIL_MEMBER_CANCELLED",
                error_message="Поток модели был остановлен.",
            )
        except TimeoutError:
            result = CouncilMemberResult(
                provider=member.provider,
                model=member.model,
                requested_model=member.model,
                role=member.role,
                label=label,
                status="error",
                error_code="COUNCIL_MEMBER_TIMEOUT",
                error_message=(
                    "Модель не завершила ответ за "
                    f"{request.member_timeout_seconds} сек."
                ),
            )
        except Exception as exc:
            result = CouncilMemberResult(
                provider=member.provider,
                model=member.model,
                requested_model=member.model,
                role=member.role,
                label=label,
                status="error",
                error_code="COUNCIL_MEMBER_EXCEPTION",
                error_message=str(exc),
            )
        else:
            if response.status == "success":
                result = CouncilMemberResult(
                    provider=response.provider,
                    model=response.model,
                    requested_model=member.model,
                    role=member.role,
                    label=label,
                    status="success",
                    fallback_used=fallback_used,
                    fallback_model=(
                        FREE_FALLBACK_MODEL if fallback_used else None
                    ),
                    answer=response.content,
                    latency_ms=self._total_latency(
                        primary_response,
                        response,
                    ),
                    usage=CouncilUsage(
                        input_tokens=response.usage.input_tokens,
                        output_tokens=response.usage.output_tokens,
                        total_tokens=response.usage.total_tokens,
                    ),
                    provider_reported_cost_usd=self._reported_cost(
                        primary_response,
                        response,
                    ),
                )
            else:
                error_message = (
                    response.error.message
                    if response.error
                    else "Модель не вернула ответ."
                )
                if fallback_used:
                    primary_code = self._error_code(primary_response)
                    if fallback_exception:
                        error_message = (
                            f"Исходная модель завершилась с ошибкой "
                            f"{primary_code}. Резервный маршрут "
                            f"{FREE_FALLBACK_MODEL} также не сработал: "
                            f"{fallback_exception}"
                        )
                    else:
                        error_message = (
                            f"Исходная модель завершилась с ошибкой "
                            f"{primary_code}. Резервный маршрут "
                            f"{FREE_FALLBACK_MODEL} также не ответил: "
                            f"{error_message}"
                        )
                result = CouncilMemberResult(
                    provider=response.provider,
                    model=response.model,
                    requested_model=member.model,
                    role=member.role,
                    label=label,
                    status="error",
                    fallback_used=fallback_used,
                    fallback_model=(
                        FREE_FALLBACK_MODEL if fallback_used else None
                    ),
                    error_code=(
                        response.error.code
                        if response.error
                        else "COUNCIL_MEMBER_FAILED"
                    ),
                    error_message=error_message,
                    latency_ms=self._total_latency(
                        primary_response,
                        response,
                    ),
                    provider_reported_cost_usd=self._reported_cost(
                        primary_response,
                        response,
                    ),
                )

        await self._event_bus.publish(
            Event(
                event_type=(
                    "council.member.completed"
                    if result.status == "success"
                    else "council.member.failed"
                ),
                source="council",
                correlation_id=request.correlation_id,
                payload={
                    "run_id": run_id,
                    "member_index": member_index,
                    "provider": result.provider,
                    "model": result.model,
                    "requested_model": result.requested_model,
                    "role": result.role.value,
                    "status": result.status,
                    "fallback_used": result.fallback_used,
                    "fallback_model": result.fallback_model,
                    "error_code": result.error_code,
                    "latency_ms": result.latency_ms,
                },
            )
        )
        await self._emit_progress(
            progress,
            (
                "member.completed"
                if result.status == "success"
                else "member.failed"
            ),
            {
                "run_id": run_id,
                "member_index": member_index,
                "member": result.model_dump(mode="json"),
            },
        )
        return result

    async def _orchestrate(
        self,
        *,
        run_id: str,
        request: CouncilRunRequest,
        successful: list[CouncilMemberResult],
        progress: ProgressCallback | None,
        cancel_event: asyncio.Event | None,
        cancellation_check: Callable[[], bool] | None,
        spent_auxiliary: list[CouncilAuxiliaryResult] | None = None,
    ) -> tuple[CouncilSynthesis, CouncilOrchestrationTrace]:
        mode = request.execution_mode
        auxiliary: list[CouncilAuxiliaryResult] = []

        if mode == CouncilExecutionMode.SOLO:
            first = successful[0]
            synthesis = CouncilSynthesis(
                provider=first.provider,
                model=first.model,
                requested_model=first.requested_model,
                status="unstructured",
                fallback_used=first.fallback_used,
                fallback_model=first.fallback_model,
                final_answer=first.answer,
                confidence=None,
            )
            return synthesis, CouncilOrchestrationTrace(
                execution_mode=mode,
                finalizer_stage="solo",
            )

        if mode == CouncilExecutionMode.BEST_OF_N:
            synthesis = await self._synthesize(
                run_id=run_id,
                request=request,
                successful=successful,
                progress=progress,
                cancel_event=cancel_event,
                cancellation_check=cancellation_check,
                system_prompt=BEST_OF_N_SYSTEM_PROMPT,
                source="council_best_of_n",
                progress_prefix="selection",
            )
            return synthesis, CouncilOrchestrationTrace(
                execution_mode=mode,
                finalizer_stage="selection",
            )

        if mode == CouncilExecutionMode.REVIEW:
            draft = await self._synthesize(
                run_id=run_id,
                request=request,
                successful=successful,
                progress=progress,
                cancel_event=cancel_event,
                cancellation_check=cancellation_check,
                source="council_review_draft",
                progress_prefix="draft",
            )
            if spent_auxiliary is not None:
                spent_auxiliary.append(self._aux_from_synthesis(draft, "draft"))
            reviewer = await self._run_auxiliary(
                run_id=run_id,
                request=request,
                stage="reviewer",
                label="Independent Reviewer",
                provider=request.reviewer_provider or successful[0].provider,
                model=request.reviewer_model or successful[0].model,
                user_prompt=self._build_review_prompt(request.question, successful, draft),
                system_prompt=REVIEWER_SYSTEM_PROMPT,
                source="council_reviewer",
                progress=progress,
                cancel_event=cancel_event,
                cancellation_check=cancellation_check,
                spent_collector=spent_auxiliary,
            )
            if reviewer.status != "success":
                return draft, CouncilOrchestrationTrace(
                    execution_mode=mode,
                    finalizer_stage="synthesis",
                    auxiliary_calls=[reviewer],
                )
            auxiliary.extend([self._aux_from_synthesis(draft, "draft"), reviewer])
            synthesis = await self._synthesize(
                run_id=run_id,
                request=request,
                successful=successful,
                progress=progress,
                cancel_event=cancel_event,
                cancellation_check=cancellation_check,
                system_prompt=REVISION_SYSTEM_PROMPT,
                user_prompt=self._build_revision_prompt(
                    request.question, successful, draft, reviewer
                ),
                source="council_review_revision",
                progress_prefix="revision",
            )
            return synthesis, CouncilOrchestrationTrace(
                execution_mode=mode,
                finalizer_stage="revision",
                auxiliary_calls=auxiliary,
            )

        if mode == CouncilExecutionMode.ARBITRATION:
            reviewer = await self._run_auxiliary(
                run_id=run_id,
                request=request,
                stage="reviewer",
                label="Independent Reviewer",
                provider=request.reviewer_provider or successful[0].provider,
                model=request.reviewer_model or successful[0].model,
                user_prompt=self._build_review_prompt(request.question, successful, None),
                system_prompt=REVIEWER_SYSTEM_PROMPT,
                source="council_reviewer",
                progress=progress,
                cancel_event=cancel_event,
                cancellation_check=cancellation_check,
                spent_collector=spent_auxiliary,
            )
            auxiliary.append(reviewer)
            arbiter_prompt = self._build_arbitration_prompt(
                request.question, successful, reviewer
            )
            synthesis = await self._synthesize(
                run_id=run_id,
                request=request,
                successful=successful,
                progress=progress,
                cancel_event=cancel_event,
                cancellation_check=cancellation_check,
                system_prompt=ARBITER_SYSTEM_PROMPT,
                user_prompt=arbiter_prompt,
                provider_override=request.arbiter_provider or successful[0].provider,
                model_override=request.arbiter_model or successful[0].model,
                source="council_arbiter",
                progress_prefix="arbiter",
            )
            return synthesis, CouncilOrchestrationTrace(
                execution_mode=mode,
                finalizer_stage="arbiter",
                auxiliary_calls=auxiliary,
            )

        if mode == CouncilExecutionMode.DELEGATE:
            chair_provider = request.synthesizer_provider or successful[0].provider
            chair_model = request.synthesizer_model or successful[0].model
            planner = await self._run_auxiliary(
                run_id=run_id,
                request=request,
                stage="planner",
                label="PRIMARY / delegation planner",
                provider=chair_provider,
                model=chair_model,
                user_prompt=self._build_delegation_planner_prompt(
                    request.question, successful, request.delegation_max_calls
                ),
                system_prompt=DELEGATION_PLANNER_SYSTEM_PROMPT,
                source="council_delegation_planner",
                progress=progress,
                cancel_event=cancel_event,
                cancellation_check=cancellation_check,
                spent_collector=spent_auxiliary,
            )
            auxiliary.append(planner)
            tasks = self._parse_delegation_tasks(
                planner.content if planner.status == "success" else "",
                request.delegation_max_calls,
            )
            delegates = await self._run_delegates(
                run_id=run_id,
                request=request,
                tasks=tasks,
                progress=progress,
                cancel_event=cancel_event,
                cancellation_check=cancellation_check,
                spent_collector=spent_auxiliary,
            )
            auxiliary.extend(delegates)
            synthesis = await self._synthesize(
                run_id=run_id,
                request=request,
                successful=successful,
                progress=progress,
                cancel_event=cancel_event,
                cancellation_check=cancellation_check,
                user_prompt=self._build_delegate_synthesis_prompt(
                    request.question, successful, delegates
                ),
                source="council_delegate_synthesis",
                progress_prefix="synthesis",
            )
            return synthesis, CouncilOrchestrationTrace(
                execution_mode=mode,
                finalizer_stage="synthesis",
                auxiliary_calls=auxiliary,
                delegation_depth=1 if tasks else 0,
                delegation_calls=len(delegates),
            )

        synthesis = await self._synthesize(
            run_id=run_id,
            request=request,
            successful=successful,
            progress=progress,
            cancel_event=cancel_event,
            cancellation_check=cancellation_check,
        )
        return synthesis, CouncilOrchestrationTrace(
            execution_mode=mode,
            finalizer_stage="synthesis",
        )

    async def _run_auxiliary(
        self,
        *,
        run_id: str,
        request: CouncilRunRequest,
        stage: str,
        label: str,
        provider: str,
        model: str,
        user_prompt: str,
        system_prompt: str,
        source: str,
        progress: ProgressCallback | None,
        cancel_event: asyncio.Event | None,
        cancellation_check: Callable[[], bool] | None,
        ordinal: int | None = None,
        metadata: dict[str, Any] | None = None,
        spent_collector: list[CouncilAuxiliaryResult] | None = None,
    ) -> CouncilAuxiliaryResult:
        self._raise_if_cancellation_requested(
            cancel_event=cancel_event,
            cancellation_check=cancellation_check,
        )
        await self._emit_progress(
            progress,
            f"{stage}.started",
            {
                "run_id": run_id,
                "stage": stage,
                "ordinal": ordinal,
                "provider": provider,
                "model": model,
                "label": label,
            },
        )
        self._raise_if_cancellation_requested(
            cancel_event=cancel_event,
            cancellation_check=cancellation_check,
        )
        provider_cancel = ThreadEvent()

        def provider_cancelled() -> bool:
            return provider_cancel.is_set() or (
                cancellation_check is not None and cancellation_check()
            )

        try:
            response, fallback_used, primary_response, fallback_exception = await self._await_operation(
                self._ask_with_free_fallback(
                    user_prompt=user_prompt,
                    system_prompt=system_prompt,
                    model=model,
                    provider=provider,
                    mode=f"{source}:{request.mode}",
                    source=source,
                    correlation_id=request.correlation_id or run_id,
                    workspace_id=request.workspace_id,
                    actor_id=request.actor_id or "council",
                    timeout_seconds=request.member_timeout_seconds,
                    cancellation_check=(provider_cancelled if cancellation_check is not None else None),
                ),
                timeout_seconds=request.member_timeout_seconds,
                cancel_event=cancel_event,
                on_abort=provider_cancel.set,
            )
        except _CouncilCancellationRequested:
            raise
        except TimeoutError:
            result = CouncilAuxiliaryResult(
                stage=stage,
                ordinal=ordinal,
                provider=provider,
                model=model,
                requested_model=model,
                label=label,
                status="error",
                error_code="COUNCIL_STAGE_TIMEOUT",
                error_message=f"Этап {stage} превысил тайм-аут.",
                metadata=dict(metadata or {}),
            )
        except Exception as exc:
            result = CouncilAuxiliaryResult(
                stage=stage,
                ordinal=ordinal,
                provider=provider,
                model=model,
                requested_model=model,
                label=label,
                status="error",
                error_code="COUNCIL_STAGE_EXCEPTION",
                error_message=str(exc),
                metadata=dict(metadata or {}),
            )
        else:
            if response.status == "success":
                result = CouncilAuxiliaryResult(
                    stage=stage,
                    ordinal=ordinal,
                    provider=response.provider,
                    model=response.model,
                    requested_model=model,
                    label=label,
                    status="success",
                    content=response.content,
                    latency_ms=self._total_latency(primary_response, response),
                    usage=CouncilUsage(
                        input_tokens=response.usage.input_tokens,
                        output_tokens=response.usage.output_tokens,
                        total_tokens=response.usage.total_tokens,
                    ),
                    provider_reported_cost_usd=self._reported_cost(primary_response, response),
                    metadata={
                        **dict(metadata or {}),
                        "fallback_used": fallback_used,
                    },
                )
            else:
                result = CouncilAuxiliaryResult(
                    stage=stage,
                    ordinal=ordinal,
                    provider=response.provider,
                    model=response.model,
                    requested_model=model,
                    label=label,
                    status="error",
                    error_code=self._error_code(response),
                    error_message=(
                        response.error.message if response.error else fallback_exception or "Этап не вернул результат."
                    ),
                    latency_ms=self._total_latency(primary_response, response),
                    usage=CouncilUsage(
                        input_tokens=response.usage.input_tokens,
                        output_tokens=response.usage.output_tokens,
                        total_tokens=response.usage.total_tokens,
                    ),
                    provider_reported_cost_usd=self._reported_cost(primary_response, response),
                    metadata={
                        **dict(metadata or {}),
                        "fallback_used": fallback_used,
                    },
                )
        if spent_collector is not None:
            spent_collector.append(result)
        await self._emit_progress(
            progress,
            f"{stage}.completed" if result.status == "success" else f"{stage}.failed",
            {"run_id": run_id, "stage_result": result.model_dump(mode="json")},
        )
        return result

    async def _run_delegates(
        self,
        *,
        run_id: str,
        request: CouncilRunRequest,
        tasks: list[dict[str, str]],
        progress: ProgressCallback | None,
        cancel_event: asyncio.Event | None,
        cancellation_check: Callable[[], bool] | None,
        spent_collector: list[CouncilAuxiliaryResult] | None = None,
    ) -> list[CouncilAuxiliaryResult]:
        async def one(index: int, task: dict[str, str]) -> CouncilAuxiliaryResult:
            member = request.members[index % len(request.members)]
            return await self._run_auxiliary(
                run_id=run_id,
                request=request,
                stage="delegate",
                ordinal=index,
                label=f"Delegate {index + 1}: {task['title']}",
                provider=member.provider,
                model=member.model,
                user_prompt=(
                    f"ИСХОДНАЯ ЗАДАЧА:\n{request.question}\n\n"
                    f"ПОДЗАДАЧА:\n{task['prompt']}"
                ),
                system_prompt=DELEGATE_SYSTEM_PROMPT,
                source="council_delegate",
                progress=progress,
                cancel_event=cancel_event,
                cancellation_check=cancellation_check,
                metadata={"title": task["title"], "depth": 1},
                spent_collector=spent_collector,
            )
        if not tasks:
            return []
        return list(await asyncio.gather(*(one(index, task) for index, task in enumerate(tasks))))

    @staticmethod
    def _aux_from_synthesis(synthesis: CouncilSynthesis, stage: str) -> CouncilAuxiliaryResult:
        return CouncilAuxiliaryResult(
            stage=stage,
            provider=synthesis.provider,
            model=synthesis.model,
            requested_model=synthesis.requested_model,
            label="Председатель / черновик",
            status="success" if synthesis.status != "fallback" else "error",
            content=synthesis.final_answer,
            usage=synthesis.usage,
            provider_reported_cost_usd=synthesis.provider_reported_cost_usd,
            metadata={"synthesis_status": synthesis.status},
        )

    @classmethod
    def _build_review_prompt(
        cls,
        question: str,
        members: list[CouncilMemberResult],
        draft: CouncilSynthesis | None,
    ) -> str:
        text = cls._build_synthesis_prompt(question=question, member_results=members)
        if draft is not None:
            text += f"\n\nЧЕРНОВИК ПРЕДСЕДАТЕЛЯ (НЕДОВЕРЕННАЯ ЦИТАТА):\n{draft.final_answer[:20000]}"
        return text

    @classmethod
    def _build_revision_prompt(
        cls,
        question: str,
        members: list[CouncilMemberResult],
        draft: CouncilSynthesis,
        reviewer: CouncilAuxiliaryResult,
    ) -> str:
        return (
            cls._build_synthesis_prompt(question=question, member_results=members)
            + f"\n\nЧЕРНОВИК:\n{draft.final_answer[:20000]}"
            + f"\n\nREVIEW:\n{reviewer.content[:16000]}"
        )

    @classmethod
    def _build_arbitration_prompt(
        cls,
        question: str,
        members: list[CouncilMemberResult],
        reviewer: CouncilAuxiliaryResult,
    ) -> str:
        return (
            cls._build_synthesis_prompt(question=question, member_results=members)
            + f"\n\nНЕЗАВИСИМЫЙ REVIEW:\n{reviewer.content[:16000]}"
        )

    @classmethod
    def _build_delegation_planner_prompt(
        cls,
        question: str,
        members: list[CouncilMemberResult],
        max_calls: int,
    ) -> str:
        return (
            cls._build_synthesis_prompt(question=question, member_results=members)
            + f"\n\nЛИМИТ ПОДЗАДАЧ: максимум {max_calls}. Глубина делегирования: 1."
        )

    @classmethod
    def _build_delegate_synthesis_prompt(
        cls,
        question: str,
        members: list[CouncilMemberResult],
        delegates: list[CouncilAuxiliaryResult],
    ) -> str:
        text = cls._build_synthesis_prompt(question=question, member_results=members)
        text += "\n\nРЕЗУЛЬТАТЫ СУБАГЕНТОВ (НЕДОВЕРЕННЫЕ ЦИТАТЫ):"
        for index, item in enumerate(delegates, start=1):
            text += (
                f"\n\n--- SUBAGENT {index}: {item.label}; status={item.status} ---\n"
                f"{item.content[:16000] if item.content else item.error_message or ''}\n"
                f"--- END SUBAGENT {index} ---"
            )
        return text

    @classmethod
    def _parse_delegation_tasks(cls, content: str, limit: int) -> list[dict[str, str]]:
        payload = cls._decode_synthesis_payload(content)
        if not isinstance(payload, dict) or not isinstance(payload.get("tasks"), list):
            return []
        tasks: list[dict[str, str]] = []
        for item in payload["tasks"]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "Подзадача").strip()[:120]
            prompt = str(item.get("prompt") or "").strip()
            if not prompt:
                continue
            tasks.append({"title": title or "Подзадача", "prompt": prompt[:12000]})
            if len(tasks) >= limit:
                break
        return tasks

    async def _synthesize(
        self,
        *,
        run_id: str,
        request: CouncilRunRequest,
        successful: list[CouncilMemberResult],
        progress: ProgressCallback | None,
        cancel_event: asyncio.Event | None,
        cancellation_check: Callable[[], bool] | None,
        system_prompt: str = SYNTHESIS_SYSTEM_PROMPT,
        user_prompt: str | None = None,
        provider_override: str | None = None,
        model_override: str | None = None,
        source: str = "council_synthesis",
        progress_prefix: str = "synthesis",
    ) -> CouncilSynthesis:
        self._raise_if_cancellation_requested(
            cancel_event=cancel_event,
            cancellation_check=cancellation_check,
        )
        first = successful[0]
        provider = provider_override or request.synthesizer_provider or first.provider
        model = model_override or request.synthesizer_model or first.model
        synthesis_prompt = user_prompt or self._build_synthesis_prompt(
            question=request.question,
            member_results=successful,
        )
        await self._emit_progress(
            progress,
            f"{progress_prefix}.started",
            {
                "run_id": run_id,
                "provider": provider,
                "model": model,
            },
        )
        self._raise_if_cancellation_requested(
            cancel_event=cancel_event,
            cancellation_check=cancellation_check,
        )
        provider_cancel = ThreadEvent()

        def provider_cancelled() -> bool:
            return provider_cancel.is_set() or (
                cancellation_check is not None
                and cancellation_check()
            )

        async def stream_delta(delta: str) -> None:
            await self._emit_progress(
                progress,
                f"{progress_prefix}.delta",
                {"run_id": run_id, "delta": delta},
            )

        try:
            (
                response,
                fallback_used,
                primary_response,
                _fallback_exception,
            ) = await self._await_operation(
                self._ask_with_free_fallback(
                    user_prompt=synthesis_prompt,
                    system_prompt=system_prompt,
                    model=model,
                    provider=provider,
                    mode=f"{source}:{request.mode}",
                    source=source,
                    correlation_id=request.correlation_id or run_id,
                    workspace_id=request.workspace_id,
                    actor_id=request.actor_id or "council",
                    timeout_seconds=request.member_timeout_seconds,
                    on_delta=(
                        stream_delta
                        if progress is not None
                        else None
                    ),
                    cancellation_check=(
                        provider_cancelled
                        if cancellation_check is not None
                        else None
                    ),
                ),
                timeout_seconds=request.member_timeout_seconds,
                cancel_event=cancel_event,
                on_abort=provider_cancel.set,
            )
        except _CouncilCancellationRequested:
            raise
        except ProviderStreamCancelled as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise _CouncilCancellationRequested() from exc
            response = None
            fallback_used = False
        except Exception:
            response = None
            fallback_used = False

        if response is None or response.status != "success":
            synthesis = CouncilSynthesis(
                provider=provider,
                model=model,
                requested_model=model,
                status="fallback",
                fallback_used=fallback_used,
                fallback_model=(
                    FREE_FALLBACK_MODEL if fallback_used else None
                ),
                final_answer=first.answer,
                disagreements=[
                    "Итоговый синтез не выполнен; показан первый успешный ответ."
                ],
                confidence=None,
            )
        else:
            parsed = self._parse_synthesis(response.content)
            if parsed is None:
                synthesis = CouncilSynthesis(
                    provider=response.provider,
                    model=response.model,
                    requested_model=model,
                    status="unstructured",
                    fallback_used=fallback_used,
                    fallback_model=(
                        FREE_FALLBACK_MODEL if fallback_used else None
                    ),
                    final_answer=(
                        self._extract_final_answer(response.content)
                        or response.content.strip()
                        or first.answer
                    ),
                    confidence=None,
                    usage=CouncilUsage(
                        input_tokens=response.usage.input_tokens,
                        output_tokens=response.usage.output_tokens,
                        total_tokens=response.usage.total_tokens,
                    ),
                    provider_reported_cost_usd=self._reported_cost(
                        primary_response,
                        response,
                    ),
                )
            else:
                synthesis = CouncilSynthesis(
                    provider=response.provider,
                    model=response.model,
                    requested_model=model,
                    status="structured",
                    fallback_used=fallback_used,
                    fallback_model=(
                        FREE_FALLBACK_MODEL if fallback_used else None
                    ),
                    usage=CouncilUsage(
                        input_tokens=response.usage.input_tokens,
                        output_tokens=response.usage.output_tokens,
                        total_tokens=response.usage.total_tokens,
                    ),
                    provider_reported_cost_usd=self._reported_cost(
                        primary_response,
                        response,
                    ),
                    **parsed,
                )

        await self._emit_progress(
            progress,
            f"{progress_prefix}.completed",
            {
                "run_id": run_id,
                "synthesis": synthesis.model_dump(mode="json"),
            },
        )
        return synthesis

    async def _ask_with_free_fallback(
        self,
        *,
        user_prompt: str,
        system_prompt: str,
        model: str,
        provider: str,
        mode: str,
        source: str,
        correlation_id: str,
        workspace_id: str | None,
        actor_id: str,
        timeout_seconds: int,
        on_delta: Callable[
            [str],
            Awaitable[None] | None,
        ]
        | None = None,
        cancellation_check: Callable[[], bool] | None = None,
    ) -> tuple[
        GatewayResponse,
        bool,
        GatewayResponse | None,
        str | None,
    ]:
        primary = await self._gateway_request(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            model=model,
            provider=provider,
            mode=mode,
            source=source,
            correlation_id=correlation_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            timeout_seconds=timeout_seconds,
            on_delta=on_delta,
            cancellation_check=cancellation_check,
        )
        if not self._should_use_free_fallback(
            provider=provider,
            model=model,
            response=primary,
        ):
            return primary, False, None, None

        try:
            fallback = await self._gateway_request(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                model=FREE_FALLBACK_MODEL,
                provider=provider,
                mode=mode,
                source=f"{source}_free_fallback",
                correlation_id=correlation_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                timeout_seconds=timeout_seconds,
                on_delta=on_delta,
                cancellation_check=cancellation_check,
            )
        except ProviderStreamCancelled:
            raise
        except Exception as exc:
            return primary, True, primary, str(exc)
        return fallback, True, primary, None

    async def _gateway_request(
        self,
        *,
        user_prompt: str,
        system_prompt: str,
        model: str,
        provider: str,
        mode: str,
        source: str,
        correlation_id: str,
        workspace_id: str | None,
        actor_id: str,
        timeout_seconds: int,
        on_delta: Callable[
            [str],
            Awaitable[None] | None,
        ]
        | None,
        cancellation_check: Callable[[], bool] | None,
    ) -> GatewayResponse:
        if on_delta is not None or cancellation_check is not None:
            return await self._gateway.ask_stream(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                on_delta=on_delta or self._ignore_delta,
                model=model,
                provider=provider,
                mode=mode,
                source=source,
                correlation_id=correlation_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                timeout_seconds=timeout_seconds,
                cancellation_check=cancellation_check,
            )
        return await self._gateway.ask(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            model=model,
            provider=provider,
            mode=mode,
            source=source,
            correlation_id=correlation_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            timeout_seconds=timeout_seconds,
        )

    async def _cancelled_error(
        self,
        *,
        request: CouncilRunRequest,
        run_id: str,
        started_at: datetime,
        started: float,
        member_results: list[CouncilMemberResult | None],
        auxiliary_results: list[CouncilAuxiliaryResult] | None = None,
    ) -> CouncilCancelledError:
        completed = self._cancelled_member_results(
            request=request,
            member_results=member_results,
        )
        finished_at = datetime.now(timezone.utc)
        duration_ms = round((perf_counter() - started) * 1000, 2)
        await self._event_bus.publish(
            Event(
                event_type="council.run.cancelled",
                source="council",
                correlation_id=request.correlation_id,
                payload={
                    "run_id": run_id,
                    "completed_members": sum(
                        result.error_code
                        != "COUNCIL_MEMBER_CANCELLED"
                        for result in completed
                    ),
                    "duration_ms": duration_ms,
                },
            )
        )
        return CouncilCancelledError(
            "Запуск Совета отменён пользователем.",
            run_id=run_id,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            member_results=completed,
            auxiliary_results=auxiliary_results,
        )

    @staticmethod
    def _cancelled_member_results(
        *,
        request: CouncilRunRequest,
        member_results: list[CouncilMemberResult | None],
    ) -> list[CouncilMemberResult]:
        completed: list[CouncilMemberResult] = []
        for index, member in enumerate(request.members):
            result = member_results[index]
            if result is not None:
                completed.append(result)
                continue
            completed.append(
                CouncilMemberResult(
                    provider=member.provider,
                    model=member.model,
                    requested_model=member.model,
                    role=member.role,
                    label=(
                        member.label
                        or f"{member.role.value} · {member.model}"
                    ),
                    status="error",
                    error_code="COUNCIL_MEMBER_CANCELLED",
                    error_message="Ответ не получен: запуск был отменён.",
                )
            )
        return completed

    @staticmethod
    def _is_cancellation_requested(
        *,
        cancel_event: asyncio.Event | None,
        cancellation_check: Callable[[], bool] | None,
    ) -> bool:
        return (
            cancel_event is not None and cancel_event.is_set()
        ) or (
            cancellation_check is not None and cancellation_check()
        )

    @classmethod
    def _raise_if_cancellation_requested(
        cls,
        *,
        cancel_event: asyncio.Event | None,
        cancellation_check: Callable[[], bool] | None,
    ) -> None:
        if cls._is_cancellation_requested(
            cancel_event=cancel_event,
            cancellation_check=cancellation_check,
        ):
            raise _CouncilCancellationRequested()

    @staticmethod
    async def _await_operation(
        operation: Awaitable[Any],
        *,
        timeout_seconds: int,
        cancel_event: asyncio.Event | None,
        on_abort: Callable[[], None] | None = None,
    ) -> Any:
        operation_task = asyncio.create_task(operation)
        cancel_task = (
            asyncio.create_task(cancel_event.wait())
            if cancel_event is not None
            else None
        )
        waiting = {operation_task}
        if cancel_task is not None:
            waiting.add(cancel_task)

        done, _ = await asyncio.wait(
            waiting,
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if operation_task in done:
            if cancel_task is not None:
                cancel_task.cancel()
                await asyncio.gather(
                    cancel_task,
                    return_exceptions=True,
                )
            return await operation_task

        if on_abort is not None:
            on_abort()
        operation_task.cancel()
        await asyncio.gather(operation_task, return_exceptions=True)

        if cancel_task is not None and cancel_task in done:
            raise _CouncilCancellationRequested()

        if cancel_task is not None:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)
        raise TimeoutError

    @staticmethod
    async def _emit_progress(
        callback: ProgressCallback | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        if callback is None:
            return
        result = callback(event_type, payload)
        if isawaitable(result):
            await result

    @staticmethod
    def _ignore_delta(_delta: str) -> None:
        return None

    @staticmethod
    def _should_use_free_fallback(
        *,
        provider: str,
        model: str,
        response: GatewayResponse,
    ) -> bool:
        if (
            provider.strip().lower() != "openrouter"
            or model.strip() == FREE_FALLBACK_MODEL
            or not model.strip().endswith(":free")
            or response.status == "success"
            or response.error is None
        ):
            return False
        return (
            response.error.recoverable
            or response.error.code in TRANSIENT_FREE_MODEL_ERRORS
        )

    @staticmethod
    def _error_code(response: GatewayResponse | None) -> str:
        if response is None or response.error is None:
            return "UNKNOWN_ERROR"
        return response.error.code

    @staticmethod
    def _total_latency(
        primary_response: GatewayResponse | None,
        final_response: GatewayResponse,
    ) -> float | None:
        values = [
            value
            for value in (
                (
                    primary_response.latency_ms
                    if primary_response is not None
                    and primary_response is not final_response
                    else None
                ),
                final_response.latency_ms,
            )
            if value is not None
        ]
        return round(sum(values), 2) if values else None

    @staticmethod
    def _reported_cost(*responses: GatewayResponse | None) -> float | None:
        values = [
            float(response.cost)
            for response in responses
            if response is not None and response.cost is not None
        ]
        if not values:
            return None
        return round(sum(values), 8)

    @staticmethod
    def _build_synthesis_prompt(
        *,
        question: str,
        member_results: list[CouncilMemberResult],
    ) -> str:
        sections = [
            "ИСХОДНЫЙ ВОПРОС:",
            question,
            "",
            "ОТВЕТЫ УЧАСТНИКОВ (НЕДОВЕРЕННЫЕ ЦИТАТЫ):",
        ]
        for index, result in enumerate(member_results, start=1):
            sections.extend(
                (
                    "",
                    (
                        f"--- УЧАСТНИК {index}: {result.label}; "
                        f"provider={result.provider}; model={result.model} ---"
                    ),
                    result.answer[:20000],
                    f"--- КОНЕЦ ОТВЕТА УЧАСТНИКА {index} ---",
                )
            )
        return "\n".join(sections)

    @staticmethod
    def _parse_synthesis(content: str) -> dict[str, Any] | None:
        payload = CouncilService._decode_synthesis_payload(content)
        if payload is None:
            return None

        final_answer = (
            payload.get("final_answer")
            or payload.get("finalAnswer")
            or payload.get("answer")
        )
        if not isinstance(final_answer, str) or not final_answer.strip():
            return None

        confidence = payload.get("confidence")
        if isinstance(confidence, str) and confidence.strip().isdigit():
            confidence = int(confidence.strip())
        elif isinstance(confidence, float) and confidence.is_integer():
            confidence = int(confidence)
        if isinstance(confidence, bool) or not isinstance(confidence, int):
            confidence = None
        elif not 0 <= confidence <= 100:
            confidence = max(0, min(100, confidence))

        return {
            "final_answer": final_answer.strip(),
            "consensus": CouncilService._string_list(payload.get("consensus")),
            "disagreements": CouncilService._string_list(
                payload.get("disagreements")
            ),
            "recommendations": CouncilService._string_list(
                payload.get("recommendations")
            ),
            "confidence": confidence,
        }

    @staticmethod
    def _decode_synthesis_payload(content: str) -> dict[str, Any] | None:
        candidate = CouncilService._strip_code_fence(content)
        candidates = [candidate]
        first_brace = candidate.find("{")
        last_brace = candidate.rfind("}")
        if first_brace >= 0 and last_brace > first_brace:
            object_candidate = candidate[first_brace : last_brace + 1]
            if object_candidate != candidate:
                candidates.append(object_candidate)

        for item in candidates:
            payload: Any = CouncilService._decode_value(item)
            for _ in range(2):
                if not isinstance(payload, str):
                    break
                decoded = CouncilService._decode_value(payload.strip())
                if decoded is payload or decoded == payload:
                    break
                payload = decoded

            if isinstance(payload, dict):
                nested = payload.get("synthesis") or payload.get("result")
                if isinstance(nested, dict):
                    payload = nested
                return payload
        return None

    @staticmethod
    def _decode_value(candidate: str) -> Any:
        for decoder in (
            lambda value: json.loads(value),
            lambda value: json.loads(value, strict=False),
            ast.literal_eval,
        ):
            try:
                return decoder(candidate)
            except (json.JSONDecodeError, SyntaxError, TypeError, ValueError):
                continue
        return candidate

    @staticmethod
    def _strip_code_fence(content: str) -> str:
        candidate = content.strip()
        if not candidate.startswith("```"):
            return candidate
        lines = candidate.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

    @staticmethod
    def _extract_final_answer(content: str) -> str | None:
        match = re.search(
            r"""["'](?:final_answer|finalAnswer|answer)["']\s*:\s*""",
            content,
        )
        if match is None:
            return None

        remainder = content[match.end() :].lstrip()
        if not remainder:
            return None
        try:
            value, _ = json.JSONDecoder(strict=False).raw_decode(remainder)
        except (json.JSONDecodeError, TypeError):
            value = CouncilService._extract_python_string(remainder)
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _extract_python_string(content: str) -> str | None:
        if not content or content[0] not in {"'", '"'}:
            return None
        quote = content[0]
        escaped = False
        for index, character in enumerate(content[1:], start=1):
            if escaped:
                escaped = False
                continue
            if character == "\\":
                escaped = True
                continue
            if character != quote:
                continue
            try:
                value = ast.literal_eval(content[: index + 1])
            except (SyntaxError, ValueError):
                return None
            return value if isinstance(value, str) else None
        return None

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, str):
            item = value.strip()
            return [item] if item else []
        if not isinstance(value, (list, tuple)):
            return []
        return [
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip()
        ]
