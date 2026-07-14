from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.models import WorkspaceModel
from backend.repositories.base import Repository


class WorkspaceRepository(Repository[WorkspaceModel]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, WorkspaceModel)

    def create(
        self,
        *,
        name: str,
        description: str = "",
        workspace_type: str = "general",
        icon: str | None = None,
        color: str | None = None,
        status: str = "active",
        metadata_json: dict | None = None,
    ) -> WorkspaceModel:
        row = WorkspaceModel(
            name=name.strip(),
            description=description.strip(),
            workspace_type=workspace_type.strip(),
            icon=icon,
            color=color,
            status=status,
            metadata_json=metadata_json or {},
        )
        return self.add(row)

    def find_by_name(self, name: str) -> WorkspaceModel | None:
        statement = select(WorkspaceModel).where(
            WorkspaceModel.name == name.strip()
        )
        return self.session.scalar(statement)

    def list_active(self) -> list[WorkspaceModel]:
        statement = (
            select(WorkspaceModel)
            .where(WorkspaceModel.status == "active")
            .order_by(WorkspaceModel.updated_at.desc())
        )
        return list(self.session.scalars(statement).all())
