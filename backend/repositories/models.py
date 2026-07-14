from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.models import ModelConfigModel
from backend.repositories.base import Repository


class ModelRepository(Repository[ModelConfigModel]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ModelConfigModel)

    def list_enabled(self) -> list[ModelConfigModel]:
        statement = (
            select(ModelConfigModel)
            .where(ModelConfigModel.enabled.is_(True))
            .order_by(ModelConfigModel.priority, ModelConfigModel.display_name)
        )
        return list(self.session.scalars(statement).all())

    def find_by_slug(self, slug: str) -> ModelConfigModel | None:
        statement = select(ModelConfigModel).where(
            ModelConfigModel.slug == slug.strip()
        )
        return self.session.scalar(statement)

    def upsert(self, **data) -> ModelConfigModel:
        row = self.find_by_slug(data["slug"])
        if row is None:
            row = ModelConfigModel(
                provider=data["provider"].strip(),
                slug=data["slug"].strip(),
                display_name=data["display_name"].strip(),
            )
            self.session.add(row)

        for key, value in data.items():
            setattr(row, key, {} if key == "metadata_json" and value is None else value)

        self.session.flush()
        return row
