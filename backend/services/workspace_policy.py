from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.core.config import AppSettings
from backend.core.events import Event, EventBus
from backend.repositories.models import ModelRepository
from backend.repositories.settings import SettingsRepository
from backend.repositories.workspaces import WorkspaceRepository


@dataclass(frozen=True)
class EffectiveWorkspacePolicy:
    workspace_id: str
    provider: str
    model: str
    temperature: float
    max_tokens: int
    memory_mode: str
    network_access: str
    filesystem_access: str
    enabled_plugins: list[str]
    disabled_plugins: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "memory_mode": self.memory_mode,
            "network_access": self.network_access,
            "filesystem_access": self.filesystem_access,
            "enabled_plugins": self.enabled_plugins,
            "disabled_plugins": self.disabled_plugins,
        }


class WorkspacePolicyService:
    POLICY_KEYS = {
        "ai.provider",
        "ai.model",
        "ai.temperature",
        "ai.max_tokens",
        "memory.mode",
        "security.network_access",
        "security.filesystem_access",
        "plugins.enabled",
        "plugins.disabled",
    }

    MEMORY_MODES = {"off", "workspace", "project", "global"}
    NETWORK_ACCESS = {"denied", "restricted", "allowed"}
    FILESYSTEM_ACCESS = {"none", "read_only", "read_write"}

    def __init__(
        self,
        *,
        workspace_repository: WorkspaceRepository,
        settings_repository: SettingsRepository,
        model_repository: ModelRepository,
        event_bus: EventBus,
        app_settings: AppSettings,
    ) -> None:
        self._workspaces = workspace_repository
        self._settings = settings_repository
        self._models = model_repository
        self._event_bus = event_bus
        self._app_settings = app_settings

    def get_effective_policy(self, workspace_id: str) -> EffectiveWorkspacePolicy:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise ValueError("Workspace не найден.")

        rows = self._settings.list_filtered(
            scope="workspace",
            workspace_id=workspace_id,
        )
        stored = {row.key: row.value_json for row in rows}

        provider = str(stored.get("ai.provider", self._app_settings.default_provider))
        model = str(stored.get("ai.model", self._app_settings.default_model))
        temperature = float(stored.get("ai.temperature", self._app_settings.temperature))
        max_tokens = int(stored.get("ai.max_tokens", self._app_settings.max_tokens))
        memory_mode = str(stored.get("memory.mode", "workspace"))
        network_access = str(stored.get("security.network_access", "restricted"))
        filesystem_access = str(stored.get("security.filesystem_access", "read_only"))
        enabled_plugins = self._as_string_list(stored.get("plugins.enabled", []))
        disabled_plugins = self._as_string_list(stored.get("plugins.disabled", []))

        self._validate_policy_values(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            memory_mode=memory_mode,
            network_access=network_access,
            filesystem_access=filesystem_access,
        )

        return EffectiveWorkspacePolicy(
            workspace_id=workspace_id,
            provider=provider,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            memory_mode=memory_mode,
            network_access=network_access,
            filesystem_access=filesystem_access,
            enabled_plugins=enabled_plugins,
            disabled_plugins=disabled_plugins,
        )

    async def update_policy(self, *, workspace_id: str, values: dict[str, Any]) -> EffectiveWorkspacePolicy:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise ValueError("Workspace не найден.")

        unknown = sorted(set(values) - self.POLICY_KEYS)
        if unknown:
            raise ValueError("Unsupported workspace policy keys: " + ", ".join(unknown))

        preview = self.get_effective_policy(workspace_id).to_dict()
        preview.update(self._policy_to_effective_fields(values))

        self._validate_policy_values(
            model=str(preview["model"]),
            temperature=float(preview["temperature"]),
            max_tokens=int(preview["max_tokens"]),
            memory_mode=str(preview["memory_mode"]),
            network_access=str(preview["network_access"]),
            filesystem_access=str(preview["filesystem_access"]),
        )

        for key, value in values.items():
            self._settings.upsert(
                scope="workspace",
                key=key,
                value=value,
                workspace_id=workspace_id,
            )

        self._settings.session.flush()

        await self._event_bus.publish(
            Event(
                event_type="workspace.policy.updated",
                source="workspace_policy_service",
                workspace_id=workspace_id,
                payload={"workspace_id": workspace_id, "updated_keys": sorted(values)},
            )
        )

        return self.get_effective_policy(workspace_id)

    async def reset_policy(self, workspace_id: str) -> EffectiveWorkspacePolicy:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise ValueError("Workspace не найден.")

        rows = self._settings.list_filtered(scope="workspace", workspace_id=workspace_id)
        for row in rows:
            if row.key in self.POLICY_KEYS:
                self._settings.delete(row)

        self._settings.session.flush()

        await self._event_bus.publish(
            Event(
                event_type="workspace.policy.reset",
                source="workspace_policy_service",
                workspace_id=workspace_id,
                payload={"workspace_id": workspace_id},
            )
        )

        return self.get_effective_policy(workspace_id)

    def is_plugin_allowed(self, *, workspace_id: str, plugin_id: str) -> bool:
        policy = self.get_effective_policy(workspace_id)
        if plugin_id in policy.disabled_plugins:
            return False
        if policy.enabled_plugins:
            return plugin_id in policy.enabled_plugins
        return True

    def _validate_policy_values(
        self,
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        memory_mode: str,
        network_access: str,
        filesystem_access: str,
    ) -> None:
        model_row = self._models.find_by_slug(model)
        if model_row is None or not model_row.enabled:
            raise ValueError("Workspace model is not registered or is disabled.")
        if not 0.0 <= temperature <= 2.0:
            raise ValueError("ai.temperature must be between 0.0 and 2.0")
        if max_tokens < 1:
            raise ValueError("ai.max_tokens must be positive")
        if model_row.max_output_tokens is not None and max_tokens > model_row.max_output_tokens:
            raise ValueError("ai.max_tokens exceeds the selected model limit.")
        if memory_mode not in self.MEMORY_MODES:
            raise ValueError(f"Unsupported memory.mode: {memory_mode}")
        if network_access not in self.NETWORK_ACCESS:
            raise ValueError(f"Unsupported security.network_access: {network_access}")
        if filesystem_access not in self.FILESYSTEM_ACCESS:
            raise ValueError(f"Unsupported security.filesystem_access: {filesystem_access}")

    @staticmethod
    def _as_string_list(value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("Plugin policy value must be an array.")
        return [str(item) for item in value]

    @staticmethod
    def _policy_to_effective_fields(values: dict[str, Any]) -> dict[str, Any]:
        mapping = {
            "ai.provider": "provider",
            "ai.model": "model",
            "ai.temperature": "temperature",
            "ai.max_tokens": "max_tokens",
            "memory.mode": "memory_mode",
            "security.network_access": "network_access",
            "security.filesystem_access": "filesystem_access",
            "plugins.enabled": "enabled_plugins",
            "plugins.disabled": "disabled_plugins",
        }
        return {mapping[key]: value for key, value in values.items() if key in mapping}
