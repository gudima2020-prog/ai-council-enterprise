from __future__ import annotations

from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from backend.database.models import SettingModel
from backend.repositories.base import Repository


class SettingsRepository(Repository[SettingModel]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, SettingModel)

    def list_filtered(
        self,
        *,
        scope: str | None = None,
        project_id: str | None = None,
        workspace_id: str | None = None,
    ) -> list[SettingModel]:
        statement = select(SettingModel)

        conditions = []
        if scope is not None:
            conditions.append(SettingModel.scope == scope)
        if project_id is not None:
            conditions.append(SettingModel.project_id == project_id)
        if workspace_id is not None:
            conditions.append(SettingModel.workspace_id == workspace_id)

        if conditions:
            statement = statement.where(and_(*conditions))

        statement = statement.order_by(
            SettingModel.scope,
            SettingModel.key,
        )

        return list(self.session.scalars(statement).all())

    def find_one(
        self,
        *,
        scope: str,
        key: str,
        project_id: str | None = None,
        workspace_id: str | None = None,
    ) -> SettingModel | None:
        statement = select(SettingModel).where(
            SettingModel.scope == scope,
            SettingModel.key == key,
            SettingModel.project_id.is_(project_id)
            if project_id is None
            else SettingModel.project_id == project_id,
            SettingModel.workspace_id.is_(workspace_id)
            if workspace_id is None
            else SettingModel.workspace_id == workspace_id,
        )
        return self.session.scalar(statement)

    def upsert(
        self,
        *,
        scope: str,
        key: str,
        value: Any,
        project_id: str | None = None,
        workspace_id: str | None = None,
    ) -> SettingModel:
        row = self.find_one(
            scope=scope,
            key=key,
            project_id=project_id,
            workspace_id=workspace_id,
        )

        if row is None:
            row = SettingModel(
                scope=scope,
                key=key,
                value_json=value,
                project_id=project_id,
                workspace_id=workspace_id,
            )
            self.session.add(row)
        else:
            row.value_json = value

        self.session.flush()
        return row
