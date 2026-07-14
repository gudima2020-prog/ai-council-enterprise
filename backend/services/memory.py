from __future__ import annotations

from typing import Any

from backend.core.events import Event, EventBus
from backend.core.logging import LoggerManager
from backend.repositories.memory import MemoryRepository


class MemoryService:
    ALLOWED_SCOPES = {
        "global",
        "project",
        "workspace",
        "session",
        "long_term",
    }

    ALLOWED_SOURCE_TYPES = {
        "manual",
        "chat",
        "document",
        "crypto",
        "agent",
        "council",
        "system",
    }

    def __init__(
        self,
        *,
        repository: MemoryRepository,
        event_bus: EventBus,
    ) -> None:
        self._repository = repository
        self._event_bus = event_bus
        self._logger = LoggerManager.get_logger("memory")

    async def create_item(
        self,
        *,
        scope: str,
        title: str,
        content: str,
        source_type: str,
        project_id: str | None = None,
        workspace_id: str | None = None,
        source_id: str | None = None,
        importance: float = 0.5,
        tags: list[str] | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._validate_scope(scope)
        self._validate_source_type(source_type)

        if not title.strip():
            raise ValueError("Memory title cannot be empty.")

        if not content.strip():
            raise ValueError("Memory content cannot be empty.")

        if not 0.0 <= importance <= 1.0:
            raise ValueError("importance must be between 0.0 and 1.0")

        row = self._repository.create(
            scope=scope,
            title=title,
            content=content,
            source_type=source_type,
            project_id=project_id,
            workspace_id=workspace_id,
            source_id=source_id,
            importance=importance,
            tags=tags,
            metadata_json=metadata_json,
        )

        self._logger.info(
            "Memory item created id=%s scope=%s source_type=%s",
            row.id,
            row.scope,
            row.source_type,
        )

        await self._event_bus.publish(
            Event(
                event_type="memory.item.created",
                source="memory_service",
                project_id=project_id,
                workspace_id=workspace_id,
                payload={
                    "memory_item_id": row.id,
                    "scope": row.scope,
                    "source_type": row.source_type,
                },
            )
        )

        return self._serialize(row)

    def list_items(
        self,
        *,
        scope: str | None = None,
        project_id: str | None = None,
        workspace_id: str | None = None,
        source_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if scope is not None:
            self._validate_scope(scope)

        if source_type is not None:
            self._validate_source_type(source_type)

        rows = self._repository.list_filtered(
            scope=scope,
            project_id=project_id,
            workspace_id=workspace_id,
            source_type=source_type,
            limit=limit,
        )

        return [self._serialize(row) for row in rows]

    def get_item(self, memory_item_id: str) -> dict[str, Any] | None:
        row = self._repository.get(memory_item_id)
        return None if row is None else self._serialize(row)

    def search(
        self,
        *,
        query: str,
        scope: str | None = None,
        project_id: str | None = None,
        workspace_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("Search query cannot be empty.")

        if scope is not None:
            self._validate_scope(scope)

        rows = self._repository.search(
            query=query,
            scope=scope,
            project_id=project_id,
            workspace_id=workspace_id,
            limit=limit,
        )

        return [self._serialize(row) for row in rows]

    async def update_item(
        self,
        *,
        memory_item_id: str,
        title: str | None = None,
        content: str | None = None,
        importance: float | None = None,
        tags: list[str] | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        row = self._repository.get(memory_item_id)

        if row is None:
            return None

        if title is not None:
            if not title.strip():
                raise ValueError("Memory title cannot be empty.")
            row.title = title.strip()

        if content is not None:
            if not content.strip():
                raise ValueError("Memory content cannot be empty.")
            row.content = content.strip()

        if importance is not None:
            if not 0.0 <= importance <= 1.0:
                raise ValueError("importance must be between 0.0 and 1.0")
            row.importance = importance

        if tags is not None:
            row.tags_json = tags

        if metadata_json is not None:
            row.metadata_json = metadata_json

        self._repository.session.flush()

        await self._event_bus.publish(
            Event(
                event_type="memory.item.updated",
                source="memory_service",
                project_id=row.project_id,
                workspace_id=row.workspace_id,
                payload={
                    "memory_item_id": row.id,
                },
            )
        )

        return self._serialize(row)

    async def delete_item(self, memory_item_id: str) -> bool:
        row = self._repository.get(memory_item_id)

        if row is None:
            return False

        project_id = row.project_id
        workspace_id = row.workspace_id

        self._repository.delete(row)

        await self._event_bus.publish(
            Event(
                event_type="memory.item.deleted",
                source="memory_service",
                project_id=project_id,
                workspace_id=workspace_id,
                payload={
                    "memory_item_id": memory_item_id,
                },
            )
        )

        return True

    @classmethod
    def _validate_scope(cls, scope: str) -> None:
        if scope not in cls.ALLOWED_SCOPES:
            raise ValueError(
                f"Unsupported memory scope: {scope}. "
                f"Allowed: {sorted(cls.ALLOWED_SCOPES)}"
            )

    @classmethod
    def _validate_source_type(cls, source_type: str) -> None:
        if source_type not in cls.ALLOWED_SOURCE_TYPES:
            raise ValueError(
                f"Unsupported memory source_type: {source_type}. "
                f"Allowed: {sorted(cls.ALLOWED_SOURCE_TYPES)}"
            )

    @staticmethod
    def _serialize(row) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope": row.scope,
            "project_id": row.project_id,
            "workspace_id": row.workspace_id,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "title": row.title,
            "content": row.content,
            "importance": row.importance,
            "tags": row.tags_json,
            "metadata": row.metadata_json,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }
