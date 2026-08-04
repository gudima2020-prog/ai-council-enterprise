from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.code_sandbox.artifact_approvals import (
    RuntimeArtifactApprovalCoordinator,
    build_runtime_artifact_descriptor,
)
from backend.code_sandbox.runtime_policy import RuntimePolicyEvaluation
from backend.core.events import EventBus
from backend.database.base import Base
from backend.database.models import WorkspaceModel
from backend.policy_approvals import PolicyApprovalStatus
from backend.policy_approvals.service import PolicyApprovalService
from backend.runtime_policy import DataClassification, PolicyAction, PolicyOperation


@pytest.fixture
def environment():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )
    with factory() as session:
        for workspace_id in ("workspace_alpha", "workspace_beta"):
            session.add(
                WorkspaceModel(
                    id=workspace_id,
                    name=workspace_id,
                    description="",
                    workspace_type="general",
                    status="active",
                    metadata_json={},
                )
            )
        session.commit()

    @contextmanager
    def scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    event_bus = EventBus()
    yield {
        "engine": engine,
        "scope": scope,
        "event_bus": event_bus,
        "coordinator": RuntimeArtifactApprovalCoordinator(
            session_scope_factory=scope,
            event_bus=event_bus,
        ),
    }
    engine.dispose()


def evaluation() -> RuntimePolicyEvaluation:
    return RuntimePolicyEvaluation(
        policy_version="p2-011.1",
        operation=PolicyOperation.ARTIFACT_EXPORT,
        action=PolicyAction.REQUIRE_APPROVAL,
        reason_codes=("CONFIDENTIAL_ARTIFACT_EXPORT_APPROVAL_REQUIRED",),
        fingerprint="a" * 64,
        data_classification=DataClassification.CONFIDENTIAL,
        runtime_trust=None,
    )


def descriptor(
    *,
    workspace_id: str = "workspace_alpha",
    bundle: bytes = b"immutable-runtime-artifact",
):
    return build_runtime_artifact_descriptor(
        workspace_id=workspace_id,
        run_id="runtime_001",
        session_id="sandbox_001",
        profile="pytest",
        bundle=bundle,
        artifact_paths=(".ai-studio-artifacts/pytest-junit.xml",),
    )


async def approve(environment, approval_id: str) -> str:
    with environment["scope"]() as session:
        grant = await PolicyApprovalService(
            session=session,
            event_bus=environment["event_bus"],
        ).approve(
            approval_id=approval_id,
            workspace_id="workspace_alpha",
            decided_by="operator_alpha",
            note="Approved for exact artifact bundle.",
        )
        return grant.token


@pytest.mark.asyncio
async def test_request_is_idempotent_and_scope_has_no_raw_bundle(environment):
    artifact = descriptor()
    coordinator = environment["coordinator"]
    first = await coordinator.request(
        descriptor=artifact,
        evaluation=evaluation(),
        requested_by="runtime-worker",
    )
    second = await coordinator.request(
        descriptor=artifact,
        evaluation=evaluation(),
        requested_by="runtime-worker",
    )
    assert first.error_code == "POLICY_APPROVAL_REQUIRED"
    assert second.metadata["created"] is False
    assert first.metadata["approval_id"] == second.metadata["approval_id"]

    with environment["scope"]() as session:
        record = PolicyApprovalService(
            session=session,
            event_bus=environment["event_bus"],
        ).get(
            approval_id=first.metadata["approval_id"],
            workspace_id="workspace_alpha",
        )
        assert record.scope.subject_payload["bundle_sha256"] == artifact.bundle_sha256
        assert (
            record.scope.subject_payload["manifest_fingerprint"]
            == artifact.manifest_fingerprint
        )
        assert "immutable-runtime-artifact" not in str(record.to_public_dict())


@pytest.mark.asyncio
async def test_exact_bundle_consumes_once(environment):
    artifact = descriptor()
    coordinator = environment["coordinator"]
    pending = await coordinator.request(
        descriptor=artifact,
        evaluation=evaluation(),
    )
    approval_id = pending.metadata["approval_id"]
    token = await approve(environment, approval_id)

    consumed = await coordinator.consume(
        descriptor=artifact,
        evaluation=evaluation(),
        approval_id=approval_id,
        token=token,
    )
    repeated = await coordinator.consume(
        descriptor=artifact,
        evaluation=evaluation(),
        approval_id=approval_id,
        token=token,
    )
    assert consumed.authorized is True
    assert consumed.metadata["status"] == "consumed"
    assert repeated.error_code == "POLICY_APPROVAL_INVALID_STATE"

    with environment["scope"]() as session:
        record = PolicyApprovalService(
            session=session,
            event_bus=environment["event_bus"],
        ).get(approval_id=approval_id, workspace_id="workspace_alpha")
        assert record.status == PolicyApprovalStatus.CONSUMED


@pytest.mark.asyncio
async def test_changed_bundle_is_scope_mismatch(environment):
    coordinator = environment["coordinator"]
    original = descriptor()
    pending = await coordinator.request(
        descriptor=original,
        evaluation=evaluation(),
    )
    approval_id = pending.metadata["approval_id"]
    token = await approve(environment, approval_id)
    outcome = await coordinator.consume(
        descriptor=descriptor(bundle=b"changed-runtime-artifact"),
        evaluation=evaluation(),
        approval_id=approval_id,
        token=token,
    )
    assert outcome.error_code == "POLICY_APPROVAL_SCOPE_MISMATCH"


@pytest.mark.asyncio
async def test_invalid_token_and_workspace_fail_closed(environment):
    coordinator = environment["coordinator"]
    artifact = descriptor()
    pending = await coordinator.request(
        descriptor=artifact,
        evaluation=evaluation(),
    )
    approval_id = pending.metadata["approval_id"]
    await approve(environment, approval_id)

    invalid = await coordinator.consume(
        descriptor=artifact,
        evaluation=evaluation(),
        approval_id=approval_id,
        token="invalid-token",
    )
    assert invalid.error_code == "POLICY_APPROVAL_TOKEN_INVALID"

    hidden = await coordinator.consume(
        descriptor=descriptor(workspace_id="workspace_beta"),
        evaluation=evaluation(),
        approval_id=approval_id,
        token="invalid-token",
    )
    assert hidden.error_code == "POLICY_APPROVAL_NOT_FOUND"


def test_descriptor_binds_manifest_without_raw_bytes():
    artifact = descriptor()
    assert len(artifact.bundle_sha256) == 64
    assert len(artifact.manifest_fingerprint) == 64
    assert artifact.bundle_size > 0
    assert artifact.artifact_count == 1
    assert "immutable-runtime-artifact" not in str(artifact.subject_payload())
