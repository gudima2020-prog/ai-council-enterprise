from __future__ import annotations

from backend.core.config import AppSettings
from backend.core.events import Event, EventBus
from backend.core.logging import LoggerManager
from backend.gateway.providers.base import ProviderAdapter
from backend.gateway.schemas import (
    GatewayMessage,
    GatewayRequest,
    GatewayResponse,
)


class AIGateway:
    def __init__(
        self,
        *,
        settings: AppSettings,
        event_bus: EventBus,
        providers: dict[str, ProviderAdapter],
    ) -> None:
        self._settings = settings
        self._event_bus = event_bus
        self._providers = providers
        self._logger = LoggerManager.get_logger("ai")

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
    ) -> GatewayResponse:
        selected_provider = provider or self._settings.default_provider
        selected_model = model or self._settings.default_model

        request = GatewayRequest(
            messages=[
                GatewayMessage(role="system", content=system_prompt),
                GatewayMessage(role="user", content=user_prompt),
            ],
            model=selected_model,
            provider=selected_provider,
            temperature=self._settings.temperature,
            max_tokens=self._settings.max_tokens,
            timeout_seconds=self._settings.request_timeout_seconds,
            source=source,
            mode=mode,
            correlation_id=correlation_id,
        )

        await self._event_bus.publish(
            Event(
                event_type="ai.request.created",
                source="ai_gateway",
                correlation_id=correlation_id,
                payload={
                    "request_id": request.request_id,
                    "provider": selected_provider,
                    "model": selected_model,
                    "source": source,
                    "mode": mode,
                },
            )
        )

        adapter = self._providers.get(selected_provider)
        if adapter is None:
            raise RuntimeError(
                f"AI provider is not registered: {selected_provider}"
            )

        self._logger.info(
            "AI request started request_id=%s provider=%s model=%s source=%s",
            request.request_id,
            selected_provider,
            selected_model,
            source,
        )

        response = adapter.complete(request)

        if response.status == "success":
            self._logger.info(
                "AI request completed request_id=%s provider=%s model=%s latency_ms=%s",
                response.request_id,
                response.provider,
                response.model,
                response.latency_ms,
            )
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
                    },
                )
            )
        else:
            self._logger.warning(
                "AI request failed request_id=%s provider=%s model=%s code=%s",
                response.request_id,
                response.provider,
                response.model,
                response.error.code if response.error else "UNKNOWN_ERROR",
            )
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
                    },
                )
            )

        return response
