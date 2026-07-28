from typing import Any

from fastapi import APIRouter, Depends

from backend.api.dependencies import get_container
from backend.core.container import AppContainer

router = APIRouter(tags=["system"])


@router.get("/system/container")
def container_diagnostics(
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    return {
        "initialized": True,
        "dependencies": {
            "settings": type(container.settings).__name__,
            "event_bus": type(container.event_bus).__name__,
            "plugin_loader": type(container.plugin_loader).__name__,
        },
        "application": {
            "name": container.settings.app_name,
            "version": container.settings.app_version,
            "environment": container.settings.environment,
        },
    }
