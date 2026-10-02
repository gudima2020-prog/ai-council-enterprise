from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
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

    for signal_name in ("SIGTERM", "SIGKILL"):
        _expect_fail_closed(
            name=f"signal-{signal_name.lower()}",
            source=f'''async def run(page, context):
    import os
    import signal
    os.kill(1, signal.{signal_name})
    return {{"unexpected": True}}
'''.encode(),
        )
    results["script_pid1_term_kill"] = "FAIL_CLOSED"

    stop_exc = _expect_fail_closed(
        name="signal-sigstop",
        source=b'''async def run(page, context):
    import os
    import signal
    os.kill(1, signal.SIGSTOP)
    return {"unexpected": True}
''',
        max_runtime_seconds=2,
    )
    assert isinstance(stop_exc, BrowserRuntimeExecutionError)
    assert stop_exc.attestation is not None
    assert stop_exc.attestation.terminal_result.value == "timeout"
    _assert_run_containers_absent(stop_exc.attestation.run_id)
    results["script_pid1_stop"] = "TIMEOUT_FAIL_CLOSED"
    results["sigstop_cleanup"] = "PASS"

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
