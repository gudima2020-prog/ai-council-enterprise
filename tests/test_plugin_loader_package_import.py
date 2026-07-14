from pathlib import Path
import json

import pytest
from fastapi import APIRouter

from backend.core.events import EventBus
from backend.plugins.loader import PluginLoader, PluginValidationError


def create_valid_plugin(root: Path) -> None:
    plugin_dir = root / "demo"
    backend_dir = plugin_dir / "backend"

    backend_dir.mkdir(parents=True)
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    (backend_dir / "__init__.py").write_text("", encoding="utf-8")
    (backend_dir / "routes.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n",
        encoding="utf-8",
    )
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "id": "demo",
                "name": "Demo",
                "version": "0.1.0",
                "backend": {
                    "entry": "demo.backend.routes:router",
                    "prefix": "/api/plugins/demo"
                }
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_loads_namespaced_plugin_package(tmp_path: Path) -> None:
    create_valid_plugin(tmp_path)

    loader = PluginLoader(
        plugins_dir=tmp_path,
        event_bus=EventBus(),
    )

    await loader.discover()
    await loader.load_all()

    plugin = loader.get_plugin("demo")

    assert plugin is not None
    assert plugin["status"] == "loaded"
    assert plugin["error"] is None


def test_rejects_non_namespaced_backend_entry(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")

    manifest_path = plugin_dir / "plugin.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": "demo",
                "name": "Demo",
                "version": "0.1.0",
                "backend": {
                    "entry": "backend.routes:router"
                }
            }
        ),
        encoding="utf-8",
    )

    loader = PluginLoader(
        plugins_dir=tmp_path,
        event_bus=EventBus(),
    )

    with pytest.raises(PluginValidationError):
        loader._load_manifest(manifest_path)
