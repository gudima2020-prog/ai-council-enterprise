from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.models import ProjectModel
from backend.repositories.base import Repository


class ProjectRepository(Repository[ProjectModel]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ProjectModel)

    def create(
        self,
        *,
        name: str,
        description: str = "",
        workspace_id: str | None = None,
    ) -> ProjectModel:
        project = ProjectModel(
            name=name.strip(),
            description=description.strip(),
            workspace_id=workspace_id,
        )
        return self.add(project)

    def find_by_name(
        self,
        name: str,
        *,
        workspace_id: str | None = None,
    ) -> ProjectModel | None:
        statement = select(ProjectModel).where(
            ProjectModel.name == name.strip()
        )

        if workspace_id is not None:
            statement = statement.where(
                ProjectModel.workspace_id == workspace_id
            )

        return self.session.scalar(statement)

    def list_by_workspace(
        self,
        workspace_id: str,
        *,
        limit: int = 100,
    ) -> list[ProjectModel]:
        statement = (
            select(ProjectModel)
            .where(ProjectModel.workspace_id == workspace_id)
            .order_by(ProjectModel.updated_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(statement).all())
