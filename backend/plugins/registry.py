from pathlib import Path

from backend.core.events import event_bus
from backend.plugins.loader import PluginLoader


ROOT_DIR = Path(__file__).resolve().parents[2]
PLUGINS_DIR = ROOT_DIR / "plugins"

plugin_loader = PluginLoader(
    plugins_dir=PLUGINS_DIR,
    event_bus=event_bus,
)
