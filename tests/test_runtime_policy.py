from backend.runtime_policy import (
    DataClassification,
    PolicyAction,
    PolicyOperation,
    ProviderTrust,
    RuntimePolicyContext,
    RuntimePolicyEngine,
    RuntimeTrust,
)


def test_public_data_allows_external_provider() -> None:
    decision = RuntimePolicyEngine().evaluate(
        RuntimePolicyContext(
            operation=PolicyOperation.MODEL_INFERENCE,
            data_classification=DataClassification.PUBLIC,
            provider_trust=ProviderTrust.EXTERNAL,
        )
    )

    assert decision.action == PolicyAction.ALLOW
    assert decision.allowed is True


def test_internal_data_preserves_external_provider_compatibility() -> None:
    decision = RuntimePolicyEngine().evaluate(
        RuntimePolicyContext(
            operation=PolicyOperation.MODEL_INFERENCE,
            provider_trust=ProviderTrust.EXTERNAL,
        )
    )

    assert decision.action == PolicyAction.ALLOW


def test_confidential_data_denies_standard_external_provider() -> None:
    decision = RuntimePolicyEngine().evaluate(
        RuntimePolicyContext(
            operation=PolicyOperation.MODEL_INFERENCE,
            data_classification=DataClassification.CONFIDENTIAL,
            provider_trust=ProviderTrust.EXTERNAL,
        )
    )

    assert decision.action == PolicyAction.DENY


def test_confidential_data_requires_approval_for_trusted_external() -> None:
    decision = RuntimePolicyEngine().evaluate(
        RuntimePolicyContext(
            operation=PolicyOperation.MODEL_INFERENCE,
            data_classification=DataClassification.CONFIDENTIAL,
            provider_trust=ProviderTrust.TRUSTED_EXTERNAL,
        )
    )

    assert decision.action == PolicyAction.REQUIRE_APPROVAL
    assert decision.approval_required is True


def test_restricted_data_allows_local_provider() -> None:
    decision = RuntimePolicyEngine().evaluate(
        RuntimePolicyContext(
            operation=PolicyOperation.MODEL_INFERENCE,
            data_classification=DataClassification.RESTRICTED,
            provider_trust=ProviderTrust.LOCAL,
        )
    )

    assert decision.action == PolicyAction.ALLOW


def test_restricted_data_denies_external_provider() -> None:
    decision = RuntimePolicyEngine().evaluate(
        RuntimePolicyContext(
            operation=PolicyOperation.MODEL_INFERENCE,
            data_classification=DataClassification.RESTRICTED,
            provider_trust=ProviderTrust.TRUSTED_EXTERNAL,
        )
    )

    assert decision.action == PolicyAction.DENY


def test_secret_material_is_always_denied() -> None:
    decision = RuntimePolicyEngine().evaluate(
        RuntimePolicyContext(
            operation=PolicyOperation.MODEL_INFERENCE,
            provider_trust=ProviderTrust.LOCAL,
            contains_secrets=True,
        )
    )

    assert decision.action == PolicyAction.DENY
    assert decision.reason_codes == ("SECRET_MATERIAL_PRESENT",)


def test_sensitive_code_requires_isolated_runtime() -> None:
    decision = RuntimePolicyEngine().evaluate(
        RuntimePolicyContext(
            operation=PolicyOperation.CODE_EXECUTION,
            data_classification=DataClassification.CONFIDENTIAL,
            runtime_trust=RuntimeTrust.SAFE_HOST_PROFILE,
        )
    )

    assert decision.action == PolicyAction.REQUIRE_ISOLATION
    assert decision.required_runtime == RuntimeTrust.ISOLATED_CONTAINER


def test_internal_code_allows_safe_host_profile() -> None:
    decision = RuntimePolicyEngine().evaluate(
        RuntimePolicyContext(
            operation=PolicyOperation.CODE_EXECUTION,
            runtime_trust=RuntimeTrust.SAFE_HOST_PROFILE,
        )
    )

    assert decision.action == PolicyAction.ALLOW


def test_restricted_artifact_export_is_denied() -> None:
    decision = RuntimePolicyEngine().evaluate(
        RuntimePolicyContext(
            operation=PolicyOperation.ARTIFACT_EXPORT,
            data_classification=DataClassification.RESTRICTED,
        )
    )

    assert decision.action == PolicyAction.DENY


def test_decision_fingerprint_is_deterministic() -> None:
    context = RuntimePolicyContext(
        operation=PolicyOperation.MODEL_INFERENCE,
        data_classification=DataClassification.INTERNAL,
        provider_trust=ProviderTrust.EXTERNAL,
        workspace_id="workspace-1",
    )
    engine = RuntimePolicyEngine()

    first = engine.evaluate(context)
    second = engine.evaluate(context)

    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64
