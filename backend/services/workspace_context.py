from __future__ import annotations

from typing import Any

from backend.core.config import AppSettings
from backend.core.events import EventBus
from backend.repositories.models import ModelRepository
from backend.repositories.settings import SettingsRepository
from backend.repositories.workspaces import WorkspaceRepository
from backend.services.workspace_policy import WorkspacePolicyService
from backend.services.workspace_state import ACTIVE_WORKSPACE_KEY


class WorkspaceContextResolver:
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

    def resolve(
        self,
        *,
        requested_workspace_id: str | None,
    ) -> dict[str, Any]:
        workspace_id = requested_workspace_id
        source = "request_header"

        if not workspace_id:
            setting = self._settings.find_one(
                scope="user",
                key=ACTIVE_WORKSPACE_KEY,
            )

            if setting is not None and setting.value_json:
                workspace_id = str(setting.value_json)
                source = "active_workspace"

        if not workspace_id:
            return {
                "workspace_id": None,
                "source": "none",
                "workspace": None,
                "policy": None,
            }

        workspace = self._workspaces.get(workspace_id)

        if workspace is None:
            raise ValueError("Workspace context references an unknown Workspace.")

        if workspace.status != "active":
            raise ValueError(
                "Workspace context can use only a Workspace with status active."
            )

        policy = WorkspacePolicyService(
            workspace_repository=self._workspaces,
            settings_repository=self._settings,
            model_repository=self._models,
            event_bus=self._event_bus,
            app_settings=self._app_settings,
        ).get_effective_policy(workspace_id)

        return {
            "workspace_id": workspace.id,
            "source": source,
            "workspace": {
                "id": workspace.id,
                "name": workspace.name,
                "workspace_type": workspace.workspace_type,
                "status": workspace.status,
            },
            "policy": policy.to_dict(),
        }
