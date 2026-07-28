from __future__ import annotations

from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
import hashlib
import locale
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
from time import perf_counter
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.code_sandbox.models import CodeSandboxApprovalModel, CodeSandboxSessionModel, new_id, utc_now
from backend.code_sandbox.schemas import (
    CodeSandboxApproval,
    CodeSandboxCreateRequest,
    CodeSandboxInspectResponse,
    CodeSandboxSession,
    CodeSandboxVerificationItem,
    CodeSandboxVerifyRequest,
)
from backend.core.events import Event, EventBus


class CodeSandboxError(RuntimeError):
    pass


class CodeSandboxConflictError(CodeSandboxError):
    pass


class CodeSandboxApprovalRequiredError(CodeSandboxError):
    pass


class CodeSandboxApprovalInvalidError(CodeSandboxError):
    pass


BLOCKED_PATTERNS = (
    ".git", ".git/*", ".git/**", ".venv/*", ".venv/**", "node_modules/*", "node_modules/**",
    ".env", "**/.env", ".env.local", "**/.env.local", ".env.production", "**/.env.production",
    ".env.development", "**/.env.development", ".env.test", "**/.env.test",
    ".npmrc", "**/.npmrc", ".pypirc", "**/.pypirc", ".ssh/*", ".ssh/**", "**/.ssh/*", "**/.ssh/**",
    "credentials.json", "**/credentials.json", "service-account*.json", "**/service-account*.json",
    "service_account*.json", "**/service_account*.json",
    "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "id_rsa", "id_ed25519",
    "data/*.db", "data/**/*.db", "*.sqlite", "*.sqlite3",
)
PROTECTED_PATTERNS = (
    ".env.example", "**/.env.example", ".env.sample", "**/.env.sample", ".env.template", "**/.env.template",
    "*secret*", "**/*secret*", "*credential*", "**/*credential*",
    ".gitattributes", "**/.gitattributes", ".gitmodules", "**/.gitmodules", ".husky/*", ".husky/**", "**/.husky/*", "**/.husky/**",
    "alembic/versions/*", "alembic/versions/**", "backend/database/*", "backend/database/**",
    "backend/control_center/*", "backend/control_center/**", "backend/secrets/*", "backend/secrets/**",
    ".github/workflows/*", ".github/workflows/**", "requirements*.txt", "package.json", "**/package.json",
    "package-lock.json", "**/package-lock.json", "pyproject.toml", "Dockerfile",
    "docker-compose*.yml", "docker-compose*.yaml",
)


class CodeSandboxService:
    PATCH_PREVIEW_LIMIT = 20_000
    PATCH_MAX_BYTES = 10 * 1024 * 1024
    APPROVAL_TTL_MINUTES = 15

    def __init__(self, *, session: Session, event_bus: EventBus) -> None:
        self._session = session
        self._event_bus = event_bus
        root = os.getenv("AI_STUDIO_CODE_SANDBOX_ROOT", "").strip()
        self._sandbox_root = Path(root).expanduser().resolve() if root else Path(tempfile.gettempdir()).resolve() / "ai_studio_code_sandbox"
        self._worktrees_root = self._sandbox_root / "worktrees"
        self._patches_root = self._sandbox_root / "patches"
        self._worktrees_root.mkdir(parents=True, exist_ok=True)
        self._patches_root.mkdir(parents=True, exist_ok=True)
        self._hooks_root = self._sandbox_root / "disabled-hooks"
        self._hooks_root.mkdir(parents=True, exist_ok=True)
        self._project_root = Path(__file__).resolve().parents[2]
        self._allowed_roots = self._load_allowed_roots()

    @staticmethod
    def git_available() -> bool:
        return shutil.which("git") is not None

    async def create(self, request: CodeSandboxCreateRequest, workspace_id: str | None) -> CodeSandboxSession:
        if not self.git_available():
            raise CodeSandboxError("Git не найден в PATH.")
        repo = self._resolve_repo(request.repo_path)
        if self._is_within(self._sandbox_root, repo):
            raise CodeSandboxError("AI_STUDIO_CODE_SANDBOX_ROOT не должен находиться внутри целевого репозитория.")
        self._ensure_safe_git_config(repo)
        self._ensure_clean(repo)
        base_commit = self._git(repo, "rev-parse", "--verify", f"{request.base_ref}^{{commit}}").stdout.strip()
        session_id = new_id("sandbox")
        worktree = (self._worktrees_root / session_id).resolve()
        if worktree.exists():
            raise CodeSandboxConflictError("Путь worktree уже существует.")
        self._git(repo, "worktree", "add", "--detach", str(worktree), base_commit)
        row = CodeSandboxSessionModel(
            id=session_id,
            workspace_id=workspace_id,
            repo_path=str(repo),
            worktree_path=str(worktree),
            base_ref=request.base_ref,
            base_commit=base_commit,
        )
        self._session.add(row)
        self._session.flush()
        await self._event_bus.publish(Event(event_type="code_sandbox.session.created", source="code_sandbox", payload={"session_id": row.id, "workspace_id": workspace_id, "base_commit": base_commit}))
        return self._to_schema(row)

    def get(self, session_id: str, workspace_id: str | None) -> CodeSandboxSession | None:
        row = self._get_row(session_id, workspace_id)
        return self._to_schema(row) if row else None

    def list(self, workspace_id: str | None, limit: int = 50) -> list[CodeSandboxSession]:
        stmt = select(CodeSandboxSessionModel).order_by(CodeSandboxSessionModel.created_at.desc()).limit(limit)
        if workspace_id is None:
            stmt = stmt.where(CodeSandboxSessionModel.workspace_id.is_(None))
        else:
            stmt = stmt.where(CodeSandboxSessionModel.workspace_id == workspace_id)
        return [self._to_schema(row) for row in self._session.scalars(stmt).all()]

    async def inspect(self, session_id: str, workspace_id: str | None) -> CodeSandboxInspectResponse:
        row = self._require_row(session_id, workspace_id)
        self._require_open(row)
        worktree = Path(row.worktree_path)
        if not worktree.exists():
            raise CodeSandboxConflictError("Worktree отсутствует на диске.")

        # Git configuration can change after session creation. Re-check immediately
        # before staging because clean/smudge/process filters may execute commands.
        self._ensure_safe_git_config(worktree)
        # Staging happens only inside the isolated worktree and lets one diff include new/deleted files.
        self._git(worktree, "add", "-A")
        patch = self._git_bytes(worktree, "diff", "--cached", "--binary", "--no-ext-diff", "--no-color", row.base_commit)
        if len(patch) > self.PATCH_MAX_BYTES:
            raise CodeSandboxConflictError("Patch превышает безопасный лимит 10 MiB.")
        patch_sha = hashlib.sha256(patch).hexdigest()
        patch_path = self._patches_root / f"{row.id}-{patch_sha[:16]}.patch"
        patch_path.write_bytes(patch)
        try:
            patch_path.chmod(0o600)
        except OSError:
            # Best effort on platforms/filesystems that do not implement POSIX modes.
            pass

        changed_paths = self._changed_paths(worktree, row.base_commit)
        insertions, deletions = self._numstat(worktree, row.base_commit)
        blocked = sorted(path for path in changed_paths if self._matches(path, BLOCKED_PATTERNS))
        special_modes = self._special_mode_paths(worktree, changed_paths)
        blocked = sorted(set(blocked) | set(special_modes))
        protected = sorted(path for path in changed_paths if self._matches(path, PROTECTED_PATTERNS))
        risk = self._risk_level(changed_paths, insertions, deletions, protected, blocked)
        fingerprint = self._fingerprint(row.base_commit, patch_sha, risk, changed_paths)

        row.patch_sha256 = patch_sha
        row.patch_fingerprint = fingerprint
        row.patch_path = str(patch_path)
        row.changed_paths_json = changed_paths
        row.protected_paths_json = protected
        row.blocked_paths_json = blocked
        row.files_changed = len(changed_paths)
        row.insertions = insertions
        row.deletions = deletions
        row.risk_level = risk
        row.approval_required = risk in {"high", "critical"} or bool(protected)
        row.verification_status = "not_run"
        row.verification_json = []
        row.status = "inspected"
        row.updated_at = utc_now()
        self._session.flush()

        preview = patch.decode("utf-8", errors="replace")
        truncated = len(preview) > self.PATCH_PREVIEW_LIMIT
        if truncated:
            preview = preview[: self.PATCH_PREVIEW_LIMIT] + "\n… PATCH PREVIEW TRUNCATED …\n"
        await self._event_bus.publish(Event(event_type="code_sandbox.patch.inspected", source="code_sandbox", payload={"session_id": row.id, "risk_level": risk, "files_changed": len(changed_paths), "approval_required": row.approval_required, "blocked_count": len(blocked), "fingerprint": fingerprint}))
        return CodeSandboxInspectResponse(session=self._to_schema(row), patch_preview=preview, patch_truncated=truncated)

    async def verify(self, session_id: str, workspace_id: str | None, request: CodeSandboxVerifyRequest) -> CodeSandboxSession:
        row = self._require_row(session_id, workspace_id)
        self._require_open(row)
        self._ensure_inspected(row)
        if row.blocked_paths_json:
            raise CodeSandboxConflictError("Патч затрагивает заблокированные пути и не может быть проверен для применения.")
        worktree = Path(row.worktree_path)
        items = [
            self._run_verification(profile, worktree, Path(row.repo_path), request.timeout_seconds)
            for profile in request.profiles
        ]
        return await self.record_verification_results(session_id, workspace_id, items)

    async def approve(self, session_id: str, workspace_id: str | None, reason: str) -> CodeSandboxApproval:
        row = self._require_row(session_id, workspace_id)
        self._require_open(row)
        self._ensure_inspected(row)
        if row.blocked_paths_json:
            raise CodeSandboxConflictError("Заблокированный путь нельзя разрешить approval-токеном.")
        if row.verification_status != "passed":
            raise CodeSandboxConflictError("Перед approval патч должен пройти verification.")
        token = secrets.token_urlsafe(32)
        now = utc_now()
        expires_at = now + timedelta(minutes=self.APPROVAL_TTL_MINUTES)
        approval = CodeSandboxApprovalModel(
            id=new_id("sandbox_approval"), session_id=row.id, workspace_id=workspace_id,
            fingerprint=row.patch_fingerprint or "", token_hash=self._token_hash(token), status="pending",
            reason=reason, created_at=now, expires_at=expires_at,
        )
        self._session.add(approval)
        self._session.flush()
        await self._event_bus.publish(Event(event_type="code_sandbox.approval.created", source="code_sandbox", payload={"session_id": row.id, "approval_id": approval.id, "fingerprint": approval.fingerprint, "expires_at": expires_at.isoformat()}))
        return CodeSandboxApproval(approval_id=approval.id, token=token, fingerprint=approval.fingerprint, expires_at=expires_at)

    async def apply(self, session_id: str, workspace_id: str | None, approval_token: str | None) -> CodeSandboxSession:
        row = self._require_row(session_id, workspace_id)
        self._require_open(row)
        self._ensure_inspected(row)
        if row.blocked_paths_json:
            raise CodeSandboxConflictError("Патч содержит заблокированные пути и не может быть применён.")
        if row.verification_status != "passed":
            raise CodeSandboxConflictError("Патч должен пройти verification перед применением.")
        if not row.changed_paths_json:
            raise CodeSandboxConflictError("В sandbox нет изменений для применения.")

        # Recompute patch and fingerprint immediately before apply to prevent TOCTOU edits.
        inspected = await self.inspect(session_id, workspace_id)
        row = self._require_row(session_id, workspace_id)
        if inspected.session.verification_status != "not_run":
            raise AssertionError("inspect must invalidate verification")
        # Restore verification only if exact patch fingerprint is unchanged from the verified state.
        # The previous fingerprint is recovered from latest successful verification event state stored below.
        # Since inspect invalidates verification, compare against approval fingerprint when approval is required,
        # otherwise require the patch SHA to match the patch file that was verified via metadata snapshot.
        verified_fingerprint = str((row.metadata_json or {}).get("verified_fingerprint") or "")
        if not verified_fingerprint or verified_fingerprint != row.patch_fingerprint:
            raise CodeSandboxConflictError("Патч изменился после verification. Выполните verification повторно.")
        row.verification_status = "passed"
        row.status = "verified"

        repo = Path(row.repo_path)
        self._ensure_clean(repo)
        head = self._git(repo, "rev-parse", "HEAD").stdout.strip()
        if head != row.base_commit:
            raise CodeSandboxConflictError("HEAD базового репозитория изменился после создания sandbox.")
        patch_path = Path(row.patch_path or "")
        if not patch_path.is_file():
            raise CodeSandboxConflictError("Patch artifact отсутствует.")
        self._git(repo, "apply", "--check", str(patch_path))
        if row.approval_required:
            self._consume_approval(row, workspace_id, approval_token)
        self._git(repo, "apply", "--whitespace=nowarn", str(patch_path))
        row.status = "applied"
        row.applied_at = utc_now()
        row.updated_at = utc_now()
        self._session.flush()
        await self._event_bus.publish(Event(event_type="code_sandbox.patch.applied", source="code_sandbox", payload={"session_id": row.id, "risk_level": row.risk_level, "fingerprint": row.patch_fingerprint, "files_changed": row.files_changed}))
        return self._to_schema(row)

    async def close(self, session_id: str, workspace_id: str | None) -> CodeSandboxSession:
        row = self._require_row(session_id, workspace_id)
        if row.status == "closed":
            return self._to_schema(row)
        repo = Path(row.repo_path)
        worktree = Path(row.worktree_path)
        if worktree.exists():
            try:
                self._git(repo, "worktree", "remove", "--force", str(worktree))
            except CodeSandboxError:
                shutil.rmtree(worktree, ignore_errors=True)
                try:
                    self._git(repo, "worktree", "prune")
                except CodeSandboxError:
                    pass
        row.status = "closed"
        row.closed_at = utc_now()
        row.updated_at = utc_now()
        self._session.flush()
        await self._event_bus.publish(Event(event_type="code_sandbox.session.closed", source="code_sandbox", payload={"session_id": row.id}))
        return self._to_schema(row)

    def patch_bytes(self, session_id: str, workspace_id: str | None) -> bytes:
        row = self._require_row(session_id, workspace_id)
        self._ensure_inspected(row)
        path = Path(row.patch_path or "")
        if not path.is_file():
            raise CodeSandboxConflictError("Patch artifact отсутствует.")
        return path.read_bytes()

    def run_safe_verification_profile(self, profile: str, worktree: Path, repo: Path, timeout: int) -> CodeSandboxVerificationItem:
        """Run a verification profile using the existing host-safe policy.

        P2-010 uses this only for diff_check, which invokes Git metadata checks
        and does not execute repository code.
        """
        if profile != "diff_check":
            raise CodeSandboxError("Только diff_check разрешён через host-safe runtime bridge.")
        return self._run_verification(profile, worktree, repo, timeout)

    async def record_verification_results(
        self,
        session_id: str,
        workspace_id: str | None,
        items: list[CodeSandboxVerificationItem],
    ) -> CodeSandboxSession:
        row = self._require_row(session_id, workspace_id)
        self._require_open(row)
        self._ensure_inspected(row)
        if row.blocked_paths_json:
            raise CodeSandboxConflictError("Патч затрагивает заблокированные пути и не может быть проверен для применения.")
        results = [item.model_dump(mode="json") for item in items]
        passed = (
            bool(results)
            and all(item["status"] in {"passed", "skipped"} for item in results)
            and any(item["status"] == "passed" for item in results)
        )
        row.verification_json = results
        row.verification_status = "passed" if passed else "failed"
        row.status = "verified" if passed else "inspected"
        if passed:
            self.remember_verified_fingerprint(row)
        row.updated_at = utc_now()
        self._session.flush()
        await self._event_bus.publish(Event(
            event_type="code_sandbox.patch.verified",
            source="code_sandbox",
            payload={
                "session_id": row.id,
                "verification_status": row.verification_status,
                "profiles": [item.profile for item in items],
                "execution_backends": sorted({item.execution_backend for item in items}),
                "fingerprint": row.patch_fingerprint,
            },
        ))
        return self._to_schema(row)

    def _run_verification(self, profile: str, worktree: Path, repo: Path, timeout: int) -> CodeSandboxVerificationItem:
        if profile == "diff_check":
            started = perf_counter()
            try:
                result = self._git(worktree, "diff", "--cached", "--check", "--no-ext-diff")
                output = ((result.stdout or "") + ("\n" + result.stderr if result.stderr else "")).strip()
                return CodeSandboxVerificationItem(
                    profile=profile, status="passed", exit_code=0,
                    duration_ms=round((perf_counter() - started) * 1000, 2), output=output,
                )
            except CodeSandboxError as exc:
                return CodeSandboxVerificationItem(
                    profile=profile, status="failed", exit_code=1,
                    duration_ms=round((perf_counter() - started) * 1000, 2), output=str(exc),
                )
        elif profile == "python_compile":
            python = self._repo_python(repo)
            args = [str(python), "-I", "-S", "-m", "compileall", "-q", "."]
            cwd = worktree
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            env.pop("PYTHONHOME", None)
        elif profile == "pytest":
            if not self._trusted_test_execution_enabled():
                return CodeSandboxVerificationItem(profile=profile, status="skipped", output="Host test execution отключён. Установите AI_STUDIO_CODE_SANDBOX_ALLOW_TEST_EXECUTION=1 только для доверенного репозитория.")
            python = self._repo_python(repo)
            args = [str(python), "-m", "pytest", "-q"]
            cwd = worktree
            env = None
        elif profile == "frontend_build":
            if not self._trusted_test_execution_enabled():
                return CodeSandboxVerificationItem(profile=profile, status="skipped", output="Host build execution отключён. Установите AI_STUDIO_CODE_SANDBOX_ALLOW_TEST_EXECUTION=1 только для доверенного репозитория.")
            frontend = worktree / "frontend"
            if not (frontend / "package.json").is_file():
                return CodeSandboxVerificationItem(profile=profile, status="skipped", output="frontend/package.json не найден.")
            npm = shutil.which("npm")
            if not npm:
                return CodeSandboxVerificationItem(profile=profile, status="failed", output="npm не найден в PATH.")
            args = [npm, "run", "build"]
            cwd = frontend
            env = os.environ.copy()
            base_bin = repo / "frontend" / "node_modules" / ".bin"
            if base_bin.is_dir():
                env["PATH"] = str(base_bin) + os.pathsep + env.get("PATH", "")
        else:
            raise CodeSandboxError(f"Неизвестный verification profile: {profile}")

        started = perf_counter()
        try:
            result = subprocess.run(args, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout, check=False, shell=False)
            output = ((result.stdout or "") + ("\n" + result.stderr if result.stderr else "")).strip()
            if len(output) > 12_000:
                output = output[-12_000:]
            status = "passed" if result.returncode == 0 else "failed"
            return CodeSandboxVerificationItem(profile=profile, status=status, exit_code=result.returncode, duration_ms=round((perf_counter()-started)*1000, 2), output=output)
        except subprocess.TimeoutExpired as exc:
            output = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") + ((exc.stderr or "") if isinstance(exc.stderr, str) else "")
            return CodeSandboxVerificationItem(profile=profile, status="failed", exit_code=None, duration_ms=round((perf_counter()-started)*1000, 2), output=(output + f"\nTimeout after {timeout}s").strip())

    def _consume_approval(self, row: CodeSandboxSessionModel, workspace_id: str | None, token: str | None) -> None:
        if not token:
            raise CodeSandboxApprovalRequiredError("Для этого патча требуется Human Approval.")
        now = utc_now()
        token_hash = self._token_hash(token)
        approval = self._session.scalar(select(CodeSandboxApprovalModel).where(CodeSandboxApprovalModel.session_id == row.id, CodeSandboxApprovalModel.token_hash == token_hash))
        if approval is None or approval.workspace_id != workspace_id:
            raise CodeSandboxApprovalInvalidError("Approval token не найден.")
        expires_at = approval.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if approval.status != "pending" or expires_at <= now:
            if approval.status == "pending" and expires_at <= now:
                approval.status = "expired"
            raise CodeSandboxApprovalInvalidError("Approval token истёк или уже использован.")
        if approval.fingerprint != row.patch_fingerprint:
            raise CodeSandboxApprovalInvalidError("Approval относится к другой версии патча.")
        approval.status = "used"
        approval.used_at = now

    def remember_verified_fingerprint(self, row: CodeSandboxSessionModel) -> None:
        metadata = dict(row.metadata_json or {})
        metadata["verified_fingerprint"] = row.patch_fingerprint
        row.metadata_json = metadata

    def _get_row(self, session_id: str, workspace_id: str | None) -> CodeSandboxSessionModel | None:
        stmt = select(CodeSandboxSessionModel).where(CodeSandboxSessionModel.id == session_id)
        stmt = stmt.where(CodeSandboxSessionModel.workspace_id.is_(None) if workspace_id is None else CodeSandboxSessionModel.workspace_id == workspace_id)
        return self._session.scalar(stmt)

    def _require_row(self, session_id: str, workspace_id: str | None) -> CodeSandboxSessionModel:
        row = self._get_row(session_id, workspace_id)
        if row is None:
            raise CodeSandboxError("Code Sandbox session не найден.")
        return row

    @staticmethod
    def _require_open(row: CodeSandboxSessionModel) -> None:
        if row.status in {"applied", "closed"}:
            raise CodeSandboxConflictError(f"Sandbox уже находится в состоянии {row.status}.")

    @staticmethod
    def _ensure_inspected(row: CodeSandboxSessionModel) -> None:
        if not row.patch_fingerprint or not row.patch_path:
            raise CodeSandboxConflictError("Сначала выполните inspect патча.")

    def _resolve_repo(self, path: str) -> Path:
        requested = Path(path).expanduser().resolve()
        if not requested.exists():
            raise CodeSandboxError("Репозиторий не найден.")
        result = self._git(requested, "rev-parse", "--show-toplevel")
        root = Path(result.stdout.strip()).resolve()
        if not root.is_dir():
            raise CodeSandboxError("Git repository root не найден.")
        if not any(self._is_within(root, allowed) for allowed in self._allowed_roots):
            allowed_text = ", ".join(str(item) for item in self._allowed_roots)
            raise CodeSandboxError(f"Репозиторий находится вне разрешённых roots: {allowed_text}")
        return root

    def _load_allowed_roots(self) -> list[Path]:
        configured = os.getenv("AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS", "").strip()
        roots = [self._project_root.resolve()]
        if configured:
            separator = ";" if ";" in configured else os.pathsep
            for raw in configured.split(separator):
                value = raw.strip()
                if value:
                    roots.append(Path(value).expanduser().resolve())
        return list(dict.fromkeys(roots))

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    @staticmethod
    def _trusted_test_execution_enabled() -> bool:
        return os.getenv("AI_STUDIO_CODE_SANDBOX_ALLOW_TEST_EXECUTION", "0").strip().lower() in {"1", "true", "yes", "on"}

    def tracked_blocked_paths(self, worktree: Path) -> list[str]:
        """Return tracked sensitive files that must never enter an execution runtime."""
        result = self._git(worktree, "ls-files", "-z")
        paths = [item for item in (result.stdout or "").split("\0") if item]
        return sorted(path for path in paths if self._matches(path, BLOCKED_PATTERNS))

    def _special_mode_paths(self, worktree: Path, paths: list[str]) -> list[str]:
        blocked: list[str] = []
        for path in paths:
            result = self._git(worktree, "ls-files", "-s", "--", path).stdout.strip()
            if not result:
                continue
            mode = result.split(maxsplit=1)[0]
            if mode in {"120000", "160000"}:
                blocked.append(path)
        return blocked

    def _ensure_safe_git_config(self, repo: Path) -> None:
        # Worktree is file-system isolation, not a trust boundary. Disable Git hooks
        # and reject configured clean/smudge/process filters that could execute
        # commands during checkout or staging. Use the same raw-byte Git runner as
        # the rest of Code Sandbox so Windows ANSI code pages cannot corrupt UTF-8
        # repository paths or config output.
        # Git for Windows commonly installs trusted global filters (most notably
        # Git LFS). Those settings belong to the user's environment and must not
        # make every otherwise-clean repository fail the sandbox preflight. What
        # the repository itself controls is its local/worktree config, so reject
        # executable filter drivers only from those scopes. ``--show-scope`` keeps
        # this distinction explicit and also avoids depending on where Git stores
        # a linked-worktree config.
        result = self._run_git_raw(
            repo,
            "config",
            "--show-scope",
            "--get-regexp",
            r"^filter\..*\.(clean|smudge|process)$",
            timeout=30,
        )
        stdout = self._decode_git_output(result.stdout)
        stderr = self._decode_git_output(result.stderr)
        if result.returncode not in {0, 1}:
            raise CodeSandboxError((stderr or stdout or "Не удалось проверить Git filters.").strip())
        if result.returncode == 0:
            unsafe_lines: list[str] = []
            for line in stdout.splitlines():
                scope, _, remainder = line.partition("\t")
                if not remainder:
                    scope, _, remainder = line.partition(" ")
                if scope.strip().lower() in {"local", "worktree"} and remainder.strip():
                    unsafe_lines.append(remainder.strip())
            if unsafe_lines:
                raise CodeSandboxError(
                    "Репозиторий использует local/worktree Git clean/smudge/process filters. "
                    "Для P2-007 такие репозитории запрещены, поскольку фильтр может исполнять локальную команду."
                )

        # ``core.fsmonitor`` may itself name an executable hook. Force it off in
        # every Git process (see _run_git_raw) rather than rejecting a harmless
        # global user setting. This gives the sandbox a deterministic no-fsmonitor
        # execution policy across system/global/local scopes.

    def _ensure_clean(self, repo: Path) -> None:
        status = self._git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout.strip()
        if status:
            raise CodeSandboxConflictError("Базовый репозиторий содержит незакоммиченные изменения. Сначала сохраните или откатите их.")

    @staticmethod
    def _matches(path: str, patterns: Iterable[str]) -> bool:
        normalized = path.replace("\\", "/").removeprefix("./")
        return any(fnmatch(normalized.lower(), pattern.lower()) for pattern in patterns)

    @staticmethod
    def _risk_level(paths: list[str], insertions: int, deletions: int, protected: list[str], blocked: list[str]) -> str:
        if blocked:
            return "critical"
        churn = insertions + deletions
        if protected or len(paths) > 30 or churn > 1200:
            return "high"
        if len(paths) > 12 or churn > 400 or deletions > 200:
            return "medium"
        if paths:
            return "low"
        return "none"

    @staticmethod
    def _fingerprint(base_commit: str, patch_sha: str, risk: str, paths: list[str]) -> str:
        payload = "\n".join([base_commit, patch_sha, risk, *sorted(paths)])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _repo_python(repo: Path) -> Path:
        candidates = [repo / ".venv" / "Scripts" / "python.exe", repo / ".venv" / "bin" / "python"]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return Path(sys.executable)

    def _changed_paths(self, worktree: Path, base_commit: str) -> list[str]:
        output = self._git(worktree, "diff", "--cached", "--name-only", "--diff-filter=ACDMRTUXB", base_commit).stdout
        return sorted({line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()})

    def _numstat(self, worktree: Path, base_commit: str) -> tuple[int, int]:
        output = self._git(worktree, "diff", "--cached", "--numstat", base_commit).stdout
        additions = deletions = 0
        for line in output.splitlines():
            parts = line.split("\t", 2)
            if len(parts) < 2:
                continue
            if parts[0].isdigit(): additions += int(parts[0])
            if parts[1].isdigit(): deletions += int(parts[1])
        return additions, deletions

    @staticmethod
    def _decode_git_output(data: bytes) -> str:
        """Decode Git pipe output without depending on the Windows console code page.

        Git for Windows stores and normally emits path data as UTF-8, while
        ``subprocess(..., text=True)`` decodes pipes with Python's preferred ANSI
        encoding on some Windows installations. A Cyrillic user profile can
        therefore turn ``C:\\Users\\Глава`` into a non-existent mojibake path.
        Prefer UTF-8 and only then fall back to platform encodings for localized
        Git diagnostics from older installations.
        """
        if not data:
            return ""
        candidates = ["utf-8", locale.getpreferredencoding(False), sys.getfilesystemencoding()]
        if os.name == "nt":
            candidates.append("mbcs")
        seen: set[str] = set()
        for encoding in candidates:
            normalized = (encoding or "").strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            try:
                return data.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue
        return data.decode("utf-8", errors="replace")

    def _run_git_raw(
        self, cwd: Path, *args: str, timeout: int = 60
    ) -> subprocess.CompletedProcess[bytes]:
        command = [
            "git",
            "-c",
            f"core.hooksPath={self._hooks_root}",
            "-c",
            "diff.external=",
            "-c",
            "core.quotepath=false",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(cwd),
            *args,
        ]
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=False,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CodeSandboxError(f"Git command failed: {exc}") from exc

    def _git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        raw = self._run_git_raw(cwd, *args)
        stdout = self._decode_git_output(raw.stdout)
        stderr = self._decode_git_output(raw.stderr)
        if raw.returncode != 0:
            raise CodeSandboxError((stderr or stdout or "Git command failed").strip())
        return subprocess.CompletedProcess(
            args=raw.args,
            returncode=raw.returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def _git_bytes(self, cwd: Path, *args: str) -> bytes:
        result = self._run_git_raw(cwd, *args)
        if result.returncode != 0:
            message = self._decode_git_output(result.stderr or result.stdout or b"Git command failed")
            raise CodeSandboxError(message.strip())
        return result.stdout

    @staticmethod
    def _to_schema(row: CodeSandboxSessionModel) -> CodeSandboxSession:
        verification = [CodeSandboxVerificationItem.model_validate(item) for item in (row.verification_json or [])]
        return CodeSandboxSession(
            id=row.id, workspace_id=row.workspace_id, repo_path=row.repo_path, worktree_path=row.worktree_path,
            base_ref=row.base_ref, base_commit=row.base_commit, status=row.status, risk_level=row.risk_level,
            approval_required=row.approval_required, verification_status=row.verification_status,
            patch_fingerprint=row.patch_fingerprint, patch_sha256=row.patch_sha256, files_changed=row.files_changed,
            insertions=row.insertions, deletions=row.deletions, changed_paths=list(row.changed_paths_json or []),
            protected_paths=list(row.protected_paths_json or []), blocked_paths=list(row.blocked_paths_json or []),
            verification=verification, created_at=row.created_at, updated_at=row.updated_at,
            applied_at=row.applied_at, closed_at=row.closed_at,
        )
