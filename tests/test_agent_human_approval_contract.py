from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib

import pytest

from backend.agent_governance import (
    AgentCapability,
    AgentEnforcementAction,
    AgentEnforcementLayer,
    AgentEnforcementLayerDecision,
    AgentHumanApprovalContract,
    AgentHumanApprovalScopeError,
    AgentHumanApprovalStateError,
    AgentToolRequest,
    BeforeToolExecutionEnforcer,
    TrustedAgentToolBinding,
    built_in_agent_profiles,
)
from backend.policy_approvals import (
    PolicyApprovalCore,
    PolicyApprovalScopeError,
    PolicyApprovalStatus,
)
from backend.runtime_policy import (
    DataClassification,
    PolicyOperation,
    RuntimePolicyContext,
    RuntimePolicyEngine,
)


WORKSPACE = "workspace_alpha"
NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def profile():
    return next(
        item
        for item in built_in_agent_profiles()
        if item.profile_id == "safe-development"
    )


def binding(**changes) -> TrustedAgentToolBinding:
    values = {
        "registry_tool_id": "tool_git_stage",
        "registry_tool_key": "git-stage",
        "governed_tool_id": "git.stage",
        "action_id": "git.stage",
        "runtime_operation": PolicyOperation.METADATA_VALIDATION,
        "capabilities": (
            AgentCapability.FILESYSTEM_WRITE,
            AgentCapability.REPOSITORY_STAGE,
        ),
        "workspace_id": WORKSPACE,
        "kind": "builtin",
        "risk_level": "high",
        "isolation_mode": "application_restricted",
        "allow_network": False,
        "allow_filesystem_read": False,
        "allow_filesystem_write": True,
    }
    values.update(changes)
    return TrustedAgentToolBinding(**values)


def layer(
    source: AgentEnforcementLayer,
    action: AgentEnforcementAction = AgentEnforcementAction.ALLOW,
    *,
    reason: str = "TEST_ALLOWED",
) -> AgentEnforcementLayerDecision:
    return AgentEnforcementLayerDecision(
        layer=source,
        workspace_id=WORKSPACE,
        action=action,
        reason_codes=(reason,),
        policy_version=f"test.{source.value}.v1",
        upstream_fingerprint=sha(
            f"{source.value}:{action.value}:{reason}"
        ),
    )


def runtime_layer() -> AgentEnforcementLayerDecision:
    runtime = RuntimePolicyEngine().evaluate(
        RuntimePolicyContext(
            operation=PolicyOperation.METADATA_VALIDATION,
            workspace_id=WORKSPACE,
            data_classification=DataClassification.INTERNAL,
        )
    )
    return AgentEnforcementLayerDecision.from_runtime_policy(
        workspace_id=WORKSPACE,
        decision=runtime,
    )


def canonical_layers():
    return (
        layer(AgentEnforcementLayer.TOOL_REGISTRY),
        layer(AgentEnforcementLayer.WORKSPACE_POLICY),
        runtime_layer(),
    )


def governed_request(
    *,
    tool_id: str = "git.stage",
    action_id: str = "git.stage",
    capabilities=(
        AgentCapability.FILESYSTEM_WRITE,
        AgentCapability.REPOSITORY_STAGE,
    ),
):
    return AgentToolRequest(
        tool_id=tool_id,
        action_id=action_id,
        data_classification=DataClassification.INTERNAL,
        capabilities=capabilities,
        workspace_id=WORKSPACE,
    )


def pre_decision(
    *,
    request=None,
    layers=None,
):
    selected = profile()
    return BeforeToolExecutionEnforcer().evaluate(
        profile=selected,
        expected_profile_fingerprint=selected.fingerprint,
        request=request or governed_request(),
        layer_decisions=layers or canonical_layers(),
    )


def approval_scope(
    *,
    decision=None,
    tool_binding=None,
    execution_id="execution_001",
    input_fingerprint=None,
):
    current = decision or pre_decision()
    return AgentHumanApprovalContract.build_scope(
        decision=current,
        binding=tool_binding or binding(),
        execution_id=execution_id,
        input_fingerprint=(
            input_fingerprint or sha("input-payload-001")
        ),
    )


def consumed_record(scope):
    pending = PolicyApprovalCore.request(
        scope=scope.policy_scope,
        reason_codes=("ENFORCEMENT_APPROVAL_REQUIRED",),
        requested_by="operator",
        now=NOW,
        approval_id="agent_approval_test",
    )
    grant = PolicyApprovalCore.approve(
        pending,
        decided_by="operator",
        now=NOW,
        token="x" * 43,
    )
    consumed = PolicyApprovalCore.consume(
        grant.record,
        token=grant.token,
        scope=scope.policy_scope,
        now=NOW,
    )
    return consumed


def test_scope_is_deterministic_and_content_free() -> None:
    decision = pre_decision()

    assert decision.approval_required is True
    assert decision.action == AgentEnforcementAction.REQUIRE_APPROVAL

    first = approval_scope(decision=decision)
    second = approval_scope(decision=decision)

    assert first.fingerprint == second.fingerprint
    assert first.policy_scope.operation == PolicyOperation.METADATA_VALIDATION

    payload = first.policy_scope.subject_payload
    assert payload["governed_tool_id"] == "git.stage"
    assert payload["action_id"] == "git.stage"
    assert payload["profile_id"] == "safe-development"
    assert payload["input_fingerprint"] == sha("input-payload-001")

    serialized = str(first.to_dict()).lower()
    assert "raw_input" not in serialized
    assert "secret_value" not in serialized
    assert "approval_token" not in serialized


def test_execution_or_input_change_changes_exact_scope() -> None:
    first = approval_scope(
        execution_id="execution_001",
        input_fingerprint=sha("payload-a"),
    )
    second = approval_scope(
        execution_id="execution_002",
        input_fingerprint=sha("payload-a"),
    )
    third = approval_scope(
        execution_id="execution_001",
        input_fingerprint=sha("payload-b"),
    )

    assert first.fingerprint != second.fingerprint
    assert first.fingerprint != third.fingerprint


@pytest.mark.parametrize(
    "changed_binding,match",
    [
        (
            binding(governed_tool_id="git.commit"),
            "tool_id",
        ),
        (
            binding(action_id="git.commit"),
            "action_id",
        ),
    ],
)
def test_binding_identity_mismatch_fails_closed(
    changed_binding,
    match,
) -> None:
    with pytest.raises(
        AgentHumanApprovalScopeError,
        match=match,
    ):
        approval_scope(tool_binding=changed_binding)


def test_binding_capability_mismatch_fails_closed() -> None:
    changed = binding(
        capabilities=(AgentCapability.REPOSITORY_STAGE,)
    )

    with pytest.raises(
        AgentHumanApprovalScopeError,
        match="capabilities",
    ):
        approval_scope(tool_binding=changed)


def test_non_approval_decision_cannot_create_scope() -> None:
    decision = pre_decision(
        request=governed_request(
            tool_id="filesystem.read",
            action_id="filesystem.read",
            capabilities=(AgentCapability.FILESYSTEM_READ,),
        )
    )

    assert decision.action == AgentEnforcementAction.ALLOW
    assert decision.approval_required is False

    with pytest.raises(
        AgentHumanApprovalScopeError,
        match="does not require",
    ):
        approval_scope(decision=decision)


def test_preapproval_scope_rejects_existing_human_layer() -> None:
    request = governed_request()
    layers = (
        *canonical_layers(),
        layer(
            AgentEnforcementLayer.HUMAN_CONTROL,
            AgentEnforcementAction.REQUIRE_APPROVAL,
            reason="HUMAN_APPROVAL_PENDING",
        ),
    )
    decision = pre_decision(
        request=request,
        layers=layers,
    )

    assert decision.approval_required is True

    with pytest.raises(
        AgentHumanApprovalScopeError,
        match="cannot contain a Human Control",
    ):
        approval_scope(decision=decision)


@pytest.mark.parametrize("mode", ["missing", "duplicate"])
def test_exactly_one_runtime_policy_layer_is_required(mode) -> None:
    decision = pre_decision()

    if mode == "missing":
        changed_layers = tuple(
            item
            for item in decision.layer_decisions
            if item.layer != AgentEnforcementLayer.RUNTIME_POLICY
        )
    else:
        changed_layers = (
            *decision.layer_decisions,
            runtime_layer(),
        )

    malformed = replace(
        decision,
        layer_decisions=changed_layers,
    )

    with pytest.raises(
        AgentHumanApprovalScopeError,
        match="Exactly one Runtime Policy",
    ):
        approval_scope(decision=malformed)


def test_approved_but_not_consumed_cannot_yield_human_allow() -> None:
    scope = approval_scope()
    pending = PolicyApprovalCore.request(
        scope=scope.policy_scope,
        reason_codes=("ENFORCEMENT_APPROVAL_REQUIRED",),
        now=NOW,
        approval_id="agent_approval_pending",
    )
    grant = PolicyApprovalCore.approve(
        pending,
        decided_by="operator",
        now=NOW,
        token="x" * 43,
    )

    for record in (pending, grant.record):
        with pytest.raises(
            AgentHumanApprovalStateError,
            match="atomically consumed",
        ):
            AgentHumanApprovalContract.evidence_from_consumed(
                record=record,
                expected_scope=scope,
            )


@pytest.mark.parametrize(
    "status",
    [
        PolicyApprovalStatus.DENIED,
        PolicyApprovalStatus.EXPIRED,
        PolicyApprovalStatus.REVOKED,
    ],
)
def test_terminal_non_consumed_states_cannot_yield_allow(status) -> None:
    scope = approval_scope()
    pending = PolicyApprovalCore.request(
        scope=scope.policy_scope,
        reason_codes=("ENFORCEMENT_APPROVAL_REQUIRED",),
        now=NOW,
        approval_id=f"agent_approval_{status.value}",
    )
    terminal = replace(
        pending,
        status=status,
    )

    with pytest.raises(
        AgentHumanApprovalStateError,
        match="atomically consumed",
    ):
        AgentHumanApprovalContract.evidence_from_consumed(
            record=terminal,
            expected_scope=scope,
        )


def test_changed_exact_scope_cannot_consume_approval() -> None:
    original = approval_scope(
        input_fingerprint=sha("payload-a"),
    )
    changed = approval_scope(
        input_fingerprint=sha("payload-b"),
    )

    pending = PolicyApprovalCore.request(
        scope=original.policy_scope,
        reason_codes=("ENFORCEMENT_APPROVAL_REQUIRED",),
        now=NOW,
        approval_id="agent_approval_scope_change",
    )
    grant = PolicyApprovalCore.approve(
        pending,
        decided_by="operator",
        now=NOW,
        token="x" * 43,
    )

    with pytest.raises(PolicyApprovalScopeError):
        PolicyApprovalCore.consume(
            grant.record,
            token=grant.token,
            scope=changed.policy_scope,
            now=NOW,
        )


def test_consumed_exact_scope_produces_trusted_human_allow() -> None:
    decision = pre_decision()
    scope = approval_scope(decision=decision)
    consumed = consumed_record(scope)

    evidence = AgentHumanApprovalContract.evidence_from_consumed(
        record=consumed,
        expected_scope=scope,
    )
    human = AgentHumanApprovalContract.allow_decision(
        record=consumed,
        expected_scope=scope,
    )

    assert human.layer == AgentEnforcementLayer.HUMAN_CONTROL
    assert human.action == AgentEnforcementAction.ALLOW
    assert human.workspace_id == WORKSPACE
    assert human.reason_codes == (
        "EXACT_SCOPE_APPROVAL_CONSUMED",
    )
    assert len(human.upstream_fingerprint) == 64

    selected = profile()
    final = BeforeToolExecutionEnforcer().evaluate(
        profile=selected,
        expected_profile_fingerprint=selected.fingerprint,
        request=decision.profile_decision.request,
        layer_decisions=(
            *decision.layer_decisions,
            human,
        ),
    )

    assert final.action == AgentEnforcementAction.ALLOW
    assert final.approval_required is False
    assert final.executable is True
    assert (
        "ENFORCEMENT_APPROVAL_SATISFIED"
        in final.reason_codes
    )


def test_consumed_evidence_cannot_be_rebound_to_changed_input() -> None:
    original = approval_scope(
        input_fingerprint=sha("payload-a"),
    )
    changed = approval_scope(
        input_fingerprint=sha("payload-b"),
    )
    consumed = consumed_record(original)

    evidence = AgentHumanApprovalContract.evidence_from_consumed(
        record=consumed,
        expected_scope=original,
    )

    with pytest.raises(AgentHumanApprovalScopeError):
        AgentHumanApprovalContract.allow_decision(
            record=consumed,
            expected_scope=changed,
        )


def test_consumed_evidence_fingerprint_is_deterministic_and_safe() -> None:
    scope = approval_scope()
    consumed = consumed_record(scope)

    first = AgentHumanApprovalContract.evidence_from_consumed(
        record=consumed,
        expected_scope=scope,
    )
    second = AgentHumanApprovalContract.evidence_from_consumed(
        record=consumed,
        expected_scope=scope,
    )

    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64

    serialized = str(first.to_dict()).lower()
    assert "token" not in serialized
    assert "raw_input" not in serialized
    assert "secret" not in serialized

def test_input_fingerprint_is_canonical_for_json_key_order() -> None:
    from backend.agent_governance import fingerprint_agent_tool_input

    first = fingerprint_agent_tool_input(
        {
            "alpha": 1,
            "nested": {
                "beta": True,
                "gamma": ["x", "y"],
            },
        }
    )
    second = fingerprint_agent_tool_input(
        {
            "nested": {
                "gamma": ["x", "y"],
                "beta": True,
            },
            "alpha": 1,
        }
    )

    assert first == second
    assert len(first) == 64


def test_input_fingerprint_changes_with_actual_content() -> None:
    from backend.agent_governance import fingerprint_agent_tool_input

    first = fingerprint_agent_tool_input({"value": "alpha"})
    second = fingerprint_agent_tool_input({"value": "beta"})

    assert first != second


def test_input_fingerprint_rejects_non_json_values() -> None:
    from backend.agent_governance import fingerprint_agent_tool_input

    with pytest.raises(
        AgentHumanApprovalScopeError,
        match="canonical JSON",
    ):
        fingerprint_agent_tool_input({"value": {1, 2, 3}})


@pytest.mark.parametrize(
    "mutation",
    [
        {"token_hash": None},
        {"decided_by": None},
        {"decided_at": None},
    ],
)
def test_consumed_record_requires_approval_proof_fields(
    mutation,
) -> None:
    scope = approval_scope()
    consumed = consumed_record(scope)
    malformed = replace(consumed, **mutation)

    with pytest.raises(AgentHumanApprovalStateError):
        AgentHumanApprovalContract.evidence_from_consumed(
            record=malformed,
            expected_scope=scope,
        )

    with pytest.raises(AgentHumanApprovalStateError):
        AgentHumanApprovalContract.allow_decision(
            record=malformed,
            expected_scope=scope,
        )


def test_consumed_record_rejects_impossible_timeline() -> None:
    scope = approval_scope()
    consumed = consumed_record(scope)

    malformed = replace(
        consumed,
        consumed_at=NOW,
        decided_at=NOW.replace(hour=13),
    )

    with pytest.raises(
        AgentHumanApprovalStateError,
        match="before it was approved",
    ):
        AgentHumanApprovalContract.allow_decision(
            record=malformed,
            expected_scope=scope,
        )


def test_approved_record_cannot_directly_build_human_allow() -> None:
    scope = approval_scope()
    pending = PolicyApprovalCore.request(
        scope=scope.policy_scope,
        reason_codes=("ENFORCEMENT_APPROVAL_REQUIRED",),
        now=NOW,
        approval_id="agent_approval_direct_allow",
    )
    grant = PolicyApprovalCore.approve(
        pending,
        decided_by="operator",
        now=NOW,
        token="x" * 43,
    )

    with pytest.raises(
        AgentHumanApprovalStateError,
        match="atomically consumed",
    ):
        AgentHumanApprovalContract.allow_decision(
            record=grant.record,
            expected_scope=scope,
        )
