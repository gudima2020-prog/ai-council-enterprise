from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, ClassVar

from backend.core.events import Event, EventBus
from backend.gateway.schemas import (
    GatewayError,
    GatewayRequest,
    GatewayResponse,
)
from backend.runtime_policy import (
    DataClassification,
    PolicyAction,
    PolicyOperation,
    ProviderTrust,
    RuntimePolicyContext,
    RuntimePolicyDecision,
    RuntimePolicyEngine,
)


ProviderPolicyResolver = Callable[
    [str | None, str],
    tuple[DataClassification, ProviderTrust],
]


def default_provider_policy_resolver(
    workspace_id: str | None,
    provider: str,
) -> tuple[DataClassification, ProviderTrust]:
    del workspace_id, provider

    return (
        DataClassification.INTERNAL,
        ProviderTrust.EXTERNAL,
    )


class GatewayRoutePolicy:
    """Evaluates one concrete provider route before secret resolution.

    The pseudo-provider ``auto`` is intentionally bypassed. Its concrete
    candidate providers are evaluated individually by the gateway loop.
    """

    _CLASSIFICATION_ORDER: ClassVar[
        dict[DataClassification, int]
    ] = {
        DataClassification.PUBLIC: 0,
        DataClassification.INTERNAL: 1,
        DataClassification.CONFIDENTIAL: 2,
        DataClassification.RESTRICTED: 3,
    }

    def __init__(
        self,
        *,
        event_bus: EventBus,
        resolver: ProviderPolicyResolver | None = None,
        engine: RuntimePolicyEngine | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._resolver = (
            resolver
            or default_provider_policy_resolver
        )
        self._engine = (
            engine
            or RuntimePolicyEngine()
        )

    async def evaluate(
        self,
        request: GatewayRequest,
    ) -> tuple[GatewayRequest, GatewayResponse | None]:
        if request.provider == "auto":
            return request, None

        try:
            workspace_classification, provider_trust = self._resolver(
                request.workspace_id,
                request.provider,
            )
            data_classification = self._effective_classification(
                workspace_classification=workspace_classification,
                requested_classification=request.data_classification,
            )
        except Exception as exc:
            policy_metadata = {
                "policy_version": (
                    RuntimePolicyEngine.POLICY_VERSION
                ),
                "action": PolicyAction.DENY.value,
                "reason_codes": [
                    "POLICY_RESOLUTION_FAILED",
                ],
                "fingerprint": None,
                "data_classification": None,
                "provider_trust": None,
                "resolution_error": (
                    exc.__class__.__name__
                ),
            }

            enriched_request = self._with_metadata(
                request,
                policy_metadata,
            )

            await self._publish(
                request=enriched_request,
                policy_metadata=policy_metadata,
            )

            return (
                enriched_request,
                self._rejection(
                    request=enriched_request,
                    code="POLICY_RESOLUTION_FAILED",
                    message=(
                        "?? ??????? ????????? ?????????? "
                        "Workspace policy ??? provider route."
                    ),
                    policy_metadata=policy_metadata,
                ),
            )

        context = RuntimePolicyContext(
            operation=PolicyOperation.MODEL_INFERENCE,
            data_classification=data_classification,
            workspace_id=request.workspace_id,
            provider_trust=provider_trust,
            contains_secrets=False,
            network_requested=False,
        )
        decision = self._engine.evaluate(context)

        policy_metadata = self._decision_metadata(
            context=context,
            decision=decision,
        )
        enriched_request = self._with_metadata(
            request,
            policy_metadata,
        )

        await self._publish(
            request=enriched_request,
            policy_metadata=policy_metadata,
        )

        if decision.action == PolicyAction.ALLOW:
            return enriched_request, None

        code, message = self._rejection_reason(
            decision
        )

        return (
            enriched_request,
            self._rejection(
                request=enriched_request,
                code=code,
                message=message,
                policy_metadata=policy_metadata,
            ),
        )

    @classmethod
    def _effective_classification(
        cls,
        *,
        workspace_classification: DataClassification,
        requested_classification: str | None,
    ) -> DataClassification:
        if not isinstance(workspace_classification, DataClassification):
            workspace_classification = DataClassification(
                str(workspace_classification)
            )
        if requested_classification is None:
            return workspace_classification
        requested = DataClassification(requested_classification)
        return max(
            (workspace_classification, requested),
            key=cls._CLASSIFICATION_ORDER.__getitem__,
        )

    @staticmethod
    def _with_metadata(
        request: GatewayRequest,
        policy_metadata: dict[str, Any],
    ) -> GatewayRequest:
        return replace(
            request,
            metadata={
                **request.metadata,
                "runtime_policy": policy_metadata,
            },
        )

    @staticmethod
    def _decision_metadata(
        *,
        context: RuntimePolicyContext,
        decision: RuntimePolicyDecision,
    ) -> dict[str, Any]:
        return {
            "policy_version": decision.policy_version,
            "action": decision.action.value,
            "reason_codes": list(
                decision.reason_codes
            ),
            "fingerprint": decision.fingerprint,
            "data_classification": (
                context.data_classification.value
            ),
            "provider_trust": (
                context.provider_trust.value
                if context.provider_trust is not None
                else None
            ),
        }

    @staticmethod
    def _rejection_reason(
        decision: RuntimePolicyDecision,
    ) -> tuple[str, str]:
        if (
            decision.action
            == PolicyAction.REQUIRE_APPROVAL
        ):
            return (
                "POLICY_APPROVAL_REQUIRED",
                (
                    "Workspace policy ??????? Human "
                    "Approval ????? ????????? ?????? "
                    "????? ??????????."
                ),
            )

        if (
            decision.action
            == PolicyAction.REQUIRE_ISOLATION
        ):
            return (
                "POLICY_ISOLATION_REQUIRED",
                (
                    "Workspace policy ??????? "
                    "?????????????? runtime."
                ),
            )

        return (
            "POLICY_DENIED",
            (
                "Workspace policy ????????? ???????? "
                "?????? ?????????? ??????????."
            ),
        )

    @staticmethod
    def _rejection(
        *,
        request: GatewayRequest,
        code: str,
        message: str,
        policy_metadata: dict[str, Any],
    ) -> GatewayResponse:
        return GatewayResponse(
            request_id=request.request_id,
            provider=request.provider,
            model=request.model,
            content="",
            status="error",
            error=GatewayError(
                code=code,
                message=message,
                provider=request.provider,
                recoverable=False,
                details={
                    "runtime_policy": (
                        policy_metadata
                    ),
                },
            ),
            metadata={
                "runtime_policy": policy_metadata,
            },
        )

    async def _publish(
        self,
        *,
        request: GatewayRequest,
        policy_metadata: dict[str, Any],
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=(
                    "ai.runtime_policy.evaluated"
                ),
                source="gateway_runtime_policy",
                workspace_id=request.workspace_id,
                correlation_id=(
                    request.correlation_id
                ),
                payload={
                    "request_id": request.request_id,
                    "provider": request.provider,
                    "model": request.model,
                    **policy_metadata,
                },
            )
        )
