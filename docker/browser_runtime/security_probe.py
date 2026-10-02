from __future__ import annotations

import json
import os
from pathlib import Path
import sys

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
        max_runtime_seconds=30,
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


def main() -> int:
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

    authority = execute_source(
        name="authority",
        source=b'''async def run(page, context):
    import importlib.util
    import shutil

    return {
        "has_context": hasattr(page, "context"),
        "has_browser": hasattr(page, "browser"),
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
        "playwright_importable": False,
        "chromium_binary": False,
    }

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

    unsupported_source = b'''async def run(page, context):
    protocol = getattr(page, "_PageProxy__protocol")
    protocol.request("new_context", {})
    return {"unexpected": True}
'''
    unsupported_blocked = False
    try:
        execute_source(
            name="unsupported-rpc",
            source=unsupported_source,
        )
    except BrowserRuntimeProtocolError:
        unsupported_blocked = True
    assert unsupported_blocked is True

    print(
        json.dumps(
            {
                "ok": True,
                "download_deny": "PASS",
                "native_browser_authority": "BLOCKED",
                "playwright_import": "BLOCKED",
                "browser_binary": "ABSENT",
                "direct_fixture_socket": "BLOCKED",
                "external_socket": "BLOCKED",
                "child_protocol_pollution": "BLOCKED",
                "unsupported_rpc": "BLOCKED",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    print("ARCH_BROWSER_RUNTIME_001_MEDIATED_SECURITY_PROBE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
