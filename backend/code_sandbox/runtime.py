from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
from time import perf_counter
from typing import Callable, Iterable
import uuid
import zipfile

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.code_sandbox.models import CodeSandboxRuntimeRunModel, utc_now
from backend.code_sandbox.runtime_policy import (
    IsolatedRuntimePolicy,
    RuntimePolicyEvaluation,
)
from backend.code_sandbox.schemas import (
    CodeSandboxSession,
    CodeSandboxVerificationItem,
    IsolatedRuntimeRun,
    IsolatedRuntimeRunRequest,
    IsolatedRuntimeRunResponse,
    IsolatedRuntimeStatus,
    RuntimeImageStatus,
)
from backend.code_sandbox.service import CodeSandboxConflictError, CodeSandboxError, CodeSandboxService
from backend.core.events import Event, EventBus


class IsolatedRuntimeError(CodeSandboxError):
    pass


class IsolatedRuntimeUnavailableError(IsolatedRuntimeError):
    pass


class IsolatedRuntimePolicyDeniedError(
    IsolatedRuntimeError
):
    pass


class IsolatedRuntimePolicyApprovalRequiredError(
    IsolatedRuntimeError
):
    pass


@dataclass(frozen=True)
class _ProfileSpec:
    image_env: str
    default_image: str
    command: str
    artifact_paths: tuple[str, ...] = ()


PROFILE_SPECS: dict[str, _ProfileSpec] = {
    "python_compile": _ProfileSpec(
        image_env="AI_STUDIO_RUNTIME_PYTHON_IMAGE",
        default_image="ai-studio-runtime-python:py313-v1",
        command="python -I -S -m compileall -q .",
    ),
    "pytest": _ProfileSpec(
        image_env="AI_STUDIO_RUNTIME_PYTEST_IMAGE",
        default_image="ai-studio-runtime-python:py313-v1",
        command="python -m pytest -q -p no:cacheprovider --junitxml=.ai-studio-artifacts/pytest-junit.xml",
        artifact_paths=(".ai-studio-artifacts/pytest-junit.xml",),
    ),
    "frontend_build": _ProfileSpec(
        image_env="AI_STUDIO_RUNTIME_NODE_IMAGE",
        default_image="ai-studio-runtime-node:node22-v1",
        command="test -d /opt/ai-studio/node_modules && ln -s /opt/ai-studio/node_modules frontend/node_modules; cd frontend && npm run build",
        artifact_paths=("frontend/dist",),
    ),
}


class IsolatedRuntimeService:
    """Docker-backed verification boundary for untrusted repository code.

    Source worktrees are mounted read-only at /input. The container copies that
    source into an ephemeral tmpfs /workspace and executes a fixed verification
    profile there. No host path is mounted writable and network access is disabled.
    """

    OUTPUT_LIMIT = 24_000
    ARTIFACT_MAX_BYTES = 32 * 1024 * 1024
    ARTIFACT_MAX_FILES = 2_000

    def __init__(
        self,
        *,
        session: Session,
        event_bus: EventBus,
        sandbox: CodeSandboxService,
        docker_executable: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
        runtime_policy: IsolatedRuntimePolicy | None = None,
    ) -> None:
        self._session = session
        self._event_bus = event_bus
        self._sandbox = sandbox
        self._runtime_policy = (
            runtime_policy
            or IsolatedRuntimePolicy(
                event_bus=event_bus,
            )
        )
        self._docker = docker_executable or shutil.which("docker") or "docker"
        self._runner = runner or subprocess.run
        root = os.getenv("AI_STUDIO_ISOLATED_RUNTIME_ROOT", "").strip()
        base = Path(root).expanduser().resolve() if root else Path(tempfile.gettempdir()).resolve() / "ai_studio_isolated_runtime"
        self._artifact_root = base / "artifacts"
        self._artifact_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def configured_enabled() -> bool:
        return os.getenv("AI_STUDIO_ISOLATED_RUNTIME_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}

    def status(self) -> IsolatedRuntimeStatus:
        cli_available = shutil.which("docker") is not None
        daemon_available = False
        daemon_error = ""
        if cli_available:
            try:
                result = self._run_docker("version", "--format", "{{.Server.Version}}", timeout=8)
                daemon_available = result.returncode == 0 and bool((result.stdout or "").strip())
                if not daemon_available:
                    daemon_error = ((result.stderr or "") or (result.stdout or "")).strip()[:1000]
            except Exception as exc:  # diagnostics must not break the app
                daemon_error = str(exc)[:1000]

        images: list[RuntimeImageStatus] = []
        for profile, spec in PROFILE_SPECS.items():
            image = os.getenv(spec.image_env, spec.default_image).strip() or spec.default_image
            present, image_id, trusted = self._image_status(image) if daemon_available else (False, None, False)
            images.append(RuntimeImageStatus(profile=profile, image=image, present=present, trusted=trusted, image_id=image_id))
        enabled = self.configured_enabled() and cli_available and daemon_available
        return IsolatedRuntimeStatus(
            enabled=enabled,
            docker_cli_available=cli_available,
            docker_daemon_available=daemon_available,
            daemon_error=daemon_error,
            network_mode="none",
            root_filesystem_read_only=True,
            source_mount_read_only=True,
            no_new_privileges=True,
            capabilities_dropped=True,
            images=images,
        )

    async def run(
        self,
        session_id: str,
        workspace_id: str | None,
        request: IsolatedRuntimeRunRequest,
    ) -> IsolatedRuntimeRunResponse:
        status = self.status()
        if not status.enabled:
            detail = status.daemon_error or "Docker CLI/daemon недоступен или isolated runtime отключён."
            raise IsolatedRuntimeUnavailableError(detail)

        sandbox = self._sandbox.get(session_id, workspace_id)
        if sandbox is None:
            raise IsolatedRuntimeError("Code Sandbox session не найден.")
        if sandbox.status in {"applied", "closed"}:
            raise CodeSandboxConflictError(f"Sandbox уже находится в состоянии {sandbox.status}.")
        if not sandbox.patch_fingerprint:
            raise CodeSandboxConflictError("Сначала выполните inspect патча.")
        if sandbox.blocked_paths:
            raise CodeSandboxConflictError("Патч затрагивает заблокированные пути и не может исполняться в runtime.")

        worktree = Path(sandbox.worktree_path).resolve()
        sensitive = self._sandbox.tracked_blocked_paths(worktree)
        if sensitive:
            raise CodeSandboxConflictError(
                "Runtime не запускается: репозиторий уже содержит tracked sensitive paths: "
                + ", ".join(sensitive[:12])
            )
        if not worktree.is_dir():
            raise CodeSandboxConflictError("Worktree отсутствует на диске.")

        items: list[CodeSandboxVerificationItem] = []
        runs: list[IsolatedRuntimeRun] = []
        for profile in request.profiles:
            policy_evaluation = (
                await self._runtime_policy.evaluate_profile(
                    workspace_id=workspace_id,
                    profile=profile,
                )
            )
            self._require_policy_allowed(
                policy_evaluation,
                subject=f"runtime profile {profile}",
            )

            if profile == "diff_check":
                item = self._sandbox.run_safe_verification_profile(
                    "diff_check",
                    worktree,
                    Path(sandbox.repo_path),
                    request.timeout_seconds,
                )
                items.append(item)
                continue

            run, item = await self._run_profile(
                sandbox=sandbox,
                worktree=worktree,
                workspace_id=workspace_id,
                profile=profile,
                request=request,
                policy_evaluation=policy_evaluation,
            )
            runs.append(run)
            items.append(item)

        updated = await self._sandbox.record_verification_results(session_id, workspace_id, items)
        return IsolatedRuntimeRunResponse(session=updated, runs=runs)

    def list_for_session(self, session_id: str, workspace_id: str | None, limit: int = 50) -> list[IsolatedRuntimeRun]:
        stmt = select(CodeSandboxRuntimeRunModel).where(CodeSandboxRuntimeRunModel.session_id == session_id)
        stmt = stmt.where(CodeSandboxRuntimeRunModel.workspace_id.is_(None) if workspace_id is None else CodeSandboxRuntimeRunModel.workspace_id == workspace_id)
        stmt = stmt.order_by(CodeSandboxRuntimeRunModel.created_at.desc()).limit(limit)
        return [self._to_schema(row) for row in self._session.scalars(stmt).all()]

    def get(self, run_id: str, workspace_id: str | None) -> IsolatedRuntimeRun | None:
        stmt = select(CodeSandboxRuntimeRunModel).where(CodeSandboxRuntimeRunModel.id == run_id)
        stmt = stmt.where(CodeSandboxRuntimeRunModel.workspace_id.is_(None) if workspace_id is None else CodeSandboxRuntimeRunModel.workspace_id == workspace_id)
        row = self._session.scalar(stmt)
        return self._to_schema(row) if row else None

    async def artifact_zip_path(
        self,
        run_id: str,
        workspace_id: str | None,
    ) -> Path:
        policy_evaluation = (
            await self._runtime_policy.evaluate_artifact_export(
                workspace_id=workspace_id,
            )
        )
        self._require_policy_allowed(
            policy_evaluation,
            subject="runtime artifact export",
        )

        row = self._require_row(run_id, workspace_id)
        path_text = (row.metadata_json or {}).get("artifact_zip_path")
        if not path_text:
            raise IsolatedRuntimeError("Для этого runtime run нет экспортированных артефактов.")
        path = Path(str(path_text)).resolve()
        if not path.is_file() or self._artifact_root.resolve() not in path.parents:
            raise IsolatedRuntimeError("Artifact bundle отсутствует или недоступен.")
        return path

    async def _run_profile(
        self,
        *,
        sandbox: CodeSandboxSession,
        worktree: Path,
        workspace_id: str | None,
        profile: str,
        request: IsolatedRuntimeRunRequest,
        policy_evaluation: RuntimePolicyEvaluation,
    ) -> tuple[IsolatedRuntimeRun, CodeSandboxVerificationItem]:
        spec = PROFILE_SPECS.get(profile)
        if spec is None:
            raise IsolatedRuntimeError(f"Профиль {profile} не поддерживается isolated runtime.")
        image = os.getenv(spec.image_env, spec.default_image).strip() or spec.default_image
        now = utc_now()
        row = CodeSandboxRuntimeRunModel(
            id=f"runtime_{uuid.uuid4().hex}",
            session_id=sandbox.id,
            workspace_id=workspace_id,
            profile=profile,
            backend="docker",
            status="running",
            image=image,
            image_id=None,
            network_mode="none",
            cpu_limit=request.cpu_limit,
            memory_mb=request.memory_mb,
            pids_limit=request.pids_limit,
            timeout_seconds=request.timeout_seconds,
            artifact_paths_json=[],
            artifact_bytes=0,
            output="",
            metadata_json={
                "runtime_policy": (
                    policy_evaluation.to_dict()
                ),
            },
            created_at=now,
            started_at=now,
            updated_at=now,
        )
        self._session.add(row)
        self._session.flush()

        if (
            request.collect_artifacts
            and spec.artifact_paths
        ):
            artifact_policy = (
                await self._runtime_policy
                .evaluate_artifact_export(
                    workspace_id=workspace_id,
                )
            )

            metadata = dict(
                row.metadata_json or {}
            )
            metadata[
                "artifact_export_policy"
            ] = artifact_policy.to_dict()
            row.metadata_json = metadata

            if not artifact_policy.allowed:
                row.status = "failed"
                row.output = self._policy_message(
                    artifact_policy,
                    subject=(
                        "runtime artifact export"
                    ),
                )
                row.finished_at = utc_now()
                row.updated_at = row.finished_at
                self._session.flush()

                await self._publish(
                    "code_sandbox.runtime.failed",
                    row,
                    {
                        "message": row.output,
                        "artifact_export_policy": (
                            artifact_policy.to_dict()
                        ),
                    },
                )

                return (
                    self._to_schema(row),
                    self._verification_item(row),
                )

        present, image_id, trusted = self._image_status(
            image
        )
        row.image_id = image_id

        await self._publish(
            "code_sandbox.runtime.started",
            row,
            {
                "profile": profile,
                "image": image,
                "runtime_policy": (
                    policy_evaluation.to_dict()
                ),
            },
        )

        if not present or not trusted:
            row.status = "failed"
            row.output = (
                f"Runtime image отсутствует локально: {image}. Выполните prepare_p2_010_runtime.bat или настройте {spec.image_env}."
                if not present
                else f"Runtime image {image} не имеет доверенной метки org.ai-studio.runtime=p2-010. Пересоберите через prepare_p2_010_runtime.bat."
            )
            row.finished_at = utc_now(); row.updated_at = row.finished_at
            self._session.flush()
            item = self._verification_item(row)
            await self._publish("code_sandbox.runtime.failed", row, {"message": row.output})
            return self._to_schema(row), item

        container_name = f"ai-studio-{row.id[-20:]}"
        create_args = self._build_create_args(
            name=container_name,
            worktree=worktree,
            image=image_id or image,
            command=spec.command,
            request=request,
        )
        started = perf_counter()
        timed_out = False
        container_created = False
        try:
            create = self._run_docker(*create_args, timeout=30)
            if create.returncode != 0:
                row.status = "failed"
                row.output = self._trim_output((create.stdout or "") + "\n" + (create.stderr or ""))
            else:
                container_created = True
                try:
                    result = self._run_docker("start", "-a", container_name, timeout=request.timeout_seconds)
                    row.exit_code = result.returncode
                    row.output = self._trim_output((result.stdout or "") + ("\n" + result.stderr if result.stderr else ""))
                    row.status = "passed" if result.returncode == 0 else "failed"
                except subprocess.TimeoutExpired as exc:
                    timed_out = True
                    self._run_docker("kill", container_name, timeout=10)
                    stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
                    stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
                    row.output = self._trim_output(stdout + "\n" + stderr + f"\nTimeout after {request.timeout_seconds}s")
                    row.status = "timeout"
                if container_created and request.collect_artifacts and spec.artifact_paths:
                    artifact_paths, artifact_bytes, zip_path = self._collect_artifacts(container_name, row.id, spec.artifact_paths)
                    row.artifact_paths_json = artifact_paths
                    row.artifact_bytes = artifact_bytes
                    if zip_path:
                        metadata = dict(
                            row.metadata_json or {}
                        )
                        metadata[
                            "artifact_zip_path"
                        ] = str(zip_path)
                        row.metadata_json = metadata
        except Exception as exc:
            row.status = "failed"
            row.output = self._trim_output(f"Isolated runtime error: {exc}")
        finally:
            if container_created:
                try:
                    self._run_docker("rm", "-f", container_name, timeout=15)
                except Exception:
                    pass

        row.duration_ms = round((perf_counter() - started) * 1000, 2)
        row.timed_out = timed_out
        row.finished_at = utc_now(); row.updated_at = row.finished_at
        self._session.flush()
        event = "code_sandbox.runtime.completed" if row.status == "passed" else "code_sandbox.runtime.failed"
        await self._publish(event, row, {"status": row.status, "exit_code": row.exit_code, "artifact_count": len(row.artifact_paths_json or [])})
        return self._to_schema(row), self._verification_item(row)

    def _build_create_args(
        self,
        *,
        name: str,
        worktree: Path,
        image: str,
        command: str,
        request: IsolatedRuntimeRunRequest,
    ) -> list[str]:
        source = str(worktree)
        memory = f"{request.memory_mb}m"
        workspace_mb = min(max(request.memory_mb, 256), 2048)
        wrapper = (
            "set -eu; "
            "mkdir -p /workspace/.ai-studio-artifacts; "
            "cp -R /input/. /workspace/; "
            "cd /workspace; "
            + command
        )
        return [
            "create",
            "--name", name,
            "--pull=never",
            "--network=none",
            "--ipc=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt", "no-new-privileges=true",
            "--security-opt", "seccomp=builtin",
            "--pids-limit", str(request.pids_limit),
            "--cpus", str(request.cpu_limit),
            "--memory", memory,
            "--memory-swap", memory,
            "--user", "65534:65534",
            "--ulimit", "nofile=1024:1024",
            "--mount", f"type=bind,src={source},dst=/input,readonly",
            "--tmpfs", f"/workspace:rw,nosuid,nodev,size={workspace_mb}m,mode=1777",
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=128m,mode=1777",
            "--env", "HOME=/tmp",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--entrypoint", "/bin/sh",
            image,
            "-lc", wrapper,
        ]

    def _collect_artifacts(self, container_name: str, run_id: str, paths: Iterable[str]) -> tuple[list[str], int, Path | None]:
        temp_root = Path(tempfile.mkdtemp(prefix=f"{run_id}-", dir=str(self._artifact_root)))
        collected: list[str] = []
        total = 0
        try:
            for raw in paths:
                rel = self._safe_artifact_path(raw)
                destination = temp_root / rel.name
                result = self._run_docker("cp", f"{container_name}:/workspace/{rel.as_posix()}", str(destination), timeout=30)
                if result.returncode != 0:
                    continue
                size, count = self._scan_artifact(destination)
                if count > self.ARTIFACT_MAX_FILES or total + size > self.ARTIFACT_MAX_BYTES:
                    raise IsolatedRuntimeError("Runtime artifacts превышают безопасный лимит.")
                total += size
                collected.append(rel.as_posix())
            if not collected:
                shutil.rmtree(temp_root, ignore_errors=True)
                return [], 0, None
            zip_path = self._artifact_root / f"{run_id}.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for item in sorted(temp_root.rglob("*")):
                    if item.is_file() and not item.is_symlink():
                        archive.write(item, item.relative_to(temp_root).as_posix())
            return collected, total, zip_path
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    @staticmethod
    def _safe_artifact_path(raw: str) -> PurePosixPath:
        path = PurePosixPath(raw)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise IsolatedRuntimeError("Недопустимый runtime artifact path.")
        return path

    def _scan_artifact(self, root: Path) -> tuple[int, int]:
        if root.is_symlink():
            raise IsolatedRuntimeError("Symlink в runtime artifacts запрещён.")
        if root.is_file():
            return root.stat().st_size, 1
        total = 0; count = 0
        for item in root.rglob("*"):
            if item.is_symlink():
                raise IsolatedRuntimeError("Symlink в runtime artifacts запрещён.")
            if item.is_file():
                total += item.stat().st_size; count += 1
        return total, count

    def _image_status(self, image: str) -> tuple[bool, str | None, bool]:
        try:
            result = self._run_docker(
                "image", "inspect", image,
                "--format", '{{.Id}}|{{index .Config.Labels "org.ai-studio.runtime"}}',
                timeout=8,
            )
            if result.returncode == 0:
                raw = (result.stdout or "").strip()
                image_id, _, label = raw.partition("|")
                trusted = label.strip() == "p2-010"
                return True, image_id.strip() or None, trusted
        except Exception:
            pass
        return False, None, False

    def _run_docker(self, *args: str, timeout: int) -> subprocess.CompletedProcess:
        return self._runner(
            [self._docker, *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            shell=False,
        )

    @staticmethod
    def _policy_message(
        evaluation: RuntimePolicyEvaluation,
        *,
        subject: str,
    ) -> str:
        reasons = ", ".join(
            evaluation.reason_codes
        ) or "UNSPECIFIED_POLICY_REASON"

        if evaluation.approval_required:
            return (
                f"Policy approval required for "
                f"{subject}: {reasons}"
            )

        return (
            f"Policy denied {subject}: {reasons}"
        )

    @classmethod
    def _require_policy_allowed(
        cls,
        evaluation: RuntimePolicyEvaluation,
        *,
        subject: str,
    ) -> None:
        if evaluation.allowed:
            return

        message = cls._policy_message(
            evaluation,
            subject=subject,
        )

        if evaluation.approval_required:
            raise (
                IsolatedRuntimePolicyApprovalRequiredError(
                    message
                )
            )

        raise IsolatedRuntimePolicyDeniedError(
            message
        )

    @classmethod
    def _trim_output(cls, text: str) -> str:
        text = text.strip()
        return text if len(text) <= cls.OUTPUT_LIMIT else text[-cls.OUTPUT_LIMIT:]

    @staticmethod
    def _verification_item(row: CodeSandboxRuntimeRunModel) -> CodeSandboxVerificationItem:
        status = "passed" if row.status == "passed" else "failed"
        return CodeSandboxVerificationItem(
            profile=row.profile,
            status=status,
            exit_code=row.exit_code,
            duration_ms=row.duration_ms,
            output=row.output,
            execution_backend="docker",
            runtime_run_id=row.id,
            image=row.image,
        )

    def _require_row(self, run_id: str, workspace_id: str | None) -> CodeSandboxRuntimeRunModel:
        stmt = select(CodeSandboxRuntimeRunModel).where(CodeSandboxRuntimeRunModel.id == run_id)
        stmt = stmt.where(CodeSandboxRuntimeRunModel.workspace_id.is_(None) if workspace_id is None else CodeSandboxRuntimeRunModel.workspace_id == workspace_id)
        row = self._session.scalar(stmt)
        if row is None:
            raise IsolatedRuntimeError("Runtime run не найден.")
        return row

    @staticmethod
    def _to_schema(row: CodeSandboxRuntimeRunModel) -> IsolatedRuntimeRun:
        return IsolatedRuntimeRun(
            id=row.id,
            session_id=row.session_id,
            workspace_id=row.workspace_id,
            profile=row.profile,
            backend="docker",
            status=row.status,
            image=row.image,
            image_id=row.image_id,
            network_mode=row.network_mode,
            cpu_limit=row.cpu_limit,
            memory_mb=row.memory_mb,
            pids_limit=row.pids_limit,
            timeout_seconds=row.timeout_seconds,
            exit_code=row.exit_code,
            duration_ms=row.duration_ms,
            timed_out=row.timed_out,
            output=row.output,
            artifact_paths=list(row.artifact_paths_json or []),
            artifact_bytes=row.artifact_bytes,
            artifact_available=bool((row.metadata_json or {}).get("artifact_zip_path")),
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            updated_at=row.updated_at,
        )

    async def _publish(self, event_type: str, row: CodeSandboxRuntimeRunModel, extra: dict) -> None:
        payload = {"runtime_run_id": row.id, "session_id": row.session_id, "workspace_id": row.workspace_id, **extra}
        await self._event_bus.publish(Event(event_type=event_type, source="isolated_runtime", payload=payload))
