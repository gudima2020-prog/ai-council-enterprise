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
    BrowserRuntimeRequirements,
    BrowserRuntimeService,
    BrowserScriptArtifact,
    fingerprint_input,
    sha256_bytes,
)


def main() -> int:
    fixture = REPO_ROOT / "docker" / "browser_runtime" / "fixture"
    script_path = REPO_ROOT / "docker" / "browser_runtime" / "smoke_script.py"
    source = script_path.read_bytes()
    raw_input = {"probe": "ready"}

    artifact = BrowserScriptArtifact(
        artifact_id="browser-runtime-smoke",
        revision_id="browser-runtime-smoke-r1",
        source_sha256=sha256_bytes(source),
        entrypoint="browser/smoke_script.py",
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
        task_id="task-browser-runtime-smoke",
        revision_id="task-browser-runtime-smoke-r1",
        workspace_id="workspace-browser-runtime-smoke",
        execution_initiator="agent",
        script_artifact_fingerprint=artifact.fingerprint,
        input_fingerprint=fingerprint_input(raw_input),
        runtime_requirements=requirements,
        requested_capabilities=("browser.execute", "browser.read"),
        effects=(BrowserEffect.BROWSER_READ,),
        requested_evidence=("structured_result",),
    )

    config = BrowserRuntimeConfig(
        image=os.environ.get(
            "AI_STUDIO_BROWSER_RUNTIME_IMAGE",
            "ai-studio-browser-runtime:mediated-v2",
        ),
        script_image=os.environ.get(
            "AI_STUDIO_BROWSER_SCRIPT_RUNTIME_IMAGE",
            "ai-studio-browser-script-runtime:mediated-v1",
        ),
    )
    result = BrowserRuntimeService(config=config).execute(
        artifact=artifact,
        spec=spec,
        source_bytes=source,
        raw_input=raw_input,
        fixture_root=fixture,
    )
    expected = {
        "title": "Browser Runtime Fixture",
        "status": "ready",
        "probe": "ready",
    }
    if result.result != expected:
        raise RuntimeError("BrowserRuntime mediated smoke result mismatch.")

    print(
        json.dumps(
            {
                "ok": True,
                "result": result.result,
                "browser_version": result.attestation.browser_version,
                "automation_runtime_version": (
                    result.attestation.automation_runtime_version
                ),
                "browser_image_digest": (
                    result.attestation.runtime_image_digest
                ),
                "script_image_digest": (
                    result.script_runtime_image_digest
                ),
                "network_policy_fingerprint": (
                    result.attestation.network_policy_fingerprint
                ),
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
