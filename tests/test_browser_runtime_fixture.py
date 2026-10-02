from __future__ import annotations

import asyncio
import importlib.util
import io
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from backend.browser_runtime import (
    BROWSER_SERVER_SCHEMA_VERSION,
    BrowserDownloadPolicy,
    BrowserEffect,
    BrowserEngine,
    BrowserExecutionSpec,
    BrowserFilesystemMode,
    BrowserNetworkMode,
    BrowserRuntimeRequirements,
    BrowserScriptArtifact,
    RUNTIME_LABEL,
    RUNTIME_LABEL_VALUE,
    RUNTIME_VERSION_LABEL,
    RUNTIME_VERSION_VALUE,
    SCRIPT_RUNTIME_LABEL,
    SCRIPT_RUNTIME_LABEL_VALUE,
    SCRIPT_RUNTIME_VERSION_LABEL,
    SCRIPT_RUNTIME_VERSION_VALUE,
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
    _BoundedLineReader,
    _MediatedTimeout,
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


def fake_images() -> SimpleNamespace:
    return SimpleNamespace(
        browser_digest="sha256:" + ("a" * 64),
        script_digest="sha256:" + ("b" * 64),
    )


def test_success_constructs_two_isolated_digest_bound_containers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_input = {"query": "input-only-marker-7f6c4b"}
    fixture = fixture_root(tmp_path)
    images = fake_images()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        BrowserRuntimeService,
        "_inspect_trusted_images",
        lambda self: images,
    )

    def fake_mediated(self, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            result={"status": "ready"},
            browser_version="140.0.0.0",
            automation_runtime_version="1.55.0",
        )

    monkeypatch.setattr(
        BrowserRuntimeService,
        "_execute_mediated",
        fake_mediated,
    )

    result = BrowserRuntimeService().execute(
        artifact=artifact(),
        spec=spec(raw_input),
        source_bytes=SOURCE,
        raw_input=raw_input,
        fixture_root=fixture,
    )

    assert result.result == {"status": "ready"}
    assert result.attestation.terminal_result.value == "completed"
    assert result.attestation.runtime_image_digest == images.browser_digest
    assert result.script_runtime_image_digest == images.script_digest
    assert result.attestation.browser_version == "140.0.0.0"
    assert result.attestation.automation_runtime_version == "1.55.0"

    browser_command = captured["browser_command"]
    script_command = captured["script_command"]
    assert isinstance(browser_command, list)
    assert isinstance(script_command, list)

    for command in (browser_command, script_command):
        assert "--interactive" in command
        assert "--network=none" in command
        assert "--read-only" in command
        assert "--ipc=none" in command
        assert "--cap-drop=ALL" in command
        assert "no-new-privileges=true" in command
        assert "--pull=never" in command
        assert "65534:65534" in command

    assert images.browser_digest in browser_command
    assert images.script_digest in script_command
    assert "ai-studio-browser-runtime:mediated-v2" not in browser_command
    assert "ai-studio-browser-script-runtime:mediated-v1" not in script_command

    browser_mounts = [
        browser_command[index + 1]
        for index, item in enumerate(browser_command)
        if item == "--mount"
    ]
    script_mounts = [
        script_command[index + 1]
        for index, item in enumerate(script_command)
        if item == "--mount"
    ]
    assert len(browser_mounts) == 1
    assert len(script_mounts) == 1
    assert "dst=/fixture" in browser_mounts[0]
    assert browser_mounts[0].endswith(",readonly")
    assert "dst=/input" in script_mounts[0]
    assert script_mounts[0].endswith(",readonly")
    assert all("dst=/input" not in item for item in browser_mounts)
    assert all("dst=/fixture" not in item for item in script_mounts)

    source_host_path = script_mounts[0].split(",dst=/input", 1)[0].split(
        "type=bind,src=", 1
    )[1]
    assert Path(source_host_path).parent == fixture.parent
    assert not Path(source_host_path).exists()
    assert raw_input["query"] not in " ".join(browser_command)
    assert raw_input["query"] not in " ".join(script_command)


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
def test_binding_drift_rejected_before_image_or_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_source: bytes | None,
    changed_input: object | None,
) -> None:
    raw_input = {"query": "fixture"}
    called = False

    def forbidden(self):
        nonlocal called
        called = True
        raise AssertionError("Images must not be inspected.")

    monkeypatch.setattr(
        BrowserRuntimeService,
        "_inspect_trusted_images",
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


def test_artifact_fingerprint_drift_rejected_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_input = {"query": "fixture"}
    other_source = b"async def run(page, context):\n    return 2\n"
    other = artifact(other_source)
    monkeypatch.setattr(
        BrowserRuntimeService,
        "_inspect_trusted_images",
        lambda self: (_ for _ in ()).throw(
            AssertionError("Images must not be inspected.")
        ),
    )
    with pytest.raises(BrowserRuntimeBindingError):
        BrowserRuntimeService().execute(
            artifact=other,
            spec=spec(raw_input, script=artifact()),
            source_bytes=other_source,
            raw_input=raw_input,
            fixture_root=fixture_root(tmp_path),
        )


@pytest.mark.parametrize(
    "bad_requirements",
    (
        requirements(network_mode=BrowserNetworkMode.NONE),
        requirements(network_mode=BrowserNetworkMode.RESTRICTED_EXTERNAL),
        requirements(filesystem_mode=BrowserFilesystemMode.WORKSPACE_ONLY),
        requirements(browser_engine=BrowserEngine.FIREFOX),
        requirements(browser_engine=BrowserEngine.WEBKIT),
        requirements(
            filesystem_mode=BrowserFilesystemMode.WORKSPACE_ONLY,
            download_policy=BrowserDownloadPolicy.WORKSPACE_ONLY,
        ),
        requirements(capture_screenshots=True),
    ),
)
def test_unsupported_runtime_profiles_fail_before_image_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_requirements: BrowserRuntimeRequirements,
) -> None:
    raw_input = {"query": "fixture"}
    monkeypatch.setattr(
        BrowserRuntimeService,
        "_inspect_trusted_images",
        lambda self: (_ for _ in ()).throw(
            AssertionError("Images must not be inspected.")
        ),
    )
    candidate = spec(
        raw_input,
        runtime_requirements=bad_requirements,
        origins=(
            ("https://example.com",)
            if bad_requirements.network_mode
            is BrowserNetworkMode.RESTRICTED_EXTERNAL
            else ()
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


def test_secret_use_profile_fails_before_execution(
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
        BrowserRuntimeService,
        "_inspect_trusted_images",
        lambda self: (_ for _ in ()).throw(
            AssertionError("Images must not be inspected.")
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
def test_side_effect_profiles_fail_before_execution(
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
    candidate = spec(
        raw_input,
        runtime_requirements=req,
        effects=(effect,),
        origins=(
            ("https://example.com",)
            if req.network_mode is BrowserNetworkMode.RESTRICTED_EXTERNAL
            else ()
        ),
    )
    monkeypatch.setattr(
        BrowserRuntimeService,
        "_inspect_trusted_images",
        lambda self: (_ for _ in ()).throw(
            AssertionError("Images must not be inspected.")
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


def test_inspect_trusted_images_binds_labels_to_each_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = BrowserRuntimeConfig()
    service = BrowserRuntimeService(config=config)
    browser_digest = "sha256:" + ("a" * 64)
    script_digest = "sha256:" + ("b" * 64)
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        del kwargs
        cmd = list(command)
        calls.append(cmd)
        if cmd[1:3] == ["image", "inspect"]:
            target = cmd[3]
            fmt = cmd[-1]
            if target == config.image and fmt == "{{.Id}}":
                return SimpleNamespace(returncode=0, stdout=browser_digest + "\n")
            if target == config.script_image and fmt == "{{.Id}}":
                return SimpleNamespace(returncode=0, stdout=script_digest + "\n")
            if target == browser_digest and fmt == "{{json .Config.Labels}}":
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            RUNTIME_LABEL: RUNTIME_LABEL_VALUE,
                            RUNTIME_VERSION_LABEL: RUNTIME_VERSION_VALUE,
                        }
                    ),
                )
            if target == script_digest and fmt == "{{json .Config.Labels}}":
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            SCRIPT_RUNTIME_LABEL: SCRIPT_RUNTIME_LABEL_VALUE,
                            SCRIPT_RUNTIME_VERSION_LABEL: (
                                SCRIPT_RUNTIME_VERSION_VALUE
                            ),
                        }
                    ),
                )
        raise AssertionError(cmd)

    monkeypatch.setattr(
        "backend.browser_runtime.runtime.subprocess.run",
        fake_run,
    )

    images = service._inspect_trusted_images()
    assert images.browser_digest == browser_digest
    assert images.script_digest == script_digest
    assert any(call[3] == browser_digest for call in calls if len(call) > 3)
    assert any(call[3] == script_digest for call in calls if len(call) > 3)


def test_untrusted_image_label_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = BrowserRuntimeService()
    digest = "sha256:" + ("a" * 64)
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        del kwargs
        calls += 1
        cmd = list(command)
        if calls == 1:
            return SimpleNamespace(returncode=0, stdout=digest + "\n")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({RUNTIME_LABEL: "wrong"}),
        )

    monkeypatch.setattr(
        "backend.browser_runtime.runtime.subprocess.run",
        fake_run,
    )
    with pytest.raises(BrowserRuntimeImageError):
        service._inspect_trusted_image(
            image=service.config.image,
            label=RUNTIME_LABEL,
            label_value=RUNTIME_LABEL_VALUE,
            version_label=RUNTIME_VERSION_LABEL,
            version_value=RUNTIME_VERSION_VALUE,
        )


def test_timeout_forces_both_containers_and_returns_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_input = {"query": "fixture"}
    images = fake_images()
    removed: list[str] = []
    monkeypatch.setattr(
        BrowserRuntimeService,
        "_inspect_trusted_images",
        lambda self: images,
    )
    monkeypatch.setattr(
        BrowserRuntimeService,
        "_execute_mediated",
        lambda self, **kwargs: (_ for _ in ()).throw(_MediatedTimeout()),
    )
    monkeypatch.setattr(
        BrowserRuntimeService,
        "_force_remove_container",
        lambda self, name: removed.append(name),
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
    assert len(set(removed)) == 2


@pytest.mark.parametrize(
    "line",
    (
        "",
        "not-json\n",
        "[]\n",
    ),
)
def test_malformed_host_protocol_fails_closed(line: str) -> None:
    reader = _BoundedLineReader(io.StringIO(line), limit_bytes=4096)
    service = BrowserRuntimeService()
    with pytest.raises(BrowserRuntimeProtocolError):
        service._read_any_object(reader, deadline=10**12)


def test_bounded_line_reader_rejects_unterminated_overrun_with_bounded_read() -> None:
    calls: list[int] = []

    class RecordingStream(io.StringIO):
        def readline(self, size: int = -1) -> str:
            calls.append(size)
            return super().readline(size)

    reader = _BoundedLineReader(
        RecordingStream("x" * (128 * 1024)),
        limit_bytes=1024,
    )

    with pytest.raises(BrowserRuntimeProtocolError, match="exceeded limit"):
        reader.read(timeout=1)

    assert calls == [1025]
    assert reader._queue.maxsize == 1


def test_bounded_line_reader_close_releases_full_queue_pump() -> None:
    reader = _BoundedLineReader(
        io.StringIO("{}\n{}\n{}\n"),
        limit_bytes=1024,
    )
    deadline = time.monotonic() + 1
    while reader._queue.qsize() != 1 and time.monotonic() < deadline:
        time.sleep(0.001)

    reader.close()
    reader._thread.join(timeout=0.5)

    assert not reader._thread.is_alive()


def test_protocol_write_obeys_shared_deadline() -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingStream:
        def write(self, value: str) -> int:
            started.set()
            release.wait(timeout=2)
            return len(value)

        def flush(self) -> None:
            return None

    service = BrowserRuntimeService()
    deadline = time.monotonic() + 0.05
    try:
        with pytest.raises(_MediatedTimeout):
            service._write_object(
                BlockingStream(),  # type: ignore[arg-type]
                {"type": "blocked"},
                deadline,
            )
        assert started.is_set()
    finally:
        release.set()


def test_mediated_write_timeout_enters_container_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingStream:
        def write(self, value: str) -> int:
            started.set()
            release.wait(timeout=2)
            return len(value)

        def flush(self) -> None:
            return None

    ready = json.dumps(
        {
            "schema_version": BROWSER_SERVER_SCHEMA_VERSION,
            "type": "ready",
            "browser_version": "140",
            "automation_runtime_version": "1.55.0",
            "fixture_origin": "http://127.0.0.1:1234",
        }
    ) + "\n"

    class FakeProcess:
        def __init__(self, *, stdin, stdout) -> None:
            self.stdin = stdin
            self.stdout = stdout
            self.returncode = None

        def poll(self):
            return None

    browser_process = FakeProcess(stdin=io.StringIO(), stdout=io.StringIO(ready))
    script_process = FakeProcess(stdin=BlockingStream(), stdout=io.StringIO())
    processes = iter((browser_process, script_process))
    removed: list[str] = []

    service = BrowserRuntimeService()
    monkeypatch.setattr(service, "_start_process", lambda command: next(processes))
    monkeypatch.setattr(
        service,
        "_force_remove_container",
        lambda name: removed.append(name),
    )

    try:
        with pytest.raises(_MediatedTimeout):
            service._execute_mediated(
                browser_command=["browser"],
                script_command=["script"],
                input_envelope=json.dumps(
                    {
                        "schema_version": "test",
                        "type": "init",
                        "input": {},
                    }
                ),
                timeout_seconds=0.05,
                browser_container="browser-container",
                script_container="script-container",
            )
        assert started.is_set()
        assert removed == ["browser-container", "script-container"]
    finally:
        release.set()


def test_exact_protocol_shape_rejects_unknown_fields() -> None:
    line = json.dumps(
        {
            "schema_version": BROWSER_SERVER_SCHEMA_VERSION,
            "type": "ready",
            "browser_version": "140",
            "automation_runtime_version": "1.55.0",
            "fixture_origin": "http://127.0.0.1:1234",
            "unexpected": True,
        }
    ) + "\n"
    reader = _BoundedLineReader(io.StringIO(line), limit_bytes=4096)
    with pytest.raises(BrowserRuntimeProtocolError):
        BrowserRuntimeService()._read_object(
            reader,
            deadline=10**12,
            exact_keys={
                "schema_version",
                "type",
                "browser_version",
                "automation_runtime_version",
                "fixture_origin",
            },
        )


def _load_script_runner_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "docker"
        / "browser_script"
        / "script_runner.py"
    )
    spec_obj = importlib.util.spec_from_file_location(
        "browser_script_runner_test",
        path,
    )
    assert spec_obj is not None and spec_obj.loader is not None
    module = importlib.util.module_from_spec(spec_obj)
    spec_obj.loader.exec_module(module)
    return module


def _load_security_probe_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "docker"
        / "browser_runtime"
        / "security_probe.py"
    )
    spec_obj = importlib.util.spec_from_file_location(
        "browser_runtime_security_probe_test",
        path,
    )
    assert spec_obj is not None and spec_obj.loader is not None
    module = importlib.util.module_from_spec(spec_obj)
    spec_obj.loader.exec_module(module)
    return module


def test_security_probe_cleanup_query_fails_closed_on_docker_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_security_probe_module()
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Cannot connect to the Docker daemon",
        ),
    )

    with pytest.raises(AssertionError, match="docker container ls failed"):
        module._container_absent("ai-council-browser-test")


def test_security_probe_cleanup_query_uses_exact_container_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_security_probe_module()
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="ai-council-browser-present\nother\n",
            stderr="",
        ),
    )

    assert module._container_absent("ai-council-browser-missing") is True
    assert module._container_absent("ai-council-browser-present") is False


def test_untrusted_page_proxy_has_no_native_context_or_browser() -> None:
    module = _load_script_runner_module()

    class Protocol:
        def request(self, op, args):
            return {"url": "http://127.0.0.1:1/"}

    page = module.PageProxy(Protocol())
    assert not hasattr(page, "context")
    assert not hasattr(page, "browser")
    with pytest.raises(AttributeError):
        _ = page.context
    with pytest.raises(AttributeError):
        _ = page.browser


def test_page_proxy_exposes_only_mediated_ops() -> None:
    module = _load_script_runner_module()
    calls: list[tuple[str, dict[str, object]]] = []

    class Protocol:
        def request(self, op, args):
            calls.append((op, args))
            if op == "locator_inner_text":
                return "ready"
            return {"url": args["url"]}

    async def exercise():
        page = module.PageProxy(Protocol())
        await page.goto(
            "http://127.0.0.1:1234/",
            wait_until="domcontentloaded",
        )
        return await page.locator("#fixture-title").inner_text()

    text = asyncio.run(exercise())
    assert text == "ready"
    assert calls == [
        (
            "goto",
            {
                "url": "http://127.0.0.1:1234/",
                "wait_until": "domcontentloaded",
            },
        ),
        (
            "locator_inner_text",
            {"selector": "#fixture-title"},
        ),
    ]


def test_script_image_contains_no_playwright_or_chromium_dependency() -> None:
    dockerfile = (
        Path(__file__).resolve().parents[1]
        / "docker"
        / "browser_script"
        / "Dockerfile"
    ).read_text(encoding="utf-8").lower()
    runner = (
        Path(__file__).resolve().parents[1]
        / "docker"
        / "browser_script"
        / "script_runner.py"
    ).read_text(encoding="utf-8").lower()
    assert "playwright" not in dockerfile
    assert "chromium" not in dockerfile
    assert "from playwright" not in runner
    assert "import playwright" not in runner


def test_browser_server_explicitly_disables_downloads() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "docker"
        / "browser_runtime"
        / "runner.py"
    ).read_text(encoding="utf-8")
    assert "accept_downloads=False" in source
    assert '"navigation_denied"' in source


def test_download_denial_fixture_is_attachment() -> None:
    runner = (
        Path(__file__).resolve().parents[1]
        / "docker"
        / "browser_runtime"
        / "runner.py"
    ).read_text(encoding="utf-8")
    fixture = (
        Path(__file__).resolve().parents[1]
        / "docker"
        / "browser_runtime"
        / "fixture"
        / "download.txt"
    )
    assert fixture.read_text(encoding="utf-8").strip() == "HELLO_DOWNLOAD"
    assert "Content-Disposition" in runner
    assert "download.txt" in runner


def test_runtime_config_validation() -> None:
    with pytest.raises(ValueError):
        BrowserRuntimeConfig(cpu_limit=0)
    with pytest.raises(ValueError):
        BrowserRuntimeConfig(script_cpu_limit=0)
    with pytest.raises(ValueError):
        BrowserRuntimeConfig(user="root")
    with pytest.raises(ValueError):
        BrowserRuntimeConfig(output_limit_bytes=10)
    with pytest.raises(ValueError):
        BrowserRuntimeConfig(max_rpc_requests=0)
