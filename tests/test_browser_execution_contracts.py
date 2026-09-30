from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest

from backend.browser_runtime import (
    BROWSER_CONTRACT_SCHEMA_VERSION,
    BrowserContractError,
    BrowserDownloadPolicy,
    BrowserEffect,
    BrowserEvidenceItem,
    BrowserEvidenceManifest,
    BrowserExecutionSpec,
    BrowserFilesystemMode,
    BrowserNetworkMode,
    BrowserProvenanceRef,
    BrowserRuntimeAttestation,
    BrowserRuntimeRequirements,
    BrowserScriptArtifact,
    BrowserTerminalResult,
    EvidenceLocationKind,
    EvidencePrivacyClass,
    EvidenceRedactionState,
    canonical_json,
    fingerprint_input,
    fingerprint_payload,
    sha256_bytes,
)


def digest(seed: str) -> str:
    return sha256_bytes(seed.encode("utf-8"))


def script_artifact() -> BrowserScriptArtifact:
    return BrowserScriptArtifact(
        artifact_id="browser-script-001",
        revision_id="rev-browser-001",
        source_sha256=digest("print('hello')"),
        entrypoint="browser/final_script.py",
        provenance=(
            BrowserProvenanceRef(
                source_kind="task",
                source_id="task-001",
                source_revision="rev-browser-001",
                source_fingerprint=digest("source"),
            ),
        ),
    )


def runtime_requirements(
    *,
    network_mode: BrowserNetworkMode = BrowserNetworkMode.NONE,
    download_policy: BrowserDownloadPolicy = BrowserDownloadPolicy.DENY,
    credential_scopes: tuple[str, ...] = (),
) -> BrowserRuntimeRequirements:
    return BrowserRuntimeRequirements(
        network_mode=network_mode,
        download_policy=download_policy,
        credential_scopes=credential_scopes,
    )


def execution_spec(
    *,
    task_id: str = "task-001",
    revision_id: str = "rev-browser-001",
    script_fp: str | None = None,
    input_fp: str | None = None,
    requirements: BrowserRuntimeRequirements | None = None,
    effects: tuple[BrowserEffect, ...] = (
        BrowserEffect.BROWSER_READ,
    ),
    origins: tuple[str, ...] = (),
) -> BrowserExecutionSpec:
    artifact = script_artifact()
    return BrowserExecutionSpec(
        task_id=task_id,
        revision_id=revision_id,
        workspace_id="workspace-001",
        execution_initiator="agent",
        script_artifact_fingerprint=(
            script_fp or artifact.fingerprint
        ),
        input_fingerprint=(
            input_fp
            or fingerprint_input(
                {"query": "status", "page": 1}
            )
        ),
        requested_capabilities=(
            "browser.execute",
            "browser.read",
        ),
        effects=effects,
        requested_navigation_origins=origins,
        requested_evidence=(
            "structured_result",
            "final_screenshot",
        ),
        runtime_requirements=(
            requirements or runtime_requirements()
        ),
    )


def evidence_item(
    *,
    evidence_id: str = "result",
    location: str = "runs/run-1/result.json",
) -> BrowserEvidenceItem:
    return BrowserEvidenceItem(
        evidence_id=evidence_id,
        evidence_type="structured_result",
        location_kind=EvidenceLocationKind.RELATIVE_PATH,
        location=location,
        sha256=digest(evidence_id + location),
        size_bytes=128,
        redaction_state=(
            EvidenceRedactionState.UNREDACTED_NON_SENSITIVE
        ),
        privacy_classification=EvidencePrivacyClass.INTERNAL,
    )


def test_script_artifact_is_immutable_and_deterministic() -> None:
    first = script_artifact()
    second = script_artifact()

    assert first.fingerprint == second.fingerprint
    assert first.to_dict() == second.to_dict()
    assert first.runtime_kind.value == "python-playwright"
    assert (
        replace(
            first,
            source_sha256=digest("different-source"),
        ).fingerprint
        != first.fingerprint
    )

    with pytest.raises(FrozenInstanceError):
        first.artifact_id = "other"  # type: ignore[misc]


def test_canonical_input_and_domain_separation_are_deterministic() -> None:
    left = {"b": [2, 1], "a": {"x": True}}
    right = {"a": {"x": True}, "b": [2, 1]}

    assert canonical_json(left) == canonical_json(right)
    assert fingerprint_input(left) == fingerprint_input(right)
    assert (
        fingerprint_payload("domain-a", left)
        != fingerprint_payload("domain-b", left)
    )

    with pytest.raises(BrowserContractError):
        canonical_json({1: "not-a-string-key"})

    with pytest.raises(BrowserContractError):
        canonical_json(float("nan"))


def test_execution_fingerprint_binds_task_revision_script_and_input() -> None:
    original = execution_spec()

    variants = (
        replace(original, task_id="task-002"),
        replace(original, revision_id="rev-browser-002"),
        replace(
            original,
            script_artifact_fingerprint=digest("other-script"),
        ),
        replace(
            original,
            input_fingerprint=digest("other-input"),
        ),
    )

    assert len(
        {original.fingerprint, *(item.fingerprint for item in variants)}
    ) == 5


def test_execution_spec_is_requirements_only_not_authorization() -> None:
    spec = execution_spec(
        requirements=runtime_requirements(
            network_mode=BrowserNetworkMode.RESTRICTED_EXTERNAL,
        ),
        origins=("https://Example.COM:443/",),
        effects=(
            BrowserEffect.BROWSER_READ,
            BrowserEffect.BROWSER_EXTERNAL_WRITE,
        ),
    )

    serialized = spec.to_dict()
    assert serialized["requested_navigation_origins"] == [
        "https://example.com"
    ]
    assert serialized["effects"] == [
        "browser_read",
        "browser_external_write",
    ]

    forbidden = {
        "granted_capabilities",
        "authorized",
        "allowed",
        "approval",
        "approval_status",
        "human_approval",
        "observed_evidence",
        "runtime_attestation",
        "evidence_manifest",
    }
    assert forbidden.isdisjoint(serialized)
    assert forbidden.isdisjoint(serialized["runtime_requirements"])


def test_effect_declarations_do_not_create_grants() -> None:
    requirements = runtime_requirements(
        network_mode=BrowserNetworkMode.RESTRICTED_EXTERNAL,
        download_policy=BrowserDownloadPolicy.WORKSPACE_ONLY,
        credential_scopes=("vault.browser.account-a",),
    )
    spec = execution_spec(
        requirements=requirements,
        effects=(
            BrowserEffect.BROWSER_READ,
            BrowserEffect.BROWSER_EXTERNAL_WRITE,
            BrowserEffect.BROWSER_DOWNLOAD,
            BrowserEffect.BROWSER_SECRET_USE,
        ),
        origins=("https://example.com",),
    )

    payload = spec.to_dict()
    assert payload["runtime_requirements"]["credential_scopes"] == [
        "vault.browser.account-a"
    ]
    assert "granted_capabilities" not in payload
    assert "credential_values" not in payload
    assert "secret_values" not in payload


def test_runtime_requirements_fail_closed_on_contradictions() -> None:
    with pytest.raises(BrowserContractError):
        BrowserRuntimeRequirements(
            ephemeral_runtime_required=False,
        )

    with pytest.raises(BrowserContractError):
        BrowserRuntimeRequirements(
            fresh_browser_session_required=False,
        )

    with pytest.raises(BrowserContractError):
        BrowserRuntimeRequirements(
            filesystem_mode=(
                BrowserFilesystemMode.READ_ONLY_WORKSPACE
            ),
            download_policy=(
                BrowserDownloadPolicy.WORKSPACE_ONLY
            ),
        )

    with pytest.raises(BrowserContractError):
        execution_spec(
            origins=("https://example.com",),
        )

    with pytest.raises(BrowserContractError):
        execution_spec(
            requirements=runtime_requirements(
                network_mode=(
                    BrowserNetworkMode.RESTRICTED_EXTERNAL
                ),
            ),
        )

    with pytest.raises(BrowserContractError):
        execution_spec(
            effects=(BrowserEffect.BROWSER_DOWNLOAD,),
        )


def test_credential_scope_is_request_only_and_must_match_effect() -> None:
    with pytest.raises(BrowserContractError):
        execution_spec(
            effects=(BrowserEffect.BROWSER_SECRET_USE,),
        )

    with pytest.raises(BrowserContractError):
        execution_spec(
            requirements=runtime_requirements(
                credential_scopes=("vault.browser.account-a",),
            ),
        )

    spec = execution_spec(
        requirements=runtime_requirements(
            credential_scopes=("vault.browser.account-a",),
        ),
        effects=(
            BrowserEffect.BROWSER_READ,
            BrowserEffect.BROWSER_SECRET_USE,
        ),
    )
    assert spec.runtime_requirements.credential_scopes == (
        "vault.browser.account-a",
    )


@pytest.mark.parametrize(
    "bad_path",
    (
        "../secret.txt",
        "/absolute/result.json",
        r"C:\temp\result.json",
        "runs/../result.json",
        "runs/result.json:ads",
        "runs//result.json",
        "./runs/result.json",
    ),
)
def test_evidence_paths_reject_traversal_absolute_drive_and_ads(
    bad_path: str,
) -> None:
    with pytest.raises(BrowserContractError):
        evidence_item(location=bad_path)


def test_evidence_manifest_is_content_addressed_and_deterministic() -> None:
    artifact = script_artifact()
    item_a = evidence_item(
        evidence_id="result",
        location="runs/run-1/result.json",
    )
    item_b = BrowserEvidenceItem(
        evidence_id="screenshot",
        evidence_type="final_screenshot",
        location_kind=EvidenceLocationKind.RELATIVE_PATH,
        location="runs/run-1/screenshots/final.png",
        sha256=digest("screenshot"),
        size_bytes=1024,
        redaction_state=EvidenceRedactionState.REDACTED,
        privacy_classification=EvidencePrivacyClass.CONFIDENTIAL,
    )

    first = BrowserEvidenceManifest(
        run_id="run-001",
        task_id="task-001",
        revision_id="rev-browser-001",
        workspace_id="workspace-001",
        script_artifact_fingerprint=artifact.fingerprint,
        input_fingerprint=fingerprint_input({"query": "status"}),
        items=(item_b, item_a),
    )
    second = BrowserEvidenceManifest(
        run_id="run-001",
        task_id="task-001",
        revision_id="rev-browser-001",
        workspace_id="workspace-001",
        script_artifact_fingerprint=artifact.fingerprint,
        input_fingerprint=fingerprint_input({"query": "status"}),
        items=(item_a, item_b),
    )

    assert first.fingerprint == second.fingerprint
    assert [item.evidence_id for item in first.items] == [
        "result",
        "screenshot",
    ]

    with pytest.raises(FrozenInstanceError):
        first.run_id = "other"  # type: ignore[misc]


def test_evidence_manifest_rejects_duplicate_ids_and_locations() -> None:
    artifact = script_artifact()
    input_fp = fingerprint_input({"query": "status"})

    with pytest.raises(BrowserContractError):
        BrowserEvidenceManifest(
            run_id="run-001",
            task_id="task-001",
            revision_id="rev-browser-001",
            script_artifact_fingerprint=artifact.fingerprint,
            input_fingerprint=input_fp,
            items=(
                evidence_item(
                    evidence_id="same",
                    location="runs/a.json",
                ),
                evidence_item(
                    evidence_id="same",
                    location="runs/b.json",
                ),
            ),
        )

    with pytest.raises(BrowserContractError):
        BrowserEvidenceManifest(
            run_id="run-001",
            task_id="task-001",
            revision_id="rev-browser-001",
            script_artifact_fingerprint=artifact.fingerprint,
            input_fingerprint=input_fp,
            items=(
                evidence_item(
                    evidence_id="one",
                    location="runs/shared.json",
                ),
                evidence_item(
                    evidence_id="two",
                    location="runs/shared.json",
                ),
            ),
        )


def test_attestation_is_observed_evidence_not_authorization() -> None:
    artifact = script_artifact()
    started = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    attestation = BrowserRuntimeAttestation(
        task_id="task-001",
        revision_id="rev-browser-001",
        workspace_id="workspace-001",
        run_id="run-001",
        script_artifact_fingerprint=artifact.fingerprint,
        input_fingerprint=fingerprint_input({"query": "status"}),
        runtime_provider="docker-worker",
        runtime_image_digest="sha256:" + digest("image"),
        browser_engine="chromium",
        browser_version="140.0.0",
        automation_runtime_version="1.55.0",
        network_policy_fingerprint=digest("network-policy"),
        governance_policy_fingerprints=(
            digest("runtime-policy"),
            digest("workspace-policy"),
        ),
        started_at=started,
        finished_at=started + timedelta(seconds=4),
        terminal_result=BrowserTerminalResult.COMPLETED,
    )

    payload = attestation.to_dict()
    assert payload["terminal_result"] == "completed"
    assert payload["runtime_image_digest"].startswith("sha256:")
    assert "authorized" not in payload
    assert "allowed" not in payload
    assert "granted_capabilities" not in payload
    assert "approval" not in payload

    with pytest.raises(FrozenInstanceError):
        attestation.run_id = "other"  # type: ignore[misc]


def test_attestation_fail_closed_timestamp_and_failure_semantics() -> None:
    artifact = script_artifact()
    now = datetime.now(timezone.utc)

    base = dict(
        task_id="task-001",
        revision_id="rev-browser-001",
        run_id="run-001",
        script_artifact_fingerprint=artifact.fingerprint,
        input_fingerprint=fingerprint_input({"query": "status"}),
        runtime_provider="docker-worker",
        browser_engine="chromium",
    )

    with pytest.raises(BrowserContractError):
        BrowserRuntimeAttestation(
            **base,
            started_at=now,
            finished_at=now - timedelta(seconds=1),
            terminal_result="completed",
        )

    with pytest.raises(BrowserContractError):
        BrowserRuntimeAttestation(
            **base,
            started_at=now,
            finished_at=now,
            terminal_result="failed",
        )

    with pytest.raises(BrowserContractError):
        BrowserRuntimeAttestation(
            **base,
            started_at=now,
            finished_at=now,
            terminal_result="completed",
            failure_class="navigation_error",
        )


def test_unknown_schema_versions_fail_closed() -> None:
    with pytest.raises(BrowserContractError):
        BrowserScriptArtifact(
            artifact_id="artifact",
            revision_id="revision",
            source_sha256=digest("source"),
            schema_version="future.v999",
        )

    with pytest.raises(BrowserContractError):
        BrowserRuntimeRequirements(
            schema_version="future.v999",
        )


def test_script_entrypoint_and_navigation_origins_reject_authority_smuggling() -> None:
    with pytest.raises(BrowserContractError):
        BrowserScriptArtifact(
            artifact_id="artifact",
            revision_id="revision",
            source_sha256=digest("source"),
            entrypoint=r"C:\browser\script.py",
        )

    requirements = runtime_requirements(
        network_mode=BrowserNetworkMode.RESTRICTED_EXTERNAL,
    )

    for origin in (
        "https://user:pass@example.com",
        "https://example.com/path",
        "https://example.com/?token=x",
        "file:///tmp/index.html",
    ):
        with pytest.raises(BrowserContractError):
            execution_spec(
                requirements=requirements,
                origins=(origin,),
            )


def test_observed_contracts_cannot_replace_runtime_requirements() -> None:
    artifact = script_artifact()
    item = evidence_item()
    manifest = BrowserEvidenceManifest(
        run_id="run-001",
        task_id="task-001",
        revision_id="rev-browser-001",
        script_artifact_fingerprint=artifact.fingerprint,
        input_fingerprint=fingerprint_input({"query": "status"}),
        items=(item,),
    )

    with pytest.raises(BrowserContractError):
        BrowserExecutionSpec(
            task_id="task-001",
            revision_id="rev-browser-001",
            execution_initiator="agent",
            script_artifact_fingerprint=artifact.fingerprint,
            input_fingerprint=fingerprint_input({"query": "status"}),
            runtime_requirements=manifest,  # type: ignore[arg-type]
        )


def test_schema_version_is_stable() -> None:
    assert (
        BROWSER_CONTRACT_SCHEMA_VERSION
        == "arch-browser-contract-001.v1"
    )
