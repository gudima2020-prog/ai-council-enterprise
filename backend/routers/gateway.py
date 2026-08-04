from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.orm import Session

from backend.api.dependencies import (
    get_ai_gateway,
    get_container,
    get_db_session,
)
from backend.control_center.governance_schemas import (
    HumanControlPermission,
)
from backend.control_center.security import (
    HumanControlPrincipal,
    HumanControlSecurityError,
    enforce_workspace_value,
    require_permission,
    security_http_exception,
)
from backend.core.config import get_settings
from backend.core.container import AppContainer
from backend.core.events import event_bus
from backend.gateway.factory import build_ai_gateway
from backend.gateway.health import provider_health
from backend.gateway.prompts import get_system_prompt
from backend.gateway.schemas import GatewayResponse
from backend.gateway.service import AIGateway
from backend.gateway.routing import GatewayRoutingService, RoutingRequirements

router = APIRouter(tags=["gateway"])


class GatewayRouteRequest(BaseModel):
    strategy: Literal["balanced", "cost", "latency", "reliability", "quality"] = "balanced"
    require_tools: bool = False
    require_vision: bool = False
    require_json: bool = False
    expected_input_tokens: int = Field(default=2000, ge=0, le=10_000_000)
    expected_output_tokens: int = Field(default=1000, ge=0, le=1_000_000)
    limit: int = Field(default=5, ge=1, le=20)


class GatewayInferenceRequest(BaseModel):
    user_prompt: str = Field(..., min_length=1, max_length=100_000)
    mode: str = Field(default="universal", min_length=1, max_length=64)
    provider: str | None = Field(default=None, min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=255)
    request_id: str | None = Field(default=None, min_length=1, max_length=255)
    approval_id: str | None = Field(default=None, min_length=1, max_length=255)
    approval_token: SecretStr | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)


def _bound_gateway_workspace(
    workspace_id: str,
    principal: HumanControlPrincipal,
) -> str:
    try:
        resolved = enforce_workspace_value(workspace_id, principal)
    except HumanControlSecurityError as exc:
        raise security_http_exception(exc) from exc
    if not resolved:
        raise ValueError("A Workspace is required.")
    return resolved


def _gateway_http_status(result: GatewayResponse) -> int:
    if result.status == "success":
        return 200
    if result.error is None:
        return 502
    code = result.error.code
    if code == "POLICY_APPROVAL_REQUIRED":
        return 202
    if code == "POLICY_APPROVAL_CREDENTIALS_INCOMPLETE":
        return 400
    if code == "POLICY_APPROVAL_NOT_FOUND":
        return 404
    if code in {
        "POLICY_APPROVAL_TOKEN_INVALID",
        "POLICY_APPROVAL_SCOPE_MISMATCH",
        "POLICY_APPROVAL_WORKSPACE_INVALID",
        "POLICY_DENIED",
    }:
        return 403
    if code in {
        "POLICY_APPROVAL_EXPIRED",
        "POLICY_APPROVAL_INVALID_STATE",
    }:
        return 409
    if code in {
        "POLICY_APPROVAL_UNAVAILABLE",
        "POLICY_APPROVAL_RESOLUTION_FAILED",
        "POLICY_APPROVAL_CONSUME_FAILED",
    }:
        return 503
    return 502


def _gateway_response_payload(result: GatewayResponse) -> dict:
    payload = {
        "request_id": result.request_id,
        "status": result.status,
        "provider": result.provider,
        "model": result.model,
        "content": result.content,
        "latency_ms": result.latency_ms,
        "cost": result.cost,
        "metadata": dict(result.metadata),
        "usage": {
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "total_tokens": result.usage.total_tokens,
        },
        "error": None,
    }
    if result.error is not None:
        payload["error"] = {
            "code": result.error.code,
            "message": result.error.message,
            "recoverable": result.error.recoverable,
            "details": dict(result.error.details),
        }
    return payload


def _provider_configuration() -> dict[str, dict]:
    settings = get_settings()
    openrouter_ref = os.getenv("AI_STUDIO_OPENROUTER_SECRET_REF", "").strip()
    svrtr_ref = os.getenv("AI_STUDIO_SVRTR_SECRET_REF", "").strip()
    svrtr_key = os.getenv("SVRTR_API_KEY", "").strip()
    return {
        "openrouter": {
            "configured": bool(openrouter_ref) or settings.has_openrouter_key,
            "credential_mode": (
                "secret_ref"
                if openrouter_ref
                else "legacy_settings"
                if settings.has_openrouter_key
                else "not_configured"
            ),
            "base_url": "https://openrouter.ai/api/v1",
            "adapter": "openrouter",
        },
        "svrtr": {
            "configured": bool(svrtr_ref) or bool(svrtr_key),
            "credential_mode": (
                "secret_ref"
                if svrtr_ref
                else "legacy_env"
                if svrtr_key
                else "not_configured"
            ),
            "base_url": os.getenv(
                "AI_STUDIO_SVRTR_BASE_URL", "https://api.svrtr.org/v1"
            ).strip(),
            "adapter": "openai_compatible",
        },
    }


@router.get("/gateway/status")
def gateway_status() -> dict:
    settings = get_settings()
    configs = _provider_configuration()
    health_by_provider = {
        item["provider"]: item for item in provider_health.list_snapshots()
    }
    providers = {}
    for provider, config in configs.items():
        providers[provider] = {
            **config,
            "health": health_by_provider.get(provider)
            or {
                "provider": provider,
                "status": "unknown",
                "circuit_open": False,
                "total_requests": 0,
            },
        }
    return {
        "default_provider": settings.default_provider,
        "default_model": settings.default_model,
        "max_tokens": settings.max_tokens,
        "temperature": settings.temperature,
        "request_timeout_seconds": settings.request_timeout_seconds,
        "auto_routing_enabled": os.getenv(
            "AI_STUDIO_GATEWAY_ENABLE_AUTO", "1"
        ).strip().lower()
        not in {"0", "false", "no", "off"},
        "auto_strategy": os.getenv(
            "AI_STUDIO_GATEWAY_AUTO_STRATEGY", "balanced"
        ).strip(),
        "failover_configured": bool(
            os.getenv("AI_STUDIO_GATEWAY_FAILOVER_JSON", "").strip()
        ),
        "providers": providers,
    }


@router.get("/gateway/providers")
def gateway_providers() -> dict:
    configs = _provider_configuration()
    health_by_provider = {
        item["provider"]: item for item in provider_health.list_snapshots()
    }
    return {
        "providers": [
            {
                "provider": provider,
                **config,
                "health": health_by_provider.get(provider)
                or {
                    "provider": provider,
                    "status": "unknown",
                    "circuit_open": False,
                    "total_requests": 0,
                    "successful_requests": 0,
                    "failed_requests": 0,
                    "consecutive_failures": 0,
                    "reliability": None,
                    "ema_latency_ms": None,
                    "last_error_code": None,
                },
            }
            for provider, config in sorted(configs.items())
        ]
    }


@router.post("/gateway/route")
def gateway_route(
    request: GatewayRouteRequest,
    session: Session = Depends(get_db_session),
) -> dict:
    service = GatewayRoutingService(
        session=session,
        health_service=provider_health,
    )
    configured = {
        key for key, value in _provider_configuration().items() if value["configured"]
    }
    candidates = service.recommend(
        strategy=request.strategy,
        requirements=RoutingRequirements(
            require_tools=request.require_tools,
            require_vision=request.require_vision,
            require_json=request.require_json,
            expected_input_tokens=request.expected_input_tokens,
            expected_output_tokens=request.expected_output_tokens,
        ),
        limit=request.limit,
        allowed_providers=configured,
    )
    return {
        "strategy": request.strategy,
        "candidates": candidates,
        "selected": candidates[0] if candidates else None,
    }


@router.post("/workspaces/{workspace_id}/gateway/inference")
async def gateway_inference(
    workspace_id: str,
    request: GatewayInferenceRequest,
    http_response: Response,
    gateway: AIGateway = Depends(get_ai_gateway),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.APPROVAL_INITIATE.value)
    ),
) -> dict:
    workspace_id = _bound_gateway_workspace(workspace_id, principal)
    actor_id = principal.actor_id or principal.identity_id or "gateway-api"
    approval_token = (
        request.approval_token.get_secret_value()
        if request.approval_token is not None
        else None
    )
    result = await gateway.ask(
        user_prompt=request.user_prompt,
        system_prompt=get_system_prompt(request.mode),
        provider=request.provider,
        model=request.model,
        mode=request.mode,
        source="gateway_api",
        workspace_id=workspace_id,
        actor_id=actor_id,
        request_id=request.request_id,
        approval_id=request.approval_id,
        approval_token=approval_token,
        timeout_seconds=request.timeout_seconds,
    )
    http_response.status_code = _gateway_http_status(result)
    return _gateway_response_payload(result)


@router.post("/gateway/test")
async def gateway_test(
    container: AppContainer = Depends(get_container),
) -> dict:
    settings = get_settings()
    gateway = build_ai_gateway(
        settings=settings,
        event_bus=event_bus,
        secret_manager=container.secret_manager_service,
    )
    response = await gateway.ask(
        user_prompt="Ответь одной фразой: AI Gateway работает.",
        system_prompt=get_system_prompt("universal"),
        source="gateway_test",
        actor_id="gateway-test",
    )
    result = {
        "request_id": response.request_id,
        "status": response.status,
        "provider": response.provider,
        "model": response.model,
        "content": response.content,
        "latency_ms": response.latency_ms,
        "cost": response.cost,
        "failover_used": bool(response.metadata.get("failover_used")),
        "route": response.metadata.get("route", []),
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
