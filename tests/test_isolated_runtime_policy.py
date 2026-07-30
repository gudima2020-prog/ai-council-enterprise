import pytest

from backend.code_sandbox.runtime_policy import (
    IsolatedRuntimePolicy,
)
from backend.core.events import EventBus
from backend.runtime_policy import (
    DataClassification,
    PolicyAction,
    PolicyOperation,
    RuntimeTrust,
)


@pytest.mark.asyncio
async def test_diff_check_is_host_metadata_validation() -> None:
    policy = IsolatedRuntimePolicy(
        event_bus=EventBus(),
        resolver=lambda workspace_id: (
            DataClassification.RESTRICTED
        ),
    )

    result = await policy.evaluate_profile(
        workspace_id="workspace-1",
        profile="diff_check",
    )

    assert result.allowed is True
    assert (
        result.operation
        == PolicyOperation.METADATA_VALIDATION
    )
    assert (
        result.runtime_trust
        == RuntimeTrust.SAFE_HOST_PROFILE
    )
    assert result.reason_codes == (
        "HOST_METADATA_VALIDATION_ALLOWED",
    )


@pytest.mark.asyncio
async def test_internal_python_compile_uses_isolated_runtime() -> None:
    policy = IsolatedRuntimePolicy(
        event_bus=EventBus(),
    )

    result = await policy.evaluate_profile(
        workspace_id="workspace-1",
        profile="python_compile",
    )

    assert result.allowed is True
    assert (
        result.operation
        == PolicyOperation.CODE_EXECUTION
    )
    assert (
        result.runtime_trust
        == RuntimeTrust.ISOLATED_CONTAINER
    )
    assert result.reason_codes == (
        "ISOLATED_RUNTIME_ALLOWED",
    )


@pytest.mark.asyncio
async def test_restricted_pytest_is_allowed_in_container() -> None:
    policy = IsolatedRuntimePolicy(
        event_bus=EventBus(),
        resolver=lambda workspace_id: (
            DataClassification.RESTRICTED
        ),
    )

    result = await policy.evaluate_profile(
        workspace_id="workspace-1",
        profile="pytest",
    )

    assert result.allowed is True
    assert (
        result.data_classification
        == DataClassification.RESTRICTED
    )
    assert (
        result.runtime_trust
        == RuntimeTrust.ISOLATED_CONTAINER
    )


@pytest.mark.asyncio
async def test_restricted_artifact_export_is_denied() -> None:
    policy = IsolatedRuntimePolicy(
        event_bus=EventBus(),
        resolver=lambda workspace_id: (
            DataClassification.RESTRICTED
        ),
    )

    result = (
        await policy.evaluate_artifact_export(
            workspace_id="workspace-1",
        )
    )

    assert result.action == PolicyAction.DENY
    assert result.reason_codes == (
        "RESTRICTED_ARTIFACT_EXPORT_DENIED",
    )


@pytest.mark.asyncio
async def test_confidential_artifact_export_requires_approval() -> None:
    policy = IsolatedRuntimePolicy(
        event_bus=EventBus(),
        resolver=lambda workspace_id: (
            DataClassification.CONFIDENTIAL
        ),
    )

    result = (
        await policy.evaluate_artifact_export(
            workspace_id="workspace-1",
        )
    )

    assert (
        result.action
        == PolicyAction.REQUIRE_APPROVAL
    )
    assert result.approval_required is True


@pytest.mark.asyncio
async def test_policy_resolution_failure_is_fail_closed() -> None:
    def failing_resolver(
        workspace_id: str | None,
    ) -> DataClassification:
        raise RuntimeError(
            "Database unavailable"
        )

    policy = IsolatedRuntimePolicy(
        event_bus=EventBus(),
        resolver=failing_resolver,
    )

    result = await policy.evaluate_profile(
        workspace_id="workspace-1",
        profile="python_compile",
    )

    assert result.action == PolicyAction.DENY
    assert result.fingerprint is None
    assert result.reason_codes == (
        "POLICY_RESOLUTION_FAILED",
    )
    assert (
        result.resolution_error
        == "RuntimeError"
    )


@pytest.mark.asyncio
async def test_policy_fingerprint_is_deterministic() -> None:
    policy = IsolatedRuntimePolicy(
        event_bus=EventBus(),
        resolver=lambda workspace_id: (
            DataClassification.CONFIDENTIAL
        ),
    )

    first = await policy.evaluate_profile(
        workspace_id="workspace-1",
        profile="python_compile",
    )
    second = await policy.evaluate_profile(
        workspace_id="workspace-1",
        profile="python_compile",
    )

    assert first.fingerprint is not None
    assert (
        first.fingerprint
        == second.fingerprint
    )
    assert len(first.fingerprint) == 64
