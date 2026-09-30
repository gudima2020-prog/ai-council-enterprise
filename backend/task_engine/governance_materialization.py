from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from backend.task_engine.governance import (
    ExecutionInitiator,
    MinimumGovernancePlanner,
    SideEffectClass,
    TaskGovernancePlan,
    TaskGovernancePolicy,
    TaskGovernanceRequest,
    TaskRiskClass,
)
from backend.task_engine.models import TaskModel


MATERIALIZATION_SCHEMA_VERSION = "arch-gov-mat-001.v1"
GOVERNANCE_PAYLOAD_KEY = "_governance"
MATERIALIZATION_AUTHORIZATION_STATE = "requirements_only"


class GovernanceMaterializationError(ValueError):
    pass


@dataclass(frozen=True)
class GovernanceMaterializationSpec:
    revision_id: str
    risk_class: TaskRiskClass | str | int
    execution_initiator: ExecutionInitiator | str
    side_effects: tuple[SideEffectClass | str, ...] = field(
        default_factory=tuple
    )
    requested_capabilities: tuple[str, ...] = field(
        default_factory=tuple
    )
    policy_tags: tuple[str, ...] = field(default_factory=tuple)

    def to_request(self, task_id: str) -> TaskGovernanceRequest:
        return TaskGovernanceRequest(
            task_id=task_id,
            revision_id=self.revision_id,
            risk_class=self.risk_class,
            execution_initiator=self.execution_initiator,
            side_effects=self.side_effects,
            requested_capabilities=self.requested_capabilities,
            policy_tags=self.policy_tags,
        )


class TaskGovernanceMaterializer:
    """Bind governance requirements to a durable Task without granting authority.

    The serialized envelope is requirements metadata only. Trusted consumers must
    call recompute_plan rather than treating the stored plan as an authorization
    decision.
    """

    def __init__(
        self,
        policy: TaskGovernancePolicy | None = None,
    ) -> None:
        self.policy = policy or TaskGovernancePolicy()

    def materialize(
        self,
        task: TaskModel,
        spec: GovernanceMaterializationSpec,
    ) -> dict[str, Any]:
        if not isinstance(spec, GovernanceMaterializationSpec):
            raise TypeError(
                "spec must be GovernanceMaterializationSpec."
            )

        request = spec.to_request(task.id)
        plan = MinimumGovernancePlanner(self.policy).plan(request)
        envelope = self._build_envelope(plan)

        payload = deepcopy(task.payload_json or {})
        existing = payload.get(GOVERNANCE_PAYLOAD_KEY)

        if existing is not None:
            self.recompute_plan(task)
            if existing != envelope:
                raise GovernanceMaterializationError(
                    "Task governance is already materialized with a "
                    "different request or policy. Create a new Task/revision "
                    "instead of mutating governance in place."
                )
            return deepcopy(existing)

        payload[GOVERNANCE_PAYLOAD_KEY] = envelope
        task.payload_json = payload
        return deepcopy(envelope)

    def validate(self, task: TaskModel) -> dict[str, Any]:
        plan = self.recompute_plan(task)
        return {
            "valid": True,
            "task_id": task.id,
            "revision_id": plan.request.revision_id,
            "policy_fingerprint": plan.policy_fingerprint,
            "plan_fingerprint": plan.fingerprint,
            "authorization_state": MATERIALIZATION_AUTHORIZATION_STATE,
        }

    def recompute_plan(self, task: TaskModel) -> TaskGovernancePlan:
        payload = task.payload_json or {}
        raw = payload.get(GOVERNANCE_PAYLOAD_KEY)

        if not isinstance(raw, dict):
            raise GovernanceMaterializationError(
                "Task has no valid materialized governance envelope."
            )

        expected_keys = {
            "schema_version",
            "authorization_state",
            "policy_snapshot",
            "plan",
            "plan_fingerprint",
        }
        if set(raw) != expected_keys:
            raise GovernanceMaterializationError(
                "Materialized governance envelope has unexpected fields."
            )

        if raw.get("schema_version") != MATERIALIZATION_SCHEMA_VERSION:
            raise GovernanceMaterializationError(
                "Unsupported governance materialization schema version."
            )

        if (
            raw.get("authorization_state")
            != MATERIALIZATION_AUTHORIZATION_STATE
        ):
            raise GovernanceMaterializationError(
                "Materialized governance must remain requirements-only."
            )

        policy_raw = raw.get("policy_snapshot")
        if not isinstance(policy_raw, dict):
            raise GovernanceMaterializationError(
                "Materialized governance policy snapshot is invalid."
            )

        try:
            policy = TaskGovernancePolicy(**policy_raw)
        except (TypeError, ValueError) as exc:
            raise GovernanceMaterializationError(
                "Materialized governance policy snapshot is invalid."
            ) from exc

        if policy.to_dict() != policy_raw:
            raise GovernanceMaterializationError(
                "Materialized governance policy snapshot is not canonical."
            )

        plan_raw = raw.get("plan")
        if not isinstance(plan_raw, dict):
            raise GovernanceMaterializationError(
                "Materialized governance plan is invalid."
            )

        request_raw = plan_raw.get("request")
        if not isinstance(request_raw, dict):
            raise GovernanceMaterializationError(
                "Materialized governance request is invalid."
            )

        try:
            request = TaskGovernanceRequest(**request_raw)
        except (TypeError, ValueError) as exc:
            raise GovernanceMaterializationError(
                "Materialized governance request is invalid."
            ) from exc

        if request.to_dict() != request_raw:
            raise GovernanceMaterializationError(
                "Materialized governance request is not canonical."
            )

        if request.task_id != task.id:
            raise GovernanceMaterializationError(
                "Materialized governance is bound to a different Task."
            )

        recomputed = MinimumGovernancePlanner(policy).plan(request)

        if plan_raw != recomputed.to_dict():
            raise GovernanceMaterializationError(
                "Stored governance plan does not match canonical "
                "recomputation."
            )

        if raw.get("plan_fingerprint") != recomputed.fingerprint:
            raise GovernanceMaterializationError(
                "Stored governance plan fingerprint does not match "
                "canonical recomputation."
            )

        return recomputed

    def _build_envelope(
        self,
        plan: TaskGovernancePlan,
    ) -> dict[str, Any]:
        return {
            "schema_version": MATERIALIZATION_SCHEMA_VERSION,
            "authorization_state": MATERIALIZATION_AUTHORIZATION_STATE,
            "policy_snapshot": self.policy.to_dict(),
            "plan": plan.to_dict(),
            "plan_fingerprint": plan.fingerprint,
        }
