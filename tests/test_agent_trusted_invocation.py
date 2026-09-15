from __future__ import annotations

import pytest

from backend.agent_governance import (
    AGENT_TRUSTED_INVOCATION_SCHEMA_VERSION,
    AgentCapability,
    AgentTrustedDestinationError,
    AgentTrustedDestinationUnavailableError,
    TrustedAgentInvocationResolver,
    TrustedAgentToolBinding,
    fingerprint_agent_tool_input,
)
from backend.runtime_policy import PolicyOperation


def binding(
    *,
    governed_tool_id: str = "filesystem.read",
    action_id: str = "filesystem.read",
    capabilities: tuple[AgentCapability, ...] = (
        AgentCapability.FILESYSTEM_READ,
    ),
    allow_network: bool = False,
) -> TrustedAgentToolBinding:
    return TrustedAgentToolBinding(
        registry_tool_id="tool_123",
        registry_tool_key="trusted.test",
        governed_tool_id=governed_tool_id,
        action_id=action_id,
        runtime_operation=PolicyOperation.METADATA_VALIDATION,
        capabilities=capabilities,
        workspace_id="workspace1",
        kind="builtin",
        risk_level="low",
        isolation_mode="restricted",
        allow_network=allow_network,
        allow_filesystem_read=(
            AgentCapability.FILESYSTEM_READ in capabilities
        ),
        allow_filesystem_write=(
            AgentCapability.FILESYSTEM_WRITE in capabilities
        ),
    )


def test_non_network_action_is_fixed_by_trusted_binding() -> None:
    trusted_binding = binding()
    input_data = {
        "path": "README.md",
        "action_id": "git.push",
    }

    facts = TrustedAgentInvocationResolver().derive(
        binding=trusted_binding,
        validated_input=input_data,
    )

    assert facts.action_id == "filesystem.read"
    assert facts.external_domain is None
    assert facts.binding_fingerprint == trusted_binding.fingerprint
    assert facts.input_fingerprint == fingerprint_agent_tool_input(
        input_data
    )
    assert facts.resolver_id == "binding.fixed-action"
    assert (
        facts.to_dict()["schema_version"]
        == AGENT_TRUSTED_INVOCATION_SCHEMA_VERSION
    )


def test_network_fetch_derives_exact_hostname_without_url_content() -> None:
    trusted_binding = binding(
        governed_tool_id="network.fetch",
        action_id="network.fetch",
        capabilities=(AgentCapability.EXTERNAL_NETWORK,),
        allow_network=True,
    )
    input_data = {
        "url": (
            "https://GitHub.com/repository/path"
            "?private_query=must-not-appear"
        )
    }

    facts = TrustedAgentInvocationResolver().derive(
        binding=trusted_binding,
        validated_input=input_data,
    )

    assert facts.action_id == "network.fetch"
    assert facts.external_domain == "github.com"
    assert facts.input_fingerprint == fingerprint_agent_tool_input(
        input_data
    )

    serialized = str(facts.to_dict())
    assert "repository/path" not in serialized
    assert "private_query" not in serialized
    assert "must-not-appear" not in serialized
    assert facts.resolver_id == "network.fetch.https-url"


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/path",
        "https://user:secret@github.com/path",
        "https://github.com:8443/path",
        "https://127.0.0.1/path",
        "https://localhost/path",
        "https://github.com/path#fragment",
        " https://github.com/path",
        "https://github.com\\@evil.example/path",
    ],
)
def test_network_fetch_rejects_unsafe_destination(url: str) -> None:
    trusted_binding = binding(
        governed_tool_id="network.fetch",
        action_id="network.fetch",
        capabilities=(AgentCapability.EXTERNAL_NETWORK,),
        allow_network=True,
    )

    with pytest.raises(AgentTrustedDestinationError):
        TrustedAgentInvocationResolver().derive(
            binding=trusted_binding,
            validated_input={"url": url},
        )


def test_network_fetch_derives_actual_host_not_claimed_allowlist() -> None:
    trusted_binding = binding(
        governed_tool_id="network.fetch",
        action_id="network.fetch",
        capabilities=(AgentCapability.EXTERNAL_NETWORK,),
        allow_network=True,
    )

    facts = TrustedAgentInvocationResolver().derive(
        binding=trusted_binding,
        validated_input={
            "url": "https://github.com.evil.example/path",
        },
    )

    assert facts.external_domain == "github.com.evil.example"


def test_network_fetch_accepts_default_https_port_only() -> None:
    trusted_binding = binding(
        governed_tool_id="network.fetch",
        action_id="network.fetch",
        capabilities=(AgentCapability.EXTERNAL_NETWORK,),
        allow_network=True,
    )
    resolver = TrustedAgentInvocationResolver()

    implicit = resolver.derive(
        binding=trusted_binding,
        validated_input={"url": "https://github.com/path"},
    )
    explicit = resolver.derive(
        binding=trusted_binding,
        validated_input={"url": "https://github.com:443/path"},
    )

    assert implicit.external_domain == "github.com"
    assert explicit.external_domain == "github.com"


def test_unregistered_network_tool_remains_fail_closed() -> None:
    trusted_binding = binding(
        governed_tool_id="git.push",
        action_id="git.push",
        capabilities=(
            AgentCapability.REMOTE_MUTATION,
            AgentCapability.EXTERNAL_NETWORK,
        ),
        allow_network=True,
    )

    with pytest.raises(
        AgentTrustedDestinationUnavailableError
    ):
        TrustedAgentInvocationResolver().derive(
            binding=trusted_binding,
            validated_input={
                "url": "https://github.com/repository.git"
            },
        )


def test_destination_change_changes_facts_fingerprint() -> None:
    trusted_binding = binding(
        governed_tool_id="network.fetch",
        action_id="network.fetch",
        capabilities=(AgentCapability.EXTERNAL_NETWORK,),
        allow_network=True,
    )
    resolver = TrustedAgentInvocationResolver()

    first = resolver.derive(
        binding=trusted_binding,
        validated_input={"url": "https://github.com/path"},
    )
    second = resolver.derive(
        binding=trusted_binding,
        validated_input={"url": "https://docs.python.org/3/"},
    )

    assert first.external_domain != second.external_domain
    assert first.input_fingerprint != second.input_fingerprint
    assert first.fingerprint != second.fingerprint


def test_same_validated_input_is_deterministic() -> None:
    trusted_binding = binding(
        governed_tool_id="network.fetch",
        action_id="network.fetch",
        capabilities=(AgentCapability.EXTERNAL_NETWORK,),
        allow_network=True,
    )
    resolver = TrustedAgentInvocationResolver()
    input_data = {"url": "https://github.com/path"}

    first = resolver.derive(
        binding=trusted_binding,
        validated_input=input_data,
    )
    second = resolver.derive(
        binding=trusted_binding,
        validated_input=dict(input_data),
    )

    assert first == second
    assert first.fingerprint == second.fingerprint
