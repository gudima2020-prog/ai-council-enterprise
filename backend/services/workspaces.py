from __future__ import annotations

from typing import Any

from backend.core.events import Event, EventBus
from backend.core.logging import LoggerManager
from backend.repositories.workspaces import WorkspaceRepository


class WorkspaceService:
    ALLOWED_TYPES = {
        "general",
        "crypto",
        "council",
        "documents",
        "code",
        "research",
        "personal",
    }

    ALLOWED_STATUSES = {
        "active",
        "archived",
        "disabled",
    }

    LIFECYCLE_TRANSITIONS = {
        "active": {
            "archive": "archived",
            "disable": "disabled",
        },
        "archived": {
            "restore": "active",
        },
        "disabled": {
            "enable": "active",
            "archive": "archived",
        },
    }

    def __init__(
        self,
        *,
        repository: WorkspaceRepository,
        event_bus: EventBus,
    ) -> None:
        self._repository = repository
        self._event_bus = event_bus
        self._logger = LoggerManager.get_logger("workspaces")

    async def create_workspace(
        self,
        *,
        name: str,
        description: str = "",
        workspace_type: str = "general",
        icon: str | None = None,
        color: str | None = None,
        status: str = "active",
        metadata_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._validate_name(name)
        self._validate_type(workspace_type)
        self._validate_status(status)

        existing = self._repository.find_by_name(name)
        if existing is not None:
            raise ValueError("Workspace with this name already exists.")

        row = self._repository.create(
            name=name,
            description=description,
            workspace_type=workspace_type,
            icon=icon,
            color=color,
            status=status,
            metadata_json=metadata_json,
        )

        self._logger.info(
            "Workspace created id=%s name=%s type=%s",
            row.id,
            row.name,
            row.workspace_type,
        )

        await self._event_bus.publish(
            Event(
                event_type="workspace.created",
                source="workspace_service",
                workspace_id=row.id,
                payload={
                    "workspace_id": row.id,
                    "name": row.name,
                    "workspace_type": row.workspace_type,
                },
            )
        )

        return self._serialize(row)

    def list_workspaces(
        self,
        *,
        active_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = (
            self._repository.list_active()
            if active_only
            else self._repository.list(limit=limit)
        )
        return [self._serialize(row) for row in rows]

    def get_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        row = self._repository.get(workspace_id)
        return None if row is None else self._serialize(row)

    async def update_workspace(
        self,
        *,
        workspace_id: str,
        name: str | None = None,
        description: str | None = None,
        workspace_type: str | None = None,
        icon: str | None = None,
        color: str | None = None,
        status: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        row = self._repository.get(workspace_id)
        if row is None:
            return None

        if name is not None:
            self._validate_name(name)
            existing = self._repository.find_by_name(name)
            if existing is not None and existing.id != workspace_id:
                raise ValueError("Workspace with this name already exists.")
            row.name = name.strip()

        if description is not None:
            row.description = description.strip()

        if workspace_type is not None:
            self._validate_type(workspace_type)
            row.workspace_type = workspace_type

        if icon is not None:
            row.icon = icon

        if color is not None:
            row.color = color

        if status is not None:
            self._validate_status(status)
            row.status = status

        if metadata_json is not None:
            row.metadata_json = metadata_json

        self._repository.session.flush()

        await self._event_bus.publish(
            Event(
                event_type="workspace.updated",
                source="workspace_service",
                workspace_id=row.id,
                payload={
                    "workspace_id": row.id,
                    "status": row.status,
                },
            )
        )

        return self._serialize(row)

    async def change_lifecycle(
        self,
        *,
        workspace_id: str,
        action: str,
    ) -> dict[str, Any] | None:
        row = self._repository.get(workspace_id)
        if row is None:
            return None

        normalized_action = action.strip().lower()
        target_status = self.LIFECYCLE_TRANSITIONS.get(
            row.status,
            {},
        ).get(normalized_action)

        if target_status is None:
            allowed_actions = sorted(
                self.LIFECYCLE_TRANSITIONS.get(row.status, {}).keys()
            )
            allowed_text = ", ".join(allowed_actions) if allowed_actions else "нет"
            raise ValueError(
                f"Недопустимый переход Workspace: status={row.status}, "
                f"action={normalized_action}. Допустимые действия: {allowed_text}."
            )

        previous_status = row.status
        row.status = target_status
        self._repository.session.flush()

        event_name = {
            "archive": "workspace.archived",
            "restore": "workspace.restored",
            "disable": "workspace.disabled",
            "enable": "workspace.enabled",
        }[normalized_action]

        await self._event_bus.publish(
            Event(
                event_type=event_name,
                source="workspace_service",
                workspace_id=row.id,
                payload={
                    "workspace_id": row.id,
                    "previous_status": previous_status,
                    "status": row.status,
                    "action": normalized_action,
                },
            )
        )

        self._logger.info(
            "Workspace lifecycle changed id=%s action=%s from=%s to=%s",
            row.id,
            normalized_action,
            previous_status,
            row.status,
        )

        return self._serialize(row)

    async def delete_workspace(self, workspace_id: str) -> bool:
        row = self._repository.get(workspace_id)
        if row is None:
            return False

        self._repository.delete(row)

        await self._event_bus.publish(
            Event(
                event_type="workspace.deleted",
                source="workspace_service",
                workspace_id=workspace_id,
                payload={"workspace_id": workspace_id},
            )
        )

        return True

    @classmethod
    def _validate_name(cls, name: str) -> None:
        if not name.strip():
            raise ValueError("Workspace name cannot be empty.")

    @classmethod
    def _validate_type(cls, workspace_type: str) -> None:
        if workspace_type not in cls.ALLOWED_TYPES:
            raise ValueError(
                f"Unsupported workspace_type: {workspace_type}. "
                f"Allowed: {sorted(cls.ALLOWED_TYPES)}"
            )

    @classmethod
    def _validate_status(cls, status: str) -> None:
        if status not in cls.ALLOWED_STATUSES:
            raise ValueError(
                f"Unsupported workspace status: {status}. "
                f"Allowed: {sorted(cls.ALLOWED_STATUSES)}"
            )

    @staticmethod
    def _serialize(row) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "workspace_type": row.workspace_type,
            "icon": row.icon,
            "color": row.color,
            "status": row.status,
            "metadata": row.metadata_json,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }
