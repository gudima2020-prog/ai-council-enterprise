from typing import Any

from fastapi import APIRouter, Response, status

from backend.core.config import get_settings
from backend.core.workspace_context import get_workspace_context
from backend.plugins.registry import plugin_loader
from backend.services.readiness import ReadinessService

router = APIRouter(tags=["system"])


@router.get("/system/readiness")
def readiness(response: Response) -> dict[str, Any]:
    result = ReadinessService(get_settings()).check()

    if not result["ready"]:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return result


@router.get("/system/diagnostics")
def diagnostics() -> dict[str, Any]:
    settings = get_settings()
    context = get_workspace_context()

    return {
        "application": {
            "name": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
            "debug": settings.debug,
            "default_provider": settings.default_provider,
            "default_model": settings.default_model,
            "max_tokens": settings.max_tokens,
            "temperature": settings.temperature,
        },
        "workspace_context": None if context is None else {
            "workspace_id": context.workspace_id,
            "source": context.source,
            "policy": context.policy,
        },
        "plugins": plugin_loader.list_plugins(),
    }
