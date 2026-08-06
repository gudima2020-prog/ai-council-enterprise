from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from inspect import isawaitable

from backend.core.config import AppSettings
from backend.core.events import Event, EventBus
from backend.core.logging import LoggerManager
from backend.gateway.approvals import (
    GatewayApprovalCoordinator,
    GatewayApprovalOutcome,
)
from backend.gateway.health import ProviderHealthService, TransientProviderHealthService
from backend.gateway.policy import GatewayRoutePolicy
from backend.gateway.providers.base import ProviderAdapter, ProviderStreamCancelled
from backend.gateway.schemas import (
    GatewayError,
    GatewayMessage,
    GatewayRequest,
    GatewayResponse,
)
from backend.runtime_policy import (
    PolicyAction,
    RuntimePolicyDecision,
)
from backend.secrets.injection import SecretLeaseAccessor, build_secret_accessor
from backend.secrets.schemas import SecretResolveContext
from backend.secrets.service import SecretManagerService

ProviderFactory = Callable[[str], ProviderAdapter]
StreamDeltaHandler = Callable[[str], Awaitable[None] | None]
Route = tuple[str, str]

FAILOVER_ERROR_CODES = {
    "RATE_LIMIT",
    "TIMEOUT",
    "NETWORK_ERROR",
    "SERVER_ERROR",
    "MODEL_UNAVAILABLE",
    "INSUFFICIENT_CREDITS",
}


class AIGateway:
    def __init__(
        self,
        *,
        settings: AppSettings,
        event_bus: EventBus,
        providers: dict[str, ProviderAdapter],
        secret_manager: SecretManagerService | None = None,
        provider_secret_refs: dict[str, str] | None = None,
        provider_factories: dict[str, ProviderFactory] | None = None,
        fallback_routes: dict[str, list[Route]] | None = None,
        health_service: ProviderHealthService | None = None,
        route_policy: GatewayRoutePolicy | None = None,
        approval_coordinator: GatewayApprovalCoordinator | None = None,
    ) -> None:
        self._settings = settings
        self._event_bus = event_bus
        self._providers = providers
        self._secret_manager = secret_manager
        self._provider_secret_refs = dict(provider_secret_refs or {})
        self._provider_factories = dict(provider_factories or {})
        self._fallback_routes = {
            key: list(value) for key, value in (fallback_routes or {}).items()
        }
        self._health = health_service or TransientProviderHealthService()
        self._route_policy = route_policy or GatewayRoutePolicy(
            event_bus=event_bus,
        )
        self._approval_coordinator = approval_coordinator
        self._logger = LoggerManager.get_logger("ai")

    def configured_providers(self) -> list[str]:
        return sorted(
            set(self._providers)
            | set(self._provider_secret_refs)
            | set(self._provider_factories)
        )

    def _candidate_routes(self, provider: str, model: str) -> list[Route]:
        routes: list[Route] = [(provider, model)]
        for candidate in self._fallback_routes.get(provider, []):
            normalized = (str(candidate[0]).strip(), str(candidate[1]).strip())
            if normalized[0] and normalized[1] and normalized not in routes:
                routes.append(normalized)
        return routes

    async def _adapter_for(
        self,
        *,
        provider: str,
        request: GatewayRequest,
        actor_id: str | None,
        workspace_id: str | None,
        correlation_id: str | None,
    ) -> tuple[ProviderAdapter | None, SecretLeaseAccessor | None]:
        adapter = self._providers.get(provider)
        if adapter is not None:
            return adapter, None

        secret_ref = self._provider_secret_refs.get(provider)
        factory = self._provider_factories.get(provider)
        if not secret_ref or factory is None or self._secret_manager is None:
            return None, None

        accessor = await build_secret_accessor(
            manager=self._secret_manager,
            bindings={"api_key": secret_ref},
            context=SecretResolveContext(
                actor_id=actor_id or request.source or "ai-gateway",
                purpose=f"AI Gateway provider {provider}",
                workspace_id=workspace_id,
                auth_method="runtime",
                source="ai_gateway",
                consumer_type="gateway",
                consumer_key=provider,
                correlation_id=correlation_id,
                metadata={"request_id": request.request_id},
            ),
            ttl_seconds=min(max(5, int(request.timeout_seconds) + 5), 900),
        )
        try:
            api_key = await accessor.get("api_key")
            return factory(api_key), accessor
        except Exception:
            await accessor.close()
            raise

    @staticmethod
    def _redact_response(
        response: GatewayResponse,
        accessor: SecretLeaseAccessor | None,
    ) -> GatewayResponse:
        if accessor is None:
            return response
        safe_error = response.error
        if safe_error is not None:
            safe_error = replace(
                safe_error,
                message=accessor.redact_text(safe_error.message),
            )
        return replace(
            response,
            content=accessor.redact_text(response.content),
            error=safe_error,
        )

    @staticmethod
    def _attach_runtime_policy(
        response: GatewayResponse,
        request: GatewayRequest,
    ) -> GatewayResponse:
        metadata = dict(response.metadata)
        changed = False

        for key in ("runtime_policy", "policy_approval"):
            value = request.metadata.get(key)
            if isinstance(value, dict):
                metadata[key] = dict(value)
                changed = True

        if not changed:
            return response

        return replace(response, metadata=metadata)

    @staticmethod
    def _runtime_policy_decision(
        request: GatewayRequest,
    ) -> RuntimePolicyDecision | None:
        metadata = request.metadata.get("runtime_policy")
        if not isinstance(metadata, dict):
            return None
        try:
            action = PolicyAction(str(metadata["action"]))
            policy_version = str(metadata["policy_version"])
            fingerprint = str(metadata["fingerprint"])
            reason_codes = tuple(
                str(value)
                for value in metadata["reason_codes"]
            )
        except (KeyError, TypeError, ValueError):
            return None

        if not policy_version or not fingerprint:
            return None

        return RuntimePolicyDecision(
            policy_version=policy_version,
            action=action,
            reason_codes=reason_codes,
            fingerprint=fingerprint,
        )

    @staticmethod
    def _approval_response(
        request: GatewayRequest,
        outcome: GatewayApprovalOutcome,
    ) -> GatewayResponse:
        approval_metadata = dict(outcome.metadata)
        metadata: dict[str, object] = {
            "policy_approval": approval_metadata,
        }
        runtime_policy = request.metadata.get("runtime_policy")
        if isinstance(runtime_policy, dict):
            metadata["runtime_policy"] = dict(runtime_policy)

        return GatewayResponse(
            request_id=request.request_id,
            provider=request.provider,
            model=request.model,
            content="",
            status="error",
            error=GatewayError(
                code=(
                    outcome.error_code
                    or "POLICY_APPROVAL_FAILED"
                ),
                message=(
                    outcome.message
                    or "The policy approval could not be verified."
                ),
                provider=request.provider,
                recoverable=False,
                details=dict(metadata),
            ),
            metadata=dict(metadata),
        )

    async def _prepare_route_approval(
        self,
        *,
        request: GatewayRequest,
        policy_rejection: GatewayResponse | None,
        approval_id: str | None,
        approval_token: str | None,
        requested_by: str,
    ) -> tuple[
        bool,
        RuntimePolicyDecision | None,
        GatewayResponse | None,
    ]:
        if policy_rejection is None:
            return False, None, None

        if (
            policy_rejection.error is None
            or policy_rejection.error.code
            != "POLICY_APPROVAL_REQUIRED"
        ):
            return False, None, policy_rejection

        decision = self._runtime_policy_decision(request)
        coordinator = self._approval_coordinator
        if decision is None or coordinator is None:
            return False, None, policy_rejection

        has_id = bool(approval_id)
        has_token = bool(approval_token)

        if not has_id and not has_token:
            outcome = await coordinator.request(
                request=request,
                decision=decision,
                requested_by=requested_by,
            )
            return (
                False,
                None,
                self._approval_response(request, outcome),
            )

        if not has_id or not has_token:
            outcome = await coordinator.consume(
                request=request,
                decision=decision,
                approval_id=approval_id or "",
                token=approval_token or "",
            )
            return (
                False,
                None,
                self._approval_response(request, outcome),
            )

        return True, decision, None

    async def _consume_route_approval(
        self,
        *,
        request: GatewayRequest,
        decision: RuntimePolicyDecision,
        approval_id: str,
        approval_token: str,
    ) -> tuple[GatewayRequest, GatewayResponse | None]:
        coordinator = self._approval_coordinator
        if coordinator is None:
            return (
                request,
                GatewayResponse(
                    request_id=request.request_id,
                    provider=request.provider,
                    model=request.model,
                    content="",
                    status="error",
                    error=GatewayError(
                        code="POLICY_APPROVAL_UNAVAILABLE",
                        message=(
                            "The policy approval service is unavailable."
                        ),
                        provider=request.provider,
                        recoverable=False,
                    ),
                ),
            )

        outcome = await coordinator.consume(
            request=request,
            decision=decision,
            approval_id=approval_id,
            token=approval_token,
        )
        if not outcome.authorized:
            return (
                request,
                self._approval_response(request, outcome),
            )

        return (
            replace(
                request,
                metadata={
                    **request.metadata,
                    "policy_approval": dict(outcome.metadata),
                },
            ),
            None,
        )

    def _circuit_available(self, provider: str) -> bool:
        try:
            return self._health.circuit_available(provider)
        except Exception as exc:
            self._logger.warning(
                "Provider health read failed provider=%s error=%s",
                provider,
                exc.__class__.__name__,
            )
            return True

    def _record_success(self, provider: str, latency_ms: float | None) -> None:
        try:
            self._health.record_success(provider, latency_ms)
        except Exception as exc:
            self._logger.warning(
                "Provider health success write failed provider=%s error=%s",
                provider,
                exc.__class__.__name__,
            )

    def _record_failure(
        self,
        provider: str,
        latency_ms: float | None,
        error_code: str | None,
        *,
        circuit_eligible: bool,
    ) -> None:
        try:
            self._health.record_failure(
                provider,
                latency_ms,
                error_code,
                circuit_eligible=circuit_eligible,
            )
        except Exception as exc:
            self._logger.warning(
                "Provider health failure write failed provider=%s error=%s",
                provider,
                exc.__class__.__name__,
            )

    @staticmethod
    def _failover_eligible(response: GatewayResponse) -> bool:
        return bool(
            response.error and response.error.code in FAILOVER_ERROR_CODES
        )

    async def _publish_result(
        self,
        response: GatewayResponse,
        *,
        correlation_id: str | None,
        stream: bool,
    ) -> None:
        if response.status == "success":
            await self._event_bus.publish(
                Event(
                    event_type="ai.response.received",
                    source="ai_gateway",
                    correlation_id=correlation_id,
                    payload={
                        "request_id": response.request_id,
                        "provider": response.provider,
                        "model": response.model,
                        "latency_ms": response.latency_ms,
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                        "total_tokens": response.usage.total_tokens,
                        "stream": stream,
                        "failover_used": bool(
                            response.metadata.get("failover_used")
                        ),
                    },
                )
            )
        else:
            await self._event_bus.publish(
                Event(
                    event_type="ai.response.failed",
                    source="ai_gateway",
                    correlation_id=correlation_id,
                    payload={
                        "request_id": response.request_id,
                        "provider": response.provider,
                        "model": response.model,
                        "error_code": (
                            response.error.code
                            if response.error
                            else "UNKNOWN_ERROR"
                        ),
                        "stream": stream,
                    },
                )
            )

    async def ask(
        self,
        *,
        user_prompt: str,
        system_prompt: str,
        model: str | None = None,
        provider: str | None = None,
        mode: str = "universal",
        source: str = "unknown",
        correlation_id: str | None = None,
        workspace_id: str | None = None,
        actor_id: str | None = None,
        request_id: str | None = None,
        approval_id: str | None = None,
        approval_token: str | None = None,
        timeout_seconds: int | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        data_classification: str | None = None,
    ) -> GatewayResponse:
        selected_provider = provider or self._settings.default_provider
        selected_model = model or self._settings.default_model
        base_request = GatewayRequest(
            messages=[
                GatewayMessage(role="system", content=system_prompt),
                GatewayMessage(role="user", content=user_prompt),
            ],
            model=selected_model,
            provider=selected_provider,
            temperature=(
                self._settings.temperature
                if temperature is None
                else temperature
            ),
            max_tokens=(
                self._settings.max_tokens
                if max_tokens is None
                else max_tokens
            ),
            timeout_seconds=(
                timeout_seconds
                if timeout_seconds is not None
                else self._settings.request_timeout_seconds
            ),
            source=source,
            mode=mode,
            workspace_id=workspace_id,
            data_classification=data_classification,
            correlation_id=correlation_id,
        )
        if request_id is not None:
            normalized_request_id = request_id.strip()
            if not normalized_request_id:
                raise ValueError("request_id cannot be empty.")
            base_request = replace(
                base_request,
                request_id=normalized_request_id,
            )
        routes = self._candidate_routes(selected_provider, selected_model)
        await self._event_bus.publish(
            Event(
                event_type="ai.request.created",
                source="ai_gateway",
                correlation_id=correlation_id,
                payload={
                    "request_id": base_request.request_id,
                    "provider": selected_provider,
                    "model": selected_model,
                    "source": source,
                    "mode": mode,
                    "candidate_routes": [
                        {"provider": p, "model": m} for p, m in routes
                    ],
                },
            )
        )

        last_response: GatewayResponse | None = None
        for attempt, (route_provider, route_model) in enumerate(routes, start=1):
            request = replace(
                base_request, provider=route_provider, model=route_model
            )
            if not self._circuit_available(route_provider):
                last_response = GatewayResponse(
                    request_id=request.request_id,
                    provider=route_provider,
                    model=route_model,
                    content="",
                    status="error",
                    error=GatewayError(
                        code="CIRCUIT_OPEN",
                        message="Провайдер временно исключён circuit breaker.",
                        provider=route_provider,
                        recoverable=True,
                    ),
                    metadata={"attempt": attempt, "circuit_skipped": True},
                )
                continue

            request, policy_rejection = (
                await self._route_policy.evaluate(request)
            )
            (
                approval_pending,
                approval_decision,
                policy_rejection,
            ) = await self._prepare_route_approval(
                request=request,
                policy_rejection=policy_rejection,
                approval_id=approval_id,
                approval_token=approval_token,
                requested_by=(
                    actor_id
                    or source
                    or "ai-gateway"
                ),
            )

            if policy_rejection is not None:
                last_response = policy_rejection
                break

            adapter: ProviderAdapter | None = None
            accessor: SecretLeaseAccessor | None = None
            try:
                adapter, accessor = await self._adapter_for(
                    provider=route_provider,
                    request=request,
                    actor_id=actor_id,
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                )
                if adapter is None:
                    last_response = GatewayResponse(
                        request_id=request.request_id,
                        provider=route_provider,
                        model=route_model,
                        content="",
                        status="error",
                        error=GatewayError(
                            code="PROVIDER_NOT_CONFIGURED",
                            message=f"AI provider is not configured: {route_provider}",
                            provider=route_provider,
                            recoverable=False,
                        ),
                    )
                    continue

                if approval_pending:
                    if (
                        approval_decision is None
                        or not approval_id
                        or not approval_token
                    ):
                        last_response = GatewayResponse(
                            request_id=request.request_id,
                            provider=request.provider,
                            model=request.model,
                            content="",
                            status="error",
                            error=GatewayError(
                                code="POLICY_APPROVAL_INVALID_STATE",
                                message=(
                                    "The policy approval state is incomplete."
                                ),
                                provider=request.provider,
                                recoverable=False,
                            ),
                        )
                        break
                    (
                        request,
                        approval_rejection,
                    ) = await self._consume_route_approval(
                        request=request,
                        decision=approval_decision,
                        approval_id=approval_id,
                        approval_token=approval_token,
                    )
                    if approval_rejection is not None:
                        last_response = approval_rejection
                        break

                self._logger.info(
                    "AI request attempt request_id=%s attempt=%s provider=%s model=%s",
                    request.request_id,
                    attempt,
                    route_provider,
                    route_model,
                )
                response = await asyncio.to_thread(adapter.complete, request)
                response = self._redact_response(response, accessor)
                response = self._attach_runtime_policy(response, request)
            finally:
                if accessor is not None:
                    await accessor.close()

            if response.status == "success":
                self._record_success(route_provider, response.latency_ms)
                response = replace(
                    response,
                    metadata={
                        **response.metadata,
                        "attempt": attempt,
                        "requested_provider": selected_provider,
                        "requested_model": selected_model,
                        "failover_used": attempt > 1,
                        "route": [
                            {"provider": p, "model": m}
                            for p, m in routes[:attempt]
                        ],
                    },
                )
                await self._publish_result(
                    response, correlation_id=correlation_id, stream=False
                )
                return response

            eligible = self._failover_eligible(response)
            self._record_failure(
                route_provider,
                response.latency_ms,
                response.error.code if response.error else None,
                circuit_eligible=eligible,
            )
            last_response = response
            if not eligible:
                break
            if attempt < len(routes):
                await self._event_bus.publish(
                    Event(
                        event_type="ai.provider.failover",
                        source="ai_gateway",
                        correlation_id=correlation_id,
                        payload={
                            "request_id": request.request_id,
                            "from_provider": route_provider,
                            "from_model": route_model,
                            "error_code": (
                                response.error.code
                                if response.error
                                else "UNKNOWN_ERROR"
                            ),
                            "to_provider": routes[attempt][0],
                            "to_model": routes[attempt][1],
                        },
                    )
                )

        if last_response is None:
            last_response = GatewayResponse(
                request_id=base_request.request_id,
                provider=selected_provider,
                model=selected_model,
                content="",
                status="error",
                error=GatewayError(
                    code="NO_ROUTE",
                    message="Нет доступного маршрута к ИИ-провайдеру.",
                    provider=selected_provider,
                    recoverable=True,
                ),
            )
        await self._publish_result(
            last_response, correlation_id=correlation_id, stream=False
        )
        return last_response

    async def ask_stream(
        self,
        *,
        user_prompt: str,
        system_prompt: str,
        on_delta: StreamDeltaHandler,
        model: str | None = None,
        provider: str | None = None,
        mode: str = "universal",
        source: str = "unknown",
        correlation_id: str | None = None,
        workspace_id: str | None = None,
        actor_id: str | None = None,
        request_id: str | None = None,
        approval_id: str | None = None,
        approval_token: str | None = None,
        timeout_seconds: int | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        data_classification: str | None = None,
        cancellation_check: Callable[[], bool] | None = None,
    ) -> GatewayResponse:
        selected_provider = provider or self._settings.default_provider
        selected_model = model or self._settings.default_model
        base_request = GatewayRequest(
            messages=[
                GatewayMessage(role="system", content=system_prompt),
                GatewayMessage(role="user", content=user_prompt),
            ],
            model=selected_model,
            provider=selected_provider,
            temperature=(
                self._settings.temperature
                if temperature is None
                else temperature
            ),
            max_tokens=(
                self._settings.max_tokens
                if max_tokens is None
                else max_tokens
            ),
            timeout_seconds=(
                timeout_seconds
                if timeout_seconds is not None
                else self._settings.request_timeout_seconds
            ),
            source=source,
            mode=mode,
            workspace_id=workspace_id,
            data_classification=data_classification,
            correlation_id=correlation_id,
        )
        if request_id is not None:
            normalized_request_id = request_id.strip()
            if not normalized_request_id:
                raise ValueError("request_id cannot be empty.")
            base_request = replace(
                base_request,
                request_id=normalized_request_id,
            )
        routes = self._candidate_routes(selected_provider, selected_model)
        await self._event_bus.publish(
            Event(
                event_type="ai.request.created",
                source="ai_gateway",
                correlation_id=correlation_id,
                payload={
                    "request_id": base_request.request_id,
                    "provider": selected_provider,
                    "model": selected_model,
                    "source": source,
                    "mode": mode,
                    "stream": True,
                },
            )
        )
        loop = asyncio.get_running_loop()

        async def deliver(delta: str) -> None:
            result = on_delta(delta)
            if isawaitable(result):
                await result

        def relay(delta: str) -> None:
            future = asyncio.run_coroutine_threadsafe(deliver(delta), loop)
            future.result()

        last_response: GatewayResponse | None = None
        emitted_content = False
        for attempt, (route_provider, route_model) in enumerate(routes, start=1):
            if cancellation_check is not None and cancellation_check():
                raise ProviderStreamCancelled("Provider stream was cancelled.")
            request = replace(
                base_request, provider=route_provider, model=route_model
            )
            if not self._circuit_available(route_provider):
                continue

            request, policy_rejection = (
                await self._route_policy.evaluate(request)
            )
            (
                approval_pending,
                approval_decision,
                policy_rejection,
            ) = await self._prepare_route_approval(
                request=request,
                policy_rejection=policy_rejection,
                approval_id=approval_id,
                approval_token=approval_token,
                requested_by=(
                    actor_id
                    or source
                    or "ai-gateway"
                ),
            )

            if policy_rejection is not None:
                last_response = policy_rejection
                break

            adapter = None
            accessor = None
            attempt_emitted = False

            def guarded_relay(delta: str) -> None:
                nonlocal attempt_emitted, emitted_content
                attempt_emitted = True
                emitted_content = True
                relay(delta)

            try:
                adapter, accessor = await self._adapter_for(
                    provider=route_provider,
                    request=request,
                    actor_id=actor_id,
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                )
                if adapter is None:
                    continue
                if approval_pending:
                    if (
                        approval_decision is None
                        or not approval_id
                        or not approval_token
                    ):
                        last_response = GatewayResponse(
                            request_id=request.request_id,
                            provider=request.provider,
                            model=request.model,
                            content="",
                            status="error",
                            error=GatewayError(
                                code="POLICY_APPROVAL_INVALID_STATE",
                                message=(
                                    "The policy approval state is incomplete."
                                ),
                                provider=request.provider,
                                recoverable=False,
                            ),
                        )
                        break
                    (
                        request,
                        approval_rejection,
                    ) = await self._consume_route_approval(
                        request=request,
                        decision=approval_decision,
                        approval_id=approval_id,
                        approval_token=approval_token,
                    )
                    if approval_rejection is not None:
                        last_response = approval_rejection
                        break
                response = await asyncio.to_thread(
                    adapter.complete_stream,
                    request,
                    guarded_relay,
                    cancellation_check,
                )
                response = self._redact_response(response, accessor)
                response = self._attach_runtime_policy(response, request)
            finally:
                if accessor is not None:
                    await accessor.close()

            if response.status == "success":
                self._record_success(route_provider, response.latency_ms)
                response = replace(
                    response,
                    metadata={
                        **response.metadata,
                        "attempt": attempt,
                        "requested_provider": selected_provider,
                        "requested_model": selected_model,
                        "failover_used": attempt > 1,
                    },
                )
                await self._publish_result(
                    response, correlation_id=correlation_id, stream=True
                )
                return response

            eligible = self._failover_eligible(response)
            self._record_failure(
                route_provider,
                response.latency_ms,
                response.error.code if response.error else None,
                circuit_eligible=eligible,
            )
            last_response = response
            # Once any streamed content reached the caller, switching providers
            # would create a mixed answer. Failover is therefore only safe before
            # the first emitted delta.
            if attempt_emitted or emitted_content or not eligible:
                break

        if last_response is None:
            last_response = GatewayResponse(
                request_id=base_request.request_id,
                provider=selected_provider,
                model=selected_model,
                content="",
                status="error",
                error=GatewayError(
                    code="NO_ROUTE",
                    message="Нет доступного маршрута к ИИ-провайдеру.",
                    provider=selected_provider,
                    recoverable=True,
                ),
            )
        await self._publish_result(
            last_response, correlation_id=correlation_id, stream=True
        )
        return last_response
