from __future__ import annotations

from pathlib import Path
import subprocess
import shutil

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.code_sandbox.runtime import IsolatedRuntimeService
from backend.code_sandbox.schemas import CodeSandboxCreateRequest, IsolatedRuntimeRunRequest
from pydantic import ValidationError
from backend.code_sandbox.service import CodeSandboxConflictError, CodeSandboxService
from backend.core.events import EventBus
from backend.database.base import Base
from backend.database import models as database_models  # noqa: F401
from backend.council import models as council_models  # noqa: F401


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "AI Studio Test")
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(repo, "add", "app.py")
    git(repo, "commit", "-m", "initial")
    return repo


def make_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


class FakeDockerRunner:
    def __init__(self, *, start_rc: int = 0, start_output: str = "ok", timeout_start: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.start_rc = start_rc
        self.start_output = start_output
        self.timeout_start = timeout_start

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        self.calls.append(list(command))
        args = command[1:]
        if args[:2] == ["version", "--format"]:
            return subprocess.CompletedProcess(command, 0, stdout="27.0.0\n", stderr="")
        if args[:2] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout="sha256:test-image|p2-010\n", stderr="")
        if args and args[0] == "create":
            return subprocess.CompletedProcess(command, 0, stdout="container-id\n", stderr="")
        if args[:2] == ["start", "-a"]:
            if self.timeout_start:
                raise subprocess.TimeoutExpired(command, timeout=int(kwargs.get("timeout") or 1), output="partial", stderr="")
            return subprocess.CompletedProcess(command, self.start_rc, stdout=self.start_output, stderr="")
        if args and args[0] in {"kill", "rm"}:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if args and args[0] == "cp":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="missing")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


class UntrustedImageRunner(FakeDockerRunner):
    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        self.calls.append(list(command))
        args = command[1:]
        if args[:2] == ["version", "--format"]:
            return subprocess.CompletedProcess(command, 0, stdout="27.0.0\n", stderr="")
        if args[:2] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout="sha256:untrusted|other-label\n", stderr="")
        if args and args[0] == "create":
            raise AssertionError("untrusted runtime image must be rejected before docker create")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


async def inspected_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: Session):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    sandbox = CodeSandboxService(session=session, event_bus=EventBus())
    created = await sandbox.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
    Path(created.worktree_path, "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    inspected = await sandbox.inspect(created.id, None)
    return repo, sandbox, inspected.session


def patch_docker_available(monkeypatch: pytest.MonkeyPatch) -> None:
    real_which = shutil.which
    monkeypatch.setattr("backend.code_sandbox.runtime.shutil.which", lambda name: "docker" if name == "docker" else real_which(name))
    monkeypatch.setenv("AI_STUDIO_ISOLATED_RUNTIME_ENABLED", "1")


@pytest.mark.asyncio
async def test_runtime_uses_read_only_source_and_hardened_container_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch_docker_available(monkeypatch)
    runner = FakeDockerRunner()
    with make_session() as db:
        repo, sandbox, created = await inspected_session(tmp_path, monkeypatch, db)
        service = IsolatedRuntimeService(session=db, event_bus=EventBus(), sandbox=sandbox, docker_executable="docker", runner=runner)
        response = await service.run(
            created.id,
            None,
            IsolatedRuntimeRunRequest(profiles=["python_compile"], collect_artifacts=False),
        )
        assert response.session.verification_status == "passed"
        assert response.runs[0].status == "passed"
        create = next(call for call in runner.calls if len(call) > 1 and call[1] == "create")
        joined = " ".join(create)
        assert "--network=none" in create
        assert "--ipc=none" in create
        assert "--read-only" in create
        assert "--cap-drop=ALL" in create
        assert "no-new-privileges=true" in create
        assert "seccomp=builtin" in create
        assert "sha256:test-image" in create
        assert "--pull=never" in create
        assert "--entrypoint /bin/sh" in joined
        assert "dst=/input,readonly" in joined
        assert "--privileged" not in create
        assert str(repo) not in joined  # only isolated worktree is mounted


@pytest.mark.asyncio
async def test_runtime_timeout_kills_container_and_fails_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch_docker_available(monkeypatch)
    runner = FakeDockerRunner(timeout_start=True)
    with make_session() as db:
        _repo, sandbox, created = await inspected_session(tmp_path, monkeypatch, db)
        service = IsolatedRuntimeService(session=db, event_bus=EventBus(), sandbox=sandbox, docker_executable="docker", runner=runner)
        response = await service.run(created.id, None, IsolatedRuntimeRunRequest(profiles=["python_compile"], timeout_seconds=5, collect_artifacts=False))
        assert response.runs[0].status == "timeout"
        assert response.runs[0].timed_out is True
        assert response.session.verification_status == "failed"
        assert any(call[1] == "kill" for call in runner.calls if len(call) > 1)
        assert any(call[1] == "rm" for call in runner.calls if len(call) > 1)


@pytest.mark.asyncio
async def test_runtime_rejects_tracked_env_before_docker_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch_docker_available(monkeypatch)
    runner = FakeDockerRunner()
    with make_session() as db:
        repo = make_repo(tmp_path)
        (repo / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
        git(repo, "add", "-f", ".env")
        git(repo, "commit", "-m", "tracked secret fixture")
        monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
        monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
        sandbox = CodeSandboxService(session=db, event_bus=EventBus())
        created = await sandbox.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        Path(created.worktree_path, "app.py").write_text("VALUE = 3\n", encoding="utf-8")
        await sandbox.inspect(created.id, None)
        service = IsolatedRuntimeService(session=db, event_bus=EventBus(), sandbox=sandbox, docker_executable="docker", runner=runner)
        with pytest.raises(CodeSandboxConflictError, match="tracked sensitive paths"):
            await service.run(created.id, None, IsolatedRuntimeRunRequest(profiles=["python_compile"], collect_artifacts=False))
        assert not any(call[1] == "create" for call in runner.calls if len(call) > 1)


def test_runtime_status_reports_images_and_security_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_docker_available(monkeypatch)
    runner = FakeDockerRunner()
    with make_session() as db:
        sandbox = CodeSandboxService(session=db, event_bus=EventBus())
        service = IsolatedRuntimeService(session=db, event_bus=EventBus(), sandbox=sandbox, docker_executable="docker", runner=runner)
        status = service.status()
        assert status.enabled is True
        assert status.docker_daemon_available is True
        assert status.network_mode == "none"
        assert status.root_filesystem_read_only is True
        assert status.source_mount_read_only is True
        assert status.no_new_privileges is True
        assert all(item.present for item in status.images)
        assert all(item.trusted for item in status.images)


def test_artifact_scanner_rejects_symlink(tmp_path: Path) -> None:
    with make_session() as db:
        sandbox = CodeSandboxService(session=db, event_bus=EventBus())
        service = IsolatedRuntimeService(session=db, event_bus=EventBus(), sandbox=sandbox)
        target = tmp_path / "target.txt"
        target.write_text("x", encoding="utf-8")
        link = tmp_path / "link.txt"
        try:
            link.symlink_to(target)
        except OSError:
            pytest.skip("symlink unavailable on this platform")
        with pytest.raises(Exception, match="Symlink"):
            service._scan_artifact(link)


@pytest.mark.asyncio
async def test_untrusted_runtime_image_is_rejected_before_container_create(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch_docker_available(monkeypatch)
    runner = UntrustedImageRunner()
    with make_session() as db:
        _repo, sandbox, created = await inspected_session(tmp_path, monkeypatch, db)
        service = IsolatedRuntimeService(session=db, event_bus=EventBus(), sandbox=sandbox, docker_executable="docker", runner=runner)
        response = await service.run(created.id, None, IsolatedRuntimeRunRequest(profiles=["python_compile"], collect_artifacts=False))
        assert response.runs[0].status == "failed"
        assert "доверенной метки" in response.runs[0].output
        assert response.session.verification_status == "failed"
        assert not any(call[1] == "create" for call in runner.calls if len(call) > 1)


def test_runtime_request_rejects_arbitrary_command_profile() -> None:
    with pytest.raises(ValidationError):
        IsolatedRuntimeRunRequest(profiles=["shell"])  # type: ignore[list-item]
