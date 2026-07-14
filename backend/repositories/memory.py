from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from backend.database.models import MemoryItemModel
from backend.repositories.base import Repository


class MemoryRepository(Repository[MemoryItemModel]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, MemoryItemModel)

    def create(
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
        metadata_json: dict | None = None,
    ) -> MemoryItemModel:
        row = MemoryItemModel(
            scope=scope,
            title=title.strip(),
            content=content.strip(),
            source_type=source_type.strip(),
            project_id=project_id,
            workspace_id=workspace_id,
            source_id=source_id,
            importance=importance,
            tags_json=tags or [],
            metadata_json=metadata_json or {},
        )
        return self.add(row)

    def list_filtered(
        self,
        *,
        scope: str | None = None,
        project_id: str | None = None,
        workspace_id: str | None = None,
        source_type: str | None = None,
        limit: int = 100,
    ) -> list[MemoryItemModel]:
        statement = select(MemoryItemModel)

        conditions = []

        if scope is not None:
            conditions.append(MemoryItemModel.scope == scope)

        if project_id is not None:
            conditions.append(MemoryItemModel.project_id == project_id)

        if workspace_id is not None:
            conditions.append(MemoryItemModel.workspace_id == workspace_id)

        if source_type is not None:
            conditions.append(MemoryItemModel.source_type == source_type)

        if conditions:
            statement = statement.where(and_(*conditions))

        statement = statement.order_by(
            MemoryItemModel.importance.desc(),
            MemoryItemModel.updated_at.desc(),
        ).limit(limit)

        return list(self.session.scalars(statement).all())

    def search(
        self,
        *,
        query: str,
        scope: str | None = None,
        project_id: str | None = None,
        workspace_id: str | None = None,
        limit: int = 20,
    ) -> list[MemoryItemModel]:
        term = f"%{query.strip()}%"

        conditions = [
            or_(
                MemoryItemModel.title.ilike(term),
                MemoryItemModel.content.ilike(term),
            )
        ]

        if scope is not None:
            conditions.append(MemoryItemModel.scope == scope)

        if project_id is not None:
            conditions.append(MemoryItemModel.project_id == project_id)

        if workspace_id is not None:
            conditions.append(MemoryItemModel.workspace_id == workspace_id)

        statement = (
            select(MemoryItemModel)
            .where(and_(*conditions))
            .order_by(
                MemoryItemModel.importance.desc(),
                MemoryItemModel.updated_at.desc(),
            )
            .limit(limit)
        )

        return list(self.session.scalars(statement).all())
