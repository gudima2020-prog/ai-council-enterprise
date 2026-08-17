from __future__ import annotations

from copy import deepcopy

import pytest

from backend.agent_governance import (
    AGENT_POLICY_ACTION_ID_METADATA_KEY,
    AGENT_POLICY_RUNTIME_OPERATION_METADATA_KEY,
    AGENT_POLICY_TOOL_ID_METADATA_KEY,
    AgentCapability,
    AgentEnforcementAction,
    AgentEnforcementLayer,
    AgentRegistryDecisionIntegrityError,
    AgentToolBindingIntegrityError,
    BeforeToolExecutionEnforcer,
    TrustedAgentEnforcementAdapter,
    built_in_agent_profiles,
)
from backend.orchestration.models import ToolDefinitionModel
from backend.runtime_policy import (
    DataClassification,
    PolicyOperation,
    ProviderTrust,
    RuntimeTrust,
)
from backend.services.workspace_policy import EffectiveWorkspacePolicy


def _workspace_policy(
    *,
    classification: DataClassification = DataClassification.INTERNAL,
    network_access: str = "restricted",
    filesystem_access: str = "read_write",
    provider_trust: ProviderTrust = ProviderTrust.LOCAL,
) -> EffectiveWorkspacePolicy:
    return EffectiveWorkspacePolicy(
        workspace_id="workspace1",
        provider="provider1",
        model="model1",
        temperature=0.2,
        max_tokens=2048,
        memory_mode="workspace",
        network_access=network_access,
        filesystem_access=filesystem_access,
        data_classification=classification,
        provider_trust={"provider1": provider_trust},
        enabled_plugins=[],
        disabled_plugins=[],
    )


def _tool(
    *,
    tool_id: str = "tool_read",
    tool_key: str = "read",
    governed_tool_id: str = "filesystem.read",
    action_id: str = "filesystem.read",
    runtime_operation: PolicyOperation = PolicyOperation.METADATA_VALIDATION,
    workspace_id: str | None = None,
    kind: str = "builtin",
    risk_level: str = "low",
    allow_network: bool = False,
    allow_filesystem_read: bool = True,
    allow_filesystem_write: bool = False,
    metadata: dict | None = None,
) -> ToolDefinitionModel:
    policy_metadata = {
        AGENT_POLICY_TOOL_ID_METADATA_KEY: governed_tool_id,
        AGENT_POLICY_ACTION_ID_METADATA_KEY: action_id,
        AGENT_POLICY_RUNTIME_OPERATION_METADATA_KEY: runtime_operation.value,
    }
    if metadata is not None:
        policy_metadata = dict(metadata)
    return ToolDefinitionModel(
        id=tool_id,
        workspace_id=workspace_id,
        tool_key=tool_key,
        display_name=tool_key,
        description="",
        kind=kind,
        handler_ref="builtin.echo",
        risk_level=risk_level,
        enabled=True,
        requires_explicit_allow=False,
        isolation_mode="restricted",
        timeout_seconds=60,
        max_concurrency=1,
        max_input_bytes=262144,
        max_output_bytes=1048576,
        allow_network=allow_network,
        allow_filesystem_read=allow_filesystem_read,
        allow_filesystem_write=allow_filesystem_write,
        input_schema_json={"type": "object"},
        output_schema_json={"type": "object"},
        metadata_json=policy_metadata,
    )


def _registry_decision(
    tool: ToolDefinitionModel,
    *,
    allowed: bool = True,
    workspace_id: str = "workspace1",
    agent_id: str | None = "agent1",
    reasons: list[str] | None = None,
    matched_permission_ids: list[str] | None = None,
    allow_permission_ids: list[str] | None = None,
    deny_permission_ids: list[str] | None = None,
    input_size_hint: int = 2,
) -> dict:
    explicit_required = (
        bool(tool.requires_explicit_allow)
        or tool.risk_level in {"high", "critical"}
        or tool.kind in {"http", "subprocess"}
        or bool(tool.allow_network)
        or bool(tool.allow_filesystem_write)
    )
    return {
        "allowed": allowed,
        "tool_id": tool.id,
        "tool_key": tool.tool_key,
        "workspace_id": workspace_id,
        "agent_id": agent_id,
        "risk_level": tool.risk_level,
        "kind": tool.kind,
        "isolation_mode": tool.isolation_mode,
        "explicit_allow_required": explicit_required,
        "matched_permission_ids": list(matched_permission_ids or []),
        "allow_permission_ids": list(allow_permission_ids or []),
        "deny_permission_ids": list(deny_permission_ids or []),
        "reasons": list(reasons or []),
        "capabilities": {
            "network": bool(tool.allow_network),
            "filesystem_read": bool(tool.allow_filesystem_read),
            "filesystem_write": bool(tool.allow_filesystem_write),
        },
        "input_size_hint": input_size_hint,
    }


def _profile(profile_id: str):
    return next(
        item
        for item in built_in_agent_profiles()
        if item.profile_id == profile_id
    )


def test_binding_requires_explicit_governed_metadata() -> None:
    with pytest.raises(AgentToolBindingIntegrityError):
        TrustedAgentEnforcementAdapter().bind_tool(_tool(metadata={}))


def test_binding_rejects_unknown_governed_tool() -> None:
    tool = _tool(
        metadata={
            AGENT_POLICY_TOOL_ID_METADATA_KEY: "unknown.tool",
            AGENT_POLICY_ACTION_ID_METADATA_KEY: "unknown.action",
            AGENT_POLICY_RUNTIME_OPERATION_METADATA_KEY: (
                PolicyOperation.METADATA_VALIDATION.value
            ),
        }
    )
    with pytest.raises(AgentToolBindingIntegrityError):
        TrustedAgentEnforcementAdapter().bind_tool(tool)


def test_binding_rejects_tool_capability_flag_drift() -> None:
    tool = _tool(
        governed_tool_id="network.fetch",
        action_id="network.fetch",
        allow_network=False,
        allow_filesystem_read=False,
    )
    with pytest.raises(AgentToolBindingIntegrityError):
        TrustedAgentEnforcementAdapter().bind_tool(tool)


def test_binding_rejects_code_execution_operation_drift() -> None:
    tool = _tool(
        governed_tool_id="tests.run",
        action_id="tests.run",
        runtime_operation=PolicyOperation.METADATA_VALIDATION,
        allow_filesystem_read=False,
    )
    with pytest.raises(AgentToolBindingIntegrityError):
        TrustedAgentEnforcementAdapter().bind_tool(tool)


def test_build_request_uses_catalog_and_workspace_classification() -> None:
    adapter = TrustedAgentEnforcementAdapter()
    request = adapter.build_request(
        tool=_tool(),
        workspace_policy=_workspace_policy(
            classification=DataClassification.CONFIDENTIAL,
        ),
    )
    assert request.tool_id == "filesystem.read"
    assert request.action_id == "filesystem.read"
    assert request.workspace_id == "workspace1"
    assert request.data_classification == DataClassification.CONFIDENTIAL
    assert request.capabilities == (AgentCapability.FILESYSTEM_READ,)


def test_build_request_requires_domain_for_network_tool() -> None:
    tool = _tool(
        tool_id="tool_network",
        tool_key="network",
        governed_tool_id="network.fetch",
        action_id="network.fetch",
        kind="http",
        allow_network=True,
        allow_filesystem_read=False,
    )
    with pytest.raises(AgentToolBindingIntegrityError):
        TrustedAgentEnforcementAdapter().build_request(
            tool=tool,
            workspace_policy=_workspace_policy(),
        )


def test_build_request_rejects_domain_for_non_network_tool() -> None:
    with pytest.raises(AgentToolBindingIntegrityError):
        TrustedAgentEnforcementAdapter().build_request(
            tool=_tool(),
            workspace_policy=_workspace_policy(),
            external_domain="github.com",
        )


def test_tool_registry_allow_is_bound_to_exact_registry_evidence() -> None:
    adapter = TrustedAgentEnforcementAdapter()
    tool = _tool()
    policy = _registry_decision(tool)
    decision = adapter.tool_registry_decision(
        tool=tool,
        registry_decision=policy,
        workspace_id="workspace1",
        agent_id="agent1",
    )
    assert decision.layer == AgentEnforcementLayer.TOOL_REGISTRY
    assert decision.action == AgentEnforcementAction.ALLOW
    assert decision.reason_codes == ("ALLOWED",)
    replay = adapter.tool_registry_decision(
        tool=tool,
        registry_decision=deepcopy(policy),
        workspace_id="workspace1",
        agent_id="agent1",
    )
    assert replay.upstream_fingerprint == decision.upstream_fingerprint


def test_tool_registry_adapter_rejects_stale_capability_evidence() -> None:
    adapter = TrustedAgentEnforcementAdapter()
    tool = _tool()
    policy = _registry_decision(tool)
    policy["capabilities"]["filesystem_read"] = False
    with pytest.raises(AgentRegistryDecisionIntegrityError):
        adapter.tool_registry_decision(
            tool=tool,
            registry_decision=policy,
            workspace_id="workspace1",
            agent_id="agent1",
        )


def test_tool_registry_adapter_rejects_allowed_network_without_allow_evidence() -> None:
    tool = _tool(
        tool_id="tool_network",
        tool_key="network",
        governed_tool_id="network.fetch",
        action_id="network.fetch",
        kind="http",
        allow_network=True,
        allow_filesystem_read=False,
    )
    with pytest.raises(AgentRegistryDecisionIntegrityError):
        TrustedAgentEnforcementAdapter().tool_registry_decision(
            tool=tool,
            registry_decision=_registry_decision(tool),
            workspace_id="workspace1",
            agent_id="agent1",
        )


def test_tool_registry_deny_preserves_canonical_reasons() -> None:
    adapter = TrustedAgentEnforcementAdapter()
    tool = _tool()
    decision = adapter.tool_registry_decision(
        tool=tool,
        registry_decision=_registry_decision(
            tool,
            allowed=False,
            reasons=["explicit_deny"],
            matched_permission_ids=["perm1"],
            deny_permission_ids=["perm1"],
        ),
        workspace_id="workspace1",
        agent_id="agent1",
    )
    assert decision.action == AgentEnforcementAction.DENY
    assert decision.reason_codes == ("EXPLICIT_DENY",)


def test_workspace_policy_denies_write_in_read_only_workspace() -> None:
    adapter = TrustedAgentEnforcementAdapter()
    tool = _tool(
        tool_id="tool_write",
        tool_key="write",
        governed_tool_id="filesystem.write",
        action_id="filesystem.write",
        allow_filesystem_read=False,
        allow_filesystem_write=True,
    )
    policy = _workspace_policy(filesystem_access="read_only")
    request = adapter.build_request(tool=tool, workspace_policy=policy)
    decision = adapter.workspace_policy_decision(
        workspace_policy=policy,
        request=request,
    )
    assert decision.action == AgentEnforcementAction.DENY
    assert decision.reason_codes == ("FILESYSTEM_WRITE_DENIED",)


def test_workspace_policy_restricted_network_requires_approval() -> None:
    adapter = TrustedAgentEnforcementAdapter()
    tool = _tool(
        tool_id="tool_network",
        tool_key="network",
        governed_tool_id="network.fetch",
        action_id="network.fetch",
        kind="http",
        allow_network=True,
        allow_filesystem_read=False,
    )
    policy = _workspace_policy(network_access="restricted")
    request = adapter.build_request(
        tool=tool,
        workspace_policy=policy,
        external_domain="github.com",
    )
    decision = adapter.workspace_policy_decision(
        workspace_policy=policy,
        request=request,
    )
    assert decision.action == AgentEnforcementAction.REQUIRE_APPROVAL
    assert decision.reason_codes == ("NETWORK_RESTRICTED",)


def test_workspace_policy_allowed_network_can_allow() -> None:
    adapter = TrustedAgentEnforcementAdapter()
    tool = _tool(
        tool_id="tool_network",
        tool_key="network",
        governed_tool_id="network.fetch",
        action_id="network.fetch",
        kind="http",
        allow_network=True,
        allow_filesystem_read=False,
    )
    policy = _workspace_policy(network_access="allowed")
    request = adapter.build_request(
        tool=tool,
        workspace_policy=policy,
        external_domain="github.com",
    )
    assert adapter.workspace_policy_decision(
        workspace_policy=policy,
        request=request,
    ).action == AgentEnforcementAction.ALLOW


def test_runtime_policy_adapter_preserves_sensitive_code_isolation() -> None:
    adapter = TrustedAgentEnforcementAdapter()
    tool = _tool(
        tool_id="tool_tests",
        tool_key="tests",
        governed_tool_id="tests.run",
        action_id="tests.run",
        runtime_operation=PolicyOperation.CODE_EXECUTION,
        allow_filesystem_read=False,
    )
    policy = _workspace_policy(
        classification=DataClassification.CONFIDENTIAL,
    )
    request = adapter.build_request(tool=tool, workspace_policy=policy)
    decision = adapter.runtime_policy_decision(
        tool=tool,
        workspace_policy=policy,
        request=request,
        runtime_trust=RuntimeTrust.SAFE_HOST_PROFILE,
    )
    assert decision.layer == AgentEnforcementLayer.RUNTIME_POLICY
    assert decision.action == AgentEnforcementAction.REQUIRE_ISOLATION
    assert decision.reason_codes == ("SENSITIVE_CODE_REQUIRES_ISOLATION",)


def test_runtime_policy_adapter_derives_provider_trust_for_model_inference() -> None:
    adapter = TrustedAgentEnforcementAdapter()
    tool = _tool(
        tool_id="tool_model",
        tool_key="model",
        governed_tool_id="models.infer",
        action_id="models.infer",
        runtime_operation=PolicyOperation.MODEL_INFERENCE,
        allow_filesystem_read=False,
    )
    policy = _workspace_policy(
        classification=DataClassification.CONFIDENTIAL,
        provider_trust=ProviderTrust.TRUSTED_EXTERNAL,
    )
    request = adapter.build_request(tool=tool, workspace_policy=policy)
    decision = adapter.runtime_policy_decision(
        tool=tool,
        workspace_policy=policy,
        request=request,
    )
    assert decision.action == AgentEnforcementAction.REQUIRE_APPROVAL
    assert decision.reason_codes == (
        "CONFIDENTIAL_EXTERNAL_APPROVAL_REQUIRED",
    )


def test_runtime_policy_adapter_rejects_forged_request_binding() -> None:
    adapter = TrustedAgentEnforcementAdapter()
    tool = _tool()
    policy = _workspace_policy()
    request = adapter.build_request(tool=tool, workspace_policy=policy)
    forged = type(request)(
        tool_id="repository.search",
        action_id=request.action_id,
        data_classification=request.data_classification,
        capabilities=request.capabilities,
        workspace_id=request.workspace_id,
    )
    with pytest.raises(AgentToolBindingIntegrityError):
        adapter.runtime_policy_decision(
            tool=tool,
            workspace_policy=policy,
            request=forged,
        )


def test_adapters_compose_with_before_tool_enforcer_without_bypass() -> None:
    adapter = TrustedAgentEnforcementAdapter()
    tool = _tool(
        tool_id="tool_pr_read",
        tool_key="pr.read",
        governed_tool_id="github.pr.read",
        action_id="github.pr.read",
        runtime_operation=PolicyOperation.METADATA_VALIDATION,
        kind="http",
        allow_network=True,
        allow_filesystem_read=False,
    )
    policy = _workspace_policy(network_access="restricted")
    request = adapter.build_request(
        tool=tool,
        workspace_policy=policy,
        external_domain="github.com",
    )
    registry = adapter.tool_registry_decision(
        tool=tool,
        registry_decision=_registry_decision(
            tool,
            matched_permission_ids=["perm1"],
            allow_permission_ids=["perm1"],
        ),
        workspace_id="workspace1",
        agent_id="agent1",
    )
    workspace = adapter.workspace_policy_decision(
        workspace_policy=policy,
        request=request,
    )
    runtime = adapter.runtime_policy_decision(
        tool=tool,
        workspace_policy=policy,
        request=request,
    )
    profile = _profile("release-manager")
    decision = BeforeToolExecutionEnforcer().evaluate(
        profile=profile,
        expected_profile_fingerprint=profile.fingerprint,
        request=request,
        layer_decisions=(registry, workspace, runtime),
    )
    assert decision.action == AgentEnforcementAction.REQUIRE_APPROVAL
    assert decision.approval_required is True
    assert decision.executable is False
    assert "WORKSPACE_POLICY_NETWORK_RESTRICTED" in decision.reason_codes
    assert "PROFILE_APPROVAL_EXTERNAL_NETWORK" in decision.reason_codes
def test_binding_fingerprint_changes_when_action_metadata_changes() -> None:
    adapter = TrustedAgentEnforcementAdapter()
    first = adapter.bind_tool(_tool(action_id="filesystem.read"))
    second = adapter.bind_tool(_tool(action_id="filesystem.read.alt"))

    assert first.governed_tool_id == second.governed_tool_id
    assert first.capabilities == second.capabilities
    assert first.fingerprint != second.fingerprint


def test_registry_fingerprint_binds_permission_evidence() -> None:
    adapter = TrustedAgentEnforcementAdapter()
    tool = _tool()
    first = adapter.tool_registry_decision(
        tool=tool,
        registry_decision=_registry_decision(
            tool,
            matched_permission_ids=["perm1"],
            allow_permission_ids=["perm1"],
        ),
        workspace_id="workspace1",
        agent_id="agent1",
    )
    second = adapter.tool_registry_decision(
        tool=tool,
        registry_decision=_registry_decision(
            tool,
            matched_permission_ids=["perm2"],
            allow_permission_ids=["perm2"],
        ),
        workspace_id="workspace1",
        agent_id="agent1",
    )

    assert first.action == AgentEnforcementAction.ALLOW
    assert second.action == AgentEnforcementAction.ALLOW
    assert first.upstream_fingerprint != second.upstream_fingerprint


def test_workspace_policy_rejects_forged_request_classification() -> None:
    adapter = TrustedAgentEnforcementAdapter()
    policy = _workspace_policy(
        classification=DataClassification.INTERNAL,
    )
    request = adapter.build_request(
        tool=_tool(),
        workspace_policy=policy,
    )
    forged = type(request)(
        tool_id=request.tool_id,
        action_id=request.action_id,
        data_classification=DataClassification.PUBLIC,
        capabilities=request.capabilities,
        workspace_id=request.workspace_id,
    )

    with pytest.raises(AgentToolBindingIntegrityError):
        adapter.workspace_policy_decision(
            workspace_policy=policy,
            request=forged,
        )


def test_adapter_composition_registry_deny_cannot_be_bypassed() -> None:
    adapter = TrustedAgentEnforcementAdapter()
    tool = _tool()
    policy = _workspace_policy(
        network_access="allowed",
        filesystem_access="read_write",
    )
    request = adapter.build_request(
        tool=tool,
        workspace_policy=policy,
    )
    registry = adapter.tool_registry_decision(
        tool=tool,
        registry_decision=_registry_decision(
            tool,
            allowed=False,
            reasons=["explicit_deny"],
            matched_permission_ids=["perm-deny"],
            deny_permission_ids=["perm-deny"],
        ),
        workspace_id="workspace1",
        agent_id="agent1",
    )
    workspace = adapter.workspace_policy_decision(
        workspace_policy=policy,
        request=request,
    )
    runtime = adapter.runtime_policy_decision(
        tool=tool,
        workspace_policy=policy,
        request=request,
    )
    profile = _profile("repository-review")

    decision = BeforeToolExecutionEnforcer().evaluate(
        profile=profile,
        expected_profile_fingerprint=profile.fingerprint,
        request=request,
        layer_decisions=(registry, workspace, runtime),
    )

    assert registry.action == AgentEnforcementAction.DENY
    assert workspace.action == AgentEnforcementAction.ALLOW
    assert runtime.action == AgentEnforcementAction.ALLOW
    assert decision.action == AgentEnforcementAction.DENY
    assert decision.executable is False
    assert "TOOL_REGISTRY_EXPLICIT_DENY" in decision.reason_codes
