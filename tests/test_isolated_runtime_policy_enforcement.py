from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.code_sandbox.models import (
    CodeSandboxRuntimeRunModel,
)
from backend.code_sandbox.runtime import (
    IsolatedRuntimePolicyDeniedError,
    IsolatedRuntimeService,
)
from backend.code_sandbox.runtime_policy import (
    IsolatedRuntimePolicy,
)
from backend.code_sandbox.schemas import (
    CodeSandboxSession,
    IsolatedRuntimeRunRequest,
)
from backend.core.events import EventBus
from backend.database.base import Base
from backend.database import models as database_models  # noqa: F401
from backend.council import models as council_models  # noqa: F401
from backend.runtime_policy import DataClassification


def make_db_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={
            "check_same_thread": False,
        },
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def make_sandbox_session(
    worktree: Path,
) -> CodeSandboxSession:
    now = datetime.now(timezone.utc)

    return CodeSandboxSession(
        id="sandbox-policy-test",
        workspace_id="workspace-1",
        repo_path=str(worktree),
        worktree_path=str(worktree),
        base_ref="HEAD",
        base_commit="abc123",
        status="inspected",
        risk_level="low",
        approval_required=False,
        verification_status="not_run",
        patch_fingerprint="fingerprint",
        patch_sha256="sha256",
        files_changed=1,
        insertions=1,
        deletions=0,
        changed_paths=["app.py"],
        protected_paths=[],
        blocked_paths=[],
        verification=[],
        created_at=now,
        updated_at=now,
        applied_at=None,
        closed_at=None,
    )


class FakeSandbox:
    def __init__(
        self,
        sandbox_session: CodeSandboxSession,
    ) -> None:
        self.sandbox_session = sandbox_session

    def get(
        self,
        session_id: str,
        workspace_id: str | None,
    ) -> CodeSandboxSession:
        return self.sandbox_session

    def tracked_blocked_paths(
        self,
        worktree: Path,
    ) -> list[str]:
        return []

    async def record_verification_results(
        self,
        session_id: str,
        workspace_id: str | None,
        items,
    ) -> CodeSandboxSession:
        return self.sandbox_session


class RecordingDockerRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(
        self,
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess:
        self.calls.append(list(command))
        args = command[1:]

        if args[:2] == ["image", "inspect"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="sha256:test-image|p2-010\n",
                stderr="",
            )

        if args and args[0] == "create":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="container-id\n",
                stderr="",
            )

        if args[:2] == ["start", "-a"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="ok",
                stderr="",
            )

        return subprocess.CompletedProcess(
            command,
            0,
            stdout="",
            stderr="",
        )


def make_service(
    *,
    session: Session,
    sandbox_session: CodeSandboxSession,
    runtime_policy: IsolatedRuntimePolicy,
    runner: RecordingDockerRunner,
) -> IsolatedRuntimeService:
    service = IsolatedRuntimeService(
        session=session,
        event_bus=EventBus(),
        sandbox=FakeSandbox(
            sandbox_session
        ),
        docker_executable="docker",
        runner=runner,
        runtime_policy=runtime_policy,
    )

    service.status = lambda: SimpleNamespace(
        enabled=True,
        daemon_error="",
    )

    return service


@pytest.mark.asyncio
async def test_policy_resolution_failure_blocks_before_execution(
    tmp_path: Path,
) -> None:
    session = make_db_session()
    runner = RecordingDockerRunner()
    sandbox_session = make_sandbox_session(
        tmp_path
    )

    def failing_resolver(
        workspace_id: str | None,
    ) -> DataClassification:
        raise RuntimeError(
            "Database unavailable"
        )

    policy = IsolatedRuntimePolicy(
        event_bus=EventBus(),
        resolver=failing_resolver,
    )
    service = make_service(
        session=session,
        sandbox_session=sandbox_session,
        runtime_policy=policy,
        runner=runner,
    )

    with pytest.raises(
        IsolatedRuntimePolicyDeniedError
    ):
        await service.run(
            sandbox_session.id,
            sandbox_session.workspace_id,
            IsolatedRuntimeRunRequest(
                profiles=["python_compile"],
                collect_artifacts=False,
            ),
        )

    assert runner.calls == []
    assert (
        session.scalar(
            select(
                CodeSandboxRuntimeRunModel
            )
        )
        is None
    )


@pytest.mark.asyncio
async def test_runtime_policy_snapshot_is_persisted(
    tmp_path: Path,
) -> None:
    session = make_db_session()
    runner = RecordingDockerRunner()
    sandbox_session = make_sandbox_session(
        tmp_path
    )
    policy = IsolatedRuntimePolicy(
        event_bus=EventBus(),
        resolver=lambda workspace_id: (
            DataClassification.INTERNAL
        ),
    )
    service = make_service(
        session=session,
        sandbox_session=sandbox_session,
        runtime_policy=policy,
        runner=runner,
    )

    response = await service.run(
        sandbox_session.id,
        sandbox_session.workspace_id,
        IsolatedRuntimeRunRequest(
            profiles=["python_compile"],
            collect_artifacts=False,
        ),
    )

    assert response.runs[0].status == "passed"

    row = session.scalar(
        select(CodeSandboxRuntimeRunModel)
    )
    assert row is not None

    metadata = row.metadata_json
    assert (
        metadata["runtime_policy"]["action"]
        == "allow"
    )
    assert (
        metadata["runtime_policy"][
            "data_classification"
        ]
        == "internal"
    )
    assert len(
        metadata["runtime_policy"][
            "fingerprint"
        ]
    ) == 64

    commands = [
        call[1]
        for call in runner.calls
        if len(call) > 1
    ]
    assert "create" in commands
    assert "start" in commands


@pytest.mark.asyncio
async def test_restricted_artifact_export_blocks_before_container(
    tmp_path: Path,
) -> None:
    session = make_db_session()
    runner = RecordingDockerRunner()
    sandbox_session = make_sandbox_session(
        tmp_path
    )
    policy = IsolatedRuntimePolicy(
        event_bus=EventBus(),
        resolver=lambda workspace_id: (
            DataClassification.RESTRICTED
        ),
    )
    service = make_service(
        session=session,
        sandbox_session=sandbox_session,
        runtime_policy=policy,
        runner=runner,
    )

    response = await service.run(
        sandbox_session.id,
        sandbox_session.workspace_id,
        IsolatedRuntimeRunRequest(
            profiles=["pytest"],
            collect_artifacts=True,
        ),
    )

    run = response.runs[0]

    assert run.status == "failed"
    assert "RESTRICTED_ARTIFACT_EXPORT_DENIED" in (
        run.output
    )
    assert runner.calls == []

    row = session.scalar(
        select(CodeSandboxRuntimeRunModel)
    )
    assert row is not None
    assert (
        row.metadata_json[
            "artifact_export_policy"
        ]["action"]
        == "deny"
    )
    assert (
        row.metadata_json[
            "artifact_export_policy"
        ]["reason_codes"]
        == [
            "RESTRICTED_ARTIFACT_EXPORT_DENIED"
        ]
    )
