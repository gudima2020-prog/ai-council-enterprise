from backend.core.config import AppSettings
from backend.core.events import EventBus
from backend.gateway.providers.openrouter import OpenRouterAdapter
from backend.gateway.service import AIGateway


def build_ai_gateway(
    *,
    settings: AppSettings,
    event_bus: EventBus,
) -> AIGateway:
    providers = {}

    if settings.openrouter_api_key:
        providers["openrouter"] = OpenRouterAdapter(
            api_key=settings.openrouter_api_key
        )

    return AIGateway(
        settings=settings,
        event_bus=event_bus,
        providers=providers,
    )
