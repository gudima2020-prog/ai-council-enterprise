from fastapi import APIRouter

from backend.core.config import get_settings
from backend.core.events import event_bus
from backend.gateway.factory import build_ai_gateway
from backend.gateway.prompts import get_system_prompt

router = APIRouter(tags=["gateway"])


@router.get("/gateway/status")
def gateway_status() -> dict:
    settings = get_settings()

    return {
        "default_provider": settings.default_provider,
        "default_model": settings.default_model,
        "max_tokens": settings.max_tokens,
        "temperature": settings.temperature,
        "request_timeout_seconds": settings.request_timeout_seconds,
        "providers": {
            "openrouter": {
                "configured": settings.has_openrouter_key,
            }
        },
    }


@router.post("/gateway/test")
async def gateway_test() -> dict:
    settings = get_settings()
    gateway = build_ai_gateway(
        settings=settings,
        event_bus=event_bus,
    )

    response = await gateway.ask(
        user_prompt="Ответь одной фразой: AI Gateway работает.",
        system_prompt=get_system_prompt("universal"),
        source="gateway_test",
    )

    result = {
        "request_id": response.request_id,
        "status": response.status,
        "provider": response.provider,
        "model": response.model,
        "content": response.content,
        "latency_ms": response.latency_ms,
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.total_tokens,
        },
    }

    if response.error:
        result["error"] = {
            "code": response.error.code,
            "message": response.error.message,
            "recoverable": response.error.recoverable,
        }

    return result
