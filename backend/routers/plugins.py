from typing import Any

from fastapi import APIRouter, HTTPException

from backend.plugins.registry import plugin_loader

router = APIRouter(tags=["plugins"])


@router.get("/plugins")
def list_plugins() -> dict[str, list[dict[str, Any]]]:
    return {"plugins": plugin_loader.list_plugins()}


@router.get("/plugins/{plugin_id}")
def get_plugin(plugin_id: str) -> dict[str, Any]:
    plugin = plugin_loader.get_plugin(plugin_id)
    if plugin is None:
        raise HTTPException(status_code=404, detail="Плагин не найден.")
    return plugin
