from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.task_engine.governance_materialization import (
    GOVERNANCE_PAYLOAD_KEY,
    MATERIALIZATION_AUTHORIZATION_STATE,
    GovernanceMaterializationError,
    TaskGovernanceMaterializer,
)
from backend.task_engine.models import TaskModel


GOVERNANCE_CONSUMPTION_SCHEMA_VERSION = "arch-gov-consume-001.v1"
GOVERNANCE_VERIFICATION_STATE = "canonical_recomputed"


class GovernanceConsumptionError(ValueError):
    pass


@dataclass(frozen=True)
class CanonicalGovernanceRequirements:
    """Immutable requirements view derived from canonical recomputation.

    This object is verified requirements metadata. It is deliberately not an
    authorization result and contains no grants, observed evidence, or approval
    state.
    """

    task_id: str
    revision_id: str
    risk_class: str
    execution_initiator: str
    side_effects: tuple[str, ...]
    requested_capabilities: tuple[str, ...]
    policy_tags: tuple[str, ...]
    controls: tuple[str, ...]
    required_roles: tuple[str, ...]
    required_evidence: tuple[str, ...]
    human_gate_required: bool
    reason_codes: tuple[str, ...]
    policy_id: str
    policy_version: str
    policy_fingerprint: str
    plan_fingerprint: str
    materialization_schema_version: str
    authorization_state: str = MATERIALIZATION_AUTHORIZATION_STATE
    verification_state: str = GOVERNANCE_VERIFICATION_STATE
    schema_version: str = GOVERNANCE_CONSUMPTION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "verification_state": self.verification_state,
            "authorization_state": self.authorization_state,
            "task_id": self.task_id,
            "revision_id": self.revision_id,
            "risk_class": self.risk_class,
            "execution_initiator": self.execution_initiator,
            "side_effects": list(self.side_effects),
            "requested_capabilities": list(
                self.requested_capabilities
            ),
            "policy_tags": list(self.policy_tags),
            "controls": list(self.controls),
            "required_roles": list(self.required_roles),
            "required_evidence": list(self.required_evidence),
            "human_gate_required": self.human_gate_required,
            "reason_codes": list(self.reason_codes),
            "policy": {
                "policy_id": self.policy_id,
                "version": self.policy_version,
                "fingerprint": self.policy_fingerprint,
            },
            "plan_fingerprint": self.plan_fingerprint,
            "materialization_schema_version": (
                self.materialization_schema_version
            ),
        }


class TaskGovernanceConsumer:
    """Canonically consume materialized governance without granting authority."""

    def __init__(
        self,
        materializer: TaskGovernanceMaterializer | None = None,
    ) -> None:
        self._materializer = materializer or TaskGovernanceMaterializer()

    def consume(
        self,
        task: TaskModel,
    ) -> CanonicalGovernanceRequirements | None:
        payload = task.payload_json or {}
        if GOVERNANCE_PAYLOAD_KEY not in payload:
            return None

        try:
            plan = self._materializer.recompute_plan(task)
        except GovernanceMaterializationError as exc:
            raise GovernanceConsumptionError(
                "Materialized governance failed canonical recomputation."
            ) from exc

        raw = payload.get(GOVERNANCE_PAYLOAD_KEY)
        if not isinstance(raw, dict):
            raise GovernanceConsumptionError(
                "Materialized governance envelope is invalid."
            )

        request = plan.request
        return CanonicalGovernanceRequirements(
            task_id=task.id,
            revision_id=request.revision_id,
            risk_class=request.risk_class.label,
            execution_initiator=request.execution_initiator.value,
            side_effects=tuple(
                item.value for item in request.side_effects
            ),
            requested_capabilities=tuple(
                request.requested_capabilities
            ),
            policy_tags=tuple(request.policy_tags),
            controls=tuple(
                item.value for item in plan.controls
            ),
            required_roles=tuple(
                item.value for item in plan.required_roles
            ),
            required_evidence=tuple(
                item.value for item in plan.required_evidence
            ),
            human_gate_required=plan.human_gate_required,
            reason_codes=tuple(plan.reason_codes),
            policy_id=plan.policy_id,
            policy_version=plan.policy_version,
            policy_fingerprint=plan.policy_fingerprint,
            plan_fingerprint=plan.fingerprint,
            materialization_schema_version=str(
                raw["schema_version"]
            ),
        )
