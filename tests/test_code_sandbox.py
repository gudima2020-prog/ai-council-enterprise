from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.code_sandbox.schemas import CodeSandboxCreateRequest, CodeSandboxVerifyRequest
from backend.code_sandbox.service import (
    CodeSandboxApprovalInvalidError,
    CodeSandboxApprovalRequiredError,
    CodeSandboxConflictError,
    CodeSandboxService,
)
from backend.core.events import EventBus
from backend.database.base import Base
from backend.database import models as database_models  # noqa: F401


def run(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init")
    run(repo, "config", "user.email", "test@example.com")
    run(repo, "config", "user.name", "AI Studio Test")
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    run(repo, "add", "app.py")
    run(repo, "commit", "-m", "initial")
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


@pytest.mark.asyncio
async def test_create_inspect_verify_and_apply_low_risk_patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_repo(tmp_path)
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    with make_session() as session:
        service = CodeSandboxService(session=session, event_bus=EventBus())
        created = await service.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        worktree = Path(created.worktree_path)
        assert worktree.is_dir()
        assert run(repo, "status", "--porcelain") == ""

        (worktree / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        inspected = await service.inspect(created.id, None)
        assert inspected.session.risk_level == "low"
        assert inspected.session.approval_required is False
        assert inspected.session.files_changed == 1
        assert inspected.session.patch_fingerprint
        assert "VALUE = 2" in inspected.patch_preview

        verified = await service.verify(created.id, None, CodeSandboxVerifyRequest(profiles=["diff_check"]))
        assert verified.verification_status == "passed"
        applied = await service.apply(created.id, None, None)
        assert applied.status == "applied"
        assert (repo / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
        assert "app.py" in run(repo, "status", "--porcelain")


@pytest.mark.asyncio
async def test_protected_path_requires_one_time_human_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_repo(tmp_path)
    (repo / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    run(repo, "add", "requirements.txt")
    run(repo, "commit", "-m", "requirements")
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    with make_session() as session:
        service = CodeSandboxService(session=session, event_bus=EventBus())
        created = await service.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        worktree = Path(created.worktree_path)
        (worktree / "requirements.txt").write_text("pytest\nhttpx\n", encoding="utf-8")
        inspected = await service.inspect(created.id, None)
        assert inspected.session.risk_level == "high"
        assert inspected.session.protected_paths == ["requirements.txt"]
        assert inspected.session.approval_required is True
        await service.verify(created.id, None, CodeSandboxVerifyRequest(profiles=["diff_check"]))

        with pytest.raises(CodeSandboxApprovalRequiredError):
            await service.apply(created.id, None, None)

        approval = await service.approve(created.id, None, "Reviewed dependency change")
        applied = await service.apply(created.id, None, approval.token)
        assert applied.status == "applied"
        assert "httpx" in (repo / "requirements.txt").read_text(encoding="utf-8")

        # Approval is one-time and session is terminal after apply.
        with pytest.raises(CodeSandboxConflictError):
            await service.apply(created.id, None, approval.token)


@pytest.mark.asyncio
async def test_blocked_private_key_cannot_be_overridden_by_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_repo(tmp_path)
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    with make_session() as session:
        service = CodeSandboxService(session=session, event_bus=EventBus())
        created = await service.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        worktree = Path(created.worktree_path)
        (worktree / "deploy.key").write_text("not-a-real-key\n", encoding="utf-8")
        inspected = await service.inspect(created.id, None)
        assert inspected.session.risk_level == "critical"
        assert inspected.session.blocked_paths == ["deploy.key"]
        with pytest.raises(CodeSandboxConflictError, match="заблокированные пути"):
            await service.verify(created.id, None, CodeSandboxVerifyRequest(profiles=["diff_check"]))
        with pytest.raises(CodeSandboxConflictError):
            await service.approve(created.id, None, "override")


@pytest.mark.asyncio
async def test_patch_change_after_verification_invalidates_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_repo(tmp_path)
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    with make_session() as session:
        service = CodeSandboxService(session=session, event_bus=EventBus())
        created = await service.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        worktree = Path(created.worktree_path)
        (worktree / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        await service.inspect(created.id, None)
        await service.verify(created.id, None, CodeSandboxVerifyRequest(profiles=["diff_check"]))
        (worktree / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
        with pytest.raises(CodeSandboxConflictError, match="изменился после verification"):
            await service.apply(created.id, None, None)
        assert (repo / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


@pytest.mark.asyncio
async def test_base_repo_head_change_blocks_stale_patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_repo(tmp_path)
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    with make_session() as session:
        service = CodeSandboxService(session=session, event_bus=EventBus())
        created = await service.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        worktree = Path(created.worktree_path)
        (worktree / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        await service.inspect(created.id, None)
        await service.verify(created.id, None, CodeSandboxVerifyRequest(profiles=["diff_check"]))

        (repo / "other.txt").write_text("base moved\n", encoding="utf-8")
        run(repo, "add", "other.txt")
        run(repo, "commit", "-m", "move base")
        with pytest.raises(CodeSandboxConflictError, match="HEAD базового репозитория изменился"):
            await service.apply(created.id, None, None)


@pytest.mark.asyncio
async def test_dirty_base_repo_is_rejected_before_worktree_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_repo(tmp_path)
    (repo / "app.py").write_text("dirty\n", encoding="utf-8")
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    with make_session() as session:
        service = CodeSandboxService(session=session, event_bus=EventBus())
        with pytest.raises(CodeSandboxConflictError, match="незакоммиченные изменения"):
            await service.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)


@pytest.mark.asyncio
async def test_close_removes_worktree_without_touching_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_repo(tmp_path)
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    with make_session() as session:
        service = CodeSandboxService(session=session, event_bus=EventBus())
        created = await service.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        worktree = Path(created.worktree_path)
        assert worktree.exists()
        closed = await service.close(created.id, None)
        assert closed.status == "closed"
        assert not worktree.exists()
        assert (repo / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"

@pytest.mark.asyncio
async def test_repo_outside_configured_allowed_roots_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_repo(tmp_path)
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(allowed))
    with make_session() as session:
        service = CodeSandboxService(session=session, event_bus=EventBus())
        with pytest.raises(Exception, match="вне разрешённых roots"):
            await service.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)


@pytest.mark.asyncio
async def test_host_pytest_execution_is_disabled_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_repo(tmp_path)
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.delenv("AI_STUDIO_CODE_SANDBOX_ALLOW_TEST_EXECUTION", raising=False)
    with make_session() as session:
        service = CodeSandboxService(session=session, event_bus=EventBus())
        created = await service.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        worktree = Path(created.worktree_path)
        (worktree / "app.py").write_text("VALUE = 9\n", encoding="utf-8")
        await service.inspect(created.id, None)
        verified = await service.verify(
            created.id,
            None,
            CodeSandboxVerifyRequest(profiles=["diff_check", "pytest"]),
        )
        assert verified.verification_status == "passed"
        by_profile = {item.profile: item for item in verified.verification}
        assert by_profile["diff_check"].status == "passed"
        assert by_profile["pytest"].status == "skipped"
        assert "AI_STUDIO_CODE_SANDBOX_ALLOW_TEST_EXECUTION=1" in by_profile["pytest"].output

@pytest.mark.asyncio
async def test_real_env_file_is_blocked_but_env_example_is_protected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    # A repository may unfortunately already track a real .env. P2-007 must
    # refuse to move such a change through the patch workflow.
    (repo / ".env").write_text("TOKEN=old-secret\n", encoding="utf-8")
    run(repo, "add", "-f", ".env")
    run(repo, "commit", "-m", "tracked env fixture")
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    with make_session() as session:
        service = CodeSandboxService(session=session, event_bus=EventBus())
        created = await service.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        worktree = Path(created.worktree_path)
        (worktree / ".env").write_text("TOKEN=new-secret\n", encoding="utf-8")
        (worktree / ".env.example").write_text("TOKEN=replace-me\n", encoding="utf-8")
        inspected = await service.inspect(created.id, None)
        assert ".env" in inspected.session.blocked_paths
        assert ".env.example" in inspected.session.protected_paths
        assert inspected.session.risk_level == "critical"


@pytest.mark.asyncio
async def test_git_filter_added_after_create_is_rejected_before_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    with make_session() as session:
        service = CodeSandboxService(session=session, event_bus=EventBus())
        created = await service.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        worktree = Path(created.worktree_path)
        (worktree / "app.py").write_text("VALUE = 4\n", encoding="utf-8")
        run(repo, "config", "filter.evil.clean", "definitely-not-a-command")
        with pytest.raises(Exception, match="clean/smudge/process filters"):
            await service.inspect(created.id, None)


@pytest.mark.asyncio
async def test_diff_external_configuration_is_never_executed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    run(repo, "config", "diff.external", "definitely-not-a-command")
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    with make_session() as session:
        service = CodeSandboxService(session=session, event_bus=EventBus())
        created = await service.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        worktree = Path(created.worktree_path)
        (worktree / "app.py").write_text("VALUE = 5\n", encoding="utf-8")
        await service.inspect(created.id, None)
        verified = await service.verify(
            created.id, None, CodeSandboxVerifyRequest(profiles=["diff_check"])
        )
        assert verified.verification_status == "passed"



@pytest.mark.asyncio
async def test_global_git_filters_do_not_block_clean_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text(
        '[filter "lfs"]\n'
        '    clean = git-lfs clean -- %f\n'
        '    smudge = git-lfs smudge -- %f\n'
        '    process = git-lfs filter-process\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))

    with make_session() as session:
        service = CodeSandboxService(session=session, event_bus=EventBus())
        created = await service.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        assert Path(created.worktree_path).is_dir()
        await service.close(created.id, None)


def test_git_runner_forces_fsmonitor_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        pairs = list(zip(command, command[1:]))
        assert ("-c", "core.fsmonitor=false") in pairs
        return subprocess.CompletedProcess(args=command, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with make_session() as session:
        service = CodeSandboxService(session=session, event_bus=EventBus())
        service._run_git_raw(tmp_path, "status")

def test_git_wrapper_decodes_utf8_unicode_paths_without_windows_ansi_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    unicode_path = "C:\\Users\\Глава\\AppData\\Local\\Temp\\repo\n"

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert kwargs.get("text") is False
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=unicode_path.encode("utf-8"),
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    with make_session() as session:
        service = CodeSandboxService(session=session, event_bus=EventBus())
        result = service._git(tmp_path, "rev-parse", "--show-toplevel")

    assert result.stdout == unicode_path
    assert "Глава" in result.stdout


@pytest.mark.asyncio
async def test_repo_inside_unicode_parent_path_is_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unicode_parent = tmp_path / "Глава"
    unicode_parent.mkdir()
    repo = make_repo(unicode_parent)
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(unicode_parent))

    with make_session() as session:
        service = CodeSandboxService(session=session, event_bus=EventBus())
        created = await service.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        assert Path(created.repo_path).resolve() == repo.resolve()
        assert Path(created.worktree_path).is_dir()
        await service.close(created.id, None)
