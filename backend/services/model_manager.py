from typing import Any

from backend.core.config import AppSettings
from backend.core.events import Event, EventBus
from backend.repositories.models import ModelRepository


DEFAULT_MODELS = [
    {
        "provider": "openrouter",
        "slug": "deepseek/deepseek-chat-v3-0324",
        "display_name": "DeepSeek Chat V3",
        "enabled": True,
        "priority": 10,
        "context_window": 163840,
        "max_output_tokens": 8192,
        "supports_tools": False,
        "supports_vision": False,
        "supports_json": True,
        "metadata_json": {},
    },
    {
        "provider": "openrouter",
        "slug": "qwen/qwen3-30b-a3b",
        "display_name": "Qwen 3 30B A3B",
        "enabled": True,
        "priority": 20,
        "context_window": 131072,
        "max_output_tokens": 8192,
        "supports_tools": True,
        "supports_vision": False,
        "supports_json": True,
        "metadata_json": {},
    },
    {
        "provider": "openrouter",
        "slug": "google/gemini-2.0-flash-001",
        "display_name": "Gemini 2.0 Flash",
        "enabled": True,
        "priority": 30,
        "context_window": 1048576,
        "max_output_tokens": 8192,
        "supports_tools": True,
        "supports_vision": True,
        "supports_json": True,
        "metadata_json": {},
    },
]


class ModelManager:
    def __init__(
        self,
        repository: ModelRepository,
        event_bus: EventBus,
        settings: AppSettings,
    ) -> None:
        self.repository = repository
        self.event_bus = event_bus
        self.settings = settings

    def seed_defaults(self) -> None:
        for item in DEFAULT_MODELS:
            self.repository.upsert(**item)

    def list_models(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        rows = (
            self.repository.list_enabled()
            if enabled_only
            else self.repository.list(limit=500)
        )
        return [self._serialize(row) for row in rows]

    def get_model(self, slug: str) -> dict[str, Any] | None:
        row = self.repository.find_by_slug(slug)
        return None if row is None else self._serialize(row)

    async def upsert_model(self, **data) -> dict[str, Any]:
        row = self.repository.upsert(**data)
        await self.event_bus.publish(
            Event(
                event_type="ai.model.updated",
                source="model_manager",
                payload={
                    "model_id": row.id,
                    "slug": row.slug,
                    "enabled": row.enabled,
                },
            )
        )
        return self._serialize(row)

    def resolve_model(
        self,
        requested_slug: str | None = None,
        require_tools: bool = False,
        require_vision: bool = False,
        require_json: bool = False,
    ) -> dict[str, Any]:
        candidates = self.repository.list_enabled()

        if requested_slug:
            row = self.repository.find_by_slug(requested_slug)
            if row is None or not row.enabled:
                raise ValueError("Requested model is not available.")
            candidates = [row]

        for row in candidates:
            if require_tools and not row.supports_tools:
                continue
            if require_vision and not row.supports_vision:
                continue
            if require_json and not row.supports_json:
                continue
            return self._serialize(row)

        raise ValueError("No suitable enabled model is available.")

    @staticmethod
    def _serialize(row) -> dict[str, Any]:
        return {
            "id": row.id,
            "provider": row.provider,
            "slug": row.slug,
            "display_name": row.display_name,
            "enabled": row.enabled,
            "priority": row.priority,
            "context_window": row.context_window,
            "max_output_tokens": row.max_output_tokens,
            "supports_tools": row.supports_tools,
            "supports_vision": row.supports_vision,
            "supports_json": row.supports_json,
            "metadata": row.metadata_json,
        }
