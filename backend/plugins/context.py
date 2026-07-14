from __future__ import annotations

from dataclasses import dataclass

from backend.core.events import EventBus
from backend.core.logging import LoggerManager
from backend.gateway.service import AIGateway


@dataclass(frozen=True)
class PluginContext:
    plugin_id: str
    event_bus: EventBus
    ai_gateway: AIGateway | None = None

    @property
    def logger(self):
        return LoggerManager.get_logger(f"plugin.{self.plugin_id}")
