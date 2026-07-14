from pathlib import Path
import json

import pytest

from backend.core.events import EventBus
from backend.plugins.loader import PluginLoader, PluginValidationError


@pytest.mark.asyncio
async def test_discovers_valid_plugin(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()

    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "id": "demo",
                "name": "Demo",
                "version": "0.1.0",
                "enabled": True,
                "permissions": [],
            }
        ),
        encoding="utf-8",
    )

    loader = PluginLoader(
        plugins_dir=tmp_path,
        event_bus=EventBus(),
    )

    manifests = await loader.discover()

    assert len(manifests) == 1
    assert manifests[0].id == "demo"
    assert loader.list_plugins()[0]["status"] == "discovered"


def test_rejects_invalid_plugin_id(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "bad"
    plugin_dir.mkdir()

    manifest_path = plugin_dir / "plugin.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": "Bad Plugin",
                "name": "Bad",
                "version": "0.1.0",
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
