from __future__ import annotations

from typing import Any

from backend.core.config import ConfigurationManager
from backend.core.events import Event, EventBus
from backend.core.logging import LoggerManager
from backend.repositories.settings import SettingsRepository


class SettingsService:
    ALLOWED_SCOPES = {"global", "project", "workspace", "user"}

    def __init__(
        self,
        *,
        repository: SettingsRepository,
        event_bus: EventBus,
    ) -> None:
        self._repository = repository
        self._event_bus = event_bus
        self._logger = LoggerManager.get_logger("settings")

    def list_settings(
        self,
        *,
        scope: str | None = None,
        project_id: str | None = None,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if scope is not None:
            self._validate_scope(scope)

        rows = self._repository.list_filtered(
            scope=scope,
            project_id=project_id,
            workspace_id=workspace_id,
        )

        return [
            {
                "id": row.id,
                "scope": row.scope,
                "project_id": row.project_id,
                "workspace_id": row.workspace_id,
                "key": row.key,
                "value": row.value_json,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }
            for row in rows
        ]

    async def set_setting(
        self,
        *,
        scope: str,
        key: str,
        value: Any,
        project_id: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        self._validate_scope(scope)
        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError("Setting key cannot be empty.")

        row = self._repository.upsert(
            scope=scope,
            key=normalized_key,
            value=value,
            project_id=project_id,
            workspace_id=workspace_id,
        )

        self._logger.info(
            "Setting updated scope=%s key=%s project_id=%s workspace_id=%s",
            scope,
            normalized_key,
            project_id or "-",
            workspace_id or "-",
        )

        await self._event_bus.publish(
            Event(
                event_type="system.config.updated",
                source="settings_service",
                project_id=project_id,
                workspace_id=workspace_id,
                payload={
                    "setting_id": row.id,
                    "scope": row.scope,
                    "key": row.key,
                },
            )
        )

        return {
            "id": row.id,
            "scope": row.scope,
            "project_id": row.project_id,
            "workspace_id": row.workspace_id,
            "key": row.key,
            "value": row.value_json,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }

    async def delete_setting(self, setting_id: str) -> bool:
        row = self._repository.get(setting_id)
        if row is None:
            return False

        event_payload = {
            "setting_id": row.id,
            "scope": row.scope,
            "key": row.key,
        }

        self._repository.delete(row)

        await self._event_bus.publish(
            Event(
                event_type="system.config.deleted",
                source="settings_service",
                project_id=row.project_id,
                workspace_id=row.workspace_id,
                payload=event_payload,
            )
        )

        return True

    def effective_runtime_settings(self) -> dict[str, Any]:
        settings = ConfigurationManager().load()
        return {
            "app_name": settings.app_name,
            "app_version": settings.app_version,
            "environment": settings.environment,
            "debug": settings.debug,
            "host": settings.host,
            "port": settings.port,
            "default_provider": settings.default_provider,
            "default_model": settings.default_model,
            "max_tokens": settings.max_tokens,
            "temperature": settings.temperature,
            "request_timeout_seconds": settings.request_timeout_seconds,
            "has_openrouter_key": settings.has_openrouter_key,
        }

    def _validate_scope(self, scope: str) -> None:
        if scope not in self.ALLOWED_SCOPES:
            raise ValueError(
                f"Unsupported settings scope: {scope}. "
                f"Allowed: {sorted(self.ALLOWED_SCOPES)}"
            )
