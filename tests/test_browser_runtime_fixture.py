from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from backend.browser_runtime import (
    BrowserDownloadPolicy,
    BrowserEffect,
    BrowserEngine,
    BrowserExecutionSpec,
    BrowserFilesystemMode,
    BrowserNetworkMode,
    BrowserRuntimeRequirements,
    BrowserScriptArtifact,
    fingerprint_input,
    sha256_bytes,
)
from backend.browser_runtime.runtime import (
    BrowserRuntimeBindingError,
    BrowserRuntimeConfig,
    BrowserRuntimeExecutionError,
    BrowserRuntimeImageError,
    BrowserRuntimeProfileError,
    BrowserRuntimeProtocolError,
    BrowserRuntimeService,
    RUNNER_SCHEMA_VERSION,
    RUNTIME_LABEL,
    RUNTIME_LABEL_VALUE,
    RUNTIME_VERSION_LABEL,
    RUNTIME_VERSION_VALUE,
)


SOURCE = b"""async def run(page, context):
    await page.goto(context["fixture_origin"] + "/")
    return {"ok": True}
"""


def artifact(source: bytes = SOURCE) -> BrowserScriptArtifact:
    return BrowserScriptArtifact(
        artifact_id="browser-runtime-script-001",
        revision_id="browser-runtime-rev-001",
        source_sha256=sha256_bytes(source),
        entrypoint="browser/runtime_script.py",
    )


def requirements(
    *,
    network_mode: BrowserNetworkMode = BrowserNetworkMode.FIXTURE_ONLY,
    filesystem_mode: BrowserFilesystemMode = (
        BrowserFilesystemMode.READ_ONLY_WORKSPACE
    ),
    browser_engine: BrowserEngine = BrowserEngine.CHROMIUM,
    download_policy: BrowserDownloadPolicy = BrowserDownloadPolicy.DENY,
    capture_screenshots: bool = False,
    credential_scopes: tuple[str, ...] = (),
) -> BrowserRuntimeRequirements:
    return BrowserRuntimeRequirements(
        browser_engine=browser_engine,
        filesystem_mode=filesystem_mode,
        network_mode=network_mode,
        download_policy=download_policy,
        capture_screenshots=capture_screenshots,
        credential_scopes=credential_scopes,
        max_runtime_seconds=30,
        max_memory_mb=512,
        max_pids=64,
    )


def spec(
    raw_input: object,
    *,
    script: BrowserScriptArtifact | None = None,
    runtime_requirements: BrowserRuntimeRequirements | None = None,
    effects: tuple[BrowserEffect, ...] = (BrowserEffect.BROWSER_READ,),
    origins: tuple[str, ...] = (),
) -> BrowserExecutionSpec:
    selected = script or artifact()
    return BrowserExecutionSpec(
        task_id="task-browser-runtime-001",
        revision_id="task-rev-browser-runtime-001",
        workspace_id="workspace-browser-runtime-001",
        execution_initiator="agent",
        script_artifact_fingerprint=selected.fingerprint,
        input_fingerprint=fingerprint_input(raw_input),
        runtime_requirements=runtime_requirements or requirements(),
        requested_capabilities=("browser.execute", "browser.read"),
        effects=effects,
        requested_navigation_origins=origins,
        requested_evidence=("structured_result",),
    )


def fixture_root(tmp_path: Path) -> Path:
    root = tmp_path / "fixture"
    root.mkdir()
    (root / "index.html").write_text(
        "<html><body><h1 id='fixture-title'>ready</h1></body></html>",
        encoding="utf-8",
    )
    return root


def trusted_labels() -> str:
    return json.dumps(
        {
            RUNTIME_LABEL: RUNTIME_LABEL_VALUE,
            RUNTIME_VERSION_LABEL: RUNTIME_VERSION_VALUE,
        }
    )


def success_envelope() -> str:
    return json.dumps(
        {
            "schema_version": RUNNER_SCHEMA_VERSION,
            "ok": True,
            "result": {"status": "ready"},
            "browser_version": "140.0.0.0",
            "automation_runtime_version": "1.55.0",
        }
    )


class RunStub:
    def __init__(self, run_result: subprocess.CompletedProcess[str]) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.run_result = run_result
        self.digest = "sha256:" + ("a" * 64)

    def __call__(self, command, **kwargs):
        cmd = list(command)
        self.calls.append((cmd, dict(kwargs)))
        if cmd[1:3] == ["image", "inspect"]:
            if cmd[-1] == "{{.Id}}":
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=self.digest + "\n",
                    stderr="",
                )
            if cmd[-1] == "{{json .Config.Labels}}":
                assert cmd[3] == self.digest
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=trusted_labels(),
                    stderr="",
                )
        if len(cmd) > 1 and cmd[1] == "run":
            return self.run_result
        if cmd[1:3] == ["rm", "--force"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"Unexpected command: {cmd}")


def test_success_uses_digest_bound_network_none_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_input = {"query": "input-only-marker-7f6c4b"}
    stub = RunStub(
        subprocess.CompletedProcess(
            ["docker", "run"],
            0,
            stdout=success_envelope(),
            stderr="",
        )
    )
    monkeypatch.setattr(
        "backend.browser_runtime.runtime.subprocess.run",
        stub,
    )

    fixture = fixture_root(tmp_path)

    result = BrowserRuntimeService().execute(
        artifact=artifact(),
        spec=spec(raw_input),
        source_bytes=SOURCE,
        raw_input=raw_input,
        fixture_root=fixture,
    )

    assert result.result == {"status": "ready"}
    assert result.attestation.terminal_result.value == "completed"
    assert result.attestation.runtime_image_digest == stub.digest
    assert result.attestation.browser_version == "140.0.0.0"
    assert result.attestation.automation_runtime_version == "1.55.0"
    assert result.attestation.network_policy_fingerprint is not None

    run_call = next(call for call in stub.calls if call[0][1] == "run")
    command, kwargs = run_call
    assert "--interactive" in command
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "no-new-privileges=true" in command
    assert "--pull=never" in command
    assert "--ipc=none" in command
    assert stub.digest in command
    assert "ai-studio-browser-runtime:fixture-v1" not in command
    assert kwargs["shell"] is False
    assert kwargs["check"] is False

    mounts = [
        command[index + 1]
        for index, item in enumerate(command)
        if item == "--mount"
    ]
    assert len(mounts) == 2
    assert all(value.endswith(",readonly") for value in mounts)
    assert any("dst=/input" in value for value in mounts)
    assert all("dst=/input/script.py" not in value for value in mounts)
    assert any("dst=/fixture" in value for value in mounts)

    source_mount = next(value for value in mounts if "dst=/input" in value)
    source_prefix = "type=bind,src="
    source_host_path = source_mount.split(",dst=/input", 1)[0][
        len(source_prefix):
    ]
    assert Path(source_host_path).parent == fixture.parent
    assert not Path(source_host_path).exists()

    stdin = str(kwargs["input"])
    assert json.loads(stdin)["input"] == raw_input
    assert raw_input["query"] not in " ".join(command)


def test_staging_directory_mode_avoids_windows_restrictive_acl() -> None:
    assert BrowserRuntimeService._staging_directory_mode("nt") == 0o755
    assert BrowserRuntimeService._staging_directory_mode("posix") == 0o700


@pytest.mark.parametrize(
    ("changed_source", "changed_input"),
    (
        (b"async def run(page, context):\n    return {'changed': True}\n", None),
        (None, {"query": "changed"}),
    ),
)
def test_binding_drift_rejected_before_docker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_source: bytes | None,
    changed_input: object | None,
) -> None:
    raw_input = {"query": "fixture"}
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("Docker must not be called.")

    monkeypatch.setattr(
        "backend.browser_runtime.runtime.subprocess.run",
        forbidden,
    )

    with pytest.raises(BrowserRuntimeBindingError):
        BrowserRuntimeService().execute(
            artifact=artifact(),
            spec=spec(raw_input),
            source_bytes=changed_source or SOURCE,
            raw_input=changed_input or raw_input,
            fixture_root=fixture_root(tmp_path),
        )

    assert called is False


def test_artifact_fingerprint_drift_rejected_before_docker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_input = {"query": "fixture"}
    other = artifact(b"async def run(page, context):\n    return 2\n")

    monkeypatch.setattr(
        "backend.browser_runtime.runtime.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Docker must not be called.")
        ),
    )

    with pytest.raises(BrowserRuntimeBindingError):
        BrowserRuntimeService().execute(
            artifact=other,
            spec=spec(raw_input, script=artifact()),
            source_bytes=b"async def run(page, context):\n    return 2\n",
            raw_input=raw_input,
            fixture_root=fixture_root(tmp_path),
        )


@pytest.mark.parametrize(
    "bad_requirements",
    (
        requirements(network_mode=BrowserNetworkMode.NONE),
        requirements(
            filesystem_mode=BrowserFilesystemMode.WORKSPACE_ONLY,
        ),
        requirements(browser_engine=BrowserEngine.FIREFOX),
        requirements(
            filesystem_mode=BrowserFilesystemMode.WORKSPACE_ONLY,
            download_policy=BrowserDownloadPolicy.WORKSPACE_ONLY,
        ),
        requirements(capture_screenshots=True),
    ),
)
def test_unsupported_runtime_profiles_fail_before_docker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_requirements: BrowserRuntimeRequirements,
) -> None:
    raw_input = {"query": "fixture"}
    monkeypatch.setattr(
        "backend.browser_runtime.runtime.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Docker must not be called.")
        ),
    )

    with pytest.raises(BrowserRuntimeProfileError):
        BrowserRuntimeService().execute(
            artifact=artifact(),
            spec=spec(
                raw_input,
                runtime_requirements=bad_requirements,
            ),
            source_bytes=SOURCE,
            raw_input=raw_input,
            fixture_root=fixture_root(tmp_path),
        )


def test_restricted_external_profile_fails_before_docker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_input = {"query": "fixture"}
    candidate = spec(
        raw_input,
        runtime_requirements=requirements(
            network_mode=BrowserNetworkMode.RESTRICTED_EXTERNAL,
        ),
        origins=("https://example.com",),
    )
    monkeypatch.setattr(
        "backend.browser_runtime.runtime.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Docker must not be called.")
        ),
    )

    with pytest.raises(BrowserRuntimeProfileError):
        BrowserRuntimeService().execute(
            artifact=artifact(),
            spec=candidate,
            source_bytes=SOURCE,
            raw_input=raw_input,
            fixture_root=fixture_root(tmp_path),
        )


def test_secret_use_profile_fails_before_docker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_input = {"query": "fixture"}
    req = requirements(
        credential_scopes=("secret://env/OPENAI_API_KEY",),
    )
    secret_spec = spec(
        raw_input,
        runtime_requirements=req,
        effects=(
            BrowserEffect.BROWSER_READ,
            BrowserEffect.BROWSER_SECRET_USE,
        ),
    )
    monkeypatch.setattr(
        "backend.browser_runtime.runtime.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Docker must not be called.")
        ),
    )

    with pytest.raises(BrowserRuntimeProfileError):
        BrowserRuntimeService().execute(
            artifact=artifact(),
            spec=secret_spec,
            source_bytes=SOURCE,
            raw_input=raw_input,
            fixture_root=fixture_root(tmp_path),
        )


@pytest.mark.parametrize(
    "effect",
    (
        BrowserEffect.BROWSER_EXTERNAL_WRITE,
        BrowserEffect.BROWSER_UPLOAD,
        BrowserEffect.BROWSER_DOWNLOAD,
    ),
)
def test_side_effect_profiles_fail_before_docker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    effect: BrowserEffect,
) -> None:
    raw_input = {"query": "fixture"}
    req = requirements(
        network_mode=(
            BrowserNetworkMode.RESTRICTED_EXTERNAL
            if effect is BrowserEffect.BROWSER_EXTERNAL_WRITE
            else BrowserNetworkMode.FIXTURE_ONLY
        ),
        filesystem_mode=(
            BrowserFilesystemMode.WORKSPACE_ONLY
            if effect is BrowserEffect.BROWSER_DOWNLOAD
            else BrowserFilesystemMode.READ_ONLY_WORKSPACE
        ),
        download_policy=(
            BrowserDownloadPolicy.WORKSPACE_ONLY
            if effect is BrowserEffect.BROWSER_DOWNLOAD
            else BrowserDownloadPolicy.DENY
        ),
    )
    origins = (
        ("https://example.com",)
        if req.network_mode is BrowserNetworkMode.RESTRICTED_EXTERNAL
        else ()
    )
    candidate = BrowserExecutionSpec(
        task_id="task-browser-runtime-001",
        revision_id="task-rev-browser-runtime-001",
        workspace_id="workspace-browser-runtime-001",
        execution_initiator="agent",
        script_artifact_fingerprint=artifact().fingerprint,
        input_fingerprint=fingerprint_input(raw_input),
        runtime_requirements=req,
        requested_capabilities=("browser.execute",),
        effects=(effect,),
        requested_navigation_origins=origins,
        requested_evidence=("structured_result",),
    )
    monkeypatch.setattr(
        "backend.browser_runtime.runtime.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Docker must not be called.")
        ),
    )

    with pytest.raises(BrowserRuntimeProfileError):
        BrowserRuntimeService().execute(
            artifact=artifact(),
            spec=candidate,
            source_bytes=SOURCE,
            raw_input=raw_input,
            fixture_root=fixture_root(tmp_path),
        )


def test_untrusted_image_label_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_input = {"query": "fixture"}
    digest = "sha256:" + ("a" * 64)
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        cmd = list(command)
        if calls == 1:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=digest + "\n", stderr=""
            )
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps({RUNTIME_LABEL: "wrong"}),
            stderr="",
        )

    monkeypatch.setattr(
        "backend.browser_runtime.runtime.subprocess.run",
        fake_run,
    )

    with pytest.raises(BrowserRuntimeImageError):
        BrowserRuntimeService().execute(
            artifact=artifact(),
            spec=spec(raw_input),
            source_bytes=SOURCE,
            raw_input=raw_input,
            fixture_root=fixture_root(tmp_path),
        )

    assert calls == 2


def test_timeout_forces_container_removal_and_returns_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_input = {"query": "fixture"}
    stub = RunStub(
        subprocess.CompletedProcess(
            ["docker", "run"],
            0,
            stdout=success_envelope(),
            stderr="",
        )
    )
    original = stub

    def timeout_run(command, **kwargs):
        cmd = list(command)
        if len(cmd) > 1 and cmd[1] == "run":
            original.calls.append((cmd, dict(kwargs)))
            raise subprocess.TimeoutExpired(cmd, timeout=30)
        return original(command, **kwargs)

    monkeypatch.setattr(
        "backend.browser_runtime.runtime.subprocess.run",
        timeout_run,
    )

    with pytest.raises(BrowserRuntimeExecutionError) as exc_info:
        BrowserRuntimeService().execute(
            artifact=artifact(),
            spec=spec(raw_input),
            source_bytes=SOURCE,
            raw_input=raw_input,
            fixture_root=fixture_root(tmp_path),
        )

    attestation = exc_info.value.attestation
    assert attestation is not None
    assert attestation.terminal_result.value == "timeout"
    assert attestation.failure_class == "runtime_timeout"
    assert any(
        call[0][1:3] == ["rm", "--force"]
        for call in original.calls
    )


@pytest.mark.parametrize(
    "stdout",
    (
        "",
        "not-json",
        json.dumps({"schema_version": "wrong", "ok": True}),
        json.dumps(
            {
                "schema_version": RUNNER_SCHEMA_VERSION,
                "ok": "yes",
            }
        ),
    ),
)
def test_malformed_runner_protocol_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
) -> None:
    raw_input = {"query": "fixture"}
    stub = RunStub(
        subprocess.CompletedProcess(
            ["docker", "run"],
            0,
            stdout=stdout,
            stderr="",
        )
    )
    monkeypatch.setattr(
        "backend.browser_runtime.runtime.subprocess.run",
        stub,
    )

    with pytest.raises(BrowserRuntimeProtocolError):
        BrowserRuntimeService().execute(
            artifact=artifact(),
            spec=spec(raw_input),
            source_bytes=SOURCE,
            raw_input=raw_input,
            fixture_root=fixture_root(tmp_path),
        )


def test_docker_failure_without_protocol_returns_failed_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_input = {"query": "fixture"}
    stub = RunStub(
        subprocess.CompletedProcess(
            ["docker", "run"],
            125,
            stdout="",
            stderr="docker daemon rejected bind mount",
        )
    )
    monkeypatch.setattr(
        "backend.browser_runtime.runtime.subprocess.run",
        stub,
    )

    with pytest.raises(BrowserRuntimeExecutionError) as exc_info:
        BrowserRuntimeService().execute(
            artifact=artifact(),
            spec=spec(raw_input),
            source_bytes=SOURCE,
            raw_input=raw_input,
            fixture_root=fixture_root(tmp_path),
        )

    attestation = exc_info.value.attestation
    assert attestation is not None
    assert attestation.terminal_result.value == "failed"
    assert attestation.failure_class == "docker_run_failed"


def test_nonzero_runner_exit_is_failed_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_input = {"query": "fixture"}
    stub = RunStub(
        subprocess.CompletedProcess(
            ["docker", "run"],
            70,
            stdout=json.dumps(
                {
                    "schema_version": RUNNER_SCHEMA_VERSION,
                    "ok": False,
                    "error_code": "script_failed",
                }
            ),
            stderr="ignored",
        )
    )
    monkeypatch.setattr(
        "backend.browser_runtime.runtime.subprocess.run",
        stub,
    )

    with pytest.raises(BrowserRuntimeExecutionError) as exc_info:
        BrowserRuntimeService().execute(
            artifact=artifact(),
            spec=spec(raw_input),
            source_bytes=SOURCE,
            raw_input=raw_input,
            fixture_root=fixture_root(tmp_path),
        )

    attestation = exc_info.value.attestation
    assert attestation is not None
    assert attestation.terminal_result.value == "failed"
    assert attestation.failure_class == "script_failed"


def test_input_limit_rejects_before_docker_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_input = {"payload": "x" * 5000}
    config = BrowserRuntimeConfig(input_limit_bytes=1024)
    stub = RunStub(
        subprocess.CompletedProcess(
            ["docker", "run"],
            0,
            stdout=success_envelope(),
            stderr="",
        )
    )
    monkeypatch.setattr(
        "backend.browser_runtime.runtime.subprocess.run",
        stub,
    )

    with pytest.raises(BrowserRuntimeBindingError):
        BrowserRuntimeService(config=config).execute(
            artifact=artifact(),
            spec=spec(raw_input),
            source_bytes=SOURCE,
            raw_input=raw_input,
            fixture_root=fixture_root(tmp_path),
        )

    assert all(
        not (len(call[0]) > 1 and call[0][1] == "run")
        for call in stub.calls
    )


def test_fixture_must_be_nonempty_and_nonindirect(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    raw_input = {"query": "fixture"}
    service = BrowserRuntimeService()

    with pytest.raises(BrowserRuntimeBindingError):
        service.execute(
            artifact=artifact(),
            spec=spec(raw_input),
            source_bytes=SOURCE,
            raw_input=raw_input,
            fixture_root=empty,
        )


def test_runtime_config_validation() -> None:
    with pytest.raises(ValueError):
        BrowserRuntimeConfig(cpu_limit=0)
    with pytest.raises(ValueError):
        BrowserRuntimeConfig(user="root")
    with pytest.raises(ValueError):
        BrowserRuntimeConfig(output_limit_bytes=10)
