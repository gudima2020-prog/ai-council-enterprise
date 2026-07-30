from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.core.events import Event, EventBus
from backend.runtime_policy import (
    DataClassification,
    PolicyAction,
    PolicyOperation,
    RuntimePolicyContext,
    RuntimePolicyDecision,
    RuntimePolicyEngine,
    RuntimeTrust,
)


RuntimeDataClassificationResolver = Callable[
    [str | None],
    DataClassification,
]


def default_runtime_data_classification_resolver(
    workspace_id: str | None,
) -> DataClassification:
    del workspace_id
    return DataClassification.INTERNAL


@dataclass(frozen=True)
class RuntimePolicyEvaluation:
    policy_version: str
    operation: PolicyOperation
    action: PolicyAction
    reason_codes: tuple[str, ...]
    fingerprint: str | None
    data_classification: DataClassification | None
    runtime_trust: RuntimeTrust | None
    profile: str | None = None
    resolution_error: str | None = None

    @property
    def allowed(self) -> bool:
        return self.action == PolicyAction.ALLOW

    @property
    def approval_required(self) -> bool:
        return (
            self.action
            == PolicyAction.REQUIRE_APPROVAL
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "operation": self.operation.value,
            "action": self.action.value,
            "reason_codes": list(
                self.reason_codes
            ),
            "fingerprint": self.fingerprint,
            "data_classification": (
                self.data_classification.value
                if self.data_classification is not None
                else None
            ),
            "runtime_trust": (
                self.runtime_trust.value
                if self.runtime_trust is not None
                else None
            ),
            "profile": self.profile,
            "resolution_error": (
                self.resolution_error
            ),
        }


class IsolatedRuntimePolicy:
    """Policy adapter for host-safe checks, Docker runs and artifacts."""

    def __init__(
        self,
        *,
        event_bus: EventBus,
        resolver: (
            RuntimeDataClassificationResolver
            | None
        ) = None,
        engine: RuntimePolicyEngine | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._resolver = (
            resolver
            or default_runtime_data_classification_resolver
        )
        self._engine = (
            engine
            or RuntimePolicyEngine()
        )

    async def evaluate_profile(
        self,
        *,
        workspace_id: str | None,
        profile: str,
    ) -> RuntimePolicyEvaluation:
        if profile == "diff_check":
            operation = (
                PolicyOperation.METADATA_VALIDATION
            )
            runtime_trust = (
                RuntimeTrust.SAFE_HOST_PROFILE
            )
        else:
            operation = (
                PolicyOperation.CODE_EXECUTION
            )
            runtime_trust = (
                RuntimeTrust.ISOLATED_CONTAINER
            )

        return await self._evaluate(
            workspace_id=workspace_id,
            operation=operation,
            runtime_trust=runtime_trust,
            profile=profile,
        )

    async def evaluate_artifact_export(
        self,
        *,
        workspace_id: str | None,
    ) -> RuntimePolicyEvaluation:
        return await self._evaluate(
            workspace_id=workspace_id,
            operation=PolicyOperation.ARTIFACT_EXPORT,
            runtime_trust=None,
            profile=None,
        )

    async def _evaluate(
        self,
        *,
        workspace_id: str | None,
        operation: PolicyOperation,
        runtime_trust: RuntimeTrust | None,
        profile: str | None,
    ) -> RuntimePolicyEvaluation:
        try:
            resolved = self._resolver(
                workspace_id
            )
            classification = (
                resolved
                if isinstance(
                    resolved,
                    DataClassification,
                )
                else DataClassification(
                    str(resolved).strip()
                )
            )
        except Exception as exc:
            evaluation = RuntimePolicyEvaluation(
                policy_version=(
                    RuntimePolicyEngine.POLICY_VERSION
                ),
                operation=operation,
                action=PolicyAction.DENY,
                reason_codes=(
                    "POLICY_RESOLUTION_FAILED",
                ),
                fingerprint=None,
                data_classification=None,
                runtime_trust=runtime_trust,
                profile=profile,
                resolution_error=(
                    exc.__class__.__name__
                ),
            )
        else:
            context = RuntimePolicyContext(
                operation=operation,
                data_classification=classification,
                workspace_id=workspace_id,
                runtime_trust=runtime_trust,
                contains_secrets=False,
                network_requested=False,
            )
            decision = self._engine.evaluate(
                context
            )
            evaluation = self._from_decision(
                context=context,
                decision=decision,
                profile=profile,
            )

        await self._publish(
            workspace_id=workspace_id,
            evaluation=evaluation,
        )

        return evaluation

    @staticmethod
    def _from_decision(
        *,
        context: RuntimePolicyContext,
        decision: RuntimePolicyDecision,
        profile: str | None,
    ) -> RuntimePolicyEvaluation:
        return RuntimePolicyEvaluation(
            policy_version=decision.policy_version,
            operation=context.operation,
            action=decision.action,
            reason_codes=decision.reason_codes,
            fingerprint=decision.fingerprint,
            data_classification=(
                context.data_classification
            ),
            runtime_trust=context.runtime_trust,
            profile=profile,
        )

    async def _publish(
        self,
        *,
        workspace_id: str | None,
        evaluation: RuntimePolicyEvaluation,
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=(
                    "code_sandbox."
                    "runtime_policy.evaluated"
                ),
                source=(
                    "isolated_runtime_policy"
                ),
                workspace_id=workspace_id,
                payload={
                    "workspace_id": workspace_id,
                    **evaluation.to_dict(),
                },
            )
        )
