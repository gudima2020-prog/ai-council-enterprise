from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.dependencies import (
    get_isolated_runtime_service,
)
from backend.code_sandbox.artifact_approvals import (
    RuntimeArtifactApprovalCoordinator,
)
from backend.code_sandbox.models import (
    CodeSandboxRuntimeRunModel,
    CodeSandboxSessionModel,
)
from backend.code_sandbox.runtime import (
    IsolatedRuntimeArtifactApprovalRejectedError,
    IsolatedRuntimeArtifactApprovalRequiredError,
    IsolatedRuntimeService,
    RuntimeArtifactExport,
)
from backend.code_sandbox.runtime_policy import (
    RuntimePolicyEvaluation,
)
from backend.core.events import EventBus
from backend.database.base import Base
from backend.database.models import WorkspaceModel
from backend.policy_approvals import PolicyApprovalStatus
from backend.policy_approvals.service import (
    PolicyApprovalService,
)
from backend.routers import code_sandbox
from backend.runtime_policy import (
    DataClassification,
    PolicyAction,
    PolicyOperation,
)


class MutableArtifactPolicy:
    def __init__(self) -> None:
        self.action = PolicyAction.REQUIRE_APPROVAL
        self.fingerprint = "a" * 64
        self.calls = 0

    async def evaluate_artifact_export(
        self,
        *,
        workspace_id: str | None,
    ) -> RuntimePolicyEvaluation:
        self.calls += 1
        del workspace_id
        return RuntimePolicyEvaluation(
            policy_version="p2-011.1",
            operation=PolicyOperation.ARTIFACT_EXPORT,
            action=self.action,
            reason_codes=(
                "CONFIDENTIAL_ARTIFACT_EXPORT_"
                "APPROVAL_REQUIRED",
            ),
            fingerprint=self.fingerprint,
            data_classification=(
                DataClassification.CONFIDENTIAL
            ),
            runtime_trust=None,
        )


@pytest.fixture
def environment(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(
        "AI_STUDIO_ISOLATED_RUNTIME_ROOT",
        str(tmp_path),
    )
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

    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_root / "runtime_001.zip"
    artifact_content = b"immutable-runtime-zip"
    artifact_path.write_bytes(artifact_content)

    with factory() as session:
        session.add(
            WorkspaceModel(
                id="workspace_alpha",
                name="Workspace Alpha",
                description="",
                workspace_type="general",
                status="active",
                metadata_json={},
            )
        )
        session.add(
            CodeSandboxSessionModel(
                id="sandbox_001",
                workspace_id="workspace_alpha",
                repo_path=str(tmp_path),
                worktree_path=str(tmp_path),
                base_ref="HEAD",
                base_commit="abc123",
                status="verified",
                risk_level="low",
                approval_required=False,
                verification_status="passed",
                patch_fingerprint="b" * 64,
                patch_sha256="c" * 64,
                files_changed=1,
                insertions=1,
                deletions=0,
                changed_paths_json=["example.py"],
                protected_paths_json=[],
                blocked_paths_json=[],
                verification_json=[],
                metadata_json={},
            )
        )
        session.add(
            CodeSandboxRuntimeRunModel(
                id="runtime_001",
                session_id="sandbox_001",
                workspace_id="workspace_alpha",
                profile="pytest",
                backend="docker",
                status="passed",
                image="runtime:test",
                image_id="sha256:test",
                network_mode="none",
                cpu_limit=1.0,
                memory_mb=512,
                pids_limit=64,
                timeout_seconds=60,
                exit_code=0,
                duration_ms=10.0,
                timed_out=False,
                output="passed",
                artifact_paths_json=[
                    ".ai-studio-artifacts/"
                    "pytest-junit.xml",
                ],
                artifact_bytes=len(artifact_content),
                metadata_json={
                    "artifact_zip_path": str(
                        artifact_path
                    ),
                },
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
    policy = MutableArtifactPolicy()
    service_session = factory()
    service = IsolatedRuntimeService(
        session=service_session,
        event_bus=event_bus,
        sandbox=SimpleNamespace(),
        runtime_policy=policy,
        artifact_approval_coordinator=(
            RuntimeArtifactApprovalCoordinator(
                session_scope_factory=scope,
                event_bus=event_bus,
            )
        ),
    )

    yield {
        "engine": engine,
        "scope": scope,
        "event_bus": event_bus,
        "policy": policy,
        "service": service,
        "service_session": service_session,
        "artifact_path": artifact_path,
        "artifact_content": artifact_content,
    }

    service_session.close()
    engine.dispose()


async def approve(environment, approval_id: str) -> str:
    with environment["scope"]() as session:
        grant = await PolicyApprovalService(
            session=session,
            event_bus=environment["event_bus"],
        ).approve(
            approval_id=approval_id,
            workspace_id="workspace_alpha",
            decided_by="operator_alpha",
            note="Approved for exact runtime ZIP.",
        )
        return grant.token


@pytest.mark.asyncio
async def test_download_requires_then_consumes_exact_zip(
    environment,
) -> None:
    service = environment["service"]

    with pytest.raises(
        IsolatedRuntimeArtifactApprovalRequiredError
    ) as pending:
        await service.artifact_zip_bytes(
            "runtime_001",
            "workspace_alpha",
        )

    approval_id = pending.value.metadata["approval_id"]
    token = await approve(environment, approval_id)

    export = await service.artifact_zip_bytes(
        "runtime_001",
        "workspace_alpha",
        approval_id=approval_id,
        approval_token=token,
    )

    assert export.content == environment["artifact_content"]
    assert (
        export.metadata["policy_approval"]["status"]
        == "consumed"
    )
    assert token not in str(export.metadata)

    with environment["scope"]() as session:
        record = PolicyApprovalService(
            session=session,
            event_bus=environment["event_bus"],
        ).get(
            approval_id=approval_id,
            workspace_id="workspace_alpha",
        )
        assert record.status == PolicyApprovalStatus.CONSUMED


@pytest.mark.asyncio
async def test_invalid_reused_and_changed_fail_closed(
    environment,
) -> None:
    service = environment["service"]

    with pytest.raises(
        IsolatedRuntimeArtifactApprovalRequiredError
    ) as pending:
        await service.artifact_zip_bytes(
            "runtime_001",
            "workspace_alpha",
        )
    approval_id = pending.value.metadata["approval_id"]
    token = await approve(environment, approval_id)

    with pytest.raises(
        IsolatedRuntimeArtifactApprovalRejectedError
    ) as invalid:
        await service.artifact_zip_bytes(
            "runtime_001",
            "workspace_alpha",
            approval_id=approval_id,
            approval_token="invalid-token",
        )
    assert invalid.value.code == "POLICY_APPROVAL_TOKEN_INVALID"

    first = await service.artifact_zip_bytes(
        "runtime_001",
        "workspace_alpha",
        approval_id=approval_id,
        approval_token=token,
    )
    assert first.content

    with pytest.raises(
        IsolatedRuntimeArtifactApprovalRejectedError
    ) as reused:
        await service.artifact_zip_bytes(
            "runtime_001",
            "workspace_alpha",
            approval_id=approval_id,
            approval_token=token,
        )
    assert reused.value.code == "POLICY_APPROVAL_INVALID_STATE"


@pytest.mark.asyncio
async def test_changed_zip_cannot_reuse_approval(
    environment,
) -> None:
    service = environment["service"]

    with pytest.raises(
        IsolatedRuntimeArtifactApprovalRequiredError
    ) as pending:
        await service.artifact_zip_bytes(
            "runtime_001",
            "workspace_alpha",
        )
    approval_id = pending.value.metadata["approval_id"]
    token = await approve(environment, approval_id)

    environment["artifact_path"].write_bytes(
        b"changed-runtime-zip"
    )

    with pytest.raises(
        IsolatedRuntimeArtifactApprovalRejectedError
    ) as changed:
        await service.artifact_zip_bytes(
            "runtime_001",
            "workspace_alpha",
            approval_id=approval_id,
            approval_token=token,
        )
    assert changed.value.code == "POLICY_APPROVAL_SCOPE_MISMATCH"


@pytest.mark.asyncio
async def test_policy_is_reevaluated_before_consume(
    environment,
) -> None:
    service = environment["service"]
    policy = environment["policy"]

    with pytest.raises(
        IsolatedRuntimeArtifactApprovalRequiredError
    ) as pending:
        await service.artifact_zip_bytes(
            "runtime_001",
            "workspace_alpha",
        )
    approval_id = pending.value.metadata["approval_id"]
    token = await approve(environment, approval_id)

    policy.action = PolicyAction.DENY

    with pytest.raises(Exception) as denied:
        await service.artifact_zip_bytes(
            "runtime_001",
            "workspace_alpha",
            approval_id=approval_id,
            approval_token=token,
        )
    assert "Policy denied" in str(denied.value)

    with environment["scope"]() as session:
        record = PolicyApprovalService(
            session=session,
            event_bus=environment["event_bus"],
        ).get(
            approval_id=approval_id,
            workspace_id="workspace_alpha",
        )
        assert record.status == PolicyApprovalStatus.APPROVED


@pytest.mark.asyncio
async def test_post_consume_change_releases_no_bytes(
    environment,
    monkeypatch,
) -> None:
    service = environment["service"]

    with pytest.raises(
        IsolatedRuntimeArtifactApprovalRequiredError
    ) as pending:
        await service.artifact_zip_bytes(
            "runtime_001",
            "workspace_alpha",
        )
    approval_id = pending.value.metadata["approval_id"]
    token = await approve(environment, approval_id)

    reads = iter(
        [
            environment["artifact_content"],
            b"changed-after-consume",
        ]
    )
    monkeypatch.setattr(
        service,
        "_read_artifact_bundle",
        lambda path: next(reads),
    )

    with pytest.raises(
        IsolatedRuntimeArtifactApprovalRejectedError
    ) as changed:
        await service.artifact_zip_bytes(
            "runtime_001",
            "workspace_alpha",
            approval_id=approval_id,
            approval_token=token,
        )
    assert changed.value.code == "RUNTIME_ARTIFACT_CHANGED"


def test_download_endpoint_uses_secret_headers() -> None:
    received: dict[str, str | None] = {}

    class FakeService:
        async def artifact_zip_bytes(
            self,
            run_id,
            workspace_id,
            *,
            approval_id=None,
            approval_token=None,
            requested_by=None,
        ):
            received.update(
                {
                    "run_id": run_id,
                    "workspace_id": workspace_id,
                    "approval_id": approval_id,
                    "approval_token": approval_token,
                    "requested_by": requested_by,
                }
            )
            return RuntimeArtifactExport(
                content=b"zip-bytes",
                metadata={
                    "policy_action": "require_approval",
                },
            )

    app = FastAPI()

    @app.middleware("http")
    async def workspace_context(
        request: Request,
        call_next,
    ):
        request.state.workspace_context = {
            "workspace_id": "workspace_alpha",
        }
        return await call_next(request)

    app.include_router(code_sandbox.router, prefix="/api")
    app.dependency_overrides[
        get_isolated_runtime_service
    ] = lambda: FakeService()

    with TestClient(app) as client:
        response = client.get(
            "/api/code-sandbox/runtime-runs/"
            "runtime_001/artifacts",
            headers={
                "X-Policy-Approval-ID": "approval_001",
                "X-Policy-Approval-Token": "secret-token",
            },
        )

    assert response.status_code == 200
    assert response.content == b"zip-bytes"
    assert received["workspace_id"] == "workspace_alpha"
    assert received["approval_id"] == "approval_001"
    assert received["approval_token"] == "secret-token"
    assert "secret-token" not in str(response.headers)
    assert "secret-token" not in response.text


def test_download_endpoint_returns_safe_pending() -> None:
    class PendingService:
        async def artifact_zip_bytes(self, *args, **kwargs):
            raise (
                IsolatedRuntimeArtifactApprovalRequiredError(
                    "Approval required.",
                    metadata={
                        "approval_id": "approval_001",
                        "status": "pending",
                        "run_id": "runtime_001",
                    },
                )
            )

    app = FastAPI()

    @app.middleware("http")
    async def workspace_context(
        request: Request,
        call_next,
    ):
        request.state.workspace_context = {
            "workspace_id": "workspace_alpha",
        }
        return await call_next(request)

    app.include_router(code_sandbox.router, prefix="/api")
    app.dependency_overrides[
        get_isolated_runtime_service
    ] = lambda: PendingService()

    with TestClient(app) as client:
        response = client.get(
            "/api/code-sandbox/runtime-runs/"
            "runtime_001/artifacts"
        )

    assert response.status_code == 202
    detail = response.json()["detail"]
    assert detail["code"] == "POLICY_APPROVAL_REQUIRED"
    assert (
        detail["policy_approval"]["approval_id"]
        == "approval_001"
    )
