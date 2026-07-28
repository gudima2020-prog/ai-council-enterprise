from __future__ import annotations

import json
import os

from backend.core.config import AppSettings
from backend.core.events import EventBus
from backend.database.session import session_scope
from backend.gateway.health import provider_health
from backend.gateway.providers.openai_compatible import OpenAICompatibleAdapter
from backend.gateway.providers.openrouter import OpenRouterAdapter
from backend.gateway.routing import GatewayRoutingService, RoutingRequirements
from backend.gateway.service import AIGateway
from backend.secrets.service import SecretManagerService


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_failover_routes() -> dict[str, list[tuple[str, str]]]:
    raw = os.getenv("AI_STUDIO_GATEWAY_FAILOVER_JSON", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, list[tuple[str, str]]] = {}
    for source, items in data.items():
        if not isinstance(source, str) or not isinstance(items, list):
            continue
        routes: list[tuple[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            provider = str(item.get("provider", "")).strip()
            model = str(item.get("model", "")).strip()
            if provider and model:
                routes.append((provider, model))
        if routes:
            result[source.strip()] = routes
    return result


def _auto_routes(allowed_providers: set[str]) -> list[tuple[str, str]]:
    strategy = os.getenv("AI_STUDIO_GATEWAY_AUTO_STRATEGY", "balanced").strip()
    if strategy not in {"balanced", "cost", "latency", "reliability", "quality"}:
        strategy = "balanced"
    with session_scope() as session:
        ranked = GatewayRoutingService(
            session=session,
            health_service=provider_health,
        ).recommend(
            strategy=strategy,  # type: ignore[arg-type]
            requirements=RoutingRequirements(),
            limit=3,
            allowed_providers=allowed_providers,
        )
    return [(item["provider"], item["model"]) for item in ranked]


def build_ai_gateway(
    *,
    settings: AppSettings,
    event_bus: EventBus,
    secret_manager: SecretManagerService | None = None,
) -> AIGateway:
    providers = {}
    provider_secret_refs: dict[str, str] = {}
    provider_factories = {}

    openrouter_secret_ref = os.getenv(
        "AI_STUDIO_OPENROUTER_SECRET_REF", ""
    ).strip()
    if openrouter_secret_ref:
        provider_secret_refs["openrouter"] = openrouter_secret_ref
        provider_factories["openrouter"] = (
            lambda api_key: OpenRouterAdapter(api_key=api_key)
        )
        if secret_manager is None:
            secret_manager = SecretManagerService(event_bus=event_bus)
    elif settings.openrouter_api_key:
        providers["openrouter"] = OpenRouterAdapter(
            api_key=settings.openrouter_api_key
        )

    svrtr_secret_ref = os.getenv("AI_STUDIO_SVRTR_SECRET_REF", "").strip()
    svrtr_legacy_key = os.getenv("SVRTR_API_KEY", "").strip()
    svrtr_base_url = os.getenv(
        "AI_STUDIO_SVRTR_BASE_URL", "https://api.svrtr.org/v1"
    ).strip().rstrip("/")

    def svrtr_factory(api_key: str) -> OpenAICompatibleAdapter:
        return OpenAICompatibleAdapter(
            name="svrtr",
            api_key=api_key,
            base_url=svrtr_base_url,
        )

    if svrtr_secret_ref:
        provider_secret_refs["svrtr"] = svrtr_secret_ref
        provider_factories["svrtr"] = svrtr_factory
        if secret_manager is None:
            secret_manager = SecretManagerService(event_bus=event_bus)
    elif svrtr_legacy_key:
        providers["svrtr"] = svrtr_factory(svrtr_legacy_key)

    configured_provider_names = set(providers) | set(provider_secret_refs)
    fallback_routes = _parse_failover_routes()
    if _truthy("AI_STUDIO_GATEWAY_ENABLE_AUTO", True):
        try:
            fallback_routes["auto"] = _auto_routes(configured_provider_names)
        except Exception:
            # Startup and migrations must never fail merely because routing
            # statistics/catalog tables are not ready yet.
            fallback_routes.setdefault("auto", [])

    return AIGateway(
        settings=settings,
        event_bus=event_bus,
        providers=providers,
        secret_manager=secret_manager,
        provider_secret_refs=provider_secret_refs,
        provider_factories=provider_factories,
        fallback_routes=fallback_routes,
        health_service=provider_health,
    )
