from __future__ import annotations

from typing import Any

from backend.gateway.schemas import GatewayError


def normalize_provider_error(
    exc: Exception,
    *,
    provider: str,
) -> GatewayError:
    text = str(exc)
    lowered = text.lower()

    code = "UNKNOWN_ERROR"
    recoverable = False
    message = "Не удалось выполнить запрос к ИИ-провайдеру."

    if "401" in lowered or "invalid api key" in lowered or "unauthorized" in lowered:
        code = "INVALID_API_KEY"
        message = "API-ключ недействителен или не принят провайдером."
    elif "402" in lowered or "insufficient" in lowered or "credits" in lowered:
        code = "INSUFFICIENT_CREDITS"
        message = "Недостаточно средств или лимита для выполнения запроса."
    elif "404" in lowered or "model" in lowered and "unavailable" in lowered:
        code = "MODEL_UNAVAILABLE"
        message = "Выбранная модель недоступна."
    elif "429" in lowered or "rate limit" in lowered or "rate-limited" in lowered:
        code = "RATE_LIMIT"
        message = "Провайдер временно ограничил частоту запросов."
        recoverable = True
    elif "timeout" in lowered or "timed out" in lowered:
        code = "TIMEOUT"
        message = "Провайдер не ответил вовремя."
        recoverable = True
    elif "network" in lowered or "connection" in lowered:
        code = "NETWORK_ERROR"
        message = "Ошибка сетевого соединения с провайдером."
        recoverable = True
    elif "bad request" in lowered or "400" in lowered:
        code = "BAD_REQUEST"
        message = "Провайдер отклонил параметры запроса."

    return GatewayError(
        code=code,
        message=message,
        provider=provider,
        recoverable=recoverable,
        details={"exception_type": exc.__class__.__name__},
    )
