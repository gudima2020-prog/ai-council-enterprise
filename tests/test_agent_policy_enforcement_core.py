from __future__ import annotations

import hashlib

import pytest

from backend.agent_governance import (
    AgentEnforcementAction,
    AgentEnforcementLayer,
    AgentEnforcementLayerDecision,
    AgentToolRequest,
    BeforeToolExecutionEnforcer,
    built_in_agent_profiles,
)
from backend.runtime_policy import (
    DataClassification,
    PolicyAction,
    PolicyOperation,
    RuntimePolicyContext,
    RuntimePolicyEngine,
    RuntimeTrust,
)


WORKSPACE = "workspace_alpha"


def profile():
    return next(
        item
        for item in built_in_agent_profiles()
        if item.profile_id == "safe-development"
    )


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def layer(
    source: AgentEnforcementLayer,
    action: AgentEnforcementAction = AgentEnforcementAction.ALLOW,
    *,
    workspace: str = WORKSPACE,
    reason: str = "TEST_DECISION",
) -> AgentEnforcementLayerDecision:
    return AgentEnforcementLayerDecision(
        layer=source,
        workspace_id=workspace,
        action=action,
        reason_codes=(reason,),
        policy_version=f"test.{source.value}.v1",
        upstream_fingerprint=sha(
            f"{source.value}:{workspace}:{action.value}:{reason}"
        ),
    )


def canonical_layers():
    return (
        layer(AgentEnforcementLayer.TOOL_REGISTRY),
        layer(AgentEnforcementLayer.WORKSPACE_POLICY),
        layer(AgentEnforcementLayer.RUNTIME_POLICY),
    )


def request(tool_id: str = "filesystem.read", *, workspace=WORKSPACE):
    return AgentToolRequest(
        tool_id=tool_id,
        action_id=tool_id,
        data_classification=DataClassification.INTERNAL,
        workspace_id=workspace,
    )


def evaluate(*, req=None, layers=None, expected=None):
    selected = profile()
    return BeforeToolExecutionEnforcer().evaluate(
        profile=selected,
        expected_profile_fingerprint=(
            selected.fingerprint if expected is None else expected
        ),
        request=req or request(),
        layer_decisions=canonical_layers() if layers is None else layers,
    )


def test_all_layers_allow() -> None:
    decision = evaluate()
    assert decision.action == AgentEnforcementAction.ALLOW
    assert decision.executable is True
    assert decision.reason_codes == ("BEFORE_TOOL_EXECUTION_ALLOWED",)
    assert len(decision.fingerprint) == 64


def test_profile_deny_wins() -> None:
    decision = evaluate(req=request("models.infer"))
    assert decision.action == AgentEnforcementAction.DENY
    assert "PROFILE_TOOL_NOT_ALLOWED" in decision.reason_codes


@pytest.mark.parametrize(
    "source",
    [
        AgentEnforcementLayer.TOOL_REGISTRY,
        AgentEnforcementLayer.WORKSPACE_POLICY,
        AgentEnforcementLayer.RUNTIME_POLICY,
    ],
)
def test_any_canonical_deny_wins(source) -> None:
    layers = list(canonical_layers())
    index = [item.layer for item in layers].index(source)
    layers[index] = layer(
        source,
        AgentEnforcementAction.DENY,
        reason="CANONICAL_DENY",
    )
    decision = evaluate(layers=tuple(layers))
    assert decision.action == AgentEnforcementAction.DENY
    assert (
        source.value.upper() + "_CANONICAL_DENY"
        in decision.reason_codes
    )


@pytest.mark.parametrize(
    "missing",
    [
        AgentEnforcementLayer.TOOL_REGISTRY,
        AgentEnforcementLayer.WORKSPACE_POLICY,
        AgentEnforcementLayer.RUNTIME_POLICY,
    ],
)
def test_missing_required_layer_fails_closed(missing) -> None:
    layers = tuple(
        item for item in canonical_layers() if item.layer != missing
    )
    decision = evaluate(layers=layers)
    assert decision.action == AgentEnforcementAction.DENY
    assert (
        "ENFORCEMENT_MISSING_"
        + missing.value.upper()
        + "_DECISION"
        in decision.reason_codes
    )


def test_duplicate_and_workspace_mismatch_fail_closed() -> None:
    duplicate = evaluate(
        layers=(
            *canonical_layers(),
            layer(AgentEnforcementLayer.RUNTIME_POLICY),
        )
    )
    assert (
        "ENFORCEMENT_DUPLICATE_RUNTIME_POLICY_DECISION"
        in duplicate.reason_codes
    )

    layers = list(canonical_layers())
    layers[1] = layer(
        AgentEnforcementLayer.WORKSPACE_POLICY,
        workspace="workspace_beta",
    )
    mismatch = evaluate(layers=tuple(layers))
    assert (
        "ENFORCEMENT_WORKSPACE_POLICY_WORKSPACE_MISMATCH"
        in mismatch.reason_codes
    )


def test_workspace_and_exact_profile_fingerprint_are_required() -> None:
    no_workspace = evaluate(req=request(workspace=None))
    assert "ENFORCEMENT_WORKSPACE_REQUIRED" in no_workspace.reason_codes

    uppercase = profile().fingerprint.upper()
    mismatch = evaluate(expected=uppercase)
    assert mismatch.action == AgentEnforcementAction.DENY
    assert (
        "ENFORCEMENT_PROFILE_FINGERPRINT_MISMATCH"
        in mismatch.reason_codes
    )


def test_profile_approval_needs_human_control() -> None:
    stage = request("git.stage")
    pending = evaluate(req=stage)
    assert pending.action == AgentEnforcementAction.REQUIRE_APPROVAL
    assert pending.approval_required is True

    approved = evaluate(
        req=stage,
        layers=(
            *canonical_layers(),
            layer(
                AgentEnforcementLayer.HUMAN_CONTROL,
                reason="EXACT_SCOPE_APPROVAL_VALID",
            ),
        ),
    )
    assert approved.action == AgentEnforcementAction.ALLOW
    assert approved.approval_required is False
    assert "ENFORCEMENT_APPROVAL_SATISFIED" in approved.reason_codes


def test_human_control_deny_wins() -> None:
    decision = evaluate(
        layers=(
            *canonical_layers(),
            layer(
                AgentEnforcementLayer.HUMAN_CONTROL,
                AgentEnforcementAction.DENY,
                reason="APPROVAL_DENIED",
            ),
        )
    )
    assert decision.action == AgentEnforcementAction.DENY
    assert "HUMAN_CONTROL_APPROVAL_DENIED" in decision.reason_codes


def test_runtime_require_isolation_is_preserved() -> None:
    runtime = RuntimePolicyEngine().evaluate(
        RuntimePolicyContext(
            operation=PolicyOperation.CODE_EXECUTION,
            workspace_id=WORKSPACE,
            data_classification=DataClassification.CONFIDENTIAL,
            runtime_trust=RuntimeTrust.SAFE_HOST_PROFILE,
        )
    )
    assert runtime.action == PolicyAction.REQUIRE_ISOLATION
    runtime_layer = AgentEnforcementLayerDecision.from_runtime_policy(
        workspace_id=WORKSPACE,
        decision=runtime,
    )
    decision = evaluate(
        layers=(
            layer(AgentEnforcementLayer.TOOL_REGISTRY),
            layer(AgentEnforcementLayer.WORKSPACE_POLICY),
            runtime_layer,
        )
    )
    assert decision.action == AgentEnforcementAction.REQUIRE_ISOLATION
    assert decision.isolation_required is True
    assert runtime_layer.upstream_fingerprint == runtime.fingerprint


def test_isolation_and_approval_are_not_collapsed() -> None:
    layers = (
        layer(AgentEnforcementLayer.TOOL_REGISTRY),
        layer(AgentEnforcementLayer.WORKSPACE_POLICY),
        layer(
            AgentEnforcementLayer.RUNTIME_POLICY,
            AgentEnforcementAction.REQUIRE_ISOLATION,
            reason="ISOLATION_REQUIRED",
        ),
    )
    pending = evaluate(req=request("git.stage"), layers=layers)
    assert pending.action == AgentEnforcementAction.REQUIRE_ISOLATION
    assert pending.isolation_required is True
    assert pending.approval_required is True


@pytest.mark.parametrize("runtime_action", list(PolicyAction))
def test_runtime_adapter_maps_all_actions(runtime_action) -> None:
    contexts = {
        PolicyAction.ALLOW: RuntimePolicyContext(
            operation=PolicyOperation.CODE_EXECUTION,
            workspace_id=WORKSPACE,
            runtime_trust=RuntimeTrust.ISOLATED_CONTAINER,
        ),
        PolicyAction.REQUIRE_APPROVAL: RuntimePolicyContext(
            operation=PolicyOperation.ARTIFACT_EXPORT,
            workspace_id=WORKSPACE,
            data_classification=DataClassification.CONFIDENTIAL,
        ),
        PolicyAction.REQUIRE_ISOLATION: RuntimePolicyContext(
            operation=PolicyOperation.CODE_EXECUTION,
            workspace_id=WORKSPACE,
            runtime_trust=RuntimeTrust.SAFE_HOST_PROFILE,
            network_requested=True,
        ),
        PolicyAction.DENY: RuntimePolicyContext(
            operation=PolicyOperation.CODE_EXECUTION,
            workspace_id=WORKSPACE,
            runtime_trust=RuntimeTrust.BLOCKED,
        ),
    }
    runtime = RuntimePolicyEngine().evaluate(contexts[runtime_action])
    assert runtime.action == runtime_action
    adapted = AgentEnforcementLayerDecision.from_runtime_policy(
        workspace_id=WORKSPACE,
        decision=runtime,
    )
    expected = {
        PolicyAction.ALLOW: AgentEnforcementAction.ALLOW,
        PolicyAction.REQUIRE_APPROVAL: AgentEnforcementAction.REQUIRE_APPROVAL,
        PolicyAction.REQUIRE_ISOLATION: AgentEnforcementAction.REQUIRE_ISOLATION,
        PolicyAction.DENY: AgentEnforcementAction.DENY,
    }[runtime_action]
    assert adapted.action == expected
    assert adapted.upstream_fingerprint == runtime.fingerprint


def test_tool_registry_cannot_claim_isolation_action() -> None:
    with pytest.raises(ValueError, match="does not support action"):
        layer(
            AgentEnforcementLayer.TOOL_REGISTRY,
            AgentEnforcementAction.REQUIRE_ISOLATION,
        )


def test_decision_is_deterministic_and_content_free() -> None:
    first = evaluate()
    second = evaluate()
    assert first.fingerprint == second.fingerprint
    payload = first.to_dict()
    request_snapshot = payload["profile_decision"]["request"]
    assert "input" not in request_snapshot
    serialized = str(payload).lower()
    assert "prompt" not in serialized
    assert "secret" not in serialized


def test_human_control_allow_cannot_override_canonical_deny() -> None:
    layers = (
        layer(
            AgentEnforcementLayer.TOOL_REGISTRY,
            AgentEnforcementAction.DENY,
            reason="EXPLICIT_DENY",
        ),
        layer(AgentEnforcementLayer.WORKSPACE_POLICY),
        layer(AgentEnforcementLayer.RUNTIME_POLICY),
        layer(
            AgentEnforcementLayer.HUMAN_CONTROL,
            reason="EXACT_SCOPE_APPROVAL_VALID",
        ),
    )

    decision = evaluate(layers=layers)

    assert decision.action == AgentEnforcementAction.DENY
    assert decision.executable is False
    assert "TOOL_REGISTRY_EXPLICIT_DENY" in decision.reason_codes


def test_human_control_allow_cannot_replace_missing_canonical_layer() -> None:
    layers = (
        layer(AgentEnforcementLayer.TOOL_REGISTRY),
        layer(AgentEnforcementLayer.RUNTIME_POLICY),
        layer(
            AgentEnforcementLayer.HUMAN_CONTROL,
            reason="EXACT_SCOPE_APPROVAL_VALID",
        ),
    )

    decision = evaluate(layers=layers)

    assert decision.action == AgentEnforcementAction.DENY
    assert decision.executable is False
    assert (
        "ENFORCEMENT_MISSING_WORKSPACE_POLICY_DECISION"
        in decision.reason_codes
    )
