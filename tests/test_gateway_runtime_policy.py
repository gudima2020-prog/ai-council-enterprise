import pytest

from backend.core.events import EventBus
from backend.gateway.policy import (
    GatewayRoutePolicy,
)
from backend.gateway.schemas import (
    GatewayMessage,
    GatewayRequest,
)
from backend.runtime_policy import (
    DataClassification,
    ProviderTrust,
)


def make_request(
    *,
    provider: str = "openrouter",
    workspace_id: str | None = "workspace-1",
) -> GatewayRequest:
    return GatewayRequest(
        messages=[
            GatewayMessage(
                role="user",
                content="Test prompt",
            ),
        ],
        model="test-model",
        provider=provider,
        workspace_id=workspace_id,
        correlation_id="corr-1",
    )


@pytest.mark.asyncio
async def test_default_internal_external_route_is_allowed() -> None:
    original = make_request()
    policy = GatewayRoutePolicy(
        event_bus=EventBus(),
    )

    enriched, rejection = await policy.evaluate(
        original
    )

    assert rejection is None
    assert original.metadata == {}

    metadata = enriched.metadata[
        "runtime_policy"
    ]

    assert metadata["action"] == "allow"
    assert (
        metadata["data_classification"]
        == "internal"
    )
    assert (
        metadata["provider_trust"]
        == "external"
    )
    assert len(metadata["fingerprint"]) == 64


@pytest.mark.asyncio
async def test_confidential_external_route_is_denied() -> None:
    policy = GatewayRoutePolicy(
        event_bus=EventBus(),
        resolver=lambda workspace_id, provider: (
            DataClassification.CONFIDENTIAL,
            ProviderTrust.EXTERNAL,
        ),
    )

    enriched, rejection = await policy.evaluate(
        make_request()
    )

    assert (
        enriched.metadata["runtime_policy"][
            "action"
        ]
        == "deny"
    )
    assert rejection is not None
    assert rejection.status == "error"
    assert rejection.error is not None
    assert (
        rejection.error.code
        == "POLICY_DENIED"
    )
    assert rejection.error.recoverable is False


@pytest.mark.asyncio
async def test_confidential_trusted_external_requires_approval() -> None:
    policy = GatewayRoutePolicy(
        event_bus=EventBus(),
        resolver=lambda workspace_id, provider: (
            DataClassification.CONFIDENTIAL,
            ProviderTrust.TRUSTED_EXTERNAL,
        ),
    )

    _, rejection = await policy.evaluate(
        make_request()
    )

    assert rejection is not None
    assert rejection.error is not None
    assert (
        rejection.error.code
        == "POLICY_APPROVAL_REQUIRED"
    )


@pytest.mark.asyncio
async def test_restricted_local_route_is_allowed() -> None:
    policy = GatewayRoutePolicy(
        event_bus=EventBus(),
        resolver=lambda workspace_id, provider: (
            DataClassification.RESTRICTED,
            ProviderTrust.LOCAL,
        ),
    )

    enriched, rejection = await policy.evaluate(
        make_request(provider="ollama")
    )

    assert rejection is None

    metadata = enriched.metadata[
        "runtime_policy"
    ]

    assert metadata["action"] == "allow"
    assert (
        metadata["data_classification"]
        == "restricted"
    )
    assert (
        metadata["provider_trust"]
        == "local"
    )


@pytest.mark.asyncio
async def test_policy_resolution_failure_is_fail_closed() -> None:
    def failing_resolver(
        workspace_id: str | None,
        provider: str,
    ):
        raise RuntimeError(
            "Database unavailable"
        )

    policy = GatewayRoutePolicy(
        event_bus=EventBus(),
        resolver=failing_resolver,
    )

    enriched, rejection = await policy.evaluate(
        make_request()
    )

    assert rejection is not None
    assert rejection.error is not None
    assert (
        rejection.error.code
        == "POLICY_RESOLUTION_FAILED"
    )
    assert (
        enriched.metadata["runtime_policy"][
            "resolution_error"
        ]
        == "RuntimeError"
    )
    assert (
        "Database unavailable"
        not in rejection.error.message
    )


@pytest.mark.asyncio
async def test_auto_pseudo_route_is_not_evaluated() -> None:
    resolver_calls: list[
        tuple[str | None, str]
    ] = []

    def resolver(
        workspace_id: str | None,
        provider: str,
    ):
        resolver_calls.append(
            (workspace_id, provider)
        )
        return (
            DataClassification.RESTRICTED,
            ProviderTrust.BLOCKED,
        )

    request = make_request(provider="auto")
    policy = GatewayRoutePolicy(
        event_bus=EventBus(),
        resolver=resolver,
    )

    returned, rejection = await policy.evaluate(
        request
    )

    assert returned is request
    assert rejection is None
    assert resolver_calls == []
    assert "runtime_policy" not in returned.metadata
