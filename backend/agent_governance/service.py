from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agent_governance.core import AgentPolicyProfile
from backend.agent_governance.models import (
    AgentPolicyProfileSelectionModel,
    AgentPolicyProfileVersionModel,
)
from backend.agent_governance.profiles import (
    BUILT_IN_AGENT_PROFILE_IDS,
    agent_tool_capability_catalog,
    built_in_agent_profiles,
)
from backend.agent_governance.repository import (
    AgentProfileRepository,
    AgentProfileVersionConflictError,
)
from backend.database.models import WorkspaceModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AgentProfileServiceError(RuntimeError):
    pass


class AgentProfileNotFoundError(AgentProfileServiceError):
    pass


class AgentProfileWorkspaceError(AgentProfileServiceError):
    pass


class AgentProfileIntegrityError(AgentProfileServiceError):
    pass


class AgentProfileImmutableBuiltInError(AgentProfileServiceError):
    pass


class AgentProfileToolBindingError(AgentProfileServiceError):
    pass


class AgentProfileSelectionNotFoundError(AgentProfileServiceError):
    pass


class AgentProfileFingerprintMismatchError(AgentProfileServiceError):
    pass


@dataclass(frozen=True, slots=True)
class AgentProfileVersionRecord:
    workspace_id: str
    profile: AgentPolicyProfile
    created_by: str | None
    created_at: datetime

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "source": "custom",
            "profile": self.profile.to_dict(),
            "profile_fingerprint": self.profile.fingerprint,
            "created_by": self.created_by,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class AgentProfileSelectionRecord:
    workspace_id: str
    source: str
    profile: AgentPolicyProfile
    updated_by: str | None
    updated_at: datetime

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "source": self.source,
            "profile_id": self.profile.profile_id,
            "version": self.profile.version,
            "profile_fingerprint": self.profile.fingerprint,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
        }


class AgentProfileService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = AgentProfileRepository(session)
        self._built_ins = {
            profile.profile_id: profile for profile in built_in_agent_profiles()
        }
        self._tool_catalog = agent_tool_capability_catalog()

    def create_custom_version(
        self,
        *,
        workspace_id: str,
        profile: AgentPolicyProfile,
        actor_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[AgentProfileVersionRecord, bool]:
        self._require_workspace(workspace_id)
        self._validate_custom_profile(profile)
        row, created = self._repository.create_version(
            workspace_id=workspace_id,
            profile_id=profile.profile_id,
            version=profile.version,
            profile_fingerprint=profile.fingerprint,
            manifest=profile.to_dict(),
            created_by=actor_id,
            created_at=now or utc_now(),
        )
        return self._version_record(row), created

    def get_custom_version(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        version: str,
    ) -> AgentProfileVersionRecord:
        self._require_workspace(workspace_id)
        row = self._repository.find_version(
            workspace_id=workspace_id,
            profile_id=profile_id,
            version=version,
        )
        if row is None:
            raise AgentProfileNotFoundError(
                "Agent profile version was not found in this Workspace."
            )
        return self._version_record(row)

    def list_custom_versions(
        self,
        *,
        workspace_id: str,
        profile_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentProfileVersionRecord]:
        self._require_workspace(workspace_id)
        return [
            self._version_record(row)
            for row in self._repository.list_versions(
                workspace_id=workspace_id,
                profile_id=profile_id,
                limit=max(1, min(int(limit), 500)),
                offset=max(0, int(offset)),
            )
        ]

    def get_profile(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        version: str,
    ) -> tuple[AgentPolicyProfile, str]:
        self._require_workspace(workspace_id)
        built_in = self._built_ins.get(profile_id)
        if built_in is not None:
            if built_in.version != version:
                raise AgentProfileNotFoundError(
                    "Built-in agent profile version was not found."
                )
            return built_in, "built_in"
        record = self.get_custom_version(
            workspace_id=workspace_id,
            profile_id=profile_id,
            version=version,
        )
        return record.profile, "custom"

    def select_active(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        version: str,
        expected_fingerprint: str,
        actor_id: str | None = None,
        now: datetime | None = None,
    ) -> AgentProfileSelectionRecord:
        profile, source = self.get_profile(
            workspace_id=workspace_id,
            profile_id=profile_id,
            version=version,
        )
        if profile.fingerprint != expected_fingerprint.lower():
            raise AgentProfileFingerprintMismatchError(
                "Agent profile fingerprint does not match the selected profile "
                "version."
            )
        row = self._repository.upsert_selection(
            workspace_id=workspace_id,
            profile_id=profile.profile_id,
            version=profile.version,
            profile_fingerprint=profile.fingerprint,
            source=source,
            updated_by=actor_id,
            updated_at=now or utc_now(),
        )
        return self._selection_record(row)

    def get_active(
        self,
        *,
        workspace_id: str,
    ) -> AgentProfileSelectionRecord:
        self._require_workspace(workspace_id)
        row = self._repository.get_selection(workspace_id=workspace_id)
        if row is None:
            raise AgentProfileSelectionNotFoundError(
                "No active agent profile is selected for this Workspace."
            )
        return self._selection_record(row)

    def built_in_profiles(self) -> tuple[AgentPolicyProfile, ...]:
        return tuple(
            self._built_ins[profile_id]
            for profile_id in BUILT_IN_AGENT_PROFILE_IDS
        )

    def _require_workspace(self, workspace_id: str) -> None:
        exists = self._session.scalar(
            select(WorkspaceModel.id).where(WorkspaceModel.id == workspace_id)
        )
        if exists is None:
            raise AgentProfileWorkspaceError("Workspace does not exist.")

    def _validate_custom_profile(self, profile: AgentPolicyProfile) -> None:
        if profile.profile_id in self._built_ins:
            raise AgentProfileImmutableBuiltInError(
                "Built-in agent profiles are immutable."
            )
        for tool_id in profile.allowed_tools:
            canonical = self._tool_catalog.get(tool_id)
            if canonical is None:
                raise AgentProfileToolBindingError(
                    f"Unknown governed tool: {tool_id}."
                )
            if tuple(profile.tool_capabilities[tool_id]) != canonical:
                raise AgentProfileToolBindingError(
                    "Custom profile cannot redefine canonical tool capabilities "
                    f"for {tool_id}."
                )

    def _profile_from_manifest(
        self,
        manifest: Mapping[str, Any],
    ) -> AgentPolicyProfile:
        if not isinstance(manifest, Mapping):
            raise AgentProfileIntegrityError(
                "Persisted agent profile manifest is not an object."
            )
        try:
            profile = AgentPolicyProfile(**dict(manifest))
        except (TypeError, ValueError) as exc:
            raise AgentProfileIntegrityError(
                "Persisted agent profile manifest is invalid."
            ) from exc
        self._validate_custom_profile(profile)
        return profile

    def _version_record(
        self,
        row: AgentPolicyProfileVersionModel,
    ) -> AgentProfileVersionRecord:
        profile = self._profile_from_manifest(dict(row.manifest_json or {}))
        if (
            profile.profile_id != row.profile_id
            or profile.version != row.version
            or profile.fingerprint != row.profile_fingerprint
        ):
            raise AgentProfileIntegrityError(
                "Persisted agent profile version failed integrity validation."
            )
        return AgentProfileVersionRecord(
            workspace_id=row.workspace_id,
            profile=profile,
            created_by=row.created_by,
            created_at=row.created_at,
        )

    def _selection_record(
        self,
        row: AgentPolicyProfileSelectionModel,
    ) -> AgentProfileSelectionRecord:
        profile, source = self.get_profile(
            workspace_id=row.workspace_id,
            profile_id=row.profile_id,
            version=row.version,
        )
        if source != row.source or profile.fingerprint != row.profile_fingerprint:
            raise AgentProfileIntegrityError(
                "Persisted agent profile selection failed integrity validation."
            )
        return AgentProfileSelectionRecord(
            workspace_id=row.workspace_id,
            source=source,
            profile=profile,
            updated_by=row.updated_by,
            updated_at=row.updated_at,
        )


__all__ = [
    "AgentProfileFingerprintMismatchError",
    "AgentProfileImmutableBuiltInError",
    "AgentProfileIntegrityError",
    "AgentProfileNotFoundError",
    "AgentProfileSelectionNotFoundError",
    "AgentProfileSelectionRecord",
    "AgentProfileService",
    "AgentProfileServiceError",
    "AgentProfileToolBindingError",
    "AgentProfileVersionConflictError",
    "AgentProfileVersionRecord",
    "AgentProfileWorkspaceError",
]
