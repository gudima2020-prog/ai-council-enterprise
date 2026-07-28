from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.code_sandbox.agent import (
    CodeAgentPathError,
    CodeAgentResponseError,
    CodeAgentService,
)
from backend.code_sandbox.schemas import CodeAgentRunRequest, CodeSandboxCreateRequest
from backend.code_sandbox.service import CodeSandboxService
from backend.core.events import EventBus
from backend.council.models import CouncilRunModel
from backend.database.base import Base
from backend.database import models as database_models  # noqa: F401
from backend.gateway.schemas import GatewayResponse, GatewayUsage


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
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


class FakeGateway:
    def __init__(self, payload: dict | str, *, cost: float | None = 0.0123) -> None:
        self.payload = payload
        self.cost = cost
        self.user_prompts: list[str] = []

    async def ask(self, **kwargs: object) -> GatewayResponse:
        self.user_prompts.append(str(kwargs.get("user_prompt") or ""))
        content = self.payload if isinstance(self.payload, str) else json.dumps(self.payload, ensure_ascii=False)
        return GatewayResponse(
            request_id="ai_req_test",
            provider=str(kwargs.get("provider") or "fake"),
            model=str(kwargs.get("model") or "fake-model"),
            content=content,
            status="success",
            usage=GatewayUsage(input_tokens=100, output_tokens=50, total_tokens=150),
            cost=self.cost,
        )


def agent_service(session: Session, gateway: FakeGateway, sandbox: CodeSandboxService) -> CodeAgentService:
    return CodeAgentService(
        session=session,
        event_bus=EventBus(),
        gateway=gateway,  # type: ignore[arg-type]
        sandbox=sandbox,
    )


@pytest.mark.asyncio
async def test_agent_writes_only_worktree_then_inspects_and_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    gateway = FakeGateway(
        {
            "summary": "Update constant",
            "operations": [
                {
                    "action": "write",
                    "path": "app.py",
                    "content": "VALUE = 2\n",
                    "reason": "Requested change",
                }
            ],
        }
    )
    with make_session() as session:
        sandbox = CodeSandboxService(session=session, event_bus=EventBus())
        created = await sandbox.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        service = agent_service(session, gateway, sandbox)
        result = await service.run(
            created.id,
            None,
            CodeAgentRunRequest(
                task="Set VALUE to 2.",
                context_paths=["app.py"],
                writable_paths=["app.py"],
                verification_profiles=["diff_check"],
            ),
        )

        assert result.run.status == "completed"
        assert result.run.actual_cost_usd == pytest.approx(0.0123)
        assert result.run.total_tokens == 150
        assert result.sandbox.verification_status == "passed"
        assert result.sandbox.files_changed == 1
        assert Path(result.sandbox.worktree_path, "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
        assert (repo / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
        assert git(repo, "status", "--porcelain") == ""
        assert result.run.operations[0].path == "app.py"


@pytest.mark.asyncio
async def test_agent_cannot_edit_path_outside_explicit_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    (repo / "other.py").write_text("SAFE = True\n", encoding="utf-8")
    git(repo, "add", "other.py")
    git(repo, "commit", "-m", "other")
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    gateway = FakeGateway(
        {
            "summary": "Try escape",
            "operations": [
                {
                    "action": "write",
                    "path": "other.py",
                    "content": "SAFE = False\n",
                    "reason": "not allowed",
                }
            ],
        }
    )
    with make_session() as session:
        sandbox = CodeSandboxService(session=session, event_bus=EventBus())
        created = await sandbox.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        service = agent_service(session, gateway, sandbox)
        with pytest.raises(CodeAgentResponseError, match="вне allowlist"):
            await service.run(
                created.id,
                None,
                CodeAgentRunRequest(
                    task="Only app.py may change",
                    context_paths=["app.py"],
                    writable_paths=["app.py"],
                ),
            )
        assert Path(created.worktree_path, "other.py").read_text(encoding="utf-8") == "SAFE = True\n"
        rows = service.list_for_session(created.id, None)
        assert rows[0].status == "failed"


@pytest.mark.asyncio
async def test_agent_rejects_path_traversal_before_llm_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    gateway = FakeGateway({"summary": "x", "operations": []})
    with make_session() as session:
        sandbox = CodeSandboxService(session=session, event_bus=EventBus())
        created = await sandbox.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        service = agent_service(session, gateway, sandbox)
        with pytest.raises(CodeAgentPathError, match="Недопустимый"):
            await service.run(
                created.id,
                None,
                CodeAgentRunRequest(task="escape", writable_paths=["../outside.py"]),
            )
        assert gateway.user_prompts == []


@pytest.mark.asyncio
async def test_agent_rejects_real_env_even_when_explicitly_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    gateway = FakeGateway({"summary": "x", "operations": []})
    with make_session() as session:
        sandbox = CodeSandboxService(session=session, event_bus=EventBus())
        created = await sandbox.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        service = agent_service(session, gateway, sandbox)
        with pytest.raises(CodeAgentPathError, match="запрещён политикой"):
            await service.run(
                created.id,
                None,
                CodeAgentRunRequest(task="write secret", writable_paths=[".env"]),
            )
        assert gateway.user_prompts == []


@pytest.mark.asyncio
async def test_agent_malformed_json_fails_without_file_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    gateway = FakeGateway("not json")
    with make_session() as session:
        sandbox = CodeSandboxService(session=session, event_bus=EventBus())
        created = await sandbox.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        service = agent_service(session, gateway, sandbox)
        with pytest.raises(CodeAgentResponseError, match="невалидный JSON"):
            await service.run(
                created.id,
                None,
                CodeAgentRunRequest(task="change", writable_paths=["app.py"]),
            )
        assert Path(created.worktree_path, "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


@pytest.mark.asyncio
async def test_protected_agent_change_is_verified_but_not_auto_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    (repo / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    git(repo, "add", "requirements.txt")
    git(repo, "commit", "-m", "requirements")
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    gateway = FakeGateway(
        {
            "summary": "Add dependency",
            "operations": [
                {
                    "action": "write",
                    "path": "requirements.txt",
                    "content": "pytest\nhttpx\n",
                    "reason": "Needed dependency",
                }
            ],
        }
    )
    with make_session() as session:
        sandbox = CodeSandboxService(session=session, event_bus=EventBus())
        created = await sandbox.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        service = agent_service(session, gateway, sandbox)
        result = await service.run(
            created.id,
            None,
            CodeAgentRunRequest(task="Add httpx", writable_paths=["requirements.txt"]),
        )
        assert result.run.status == "completed"
        assert result.sandbox.approval_required is True
        assert result.sandbox.verification_status == "passed"
        assert "httpx" not in (repo / "requirements.txt").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_council_run_can_be_handed_to_agent_without_persisting_full_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    gateway = FakeGateway(
        {
            "summary": "Council implementation",
            "operations": [
                {
                    "action": "write",
                    "path": "app.py",
                    "content": "VALUE = 7\n",
                    "reason": "Council decision",
                }
            ],
        },
        cost=None,
    )
    with make_session() as session:
        now = datetime.now(timezone.utc)
        session.add(
            CouncilRunModel(
                id="council_handoff",
                status="completed",
                execution_mode="council",
                question="Set value",
                mode="code",
                final_answer="Implement VALUE = 7 in app.py.",
                member_count=2,
                successful_member_count=2,
                duration_ms=10.0,
                error_message="",
                metadata_json={},
                consensus_json=[],
                disagreements_json=[],
                recommendations_json=[],
                started_at=now,
                finished_at=now,
            )
        )
        session.flush()
        sandbox = CodeSandboxService(session=session, event_bus=EventBus())
        created = await sandbox.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        service = agent_service(session, gateway, sandbox)
        result = await service.run(
            created.id,
            None,
            CodeAgentRunRequest(
                council_run_id="council_handoff",
                writable_paths=["app.py"],
            ),
        )
        assert result.run.council_run_id == "council_handoff"
        assert result.run.cost_status == "unknown"
        assert "COUNCIL DECISION TO IMPLEMENT" in gateway.user_prompts[0]
        assert "Implement VALUE = 7" in gateway.user_prompts[0]
        assert result.run.task_sha256
        assert result.run.task_preview == "Council handoff: council_handoff"

@pytest.mark.asyncio
async def test_agent_detects_concurrent_writable_file_change_before_applying_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    with make_session() as session:
        sandbox = CodeSandboxService(session=session, event_bus=EventBus())
        created = await sandbox.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        worktree_file = Path(created.worktree_path, "app.py")

        class RacingGateway(FakeGateway):
            async def ask(self, **kwargs: object) -> GatewayResponse:
                worktree_file.write_text("VALUE = 99\n", encoding="utf-8")
                return await super().ask(**kwargs)

        gateway = RacingGateway(
            {
                "summary": "Change",
                "operations": [
                    {
                        "action": "write",
                        "path": "app.py",
                        "content": "VALUE = 2\n",
                        "reason": "Requested",
                    }
                ],
            }
        )
        service = agent_service(session, gateway, sandbox)
        with pytest.raises(Exception, match="изменились во время работы агента"):
            await service.run(
                created.id,
                None,
                CodeAgentRunRequest(task="Set to 2", writable_paths=["app.py"]),
            )
        assert worktree_file.read_text(encoding="utf-8") == "VALUE = 99\n"

@pytest.mark.asyncio
async def test_agent_exact_once_replace_is_token_efficient_and_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    gateway = FakeGateway(
        {
            "summary": "Use localized replacement",
            "operations": [
                {
                    "action": "replace",
                    "path": "app.py",
                    "old_text": "VALUE = 1",
                    "new_text": "VALUE = 3",
                    "reason": "Localized edit",
                }
            ],
        }
    )
    with make_session() as session:
        sandbox = CodeSandboxService(session=session, event_bus=EventBus())
        created = await sandbox.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        service = agent_service(session, gateway, sandbox)
        result = await service.run(
            created.id,
            None,
            CodeAgentRunRequest(task="Set VALUE to 3", writable_paths=["app.py"]),
        )
        assert result.run.status == "completed"
        assert result.run.operations[0].action == "replace"
        assert Path(created.worktree_path, "app.py").read_text(encoding="utf-8") == "VALUE = 3\n"
        assert (repo / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


@pytest.mark.asyncio
async def test_agent_cannot_modify_gitattributes_before_inspect_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", str(tmp_path))
    gateway = FakeGateway({"summary": "x", "operations": []})
    with make_session() as session:
        sandbox = CodeSandboxService(session=session, event_bus=EventBus())
        created = await sandbox.create(CodeSandboxCreateRequest(repo_path=str(repo)), None)
        service = agent_service(session, gateway, sandbox)
        with pytest.raises(CodeAgentPathError, match="запрещён политикой"):
            await service.run(
                created.id,
                None,
                CodeAgentRunRequest(task="configure filter", writable_paths=[".gitattributes"]),
            )
        assert gateway.user_prompts == []


def test_agent_request_rejects_host_execution_verification_profiles() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CodeAgentRunRequest(
            task="unsafe auto test",
            writable_paths=["app.py"],
            verification_profiles=["pytest"],  # type: ignore[list-item]
        )
