from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agent_governance.models import (
    AgentPolicyProfileSelectionModel,
    AgentPolicyProfileVersionModel,
)


class AgentProfileRepositoryError(RuntimeError):
    pass


class AgentProfileVersionConflictError(AgentProfileRepositoryError):
    pass


class AgentProfileRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_version(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        version: str,
    ) -> AgentPolicyProfileVersionModel | None:
        return self._session.scalar(
            select(AgentPolicyProfileVersionModel).where(
                AgentPolicyProfileVersionModel.workspace_id == workspace_id,
                AgentPolicyProfileVersionModel.profile_id == profile_id,
                AgentPolicyProfileVersionModel.version == version,
            )
        )

    def create_version(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        version: str,
        profile_fingerprint: str,
        manifest: dict,
        created_by: str | None,
        created_at: datetime,
    ) -> tuple[AgentPolicyProfileVersionModel, bool]:
        existing = self.find_version(
            workspace_id=workspace_id,
            profile_id=profile_id,
            version=version,
        )
        if existing is not None:
            if (
                existing.profile_fingerprint != profile_fingerprint
                or dict(existing.manifest_json or {}) != manifest
            ):
                raise AgentProfileVersionConflictError(
                    "Agent profile version is immutable and already exists "
                    "with different content."
                )
            return existing, False

        row = AgentPolicyProfileVersionModel(
            workspace_id=workspace_id,
            profile_id=profile_id,
            version=version,
            profile_fingerprint=profile_fingerprint,
            manifest_json=dict(manifest),
            created_by=created_by,
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row, True

    def list_versions(
        self,
        *,
        workspace_id: str,
        profile_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentPolicyProfileVersionModel]:
        query = select(AgentPolicyProfileVersionModel).where(
            AgentPolicyProfileVersionModel.workspace_id == workspace_id
        )
        if profile_id is not None:
            query = query.where(
                AgentPolicyProfileVersionModel.profile_id == profile_id
            )
        query = (
            query.order_by(
                AgentPolicyProfileVersionModel.profile_id.asc(),
                AgentPolicyProfileVersionModel.created_at.desc(),
                AgentPolicyProfileVersionModel.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        return list(self._session.scalars(query).all())

    def get_selection(
        self,
        *,
        workspace_id: str,
    ) -> AgentPolicyProfileSelectionModel | None:
        return self._session.get(AgentPolicyProfileSelectionModel, workspace_id)

    def upsert_selection(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        version: str,
        profile_fingerprint: str,
        source: str,
        updated_by: str | None,
        updated_at: datetime,
    ) -> AgentPolicyProfileSelectionModel:
        row = self.get_selection(workspace_id=workspace_id)
        if row is None:
            row = AgentPolicyProfileSelectionModel(
                workspace_id=workspace_id,
                profile_id=profile_id,
                version=version,
                profile_fingerprint=profile_fingerprint,
                source=source,
                updated_by=updated_by,
                updated_at=updated_at,
            )
            self._session.add(row)
        else:
            row.profile_id = profile_id
            row.version = version
            row.profile_fingerprint = profile_fingerprint
            row.source = source
            row.updated_by = updated_by
            row.updated_at = updated_at

        self._session.flush()
        return row
