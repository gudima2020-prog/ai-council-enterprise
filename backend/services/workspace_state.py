from __future__ import annotations

from typing import Any

from backend.core.events import Event, EventBus
from backend.repositories.chats import ChatRepository
from backend.repositories.memory import MemoryRepository
from backend.repositories.projects import ProjectRepository
from backend.repositories.settings import SettingsRepository
from backend.repositories.workspaces import WorkspaceRepository


ACTIVE_WORKSPACE_KEY = "ui.active_workspace_id"


class ActiveWorkspaceService:
    """
    Single-user active Workspace state.

    The state is persisted in the existing settings table under user scope.
    A future authentication phase can add user_id without changing the public API.
    """

    def __init__(
        self,
        *,
        workspace_repository: WorkspaceRepository,
        settings_repository: SettingsRepository,
        project_repository: ProjectRepository,
        chat_repository: ChatRepository,
        memory_repository: MemoryRepository,
        event_bus: EventBus,
    ) -> None:
        self._workspaces = workspace_repository
        self._settings = settings_repository
        self._projects = project_repository
        self._chats = chat_repository
        self._memory = memory_repository
        self._event_bus = event_bus

    def get_active_workspace(self) -> dict[str, Any] | None:
        setting = self._settings.find_one(
            scope="user",
            key=ACTIVE_WORKSPACE_KEY,
        )

        if setting is None or not setting.value_json:
            return None

        workspace_id = str(setting.value_json)
        workspace = self._workspaces.get(workspace_id)

        if workspace is None:
            # Remove stale selection automatically.
            self._settings.delete(setting)
            return None

        return self._serialize_with_summary(workspace)

    async def activate_workspace(
        self,
        workspace_id: str,
    ) -> dict[str, Any]:
        workspace = self._workspaces.get(workspace_id)

        if workspace is None:
            raise ValueError("Workspace не найден.")

        if workspace.status != "active":
            raise ValueError(
                "Активировать можно только Workspace со статусом active."
            )

        previous = self.get_active_workspace()
        previous_id = previous["id"] if previous else None

        self._settings.upsert(
            scope="user",
            key=ACTIVE_WORKSPACE_KEY,
            value=workspace_id,
        )
        self._settings.session.flush()

        await self._event_bus.publish(
            Event(
                event_type="workspace.activated",
                source="active_workspace_service",
                workspace_id=workspace_id,
                payload={
                    "workspace_id": workspace_id,
                    "previous_workspace_id": previous_id,
                },
            )
        )

        return self._serialize_with_summary(workspace)

    async def clear_active_workspace(self) -> bool:
        setting = self._settings.find_one(
            scope="user",
            key=ACTIVE_WORKSPACE_KEY,
        )

        if setting is None:
            return False

        workspace_id = (
            str(setting.value_json)
            if setting.value_json
            else None
        )

        self._settings.delete(setting)

        await self._event_bus.publish(
            Event(
                event_type="workspace.deactivated",
                source="active_workspace_service",
                workspace_id=workspace_id,
                payload={"workspace_id": workspace_id},
            )
        )

        return True

    def _serialize_with_summary(self, workspace) -> dict[str, Any]:
        projects = self._projects.list_by_workspace(
            workspace.id,
            limit=500,
        )
        chats = self._chats.list_by_workspace(
            workspace.id,
            limit=500,
        )
        memory_items = self._memory.list_filtered(
            workspace_id=workspace.id,
            limit=500,
        )

        return {
            "id": workspace.id,
            "name": workspace.name,
            "description": workspace.description,
            "workspace_type": workspace.workspace_type,
            "icon": workspace.icon,
            "color": workspace.color,
            "status": workspace.status,
            "metadata": workspace.metadata_json,
            "counts": {
                "projects": len(projects),
                "chats": len(chats),
                "memory_items": len(memory_items),
            },
            "created_at": workspace.created_at.isoformat(),
            "updated_at": workspace.updated_at.isoformat(),
        }
