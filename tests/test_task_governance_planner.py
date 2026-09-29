from __future__ import annotations

from dataclasses import replace

import pytest

from backend.task_engine.governance import (
    EvidenceProvenance,
    ExecutionInitiator,
    GovernanceControl,
    GovernanceRole,
    MinimumGovernancePlanner,
    SideEffectClass,
    TaskGovernancePolicy,
    TaskGovernanceRequest,
    TaskRiskClass,
)


def request(**changes) -> TaskGovernanceRequest:
    values = {
        "task_id": "task_001",
        "revision_id": "rev_001",
        "risk_class": TaskRiskClass.LOW,
        "execution_initiator": ExecutionInitiator.HUMAN,
        "side_effects": (SideEffectClass.NONE,),
        "requested_capabilities": (),
        "policy_tags": (),
    }
    values.update(changes)
    return TaskGovernanceRequest(**values)


def test_low_risk_uses_minimum_governance() -> None:
    plan = MinimumGovernancePlanner().plan(request())

    assert plan.controls == (
        GovernanceControl.IMPLEMENTATION_SELF_CHECK,
    )
    assert plan.required_roles == (
        GovernanceRole.IMPLEMENTATION,
    )
    assert plan.required_evidence == (
        EvidenceProvenance.IMPLEMENTATION_SELF_TEST,
    )
    assert plan.human_gate_required is False


def test_medium_risk_adds_deterministic_validation_only() -> None:
    plan = MinimumGovernancePlanner().plan(
        request(risk_class=TaskRiskClass.MEDIUM)
    )

    assert (
        GovernanceControl.DETERMINISTIC_VALIDATION
        in plan.controls
    )
    assert GovernanceControl.EVIDENCE_AGENT not in plan.controls
    assert (
        GovernanceControl.INDEPENDENT_REVIEW
        not in plan.controls
    )
    assert (
        EvidenceProvenance.DETERMINISTIC_VALIDATOR
        in plan.required_evidence
    )
    assert plan.human_gate_required is False


def test_high_risk_adds_evidence_and_independent_review() -> None:
    plan = MinimumGovernancePlanner().plan(
        request(risk_class=TaskRiskClass.HIGH)
    )

    assert GovernanceControl.EVIDENCE_AGENT in plan.controls
    assert (
        GovernanceControl.INDEPENDENT_REVIEW
        in plan.controls
    )
    assert GovernanceRole.TEST_EVIDENCE in plan.required_roles
    assert (
        GovernanceRole.INDEPENDENT_REVIEW
        in plan.required_roles
    )
    assert (
        EvidenceProvenance.INDEPENDENT_TEST
        in plan.required_evidence
    )
    assert GovernanceControl.SECURITY_REVIEW not in plan.controls
    assert plan.human_gate_required is False


def test_critical_risk_requires_full_review_and_human_promotion() -> None:
    plan = MinimumGovernancePlanner().plan(
        request(risk_class=TaskRiskClass.CRITICAL)
    )

    assert GovernanceControl.SECURITY_REVIEW in plan.controls
    assert (
        GovernanceControl.INDEPENDENT_REVIEW
        in plan.controls
    )
    assert GovernanceControl.HUMAN_PROMOTION in plan.controls
    assert GovernanceRole.SECURITY_REVIEW in plan.required_roles
    assert plan.human_gate_required is True
    assert (
        EvidenceProvenance.SECURITY_REVIEW
        in plan.required_evidence
    )
    assert (
        EvidenceProvenance.HUMAN_APPROVAL
        in plan.required_evidence
    )


def test_external_write_escalates_only_to_human_gate_by_default() -> None:
    plan = MinimumGovernancePlanner().plan(
        request(
            side_effects=(SideEffectClass.EXTERNAL_WRITE,)
        )
    )

    assert plan.human_gate_required is True
    assert GovernanceControl.HUMAN_PROMOTION in plan.controls
    assert GovernanceControl.SECURITY_REVIEW not in plan.controls
    assert (
        GovernanceControl.INDEPENDENT_REVIEW
        not in plan.controls
    )


def test_credential_use_requires_evidence_security_and_human_gate() -> None:
    plan = MinimumGovernancePlanner().plan(
        request(
            side_effects=(SideEffectClass.CREDENTIAL_USE,)
        )
    )

    assert GovernanceControl.EVIDENCE_AGENT in plan.controls
    assert GovernanceControl.SECURITY_REVIEW in plan.controls
    assert GovernanceControl.HUMAN_PROMOTION in plan.controls
    assert (
        GovernanceControl.INDEPENDENT_REVIEW
        not in plan.controls
    )


def test_production_change_requires_evidence_security_independent_and_human() -> None:
    plan = MinimumGovernancePlanner().plan(
        request(
            side_effects=(SideEffectClass.PRODUCTION_CHANGE,)
        )
    )

    assert GovernanceControl.EVIDENCE_AGENT in plan.controls
    assert GovernanceControl.SECURITY_REVIEW in plan.controls
    assert (
        GovernanceControl.INDEPENDENT_REVIEW
        in plan.controls
    )
    assert GovernanceControl.HUMAN_PROMOTION in plan.controls


def test_policy_is_configurable_not_a_hard_coded_four_level_template() -> None:
    policy = replace(
        TaskGovernancePolicy(),
        independent_review_min_risk=TaskRiskClass.CRITICAL,
        human_gate_side_effects=(
            SideEffectClass.PRODUCTION_CHANGE,
        ),
    )
    plan = MinimumGovernancePlanner(policy).plan(
        request(
            risk_class=TaskRiskClass.HIGH,
            side_effects=(SideEffectClass.EXTERNAL_WRITE,),
        )
    )

    assert GovernanceControl.EVIDENCE_AGENT in plan.controls
    assert (
        GovernanceControl.INDEPENDENT_REVIEW
        not in plan.controls
    )
    assert plan.human_gate_required is False


def test_capabilities_are_requests_not_grants() -> None:
    plan = MinimumGovernancePlanner().plan(
        request(
            requested_capabilities=(
                "github.issue.comment",
                "shell.execute",
            )
        )
    )

    serialized = plan.to_dict()
    assert serialized["requested_capabilities"] == [
        "github.issue.comment",
        "shell.execute",
    ]
    assert "granted_capabilities" not in serialized
    assert "capabilities" not in serialized


def test_normalization_makes_fingerprint_order_independent() -> None:
    first = MinimumGovernancePlanner().plan(
        request(
            side_effects=(
                SideEffectClass.EXTERNAL_WRITE,
                SideEffectClass.LOCAL_WRITE,
            ),
            requested_capabilities=(
                "shell.execute",
                "github.issue.comment",
            ),
            policy_tags=(
                "release",
                "customer-facing",
            ),
        )
    )
    second = MinimumGovernancePlanner().plan(
        request(
            side_effects=(
                SideEffectClass.LOCAL_WRITE,
                SideEffectClass.EXTERNAL_WRITE,
            ),
            requested_capabilities=(
                "github.issue.comment",
                "shell.execute",
            ),
            policy_tags=(
                "customer-facing",
                "release",
            ),
        )
    )

    assert first.fingerprint == second.fingerprint


def test_initiator_is_provenance_and_changes_fingerprint() -> None:
    human = MinimumGovernancePlanner().plan(request())
    scheduler = MinimumGovernancePlanner().plan(
        request(
            execution_initiator=ExecutionInitiator.SCHEDULER
        )
    )

    assert (
        human.request.execution_initiator
        == ExecutionInitiator.HUMAN
    )
    assert (
        scheduler.request.execution_initiator
        == ExecutionInitiator.SCHEDULER
    )
    assert human.fingerprint != scheduler.fingerprint


def test_none_side_effect_cannot_be_combined_with_real_effects() -> None:
    with pytest.raises(ValueError, match="cannot combine"):
        request(
            side_effects=(
                SideEffectClass.NONE,
                SideEffectClass.EXTERNAL_WRITE,
            )
        )


def test_evidence_provenance_classes_remain_distinct() -> None:
    values = {
        EvidenceProvenance.IMPLEMENTATION_SELF_TEST.value,
        EvidenceProvenance.USER_RUN_TEST.value,
        EvidenceProvenance.INDEPENDENT_TEST.value,
        EvidenceProvenance.EXTERNAL_ON_WIRE_EVIDENCE.value,
    }

    assert len(values) == 4
