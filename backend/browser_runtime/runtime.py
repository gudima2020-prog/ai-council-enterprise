from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
from typing import Any, TextIO
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


RUNNER_SCHEMA_VERSION = "arch-browser-runtime-001.mediated.v2"
BROWSER_SERVER_SCHEMA_VERSION = "arch-browser-runtime-001.browser-server.v1"
SCRIPT_RUNNER_SCHEMA_VERSION = "arch-browser-runtime-001.script-runner.v1"
RUNTIME_PROVIDER = "docker-playwright-mediated-fixture"

RUNTIME_LABEL = "org.ai-studio.browser-runtime"
RUNTIME_LABEL_VALUE = "arch-browser-runtime-001"
RUNTIME_VERSION_LABEL = "org.ai-studio.browser-runtime-version"
RUNTIME_VERSION_VALUE = "mediated-v2"

SCRIPT_RUNTIME_LABEL = "org.ai-studio.browser-script-runtime"
SCRIPT_RUNTIME_LABEL_VALUE = "arch-browser-runtime-001-script"
SCRIPT_RUNTIME_VERSION_LABEL = "org.ai-studio.browser-script-runtime-version"
SCRIPT_RUNTIME_VERSION_VALUE = "mediated-v1"

_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FIXTURE_ORIGIN_RE = re.compile(r"^http://127\.0\.0\.1:[1-9][0-9]{0,4}$")
_ALLOWED_RPC_OPS = frozenset({"goto", "locator_inner_text"})


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


class _MediatedTimeout(RuntimeError):
    pass


@dataclass(frozen=True)
class BrowserRuntimeConfig:
    image: str = "ai-studio-browser-runtime:mediated-v2"
    script_image: str = "ai-studio-browser-script-runtime:mediated-v1"
    docker_binary: str = "docker"
    cpu_limit: float = 1.0
    script_cpu_limit: float = 0.5
    output_limit_bytes: int = 256 * 1024
    input_limit_bytes: int = 1024 * 1024
    protocol_line_limit_bytes: int = 1024 * 1024
    max_rpc_requests: int = 256
    fixture_max_files: int = 256
    fixture_max_bytes: int = 8 * 1024 * 1024
    tmpfs_size_mb: int = 256
    user: str = "65534:65534"

    def __post_init__(self) -> None:
        if not self.image.strip() or not self.script_image.strip():
            raise ValueError("BrowserRuntime image names cannot be empty.")
        if not self.docker_binary.strip():
            raise ValueError("docker_binary cannot be empty.")
        if not 0.1 <= self.cpu_limit <= 8.0:
            raise ValueError("cpu_limit must be 0.1..8.0.")
        if not 0.1 <= self.script_cpu_limit <= 8.0:
            raise ValueError("script_cpu_limit must be 0.1..8.0.")
        if not 1024 <= self.output_limit_bytes <= 4 * 1024 * 1024:
            raise ValueError("output_limit_bytes must be 1 KiB..4 MiB.")
        if not 1024 <= self.input_limit_bytes <= 8 * 1024 * 1024:
            raise ValueError("input_limit_bytes must be 1 KiB..8 MiB.")
        if not 1024 <= self.protocol_line_limit_bytes <= 2 * 1024 * 1024:
            raise ValueError("protocol_line_limit_bytes must be 1 KiB..2 MiB.")
        if not 1 <= self.max_rpc_requests <= 4096:
            raise ValueError("max_rpc_requests must be 1..4096.")
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
    script_runtime_image_digest: str


@dataclass(frozen=True)
class _TrustedImages:
    browser_digest: str
    script_digest: str


@dataclass(frozen=True)
class _MediatedOutcome:
    result: Any
    browser_version: str
    automation_runtime_version: str


class _BoundedLineReader:
    def __init__(self, stream: TextIO, *, limit_bytes: int) -> None:
        self._stream = stream
        self._limit_bytes = limit_bytes
        self._queue: queue.Queue[tuple[str, str | None]] = queue.Queue(maxsize=1)
        self._closed = threading.Event()
        self._thread = threading.Thread(
            target=self._pump,
            name="browser-runtime-protocol-reader",
            daemon=True,
        )
        self._thread.start()

    def _emit(self, item: tuple[str, str | None]) -> bool:
        while not self._closed.is_set():
            try:
                self._queue.put(item, timeout=0.05)
            except queue.Full:
                continue
            return True
        return False

    def _pump(self) -> None:
        try:
            while not self._closed.is_set():
                # ``readline()`` without a size bound can accumulate an
                # attacker-controlled unterminated line in host memory.  A
                # character cap of limit+1 is sufficient to detect every
                # ASCII overrun; multi-byte UTF-8 is rejected by the exact
                # byte check below and therefore cannot evade the byte limit.
                line = self._stream.readline(self._limit_bytes + 1)
                if line == "":
                    self._emit(("eof", None))
                    return
                if len(line.encode("utf-8", errors="replace")) > self._limit_bytes:
                    self._emit(("oversized", None))
                    return
                if not self._emit(("line", line)):
                    return
        except BaseException:
            self._emit(("error", None))

    def close(self) -> None:
        self._closed.set()

    def read(self, timeout: float) -> str:
        try:
            kind, value = self._queue.get(timeout=max(timeout, 0.001))
        except queue.Empty as exc:
            raise _MediatedTimeout from exc
        if kind == "line" and value is not None:
            return value
        if kind == "oversized":
            raise BrowserRuntimeProtocolError("Runtime protocol line exceeded limit.")
        if kind == "eof":
            raise BrowserRuntimeProtocolError("Runtime protocol closed unexpectedly.")
        raise BrowserRuntimeProtocolError("Runtime protocol reader failed.")


class BrowserRuntimeService:
    """Disposable, mediated, fixture-only browser runtime.

    Untrusted Python executes in a separate minimal container and receives only a
    narrow PageProxy. Native Playwright objects remain inside the trusted browser
    container.
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
        images = self._inspect_trusted_images()

        run_id = f"browser-run-{uuid4().hex}"
        browser_container = f"ai-council-browser-{run_id}"
        script_container = f"ai-council-script-{run_id}"
        started = datetime.now(timezone.utc)

        staging_parent = fixture.parent
        script_root = (
            staging_parent / f".ai-council-browser-runtime-{uuid4().hex}"
        )
        script_root.mkdir(mode=self._staging_directory_mode())

        try:
            script_path = script_root / "script.py"
            script_path.write_bytes(source_bytes)

            try:
                input_envelope = canonical_json(
                    {
                        "schema_version": SCRIPT_RUNNER_SCHEMA_VERSION,
                        "type": "init",
                        "input": raw_input,
                    }
                )
            except Exception as exc:
                raise BrowserRuntimeBindingError(
                    "Raw browser input is not canonical-JSON serializable."
                ) from exc
            if len(input_envelope.encode("utf-8")) > self.config.input_limit_bytes:
                raise BrowserRuntimeBindingError(
                    "Raw browser input exceeds the configured limit."
                )

            browser_command = self._build_browser_command(
                spec=spec,
                fixture_root=fixture,
                image_ref=images.browser_digest,
                container_name=browser_container,
            )
            script_command = self._build_script_command(
                spec=spec,
                script_root=script_root,
                image_ref=images.script_digest,
                container_name=script_container,
            )

            try:
                outcome = self._execute_mediated(
                    browser_command=browser_command,
                    script_command=script_command,
                    input_envelope=input_envelope,
                    timeout_seconds=spec.runtime_requirements.max_runtime_seconds,
                    browser_container=browser_container,
                    script_container=script_container,
                )
            except _MediatedTimeout as exc:
                self._force_remove_container(browser_container)
                self._force_remove_container(script_container)
                finished = datetime.now(timezone.utc)
                attestation = self._attestation(
                    spec=spec,
                    run_id=run_id,
                    image_digest=images.browser_digest,
                    started=started,
                    finished=finished,
                    terminal_result=BrowserTerminalResult.TIMEOUT,
                    failure_class="runtime_timeout",
                )
                raise BrowserRuntimeExecutionError(
                    "Browser runtime timed out.",
                    attestation=attestation,
                ) from exc
            except FileNotFoundError as exc:
                raise BrowserRuntimeUnavailableError(
                    "Docker CLI is unavailable."
                ) from exc
            except BrowserRuntimeError as exc:
                self._force_remove_container(browser_container)
                self._force_remove_container(script_container)
                if exc.attestation is not None:
                    raise
                finished = datetime.now(timezone.utc)
                attestation = self._attestation(
                    spec=spec,
                    run_id=run_id,
                    image_digest=images.browser_digest,
                    started=started,
                    finished=finished,
                    terminal_result=BrowserTerminalResult.FAILED,
                    failure_class=exc.code,
                )
                raise type(exc)(str(exc), attestation=attestation) from exc
        finally:
            try:
                shutil.rmtree(script_root)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise BrowserRuntimeExecutionError(
                    "Browser runtime source staging cleanup failed."
                ) from exc

        finished = datetime.now(timezone.utc)
        attestation = self._attestation(
            spec=spec,
            run_id=run_id,
            image_digest=images.browser_digest,
            started=started,
            finished=finished,
            terminal_result=BrowserTerminalResult.COMPLETED,
            failure_class=None,
            browser_version=outcome.browser_version,
            automation_runtime_version=outcome.automation_runtime_version,
        )
        return BrowserRuntimeExecutionResult(
            run_id=run_id,
            result=outcome.result,
            attestation=attestation,
            script_runtime_image_digest=images.script_digest,
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
            raise BrowserRuntimeBindingError("source_bytes must be bytes.")
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
                "RUNTIME-001 requires download_policy=deny."
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

    def _inspect_trusted_images(self) -> _TrustedImages:
        browser = self._inspect_trusted_image(
            image=self.config.image,
            label=RUNTIME_LABEL,
            label_value=RUNTIME_LABEL_VALUE,
            version_label=RUNTIME_VERSION_LABEL,
            version_value=RUNTIME_VERSION_VALUE,
        )
        script = self._inspect_trusted_image(
            image=self.config.script_image,
            label=SCRIPT_RUNTIME_LABEL,
            label_value=SCRIPT_RUNTIME_LABEL_VALUE,
            version_label=SCRIPT_RUNTIME_VERSION_LABEL,
            version_value=SCRIPT_RUNTIME_VERSION_VALUE,
        )
        return _TrustedImages(
            browser_digest=browser,
            script_digest=script,
        )

    def _inspect_trusted_image(
        self,
        *,
        image: str,
        label: str,
        label_value: str,
        version_label: str,
        version_value: str,
    ) -> str:
        digest = self._docker_text(
            "image", "inspect", image, "--format", "{{.Id}}"
        ).strip().lower()
        if not _IMAGE_ID_RE.fullmatch(digest):
            raise BrowserRuntimeImageError("Runtime image digest is invalid.")

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
                "Runtime image labels are malformed."
            ) from exc
        if not isinstance(parsed_labels, dict):
            raise BrowserRuntimeImageError("Runtime image labels are missing.")
        if parsed_labels.get(label) != label_value:
            raise BrowserRuntimeImageError("Runtime image trust label mismatch.")
        if parsed_labels.get(version_label) != version_value:
            raise BrowserRuntimeImageError("Runtime image version label mismatch.")
        return digest

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
                "Trusted runtime image is unavailable."
            )
        return (completed.stdout or "").strip()

    @staticmethod
    def _staging_directory_mode(platform_name: str = os.name) -> int:
        return 0o755 if platform_name == "nt" else 0o700

    def _common_isolation_args(
        self,
        *,
        spec: BrowserExecutionSpec,
        container_name: str,
        cpu_limit: float,
    ) -> list[str]:
        req = spec.runtime_requirements
        memory = f"{req.max_memory_mb}m"
        return [
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
            str(cpu_limit),
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
        ]

    def _build_browser_command(
        self,
        *,
        spec: BrowserExecutionSpec,
        fixture_root: Path,
        image_ref: str,
        container_name: str,
    ) -> list[str]:
        return [
            self.config.docker_binary,
            "run",
            "--interactive",
            *self._common_isolation_args(
                spec=spec,
                container_name=container_name,
                cpu_limit=self.config.cpu_limit,
            ),
            "--mount",
            f"type=bind,src={fixture_root},dst=/fixture,readonly",
            image_ref,
            "--fixture-root",
            "/fixture",
        ]

    def _build_script_command(
        self,
        *,
        spec: BrowserExecutionSpec,
        script_root: Path,
        image_ref: str,
        container_name: str,
    ) -> list[str]:
        return [
            self.config.docker_binary,
            "run",
            "--interactive",
            *self._common_isolation_args(
                spec=spec,
                container_name=container_name,
                cpu_limit=self.config.script_cpu_limit,
            ),
            "--mount",
            f"type=bind,src={script_root},dst=/input,readonly",
            image_ref,
            "--script",
            "/input/script.py",
            "--max-result-bytes",
            str(self.config.output_limit_bytes),
        ]

    def _start_process(self, command: list[str]) -> subprocess.Popen[str]:
        return subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            shell=False,
        )

    def _execute_mediated(
        self,
        *,
        browser_command: list[str],
        script_command: list[str],
        input_envelope: str,
        timeout_seconds: int,
        browser_container: str,
        script_container: str,
    ) -> _MediatedOutcome:
        deadline = time.monotonic() + timeout_seconds
        browser_process: subprocess.Popen[str] | None = None
        script_process: subprocess.Popen[str] | None = None
        browser_reader: _BoundedLineReader | None = None
        script_reader: _BoundedLineReader | None = None
        try:
            browser_process = self._start_process(browser_command)
            if browser_process.stdin is None or browser_process.stdout is None:
                raise BrowserRuntimeProtocolError(
                    "Browser container stdio is unavailable."
                )
            browser_reader = _BoundedLineReader(
                browser_process.stdout,
                limit_bytes=self.config.protocol_line_limit_bytes,
            )

            ready = self._read_object(
                browser_reader,
                deadline,
                exact_keys={
                    "schema_version",
                    "type",
                    "browser_version",
                    "automation_runtime_version",
                    "fixture_origin",
                },
            )
            if (
                ready["schema_version"] != BROWSER_SERVER_SCHEMA_VERSION
                or ready["type"] != "ready"
                or not isinstance(ready["browser_version"], str)
                or not ready["browser_version"].strip()
                or not isinstance(ready["automation_runtime_version"], str)
                or not ready["automation_runtime_version"].strip()
                or not isinstance(ready["fixture_origin"], str)
                or not _FIXTURE_ORIGIN_RE.fullmatch(ready["fixture_origin"])
            ):
                raise BrowserRuntimeProtocolError(
                    "Browser server ready envelope is invalid."
                )

            script_process = self._start_process(script_command)
            if script_process.stdin is None or script_process.stdout is None:
                raise BrowserRuntimeProtocolError(
                    "Script container stdio is unavailable."
                )
            script_reader = _BoundedLineReader(
                script_process.stdout,
                limit_bytes=self.config.protocol_line_limit_bytes,
            )
            init = json.loads(input_envelope)
            init["fixture_origin"] = ready["fixture_origin"]
            self._write_object(script_process.stdin, init, deadline)

            request_count = 0
            expected_id = 1

            while True:
                message = self._read_any_object(script_reader, deadline)
                message_type = message.get("type")

                if message_type == "request":
                    if set(message) != {
                        "schema_version",
                        "type",
                        "id",
                        "op",
                        "args",
                    }:
                        raise BrowserRuntimeProtocolError(
                            "Script request envelope shape mismatch."
                        )
                    if message["schema_version"] != SCRIPT_RUNNER_SCHEMA_VERSION:
                        raise BrowserRuntimeProtocolError(
                            "Script request schema mismatch."
                        )
                    if (
                        not isinstance(message["id"], int)
                        or isinstance(message["id"], bool)
                        or message["id"] != expected_id
                    ):
                        raise BrowserRuntimeProtocolError(
                            "Script request id is invalid."
                        )
                    if message["op"] not in _ALLOWED_RPC_OPS:
                        raise BrowserRuntimeProtocolError(
                            "Script requested unsupported browser operation."
                        )
                    if not isinstance(message["args"], dict):
                        raise BrowserRuntimeProtocolError(
                            "Script request args must be an object."
                        )
                    request_count += 1
                    if request_count > self.config.max_rpc_requests:
                        raise BrowserRuntimeProtocolError(
                            "Browser RPC request limit exceeded."
                        )

                    browser_request = {
                        "schema_version": BROWSER_SERVER_SCHEMA_VERSION,
                        "type": "request",
                        "id": message["id"],
                        "op": message["op"],
                        "args": message["args"],
                    }
                    self._write_object(
                        browser_process.stdin,
                        browser_request,
                        deadline,
                    )
                    response = self._read_any_object(browser_reader, deadline)
                    if set(response) not in (
                        {
                            "schema_version",
                            "type",
                            "id",
                            "ok",
                            "result",
                        },
                        {
                            "schema_version",
                            "type",
                            "id",
                            "ok",
                            "error_code",
                        },
                    ):
                        raise BrowserRuntimeProtocolError(
                            "Browser response envelope shape mismatch."
                        )
                    if (
                        response.get("schema_version") != BROWSER_SERVER_SCHEMA_VERSION
                        or response.get("type") != "response"
                        or response.get("id") != expected_id
                        or not isinstance(response.get("ok"), bool)
                    ):
                        raise BrowserRuntimeProtocolError(
                            "Browser response envelope is invalid."
                        )
                    self._write_object(script_process.stdin, response, deadline)
                    expected_id += 1
                    continue

                if message_type == "result":
                    if set(message) != {
                        "schema_version",
                        "type",
                        "result",
                    }:
                        raise BrowserRuntimeProtocolError(
                            "Script result envelope shape mismatch."
                        )
                    if message["schema_version"] != SCRIPT_RUNNER_SCHEMA_VERSION:
                        raise BrowserRuntimeProtocolError(
                            "Script result schema mismatch."
                        )
                    result_json = canonical_json(message["result"])
                    if len(result_json.encode("utf-8")) > self.config.output_limit_bytes:
                        raise BrowserRuntimeProtocolError(
                            "Script result exceeded configured limit."
                        )
                    self._write_object(
                        browser_process.stdin,
                        {
                            "schema_version": BROWSER_SERVER_SCHEMA_VERSION,
                            "type": "shutdown",
                        },
                        deadline,
                    )
                    self._wait_process(script_process, deadline)
                    self._wait_process(browser_process, deadline)
                    if script_process.returncode != 0:
                        raise BrowserRuntimeExecutionError(
                            "Script runtime exited unsuccessfully."
                        )
                    if browser_process.returncode != 0:
                        raise BrowserRuntimeExecutionError(
                            "Browser server exited unsuccessfully."
                        )
                    return _MediatedOutcome(
                        result=message["result"],
                        browser_version=ready["browser_version"].strip(),
                        automation_runtime_version=ready[
                            "automation_runtime_version"
                        ].strip(),
                    )

                if message_type == "error":
                    if set(message) != {
                        "schema_version",
                        "type",
                        "error_code",
                    }:
                        raise BrowserRuntimeProtocolError(
                            "Script error envelope shape mismatch."
                        )
                    if (
                        message["schema_version"] != SCRIPT_RUNNER_SCHEMA_VERSION
                        or not self._valid_error_code(message["error_code"])
                    ):
                        raise BrowserRuntimeProtocolError(
                            "Script error envelope is invalid."
                        )
                    raise BrowserRuntimeExecutionError(
                        "Untrusted browser script failed."
                    )

                raise BrowserRuntimeProtocolError(
                    "Unsupported script protocol message."
                )
        finally:
            if browser_reader is not None:
                browser_reader.close()
            if script_reader is not None:
                script_reader.close()
            if browser_process is not None and browser_process.poll() is None:
                self._force_remove_container(browser_container)
            if script_process is not None and script_process.poll() is None:
                self._force_remove_container(script_container)

    def _read_any_object(
        self,
        reader: _BoundedLineReader,
        deadline: float,
    ) -> dict[str, Any]:
        line = reader.read(self._remaining(deadline))
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BrowserRuntimeProtocolError(
                "Runtime protocol returned malformed JSON."
            ) from exc
        if not isinstance(value, dict):
            raise BrowserRuntimeProtocolError(
                "Runtime protocol envelope must be an object."
            )
        return value

    def _read_object(
        self,
        reader: _BoundedLineReader,
        deadline: float,
        *,
        exact_keys: set[str],
    ) -> dict[str, Any]:
        value = self._read_any_object(reader, deadline)
        if set(value) != exact_keys:
            raise BrowserRuntimeProtocolError(
                "Runtime protocol envelope shape mismatch."
            )
        return value

    def _write_object(
        self,
        stream: TextIO,
        value: dict[str, Any],
        deadline: float,
    ) -> None:
        encoded = canonical_json(value)
        if len(encoded.encode("utf-8")) > self.config.protocol_line_limit_bytes:
            raise BrowserRuntimeProtocolError(
                "Runtime protocol outbound line exceeded limit."
            )
        outcome: queue.Queue[BaseException | None] = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                stream.write(encoded + "\n")
                stream.flush()
            except BaseException as exc:  # fail closed across the thread boundary
                outcome.put(exc)
            else:
                outcome.put(None)

        thread = threading.Thread(
            target=worker,
            name="browser-runtime-protocol-writer",
            daemon=True,
        )
        thread.start()

        try:
            error = outcome.get(timeout=self._remaining(deadline))
        except queue.Empty as exc:
            raise _MediatedTimeout from exc

        if error is not None:
            raise BrowserRuntimeProtocolError(
                "Runtime protocol write failed."
            ) from error

    @staticmethod
    def _valid_error_code(value: Any) -> bool:
        return (
            isinstance(value, str)
            and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", value) is not None
        )

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _MediatedTimeout
        return remaining

    def _wait_process(
        self,
        process: subprocess.Popen[str],
        deadline: float,
    ) -> None:
        try:
            process.wait(timeout=self._remaining(deadline))
        except subprocess.TimeoutExpired as exc:
            raise _MediatedTimeout from exc

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

    @staticmethod
    def _network_policy_fingerprint() -> str:
        return fingerprint_payload(
            "browser-runtime-network-policy-v2",
            {
                "browser_container_network": "none",
                "script_container_network": "none",
                "mediation": "host_stdio_json_rpc",
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
        image_digest: str,
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
            runtime_image_digest=image_digest,
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
