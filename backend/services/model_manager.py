from typing import Any
import os

from backend.core.config import AppSettings
from backend.core.events import Event, EventBus
from backend.repositories.models import ModelRepository


DEFAULT_MODELS = [
    {
        "provider": "openrouter",
        "slug": "openrouter/free",
        "display_name": "OpenRouter Free Router",
        "enabled": True,
        "priority": 1,
        "context_window": 200000,
        "max_output_tokens": None,
        "supports_tools": True,
        "supports_vision": True,
        "supports_json": True,
        "metadata_json": {
            "billing": "free",
            "kind": "router",
            "catalog_checked_at": "2026-07-23",
        },
    },
    {
        "provider": "openrouter",
        "slug": "openai/gpt-oss-20b:free",
        "display_name": "OpenAI gpt-oss 20B (free)",
        "enabled": True,
        "priority": 2,
        "context_window": 131072,
        "max_output_tokens": 32768,
        "supports_tools": True,
        "supports_vision": False,
        "supports_json": True,
        "metadata_json": {
            "billing": "free",
            "kind": "concrete",
            "catalog_checked_at": "2026-07-23",
        },
    },
    {
        "provider": "openrouter",
        "slug": "google/gemma-4-31b-it:free",
        "display_name": "Google Gemma 4 31B (free)",
        "enabled": True,
        "priority": 3,
        "context_window": 262144,
        "max_output_tokens": 32768,
        "supports_tools": True,
        "supports_vision": True,
        "supports_json": True,
        "metadata_json": {
            "billing": "free",
            "kind": "concrete",
            "catalog_checked_at": "2026-07-23",
        },
    },
    {
        "provider": "openrouter",
        "slug": "~openai/gpt-latest",
        "display_name": "OpenAI GPT Latest",
        "enabled": True,
        "priority": 20,
        "context_window": None,
        "max_output_tokens": None,
        "supports_tools": True,
        "supports_vision": True,
        "supports_json": True,
        "metadata_json": {
            "billing": "metered",
            "kind": "latest_alias",
        },
    },
    {
        "provider": "openrouter",
        "slug": "~anthropic/claude-sonnet-latest",
        "display_name": "Anthropic Claude Sonnet Latest",
        "enabled": True,
        "priority": 30,
        "context_window": None,
        "max_output_tokens": None,
        "supports_tools": True,
        "supports_vision": True,
        "supports_json": True,
        "metadata_json": {
            "billing": "metered",
            "kind": "latest_alias",
        },
    },
]


SVRTR_MODELS = [
    {
        "provider": "svrtr",
        "slug": "claude-fable-5",
        "display_name": "SVRTR · Claude Fable 5",
        "priority": 40,
        "context_window": 1_000_000,
        "max_output_tokens": 131_072,
        "supports_tools": True,
        "supports_vision": True,
        "supports_json": True,
        "metadata_json": {
            "billing": "metered",
            "kind": "concrete",
            "quality_score": 100,
            "latency_ms_hint": 2400,
            "provider_adapter": "openai_compatible",
            "catalog_checked_at": "2026-07-24",
            "pricing": {
                "input_per_million_usd": 10.0,
                "output_per_million_usd": 50.0,
            },
        },
    },
    {
        "provider": "svrtr",
        "slug": "claude-opus-4-8",
        "display_name": "SVRTR · Claude Opus 4.8",
        "priority": 41,
        "context_window": 1_000_000,
        "max_output_tokens": 65_536,
        "supports_tools": True,
        "supports_vision": True,
        "supports_json": True,
        "metadata_json": {
            "billing": "metered",
            "kind": "concrete",
            "quality_score": 96,
            "latency_ms_hint": 1800,
            "provider_adapter": "openai_compatible",
            "catalog_checked_at": "2026-07-24",
            "pricing": {
                "input_per_million_usd": 5.0,
                "output_per_million_usd": 25.0,
            },
        },
    },
    {
        "provider": "svrtr",
        "slug": "claude-sonnet-5",
        "display_name": "SVRTR · Claude Sonnet 5",
        "priority": 42,
        "context_window": 1_000_000,
        "max_output_tokens": 65_536,
        "supports_tools": True,
        "supports_vision": True,
        "supports_json": True,
        "metadata_json": {
            "billing": "metered",
            "kind": "concrete",
            "quality_score": 90,
            "latency_ms_hint": 1100,
            "provider_adapter": "openai_compatible",
            "catalog_checked_at": "2026-07-24",
            "pricing": {
                "input_per_million_usd": 2.0,
                "output_per_million_usd": 10.0,
                "valid_until": "2026-08-31",
                "note": "SVRTR promotional price published for Sonnet 5.",
            },
        },
    },
    {
        "provider": "svrtr",
        "slug": "claude-haiku-4-5",
        "display_name": "SVRTR · Claude Haiku 4.5",
        "priority": 43,
        "context_window": 200_000,
        "max_output_tokens": 65_536,
        "supports_tools": True,
        "supports_vision": True,
        "supports_json": True,
        "metadata_json": {
            "billing": "metered",
            "kind": "concrete",
            "quality_score": 72,
            "latency_ms_hint": 500,
            "provider_adapter": "openai_compatible",
            "catalog_checked_at": "2026-07-24",
            "pricing": {
                "input_per_million_usd": 1.0,
                "output_per_million_usd": 5.0,
            },
        },
    },
]


def _svrtr_configured() -> bool:
    return bool(
        os.getenv("AI_STUDIO_SVRTR_SECRET_REF", "").strip()
        or os.getenv("SVRTR_API_KEY", "").strip()
    )

LEGACY_DEFAULT_MODELS = {
    "deepseek/deepseek-chat-v3-0324": {
        "provider": "openrouter",
        "display_name": "DeepSeek Chat V3",
        "priority": 10,
        "context_window": 163840,
        "max_output_tokens": 8192,
        "supports_tools": False,
        "supports_vision": False,
        "supports_json": True,
    },
    "qwen/qwen3-30b-a3b": {
        "provider": "openrouter",
        "display_name": "Qwen 3 30B A3B",
        "priority": 20,
        "context_window": 131072,
        "max_output_tokens": 8192,
        "supports_tools": True,
        "supports_vision": False,
        "supports_json": True,
    },
    "google/gemini-2.0-flash-001": {
        "provider": "openrouter",
        "display_name": "Gemini 2.0 Flash",
        "priority": 30,
        "context_window": 1048576,
        "max_output_tokens": 8192,
        "supports_tools": True,
        "supports_vision": True,
        "supports_json": True,
    },
}

P2_001_2_RETIREMENT_METADATA = {
    "retired_default": True,
    "retired_in": "P2-001.2",
    "replacement": "openrouter/free",
}

LEGACY_CATALOG_METADATA = {
    "retired_default": True,
    "retired_in": "P2-001.3",
    "replacement": "openrouter/free",
    "catalog_hidden": True,
    "enabled_preserved": True,
}


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
        svrtr_enabled = _svrtr_configured()
        for item in SVRTR_MODELS:
            self.repository.upsert(**{**item, "enabled": svrtr_enabled})
        self._retire_legacy_defaults()

    def _retire_legacy_defaults(self) -> None:
        changed = False
        for slug, expected in LEGACY_DEFAULT_MODELS.items():
            row = self.repository.find_by_slug(slug)
            if row is None:
                continue
            if any(
                getattr(row, field) != value
                for field, value in expected.items()
            ):
                continue

            metadata = dict(row.metadata_json or {})
            if metadata == P2_001_2_RETIREMENT_METADATA:
                # P2-001.2 disabled legacy defaults before checking whether
                # an existing Workspace policy still referenced them.
                row.enabled = True
            elif metadata:
                continue

            # Preserve runtime compatibility for existing Workspace policies,
            # while allowing the product UI to hide obsolete starter entries.
            row.metadata_json = dict(LEGACY_CATALOG_METADATA)
            changed = True

        if changed:
            self.repository.session.flush()

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
