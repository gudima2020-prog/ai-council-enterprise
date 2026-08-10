from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.agent_governance import (
    AgentCapability,
    AgentFilesystemAccess,
    AgentNetworkAccess,
    AgentPolicyProfile,
    AgentProfileFingerprintMismatchError,
    AgentProfileImmutableBuiltInError,
    AgentProfileIntegrityError,
    AgentProfileService,
    AgentProfileToolBindingError,
    AgentProfileVersionConflictError,
    agent_tool_capability_catalog,
    built_in_agent_profiles,
)
from backend.agent_governance.models import (
    AgentPolicyProfileSelectionModel,
    AgentPolicyProfileVersionModel,
)
from backend.agent_governance.service import AgentProfileNotFoundError
from backend.database.base import Base
from backend.database.models import WorkspaceModel
from backend.runtime_policy import DataClassification


NOW = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)


def add_workspace(session: Session, workspace_id: str) -> None:
    session.add(
        WorkspaceModel(
            id=workspace_id,
            name=workspace_id,
            description="",
            workspace_type="development",
            status="active",
            metadata_json={},
        )
    )
    session.flush()


@pytest.fixture
def context():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    add_workspace(session, "workspace_alpha")
    add_workspace(session, "workspace_beta")
    service = AgentProfileService(session)
    yield session, service
    session.close()
    engine.dispose()


def custom_profile(
    *,
    profile_id: str = "custom-safe",
    version: str = "1.0.0",
    primary_model: str = "workspace.primary",
) -> AgentPolicyProfile:
    catalog = agent_tool_capability_catalog()
    allowed_tools = ("filesystem.read", "tests.run")
    return AgentPolicyProfile(
        profile_id=profile_id,
        version=version,
        audit_version="2026.08.07",
        allowed_tools=allowed_tools,
        tool_capabilities={
            tool_id: catalog[tool_id]
            for tool_id in allowed_tools
        },
        denied_actions=("git.force_push",),
        mandatory_checks=("policy.runtime", "review.independent"),
        approval_conditions=(AgentCapability.CODE_EXECUTION,),
        external_domains=(),
        allowed_classifications=(
            DataClassification.PUBLIC,
            DataClassification.INTERNAL,
        ),
        primary_model=primary_model,
        reviewer_model="workspace.reviewer",
        network_access=AgentNetworkAccess.DENIED,
        filesystem_access=AgentFilesystemAccess.READ_ONLY,
    )


def test_workspace_foreign_keys_restrict_governance_deletion() -> None:
    version_foreign_keys = list(
        AgentPolicyProfileVersionModel.__table__.foreign_keys
    )
    selection_foreign_keys = list(
        AgentPolicyProfileSelectionModel.__table__.foreign_keys
    )

    assert len(version_foreign_keys) == 1
    assert len(selection_foreign_keys) == 1
    assert version_foreign_keys[0].target_fullname == "workspaces.id"
    assert selection_foreign_keys[0].target_fullname == "workspaces.id"
    assert version_foreign_keys[0].ondelete == "RESTRICT"
    assert selection_foreign_keys[0].ondelete == "RESTRICT"



def test_custom_profile_version_round_trip(context) -> None:
    _, service = context
    profile = custom_profile()
    created_record, created = service.create_custom_version(
        workspace_id="workspace_alpha",
        profile=profile,
        actor_id="operator_alpha",
        now=NOW,
    )
    fetched = service.get_custom_version(
        workspace_id="workspace_alpha",
        profile_id=profile.profile_id,
        version=profile.version,
    )
    assert created is True
    assert created_record.profile == profile
    assert fetched.profile == profile
    assert fetched.profile.fingerprint == profile.fingerprint
    assert fetched.created_by == "operator_alpha"


def test_profile_version_is_idempotent_but_immutable(context) -> None:
    _, service = context
    profile = custom_profile()
    first, first_created = service.create_custom_version(
        workspace_id="workspace_alpha",
        profile=profile,
        actor_id="operator_alpha",
        now=NOW,
    )
    second, second_created = service.create_custom_version(
        workspace_id="workspace_alpha",
        profile=profile,
        actor_id="operator_beta",
        now=NOW,
    )
    assert first_created is True
    assert second_created is False
    assert first.profile.fingerprint == second.profile.fingerprint
    assert second.created_by == "operator_alpha"

    changed = custom_profile(primary_model="workspace.changed")
    with pytest.raises(AgentProfileVersionConflictError):
        service.create_custom_version(
            workspace_id="workspace_alpha",
            profile=changed,
            actor_id="operator_alpha",
            now=NOW,
        )


def test_builtin_ids_are_immutable(context) -> None:
    _, service = context
    with pytest.raises(AgentProfileImmutableBuiltInError):
        service.create_custom_version(
            workspace_id="workspace_alpha",
            profile=built_in_agent_profiles()[0],
            actor_id="operator_alpha",
            now=NOW,
        )


def test_custom_profile_cannot_redefine_tool_capabilities(context) -> None:
    _, service = context
    profile = AgentPolicyProfile(
        profile_id="underdeclared-tool",
        version="1.0.0",
        audit_version="2026.08.07",
        allowed_tools=("git.push",),
        tool_capabilities={"git.push": ()},
        denied_actions=(),
        mandatory_checks=("policy.runtime",),
        approval_conditions=(),
        external_domains=(),
        allowed_classifications=(DataClassification.PUBLIC,),
        primary_model="workspace.primary",
        reviewer_model="workspace.reviewer",
        network_access=AgentNetworkAccess.DENIED,
        filesystem_access=AgentFilesystemAccess.READ_ONLY,
    )
    with pytest.raises(AgentProfileToolBindingError):
        service.create_custom_version(
            workspace_id="workspace_alpha",
            profile=profile,
            actor_id="operator_alpha",
            now=NOW,
        )


def test_workspace_isolation_hides_custom_versions(context) -> None:
    _, service = context
    profile = custom_profile()
    service.create_custom_version(
        workspace_id="workspace_alpha",
        profile=profile,
        actor_id="operator_alpha",
        now=NOW,
    )
    with pytest.raises(AgentProfileNotFoundError):
        service.get_custom_version(
            workspace_id="workspace_beta",
            profile_id=profile.profile_id,
            version=profile.version,
        )


def test_persisted_version_fingerprint_tampering_fails_closed(context) -> None:
    session, service = context
    profile = custom_profile()
    service.create_custom_version(
        workspace_id="workspace_alpha",
        profile=profile,
        actor_id="operator_alpha",
        now=NOW,
    )
    row = session.scalar(
        select(AgentPolicyProfileVersionModel).where(
            AgentPolicyProfileVersionModel.workspace_id == "workspace_alpha"
        )
    )
    assert row is not None
    row.profile_fingerprint = "0" * 64
    session.flush()
    with pytest.raises(AgentProfileIntegrityError):
        service.get_custom_version(
            workspace_id="workspace_alpha",
            profile_id=profile.profile_id,
            version=profile.version,
        )


def test_active_selection_binds_exact_custom_fingerprint(context) -> None:
    _, service = context
    profile = custom_profile()
    service.create_custom_version(
        workspace_id="workspace_alpha",
        profile=profile,
        actor_id="operator_alpha",
        now=NOW,
    )
    selected = service.select_active(
        workspace_id="workspace_alpha",
        profile_id=profile.profile_id,
        version=profile.version,
        expected_fingerprint=profile.fingerprint,
        actor_id="operator_alpha",
        now=NOW,
    )
    active = service.get_active(workspace_id="workspace_alpha")
    assert selected.source == "custom"
    assert active.profile == profile
    assert active.updated_by == "operator_alpha"


def test_active_selection_supports_immutable_builtin(context) -> None:
    _, service = context
    built_in = next(
        p for p in built_in_agent_profiles()
        if p.profile_id == "safe-development"
    )
    selected = service.select_active(
        workspace_id="workspace_alpha",
        profile_id=built_in.profile_id,
        version=built_in.version,
        expected_fingerprint=built_in.fingerprint,
        actor_id="operator_alpha",
        now=NOW,
    )
    assert selected.source == "built_in"
    assert service.get_active(workspace_id="workspace_alpha").profile == built_in


def test_selection_rejects_wrong_expected_fingerprint(context) -> None:
    _, service = context
    profile = custom_profile()
    service.create_custom_version(
        workspace_id="workspace_alpha",
        profile=profile,
        actor_id="operator_alpha",
        now=NOW,
    )
    with pytest.raises(AgentProfileFingerprintMismatchError):
        service.select_active(
            workspace_id="workspace_alpha",
            profile_id=profile.profile_id,
            version=profile.version,
            expected_fingerprint="0" * 64,
            actor_id="operator_alpha",
            now=NOW,
        )


def test_selection_tampering_fails_closed(context) -> None:
    session, service = context
    built_in = next(
        p for p in built_in_agent_profiles()
        if p.profile_id == "safe-development"
    )
    service.select_active(
        workspace_id="workspace_alpha",
        profile_id=built_in.profile_id,
        version=built_in.version,
        expected_fingerprint=built_in.fingerprint,
        actor_id="operator_alpha",
        now=NOW,
    )
    row = session.get(
        AgentPolicyProfileSelectionModel,
        "workspace_alpha",
    )
    assert row is not None
    row.profile_fingerprint = "f" * 64
    session.flush()
    with pytest.raises(AgentProfileIntegrityError):
        service.get_active(workspace_id="workspace_alpha")
