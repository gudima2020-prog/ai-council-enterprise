from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.browser_runtime import (  # noqa: E402
    BrowserDownloadPolicy,
    BrowserEffect,
    BrowserEngine,
    BrowserExecutionSpec,
    BrowserFilesystemMode,
    BrowserNetworkMode,
    BrowserRuntimeConfig,
    BrowserRuntimeExecutionError,
    BrowserRuntimeProtocolError,
    BrowserRuntimeRequirements,
    BrowserRuntimeService,
    BrowserScriptArtifact,
    fingerprint_input,
    sha256_bytes,
)


FIXTURE = REPO_ROOT / "docker" / "browser_runtime" / "fixture"


def runtime() -> BrowserRuntimeService:
    return BrowserRuntimeService(
        config=BrowserRuntimeConfig(
            image=os.environ.get(
                "AI_STUDIO_BROWSER_RUNTIME_IMAGE",
                "ai-studio-browser-runtime:mediated-v2",
            ),
            script_image=os.environ.get(
                "AI_STUDIO_BROWSER_SCRIPT_RUNTIME_IMAGE",
                "ai-studio-browser-script-runtime:mediated-v1",
            ),
        )
    )


def execute_source(
    *,
    name: str,
    source: bytes,
    raw_input: object | None = None,
    max_runtime_seconds: int = 30,
):
    input_value = raw_input if raw_input is not None else {"probe": name}
    artifact = BrowserScriptArtifact(
        artifact_id=f"browser-probe-{name}",
        revision_id="browser-probe-r1",
        source_sha256=sha256_bytes(source),
        entrypoint=f"browser/{name}.py",
    )
    requirements = BrowserRuntimeRequirements(
        browser_engine=BrowserEngine.CHROMIUM,
        filesystem_mode=BrowserFilesystemMode.READ_ONLY_WORKSPACE,
        network_mode=BrowserNetworkMode.FIXTURE_ONLY,
        download_policy=BrowserDownloadPolicy.DENY,
        capture_screenshots=False,
        credential_scopes=(),
        max_runtime_seconds=max_runtime_seconds,
        max_memory_mb=1024,
        max_pids=128,
    )
    spec = BrowserExecutionSpec(
        task_id=f"task-browser-probe-{name}",
        revision_id="task-browser-probe-r1",
        workspace_id="workspace-browser-security-probe",
        execution_initiator="agent",
        script_artifact_fingerprint=artifact.fingerprint,
        input_fingerprint=fingerprint_input(input_value),
        runtime_requirements=requirements,
        requested_capabilities=("browser.execute", "browser.read"),
        effects=(BrowserEffect.BROWSER_READ,),
        requested_evidence=("structured_result",),
    )
    return runtime().execute(
        artifact=artifact,
        spec=spec,
        source_bytes=source,
        raw_input=input_value,
        fixture_root=FIXTURE,
    )


def _expect_fail_closed(
    *,
    name: str,
    source: bytes,
    max_runtime_seconds: int = 10,
) -> Exception:
    try:
        execute_source(
            name=name,
            source=source,
            max_runtime_seconds=max_runtime_seconds,
        )
    except (BrowserRuntimeProtocolError, BrowserRuntimeExecutionError) as exc:
        return exc
    raise AssertionError(f"{name} unexpectedly completed successfully.")


def _container_absent(name: str) -> bool:
    completed = subprocess.run(
        ["docker", "container", "inspect", name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
        shell=False,
    )
    return completed.returncode != 0


def _assert_run_containers_absent(run_id: str) -> None:
    browser = f"ai-council-browser-{run_id}"
    script = f"ai-council-script-{run_id}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _container_absent(browser) and _container_absent(script):
            return
        time.sleep(0.2)
    raise AssertionError(
        f"Runtime containers still present after cleanup: {browser}, {script}"
    )


def _running_container_names(prefix: str) -> set[str]:
    completed = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        raise AssertionError("docker ps failed during signal probe.")
    return {
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip().startswith(prefix)
    }


def _wait_for_new_script_container(
    *,
    before: set[str],
    timeout_seconds: float = 10,
) -> str:
    prefix = "ai-council-script-browser-run-"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current = _running_container_names(prefix)
        new_names = sorted(current - before)
        if len(new_names) == 1:
            return new_names[0]
        if len(new_names) > 1:
            raise AssertionError(
                f"Multiple new script containers found: {new_names}"
            )
        time.sleep(0.1)
    raise AssertionError("Timed out waiting for script container.")


def _run_host_signal_probe(
    *,
    signal_name: str,
    expected_timeout: bool,
) -> Exception:
    before = _running_container_names(
        "ai-council-script-browser-run-"
    )
    outcome: queue.Queue[tuple[str, object]] = queue.Queue()

    source = b'''async def run(page, context):
    import asyncio
    await asyncio.sleep(30)
    return {"unexpected": True}
'''

    def worker() -> None:
        try:
            value = execute_source(
                name=f"host-signal-{signal_name.lower()}",
                source=source,
                max_runtime_seconds=(2 if expected_timeout else 10),
            )
        except BaseException as exc:
            outcome.put(("error", exc))
        else:
            outcome.put(("result", value))

    thread = threading.Thread(
        target=worker,
        name=f"browser-security-signal-{signal_name.lower()}",
        daemon=True,
    )
    thread.start()

    script_name = _wait_for_new_script_container(before=before)
    run_id = script_name.removeprefix("ai-council-script-")
    browser_name = f"ai-council-browser-{run_id}"

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not _container_absent(browser_name):
            break
        time.sleep(0.1)
    else:
        raise AssertionError(
            f"Browser container did not appear for {signal_name}: "
            f"{browser_name}"
        )

    completed = subprocess.run(
        ["docker", "kill", "--signal", signal_name, script_name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"docker kill --signal {signal_name} failed: "
            f"{completed.stderr.strip()}"
        )

    thread.join(timeout=20)
    if thread.is_alive():
        raise AssertionError(
            f"Runtime host thread did not finish after {signal_name}."
        )

    try:
        kind, value = outcome.get_nowait()
    except queue.Empty as exc:
        raise AssertionError("Signal probe produced no host outcome.") from exc

    if kind != "error" or not isinstance(
        value,
        (BrowserRuntimeProtocolError, BrowserRuntimeExecutionError),
    ):
        raise AssertionError(
            f"{signal_name} did not fail closed: {kind} {value!r}"
        )

    if expected_timeout:
        if not isinstance(value, BrowserRuntimeExecutionError):
            raise AssertionError(
                f"{signal_name} timeout returned unexpected error type."
            )
        if (
            value.attestation is None
            or value.attestation.terminal_result.value != "timeout"
        ):
            raise AssertionError(
                f"{signal_name} did not produce timeout attestation."
            )

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _container_absent(browser_name) and _container_absent(script_name):
            return value
        time.sleep(0.2)
    raise AssertionError(
        f"Containers remained after {signal_name}: "
        f"{browser_name}, {script_name}"
    )


def main() -> int:
    results: dict[str, Any] = {}

    download = execute_source(
        name="download-deny",
        source=b'''async def run(page, context):
    blocked = False
    try:
        await page.goto(
            context["fixture_origin"] + "/download.txt",
            wait_until="domcontentloaded",
            timeout=5000,
        )
    except Exception:
        blocked = True
    return {
        "download_blocked": blocked,
        "page_has_download_api": hasattr(page, "expect_download"),
    }
''',
    )
    assert download.result == {
        "download_blocked": True,
        "page_has_download_api": False,
    }
    results["download_deny"] = "PASS"

    authority = execute_source(
        name="authority",
        source=b'''async def run(page, context):
    import importlib.util
    import shutil

    return {
        "has_context": hasattr(page, "context"),
        "has_browser": hasattr(page, "browser"),
        "has_evaluate": hasattr(page, "evaluate"),
        "has_expect_download": hasattr(page, "expect_download"),
        "playwright_importable": (
            importlib.util.find_spec("playwright") is not None
        ),
        "chromium_binary": any(
            shutil.which(name) is not None
            for name in (
                "chromium",
                "chromium-browser",
                "google-chrome",
                "chrome",
            )
        ),
    }
''',
    )
    assert authority.result == {
        "has_context": False,
        "has_browser": False,
        "has_evaluate": False,
        "has_expect_download": False,
        "playwright_importable": False,
        "chromium_binary": False,
    }
    results["native_browser_authority"] = "BLOCKED"
    results["playwright_import"] = "BLOCKED"
    results["browser_binary"] = "ABSENT"

    network = execute_source(
        name="network",
        source=b'''async def run(page, context):
    import socket
    from urllib.parse import urlsplit

    parsed = urlsplit(context["fixture_origin"])

    loopback_blocked = False
    external_blocked = False

    first = socket.socket()
    first.settimeout(2)
    try:
        first.connect(("127.0.0.1", parsed.port))
    except OSError:
        loopback_blocked = True
    finally:
        first.close()

    second = socket.socket()
    second.settimeout(2)
    try:
        second.connect(("1.1.1.1", 443))
    except OSError:
        external_blocked = True
    finally:
        second.close()

    return {
        "direct_fixture_socket_blocked": loopback_blocked,
        "external_socket_blocked": external_blocked,
    }
''',
    )
    assert network.result == {
        "direct_fixture_socket_blocked": True,
        "external_socket_blocked": True,
    }
    results["direct_fixture_socket"] = "BLOCKED"
    results["external_socket"] = "BLOCKED"

    navigation = execute_source(
        name="navigation-denial",
        source=b'''async def run(page, context):
    probes = {
        "https": "https://example.com/",
        "metadata": "http://169.254.169.254/latest/meta-data/",
        "alternate_loopback": "http://127.0.0.1:1/",
        "file": "file:///etc/passwd",
        "data": "data:text/plain,HELLO",
        "websocket": "ws://example.com/socket",
    }
    blocked = {}
    for key, url in probes.items():
        try:
            await page.goto(url, timeout=2000)
        except Exception:
            blocked[key] = True
        else:
            blocked[key] = False
    return blocked
''',
    )
    assert navigation.result == {
        "https": True,
        "metadata": True,
        "alternate_loopback": True,
        "file": True,
        "data": True,
        "websocket": True,
    }
    results["external_navigation"] = "BLOCKED"
    results["websocket_navigation"] = "BLOCKED"

    traversal = execute_source(
        name="fixture-traversal",
        source=b'''async def run(page, context):
    candidates = (
        "/%2e%2e/%2e%2e/etc/passwd",
        "/..%2f..%2fetc%2fpasswd",
    )
    exposed = False
    for candidate in candidates:
        try:
            await page.goto(
                context["fixture_origin"] + candidate,
                wait_until="domcontentloaded",
                timeout=5000,
            )
            body = await page.locator("body").inner_text(timeout=2000)
        except Exception:
            continue
        if "root:x:" in body or "daemon:x:" in body:
            exposed = True
    return {"host_or_container_passwd_exposed": exposed}
''',
    )
    assert traversal.result == {"host_or_container_passwd_exposed": False}
    results["fixture_traversal"] = "BLOCKED"

    child = execute_source(
        name="child-output",
        source=b'''async def run(page, context):
    import subprocess
    import sys

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; print('CHILD_STDOUT_FORGE'); "
            "print('CHILD_STDERR_FORGE', file=sys.stderr)",
        ],
        check=False,
    )
    return {"child_returncode": completed.returncode}
''',
    )
    assert child.result == {"child_returncode": 0}
    results["child_protocol_pollution"] = "BLOCKED"

    proc_view = execute_source(
        name="proc-view",
        source=b'''async def run(page, context):
    import os

    pids = sorted(
        name for name in os.listdir("/proc")
        if name.isdigit()
    )
    fd_targets = {}
    for name in os.listdir("/proc/1/fd"):
        try:
            fd_targets[name] = os.readlink("/proc/1/fd/" + name)
        except OSError:
            fd_targets[name] = "unreadable"
    browser_markers = tuple(
        marker
        for marker in (
            "chromium",
            "chrome",
            "playwright",
            "browser-runtime",
        )
        if any(marker in target.lower() for target in fd_targets.values())
    )
    return {
        "pid_count": len(pids),
        "browser_fd_markers": list(browser_markers),
        "has_proc_1": os.path.isdir("/proc/1"),
    }
''',
    )
    assert proc_view.result["has_proc_1"] is True
    assert proc_view.result["browser_fd_markers"] == []
    results["proc_browser_boundary"] = "NO_BROWSER_FD"

    _expect_fail_closed(
        name="protocol-fd-injection",
        source=b'''async def run(page, context):
    import json
    import os

    payload = (
        json.dumps(
            {
                "schema_version":
                    "arch-browser-runtime-001.script-runner.v1",
                "type": "ready",
                "browser_version": "FORGED",
                "automation_runtime_version": "FORGED",
                "fixture_origin": "http://127.0.0.1:1",
            },
            separators=(",", ":"),
        )
        + "\\n"
    ).encode()

    for name in os.listdir("/proc/1/fd"):
        try:
            fd = int(name)
        except ValueError:
            continue
        if fd <= 2:
            continue
        try:
            os.write(fd, payload)
        except OSError:
            pass

    return {"unexpected": True}
''',
    )
    results["proc_fd_protocol_forgery"] = "BLOCKED"

    unsupported_source = b'''async def run(page, context):
    protocol = getattr(page, "_PageProxy__protocol")
    protocol.request("new_context", {})
    return {"unexpected": True}
'''
    _expect_fail_closed(
        name="unsupported-rpc",
        source=unsupported_source,
    )
    results["unsupported_rpc"] = "BLOCKED"

    pid1_signal_source = b'''async def run(page, context):
    import os
    import signal

    outcome = {}
    for name in ("SIGTERM", "SIGKILL", "SIGSTOP"):
        try:
            os.kill(1, getattr(signal, name))
        except OSError as exc:
            outcome[name] = "error:" + type(exc).__name__
        else:
            outcome[name] = "returned"
    return {
        "self_pid": os.getpid(),
        "pid1_signal_outcomes": outcome,
    }
'''
    try:
        pid1_result = execute_source(
            name="pid1-internal-signal-semantics",
            source=pid1_signal_source,
            max_runtime_seconds=5,
        )
    except (BrowserRuntimeProtocolError, BrowserRuntimeExecutionError):
        results["pid1_internal_signal_semantics"] = "FAIL_CLOSED"
    else:
        assert isinstance(pid1_result.result["self_pid"], int)
        assert isinstance(
            pid1_result.result["pid1_signal_outcomes"],
            dict,
        )
        results["pid1_internal_signal_semantics"] = (
            "KERNEL_RESTRICTED_OR_INEFFECTIVE"
        )

    _run_host_signal_probe(
        signal_name="KILL",
        expected_timeout=False,
    )
    results["host_sigkill_script_runtime"] = "FAIL_CLOSED"
    results["host_sigkill_cleanup"] = "PASS"

    _run_host_signal_probe(
        signal_name="STOP",
        expected_timeout=True,
    )
    results["host_sigstop_script_runtime"] = "TIMEOUT_FAIL_CLOSED"
    results["host_sigstop_cleanup"] = "PASS"

    timeout_exc = _expect_fail_closed(
        name="timeout-cleanup",
        source=b'''async def run(page, context):
    import asyncio
    await asyncio.sleep(30)
    return {"unexpected": True}
''',
        max_runtime_seconds=2,
    )
    assert isinstance(timeout_exc, BrowserRuntimeExecutionError)
    assert timeout_exc.attestation is not None
    assert timeout_exc.attestation.terminal_result.value == "timeout"
    _assert_run_containers_absent(timeout_exc.attestation.run_id)
    results["timeout_cleanup"] = "PASS"

    results["ok"] = True
    print(
        json.dumps(
            results,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    print("ARCH_BROWSER_RUNTIME_001_MEDIATED_SECURITY_PROBE_V2=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
