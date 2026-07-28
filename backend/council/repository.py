from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from backend.council.models import (
    CouncilRunMemberModel,
    CouncilRunModel,
)


class CouncilRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(
        self,
        run: CouncilRunModel,
        members: list[CouncilRunMemberModel],
    ) -> CouncilRunModel:
        run.members = members
        self.session.add(run)
        self.session.flush()
        return run

    def get_for_workspace(
        self,
        *,
        run_id: str,
        workspace_id: str | None,
    ) -> CouncilRunModel | None:
        statement = (
            select(CouncilRunModel)
            .options(selectinload(CouncilRunModel.members))
            .where(
                CouncilRunModel.id == run_id,
                self._workspace_condition(workspace_id),
            )
        )
        return self.session.scalar(statement)

    def list_for_workspace(
        self,
        *,
        workspace_id: str | None,
        status: str | None = None,
        mode: str | None = None,
        query: str | None = None,
        started_from: datetime | None = None,
        started_to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CouncilRunModel]:
        statement = select(CouncilRunModel).where(
            self._workspace_condition(workspace_id)
        )
        statement = self._apply_filters(
            statement,
            status=status,
            mode=mode,
            query=query,
            started_from=started_from,
            started_to=started_to,
        )
        statement = statement.order_by(
            CouncilRunModel.started_at.desc(),
            CouncilRunModel.id.desc(),
        ).offset(offset).limit(limit)
        return list(self.session.scalars(statement).all())

    def count_for_workspace(
        self,
        *,
        workspace_id: str | None,
        status: str | None = None,
        mode: str | None = None,
        query: str | None = None,
        started_from: datetime | None = None,
        started_to: datetime | None = None,
    ) -> int:
        statement = select(func.count(CouncilRunModel.id)).where(
            self._workspace_condition(workspace_id)
        )
        statement = self._apply_filters(
            statement,
            status=status,
            mode=mode,
            query=query,
            started_from=started_from,
            started_to=started_to,
        )
        return int(self.session.scalar(statement) or 0)

    def list_expired_for_workspace(
        self,
        *,
        workspace_id: str | None,
        finished_before: datetime,
    ) -> list[CouncilRunModel]:
        statement = (
            select(CouncilRunModel)
            .options(selectinload(CouncilRunModel.members))
            .where(
                self._workspace_condition(workspace_id),
                CouncilRunModel.finished_at < finished_before,
            )
            .order_by(CouncilRunModel.finished_at.asc())
        )
        return list(self.session.scalars(statement).all())

    def delete(self, run: CouncilRunModel) -> None:
        self.session.execute(
            update(CouncilRunModel)
            .where(CouncilRunModel.replay_of_run_id == run.id)
            .values(replay_of_run_id=None)
        )
        self.session.delete(run)
        self.session.flush()

    @staticmethod
    def _workspace_condition(workspace_id: str | None):
        if workspace_id is None:
            return CouncilRunModel.workspace_id.is_(None)
        return CouncilRunModel.workspace_id == workspace_id

    @staticmethod
    def _apply_filters(
        statement,
        *,
        status: str | None,
        mode: str | None,
        query: str | None,
        started_from: datetime | None,
        started_to: datetime | None,
    ):
        if status is not None:
            statement = statement.where(CouncilRunModel.status == status)
        if mode is not None:
            statement = statement.where(CouncilRunModel.mode == mode)
        if query is not None and query.strip():
            statement = statement.where(
                CouncilRunModel.question.ilike(f"%{query.strip()}%")
            )
        if started_from is not None:
            statement = statement.where(
                CouncilRunModel.started_at >= started_from
            )
        if started_to is not None:
            statement = statement.where(
                CouncilRunModel.started_at <= started_to
            )
        return statement
