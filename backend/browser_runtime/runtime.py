from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any
from uuid import uuid4

from backend.browser_runtime.contracts import (
    BrowserDownloadPolicy,
    BrowserEffect,
    BrowserEngine,
    BrowserExecutionSpec,
    BrowserFilesystemMode,
    BrowserNetworkMode,
    BrowserRuntimeAttestation,
    BrowserScriptArtifact,
    BrowserTerminalResult,
    canonical_json,
    fingerprint_input,
    fingerprint_payload,
    sha256_bytes,
)


RUNNER_SCHEMA_VERSION = "arch-browser-runtime-001.runner.v1"
RUNTIME_PROVIDER = "docker-playwright-fixture"
RUNTIME_LABEL = "org.ai-studio.browser-runtime"
RUNTIME_LABEL_VALUE = "arch-browser-runtime-001"
RUNTIME_VERSION_LABEL = "org.ai-studio.browser-runtime-version"
RUNTIME_VERSION_VALUE = "fixture-v1"
_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class BrowserRuntimeError(RuntimeError):
    code = "browser_runtime_error"

    def __init__(
        self,
        message: str,
        *,
        attestation: BrowserRuntimeAttestation | None = None,
    ) -> None:
        super().__init__(message)
        self.attestation = attestation


class BrowserRuntimeBindingError(BrowserRuntimeError):
    code = "browser_runtime_binding_error"


class BrowserRuntimeProfileError(BrowserRuntimeError):
    code = "browser_runtime_profile_error"


class BrowserRuntimeUnavailableError(BrowserRuntimeError):
    code = "browser_runtime_unavailable"


class BrowserRuntimeImageError(BrowserRuntimeError):
    code = "browser_runtime_image_error"


class BrowserRuntimeExecutionError(BrowserRuntimeError):
    code = "browser_runtime_execution_error"


class BrowserRuntimeProtocolError(BrowserRuntimeError):
    code = "browser_runtime_protocol_error"


@dataclass(frozen=True)
class BrowserRuntimeConfig:
    image: str = "ai-studio-browser-runtime:fixture-v1"
    docker_binary: str = "docker"
    cpu_limit: float = 1.0
    output_limit_bytes: int = 256 * 1024
    input_limit_bytes: int = 1024 * 1024
    fixture_max_files: int = 256
    fixture_max_bytes: int = 8 * 1024 * 1024
    tmpfs_size_mb: int = 256
    user: str = "65534:65534"

    def __post_init__(self) -> None:
        if not self.image.strip():
            raise ValueError("BrowserRuntime image cannot be empty.")
        if not self.docker_binary.strip():
            raise ValueError("docker_binary cannot be empty.")
        if not 0.1 <= self.cpu_limit <= 8.0:
            raise ValueError("cpu_limit must be 0.1..8.0.")
        if not 1024 <= self.output_limit_bytes <= 4 * 1024 * 1024:
            raise ValueError("output_limit_bytes must be 1 KiB..4 MiB.")
        if not 1024 <= self.input_limit_bytes <= 8 * 1024 * 1024:
            raise ValueError("input_limit_bytes must be 1 KiB..8 MiB.")
        if not 1 <= self.fixture_max_files <= 4096:
            raise ValueError("fixture_max_files must be 1..4096.")
        if not 1024 <= self.fixture_max_bytes <= 128 * 1024 * 1024:
            raise ValueError("fixture_max_bytes must be 1 KiB..128 MiB.")
        if not 16 <= self.tmpfs_size_mb <= 2048:
            raise ValueError("tmpfs_size_mb must be 16..2048.")
        if not re.fullmatch(r"[0-9]+:[0-9]+", self.user):
            raise ValueError("BrowserRuntime user must be numeric uid:gid.")


@dataclass(frozen=True)
class BrowserRuntimeExecutionResult:
    run_id: str
    result: Any
    attestation: BrowserRuntimeAttestation


@dataclass(frozen=True)
class _TrustedImage:
    digest: str


class BrowserRuntimeService:
    """Disposable fixture-only browser runtime.

    This service is deliberately not wired into ToolExecutionRuntime yet.
    It executes only the narrow ARCH-BROWSER-RUNTIME-001 fixture profile.
    """

    def __init__(
        self,
        *,
        config: BrowserRuntimeConfig | None = None,
    ) -> None:
        self.config = config or BrowserRuntimeConfig()

    def execute(
        self,
        *,
        artifact: BrowserScriptArtifact,
        spec: BrowserExecutionSpec,
        source_bytes: bytes,
        raw_input: Any,
        fixture_root: Path,
    ) -> BrowserRuntimeExecutionResult:
        self._validate_binding(
            artifact=artifact,
            spec=spec,
            source_bytes=source_bytes,
            raw_input=raw_input,
        )
        self._validate_profile(spec)
        fixture = self._validate_fixture_root(fixture_root)
        image = self._inspect_trusted_image()

        run_id = f"browser-run-{uuid4().hex}"
        started = datetime.now(timezone.utc)

        with tempfile.TemporaryDirectory(
            prefix="ai-council-browser-runtime-"
        ) as temporary:
            script_root = Path(temporary)
            script_path = script_root / "script.py"
            script_path.write_bytes(source_bytes)

            container_name = f"ai-council-{run_id}"
            command = self._build_docker_command(
                spec=spec,
                script_root=script_root,
                fixture_root=fixture,
                image_ref=image.digest,
                container_name=container_name,
            )
            try:
                stdin_payload = canonical_json(
                    {
                        "schema_version": RUNNER_SCHEMA_VERSION,
                        "input": raw_input,
                    }
                )
            except Exception as exc:
                raise BrowserRuntimeBindingError(
                    "Raw browser input is not canonical-JSON serializable."
                ) from exc
            if (
                len(stdin_payload.encode("utf-8"))
                > self.config.input_limit_bytes
            ):
                raise BrowserRuntimeBindingError(
                    "Raw browser input exceeds the configured limit."
                )

            try:
                completed = subprocess.run(
                    command,
                    input=stdin_payload,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=spec.runtime_requirements.max_runtime_seconds,
                    check=False,
                    shell=False,
                )
            except FileNotFoundError as exc:
                raise BrowserRuntimeUnavailableError(
                    "Docker CLI is unavailable."
                ) from exc
            except subprocess.TimeoutExpired as exc:
                self._force_remove_container(container_name)
                finished = datetime.now(timezone.utc)
                attestation = self._attestation(
                    spec=spec,
                    run_id=run_id,
                    image=image,
                    started=started,
                    finished=finished,
                    terminal_result=BrowserTerminalResult.TIMEOUT,
                    failure_class="runtime_timeout",
                )
                raise BrowserRuntimeExecutionError(
                    "Browser runtime timed out.",
                    attestation=attestation,
                ) from exc

        finished = datetime.now(timezone.utc)

        if len((completed.stdout or "").encode("utf-8")) > self.config.output_limit_bytes:
            attestation = self._attestation(
                spec=spec,
                run_id=run_id,
                image=image,
                started=started,
                finished=finished,
                terminal_result=BrowserTerminalResult.FAILED,
                failure_class="runner_output_limit",
            )
            raise BrowserRuntimeProtocolError(
                "Browser runtime output exceeded the configured limit.",
                attestation=attestation,
            )

        if completed.returncode != 0 and not (completed.stdout or "").strip():
            attestation = self._attestation(
                spec=spec,
                run_id=run_id,
                image=image,
                started=started,
                finished=finished,
                terminal_result=BrowserTerminalResult.FAILED,
                failure_class="docker_run_failed",
            )
            raise BrowserRuntimeExecutionError(
                "Browser runtime process failed before protocol output.",
                attestation=attestation,
            )

        envelope = self._parse_runner_output(completed.stdout or "")

        browser_version = envelope.get("browser_version")
        automation_version = envelope.get("automation_runtime_version")

        if completed.returncode != 0 or envelope.get("ok") is not True:
            failure_class = self._failure_class(envelope)
            attestation = self._attestation(
                spec=spec,
                run_id=run_id,
                image=image,
                started=started,
                finished=finished,
                terminal_result=BrowserTerminalResult.FAILED,
                failure_class=failure_class,
                browser_version=browser_version,
                automation_runtime_version=automation_version,
            )
            raise BrowserRuntimeExecutionError(
                "Browser runtime execution failed.",
                attestation=attestation,
            )

        if not isinstance(browser_version, str) or not browser_version.strip():
            raise BrowserRuntimeProtocolError(
                "Runner did not report browser_version."
            )
        if not isinstance(automation_version, str) or not automation_version.strip():
            raise BrowserRuntimeProtocolError(
                "Runner did not report automation_runtime_version."
            )

        attestation = self._attestation(
            spec=spec,
            run_id=run_id,
            image=image,
            started=started,
            finished=finished,
            terminal_result=BrowserTerminalResult.COMPLETED,
            failure_class=None,
            browser_version=browser_version,
            automation_runtime_version=automation_version,
        )

        return BrowserRuntimeExecutionResult(
            run_id=run_id,
            result=envelope.get("result"),
            attestation=attestation,
        )

    @staticmethod
    def _validate_binding(
        *,
        artifact: BrowserScriptArtifact,
        spec: BrowserExecutionSpec,
        source_bytes: bytes,
        raw_input: Any,
    ) -> None:
        if not isinstance(artifact, BrowserScriptArtifact):
            raise BrowserRuntimeBindingError(
                "artifact must be BrowserScriptArtifact."
            )
        if not isinstance(spec, BrowserExecutionSpec):
            raise BrowserRuntimeBindingError(
                "spec must be BrowserExecutionSpec."
            )
        if not isinstance(source_bytes, bytes):
            raise BrowserRuntimeBindingError(
                "source_bytes must be bytes."
            )
        if sha256_bytes(source_bytes) != artifact.source_sha256:
            raise BrowserRuntimeBindingError(
                "Browser source bytes do not match artifact source_sha256."
            )
        if artifact.fingerprint != spec.script_artifact_fingerprint:
            raise BrowserRuntimeBindingError(
                "BrowserExecutionSpec does not bind the supplied script artifact."
            )
        if fingerprint_input(raw_input) != spec.input_fingerprint:
            raise BrowserRuntimeBindingError(
                "Raw browser input does not match input_fingerprint."
            )

    @staticmethod
    def _validate_profile(spec: BrowserExecutionSpec) -> None:
        req = spec.runtime_requirements
        if req.network_mode is not BrowserNetworkMode.FIXTURE_ONLY:
            raise BrowserRuntimeProfileError(
                "RUNTIME-001 accepts fixture_only network mode only."
            )
        if req.browser_engine is not BrowserEngine.CHROMIUM:
            raise BrowserRuntimeProfileError(
                "RUNTIME-001 supports Chromium only."
            )
        if req.filesystem_mode is not BrowserFilesystemMode.READ_ONLY_WORKSPACE:
            raise BrowserRuntimeProfileError(
                "RUNTIME-001 requires read_only_workspace."
            )
        if req.download_policy is not BrowserDownloadPolicy.DENY:
            raise BrowserRuntimeProfileError(
                "RUNTIME-001 does not allow downloads."
            )
        if req.capture_screenshots is not False:
            raise BrowserRuntimeProfileError(
                "RUNTIME-001 does not materialize screenshots yet."
            )
        if req.credential_scopes:
            raise BrowserRuntimeProfileError(
                "RUNTIME-001 does not accept credential scopes."
            )
        if spec.effects != (BrowserEffect.BROWSER_READ,):
            raise BrowserRuntimeProfileError(
                "RUNTIME-001 supports browser_read only."
            )
        if spec.requested_navigation_origins:
            raise BrowserRuntimeProfileError(
                "fixture_only execution cannot carry requested external origins."
            )

    def _validate_fixture_root(self, fixture_root: Path) -> Path:
        root = Path(fixture_root)
        root_is_junction = getattr(root, "is_junction", lambda: False)()
        if root.is_symlink() or root_is_junction:
            raise BrowserRuntimeBindingError(
                "fixture_root cannot be a symlink or junction."
            )
        if not root.is_dir():
            raise BrowserRuntimeBindingError(
                "fixture_root must be an existing directory."
            )
        root = root.resolve()
        count = 0
        total = 0
        for path in root.rglob("*"):
            path_is_junction = getattr(path, "is_junction", lambda: False)()
            if path.is_symlink() or path_is_junction:
                raise BrowserRuntimeBindingError(
                    "Browser fixture cannot contain symlinks or junctions."
                )
            if path.is_file():
                resolved = path.resolve()
                try:
                    resolved.relative_to(root)
                except ValueError as exc:
                    raise BrowserRuntimeBindingError(
                        "Browser fixture path escapes fixture_root."
                    ) from exc
                count += 1
                total += path.stat().st_size
                if count > self.config.fixture_max_files:
                    raise BrowserRuntimeBindingError(
                        "Browser fixture file limit exceeded."
                    )
                if total > self.config.fixture_max_bytes:
                    raise BrowserRuntimeBindingError(
                        "Browser fixture byte limit exceeded."
                    )
        if count == 0:
            raise BrowserRuntimeBindingError(
                "Browser fixture must contain at least one file."
            )
        return root

    def _inspect_trusted_image(self) -> _TrustedImage:
        digest = self._docker_text(
            "image",
            "inspect",
            self.config.image,
            "--format",
            "{{.Id}}",
        ).strip().lower()
        if not _IMAGE_ID_RE.fullmatch(digest):
            raise BrowserRuntimeImageError(
                "Browser runtime image digest is invalid."
            )

        labels = self._docker_text(
            "image",
            "inspect",
            digest,
            "--format",
            "{{json .Config.Labels}}",
        )
        try:
            parsed_labels = json.loads(labels)
        except json.JSONDecodeError as exc:
            raise BrowserRuntimeImageError(
                "Browser runtime image labels are malformed."
            ) from exc

        if not isinstance(parsed_labels, dict):
            raise BrowserRuntimeImageError(
                "Browser runtime image labels are missing."
            )
        if parsed_labels.get(RUNTIME_LABEL) != RUNTIME_LABEL_VALUE:
            raise BrowserRuntimeImageError(
                "Browser runtime image trust label mismatch."
            )
        if (
            parsed_labels.get(RUNTIME_VERSION_LABEL)
            != RUNTIME_VERSION_VALUE
        ):
            raise BrowserRuntimeImageError(
                "Browser runtime image version label mismatch."
            )
        return _TrustedImage(digest=digest)

    def _docker_text(self, *args: str) -> str:
        try:
            completed = subprocess.run(
                [self.config.docker_binary, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise BrowserRuntimeUnavailableError(
                "Docker CLI is unavailable."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise BrowserRuntimeUnavailableError(
                "Docker image inspection timed out."
            ) from exc
        if completed.returncode != 0:
            raise BrowserRuntimeUnavailableError(
                "Trusted BrowserRuntime image is unavailable."
            )
        return (completed.stdout or "").strip()

    def _build_docker_command(
        self,
        *,
        spec: BrowserExecutionSpec,
        script_root: Path,
        fixture_root: Path,
        image_ref: str,
        container_name: str,
    ) -> list[str]:
        req = spec.runtime_requirements
        memory = f"{req.max_memory_mb}m"
        return [
            self.config.docker_binary,
            "run",
            "--interactive",
            "--rm",
            "--name",
            container_name,
            "--pull=never",
            "--network=none",
            "--ipc=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--pids-limit",
            str(req.max_pids),
            "--cpus",
            str(self.config.cpu_limit),
            "--memory",
            memory,
            "--memory-swap",
            memory,
            "--user",
            self.config.user,
            "--env",
            "HOME=/tmp/home",
            "--env",
            "XDG_CACHE_HOME=/tmp/cache",
            "--tmpfs",
            (
                "/tmp:rw,nosuid,nodev,"
                f"size={self.config.tmpfs_size_mb}m,mode=1777"
            ),
            "--mount",
            f"type=bind,src={script_root},dst=/input,readonly",
            "--mount",
            f"type=bind,src={fixture_root},dst=/fixture,readonly",
            image_ref,
            "--script",
            "/input/script.py",
            "--fixture-root",
            "/fixture",
            "--max-result-bytes",
            str(self.config.output_limit_bytes),
        ]

    def _force_remove_container(self, container_name: str) -> None:
        try:
            subprocess.run(
                [
                    self.config.docker_binary,
                    "rm",
                    "--force",
                    container_name,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
                shell=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    def _parse_runner_output(self, raw: str) -> dict[str, Any]:
        stripped = raw.strip()
        if not stripped:
            raise BrowserRuntimeProtocolError(
                "Browser runtime returned no protocol output."
            )
        try:
            envelope = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise BrowserRuntimeProtocolError(
                "Browser runtime returned malformed JSON."
            ) from exc
        if not isinstance(envelope, dict):
            raise BrowserRuntimeProtocolError(
                "Browser runtime protocol envelope must be an object."
            )
        if envelope.get("schema_version") != RUNNER_SCHEMA_VERSION:
            raise BrowserRuntimeProtocolError(
                "Browser runtime protocol schema mismatch."
            )
        if not isinstance(envelope.get("ok"), bool):
            raise BrowserRuntimeProtocolError(
                "Browser runtime protocol is missing boolean ok."
            )
        return envelope

    @staticmethod
    def _failure_class(envelope: dict[str, Any]) -> str:
        value = envelope.get("error_code")
        if (
            isinstance(value, str)
            and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", value.strip().lower())
        ):
            return value.strip().lower()
        return "runner_failed"

    @staticmethod
    def _network_policy_fingerprint() -> str:
        return fingerprint_payload(
            "browser-runtime-network-policy-v1",
            {
                "docker_network": "none",
                "fixture_transport": "loopback_http",
                "mode": BrowserNetworkMode.FIXTURE_ONLY.value,
                "external_egress": False,
            },
        )

    def _attestation(
        self,
        *,
        spec: BrowserExecutionSpec,
        run_id: str,
        image: _TrustedImage,
        started: datetime,
        finished: datetime,
        terminal_result: BrowserTerminalResult,
        failure_class: str | None,
        browser_version: str | None = None,
        automation_runtime_version: str | None = None,
    ) -> BrowserRuntimeAttestation:
        return BrowserRuntimeAttestation(
            task_id=spec.task_id,
            revision_id=spec.revision_id,
            workspace_id=spec.workspace_id,
            run_id=run_id,
            script_artifact_fingerprint=spec.script_artifact_fingerprint,
            input_fingerprint=spec.input_fingerprint,
            runtime_provider=RUNTIME_PROVIDER,
            runtime_image_digest=image.digest,
            browser_engine=BrowserEngine.CHROMIUM,
            browser_version=browser_version,
            automation_runtime_version=automation_runtime_version,
            network_policy_fingerprint=self._network_policy_fingerprint(),
            governance_policy_fingerprints=(),
            started_at=started,
            finished_at=finished,
            terminal_result=terminal_result,
            failure_class=failure_class,
        )
