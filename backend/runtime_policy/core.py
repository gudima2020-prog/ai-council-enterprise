from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from typing import Any


class DataClassification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class ProviderTrust(StrEnum):
    LOCAL = "local"
    TRUSTED_EXTERNAL = "trusted_external"
    EXTERNAL = "external"
    BLOCKED = "blocked"


class RuntimeTrust(StrEnum):
    SAFE_HOST_PROFILE = "safe_host_profile"
    ISOLATED_CONTAINER = "isolated_container"
    BLOCKED = "blocked"


class PolicyOperation(StrEnum):
    MODEL_INFERENCE = "model_inference"
    CODE_EXECUTION = "code_execution"
    METADATA_VALIDATION = "metadata_validation"
    ARTIFACT_EXPORT = "artifact_export"


class PolicyAction(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    REQUIRE_ISOLATION = "require_isolation"
    DENY = "deny"


@dataclass(frozen=True)
class RuntimePolicyContext:
    operation: PolicyOperation
    data_classification: DataClassification = DataClassification.INTERNAL
    workspace_id: str | None = None
    provider_trust: ProviderTrust | None = None
    runtime_trust: RuntimeTrust | None = None
    contains_secrets: bool = False
    network_requested: bool = False


@dataclass(frozen=True)
class RuntimePolicyDecision:
    policy_version: str
    action: PolicyAction
    reason_codes: tuple[str, ...]
    fingerprint: str
    required_runtime: RuntimeTrust | None = None

    @property
    def allowed(self) -> bool:
        return self.action == PolicyAction.ALLOW

    @property
    def approval_required(self) -> bool:
        return self.action == PolicyAction.REQUIRE_APPROVAL


class RuntimePolicyEngine:
    """Fail-closed policy evaluator for provider and runtime admission."""

    POLICY_VERSION = "p2-011.1"

    def evaluate(
        self,
        context: RuntimePolicyContext,
    ) -> RuntimePolicyDecision:
        if context.contains_secrets:
            return self._decision(
                context,
                PolicyAction.DENY,
                "SECRET_MATERIAL_PRESENT",
            )

        if context.operation == PolicyOperation.MODEL_INFERENCE:
            return self._evaluate_model_inference(context)

        if context.operation == PolicyOperation.CODE_EXECUTION:
            return self._evaluate_code_execution(context)

        if (
            context.operation
            == PolicyOperation.METADATA_VALIDATION
        ):
            return self._decision(
                context,
                PolicyAction.ALLOW,
                "HOST_METADATA_VALIDATION_ALLOWED",
            )

        if context.operation == PolicyOperation.ARTIFACT_EXPORT:
            return self._evaluate_artifact_export(context)

        return self._decision(
            context,
            PolicyAction.DENY,
            "UNSUPPORTED_OPERATION",
        )

    def _evaluate_model_inference(
        self,
        context: RuntimePolicyContext,
    ) -> RuntimePolicyDecision:
        provider = context.provider_trust

        if provider is None:
            return self._decision(
                context,
                PolicyAction.DENY,
                "PROVIDER_TRUST_REQUIRED",
            )

        if provider == ProviderTrust.BLOCKED:
            return self._decision(
                context,
                PolicyAction.DENY,
                "PROVIDER_BLOCKED",
            )

        classification = context.data_classification

        if classification == DataClassification.RESTRICTED:
            if provider == ProviderTrust.LOCAL:
                return self._decision(
                    context,
                    PolicyAction.ALLOW,
                    "RESTRICTED_LOCAL_PROVIDER_ALLOWED",
                )

            return self._decision(
                context,
                PolicyAction.DENY,
                "RESTRICTED_EXTERNAL_PROVIDER_DENIED",
            )

        if classification == DataClassification.CONFIDENTIAL:
            if provider == ProviderTrust.LOCAL:
                return self._decision(
                    context,
                    PolicyAction.ALLOW,
                    "CONFIDENTIAL_LOCAL_PROVIDER_ALLOWED",
                )

            if provider == ProviderTrust.TRUSTED_EXTERNAL:
                return self._decision(
                    context,
                    PolicyAction.REQUIRE_APPROVAL,
                    "CONFIDENTIAL_EXTERNAL_APPROVAL_REQUIRED",
                )

            return self._decision(
                context,
                PolicyAction.DENY,
                "CONFIDENTIAL_EXTERNAL_PROVIDER_DENIED",
            )

        return self._decision(
            context,
            PolicyAction.ALLOW,
            "PROVIDER_POLICY_ALLOWED",
        )

    def _evaluate_code_execution(
        self,
        context: RuntimePolicyContext,
    ) -> RuntimePolicyDecision:
        runtime = context.runtime_trust

        if runtime is None:
            return self._decision(
                context,
                PolicyAction.DENY,
                "RUNTIME_TRUST_REQUIRED",
            )

        if runtime == RuntimeTrust.BLOCKED:
            return self._decision(
                context,
                PolicyAction.DENY,
                "RUNTIME_BLOCKED",
            )

        if runtime == RuntimeTrust.ISOLATED_CONTAINER:
            return self._decision(
                context,
                PolicyAction.ALLOW,
                "ISOLATED_RUNTIME_ALLOWED",
            )

        if context.network_requested:
            return self._decision(
                context,
                PolicyAction.REQUIRE_ISOLATION,
                "NETWORKED_HOST_EXECUTION_DENIED",
                required_runtime=RuntimeTrust.ISOLATED_CONTAINER,
            )

        if context.data_classification in {
            DataClassification.CONFIDENTIAL,
            DataClassification.RESTRICTED,
        }:
            return self._decision(
                context,
                PolicyAction.REQUIRE_ISOLATION,
                "SENSITIVE_CODE_REQUIRES_ISOLATION",
                required_runtime=RuntimeTrust.ISOLATED_CONTAINER,
            )

        return self._decision(
            context,
            PolicyAction.ALLOW,
            "SAFE_HOST_PROFILE_ALLOWED",
        )

    def _evaluate_artifact_export(
        self,
        context: RuntimePolicyContext,
    ) -> RuntimePolicyDecision:
        if context.data_classification == DataClassification.RESTRICTED:
            return self._decision(
                context,
                PolicyAction.DENY,
                "RESTRICTED_ARTIFACT_EXPORT_DENIED",
            )

        if context.data_classification == DataClassification.CONFIDENTIAL:
            return self._decision(
                context,
                PolicyAction.REQUIRE_APPROVAL,
                "CONFIDENTIAL_ARTIFACT_EXPORT_APPROVAL_REQUIRED",
            )

        return self._decision(
            context,
            PolicyAction.ALLOW,
            "ARTIFACT_EXPORT_ALLOWED",
        )

    def _decision(
        self,
        context: RuntimePolicyContext,
        action: PolicyAction,
        *reason_codes: str,
        required_runtime: RuntimeTrust | None = None,
    ) -> RuntimePolicyDecision:
        payload: dict[str, Any] = {
            "policy_version": self.POLICY_VERSION,
            "operation": context.operation.value,
            "data_classification": context.data_classification.value,
            "workspace_id": context.workspace_id,
            "provider_trust": (
                context.provider_trust.value
                if context.provider_trust is not None
                else None
            ),
            "runtime_trust": (
                context.runtime_trust.value
                if context.runtime_trust is not None
                else None
            ),
            "contains_secrets": context.contains_secrets,
            "network_requested": context.network_requested,
            "action": action.value,
            "reason_codes": list(reason_codes),
            "required_runtime": (
                required_runtime.value
                if required_runtime is not None
                else None
            ),
        }

        canonical = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()

        return RuntimePolicyDecision(
            policy_version=self.POLICY_VERSION,
            action=action,
            reason_codes=tuple(reason_codes),
            fingerprint=fingerprint,
            required_runtime=required_runtime,
        )
