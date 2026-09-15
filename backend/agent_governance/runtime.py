from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy.orm import Session

from backend.agent_governance.approval import (
    AgentHumanApprovalContract,
    AgentHumanApprovalError,
    AgentToolApprovalScope,
    fingerprint_agent_tool_input,
)
from backend.agent_governance.core import (
    AgentCapability,
    AgentPolicyProfile,
    AgentToolRequest,
)
from backend.agent_governance.enforcement import (
    AgentBeforeToolExecutionDecision,
    AgentEnforcementAction,
    AgentEnforcementLayerDecision,
    BeforeToolExecutionEnforcer,
)
from backend.agent_governance.invocation import (
    AgentTrustedInvocationError,
    TrustedAgentInvocationFacts,
    TrustedAgentInvocationResolver,
)
from backend.agent_governance.enforcement_adapters import (
    AGENT_POLICY_ACTION_ID_METADATA_KEY,
    AGENT_POLICY_RUNTIME_OPERATION_METADATA_KEY,
    AGENT_POLICY_TOOL_ID_METADATA_KEY,
    AgentEnforcementAdapterError,
    TrustedAgentEnforcementAdapter,
    TrustedAgentToolBinding,
)
from backend.agent_governance.service import (
    AgentProfileSelectionNotFoundError,
    AgentProfileService,
    AgentProfileServiceError,
)
from backend.orchestration.models import ToolDefinitionModel
from backend.runtime_policy import RuntimeTrust
from backend.policy_approvals.core import PolicyApprovalRecord
from backend.services.workspace_policy import (
    EffectiveWorkspacePolicy,
)


AGENT_TOOL_RUNTIME_GOVERNANCE_SCHEMA_VERSION = (
    "p3-002.2b-c3b.runtime-gate"
)

WorkspacePolicyResolver = Callable[
    [Session, str],
    EffectiveWorkspacePolicy,
]

_GOVERNED_METADATA_KEYS = frozenset(
    {
        AGENT_POLICY_TOOL_ID_METADATA_KEY,
        AGENT_POLICY_ACTION_ID_METADATA_KEY,
        AGENT_POLICY_RUNTIME_OPERATION_METADATA_KEY,
    }
)


class AgentToolRuntimeGovernanceError(RuntimeError):
    pass


class AgentToolRuntimeBoundaryUnavailableError(
    AgentToolRuntimeGovernanceError
):
    pass


@dataclass(frozen=True)
class AgentToolRuntimeGate:
    profile: AgentPolicyProfile
    profile_source: str
    binding: TrustedAgentToolBinding
    request: AgentToolRequest
    registry_layer: AgentEnforcementLayerDecision
    workspace_layer: AgentEnforcementLayerDecision
    runtime_layer: AgentEnforcementLayerDecision
    decision: AgentBeforeToolExecutionDecision
    input_fingerprint: str
    invocation_facts: TrustedAgentInvocationFacts | None = None
    human_control_layer: AgentEnforcementLayerDecision | None = None

    @property
    def layer_decisions(
        self,
    ) -> tuple[AgentEnforcementLayerDecision, ...]:
        layers = (
            self.registry_layer,
            self.workspace_layer,
            self.runtime_layer,
        )
        if self.human_control_layer is None:
            return layers
        return (*layers, self.human_control_layer)

    def to_policy_dict(self) -> dict[str, Any]:
        return {
            "schema_version": (
                AGENT_TOOL_RUNTIME_GOVERNANCE_SCHEMA_VERSION
            ),
            "profile": {
                "source": self.profile_source,
                "profile_id": self.profile.profile_id,
                "version": self.profile.version,
                "fingerprint": self.profile.fingerprint,
            },
            "binding": {
                "registry_tool_id": self.binding.registry_tool_id,
                "registry_tool_key": self.binding.registry_tool_key,
                "governed_tool_id": self.binding.governed_tool_id,
                "action_id": self.binding.action_id,
                "fingerprint": self.binding.fingerprint,
            },
            "input_fingerprint": self.input_fingerprint,
            "trusted_invocation": (
                None
                if self.invocation_facts is None
                else {
                    **self.invocation_facts.to_dict(),
                    "fingerprint": self.invocation_facts.fingerprint,
                }
            ),
            "layers": [
                {
                    "layer": item.layer.value,
                    "action": item.action.value,
                    "reason_codes": list(item.reason_codes),
                    "fingerprint": item.fingerprint,
                }
                for item in self.layer_decisions
            ],
            "decision": {
                "action": self.decision.action.value,
                "reason_codes": list(self.decision.reason_codes),
                "approval_required": self.decision.approval_required,
                "isolation_required": self.decision.isolation_required,
                "fingerprint": self.decision.fingerprint,
            },
        }

    def event_payload(
        self,
        *,
        invocation_id: str,
    ) -> dict[str, Any]:
        return {
            "invocation_id": invocation_id,
            "registry_tool_id": self.binding.registry_tool_id,
            "governed_tool_id": self.binding.governed_tool_id,
            "action_id": self.binding.action_id,
            "profile_id": self.profile.profile_id,
            "profile_fingerprint": self.profile.fingerprint,
            "binding_fingerprint": self.binding.fingerprint,
            "input_fingerprint": self.input_fingerprint,
            "trusted_invocation_fingerprint": (
                None
                if self.invocation_facts is None
                else self.invocation_facts.fingerprint
            ),
            "external_domain": (
                None
                if self.invocation_facts is None
                else self.invocation_facts.external_domain
            ),
            "decision_action": self.decision.action.value,
            "decision_fingerprint": self.decision.fingerprint,
            "approval_required": self.decision.approval_required,
            "isolation_required": self.decision.isolation_required,
            "reason_codes": list(self.decision.reason_codes),
        }


class AgentToolRuntimeGovernance:
    """Trusted P3-002 admission assembly for ToolExecutionRuntime.

    A Workspace without an active Agent Policy Profile remains on the
    existing Tool Registry path unless the ToolDefinition explicitly
    declares agent_policy_* metadata.

    Once a Workspace has an active profile, every executed ToolDefinition
    must have a valid governed binding. Missing or inconsistent bindings
    fail closed.

    P3-002.2b-C3b derives an exact audited destination for supported network
    tools from canonical validated input. Network transport enforcement and
    isolated code execution remain unavailable, so executable network/code
    paths still fail closed until those runtime boundaries are wired.
    """

    def __init__(
        self,
        *,
        workspace_policy_resolver: WorkspacePolicyResolver,
    ) -> None:
        self._workspace_policy_resolver = (
            workspace_policy_resolver
        )
        self._adapter = TrustedAgentEnforcementAdapter()
        self._invocation_resolver = TrustedAgentInvocationResolver()
        self._enforcer = BeforeToolExecutionEnforcer()

    def evaluate(
        self,
        *,
        session: Session,
        tool: ToolDefinitionModel,
        registry_decision: Mapping[str, Any],
        workspace_id: str | None,
        agent_id: str | None,
        input_data: dict[str, Any],
        validated_input: dict[str, Any] | None = None,
    ) -> AgentToolRuntimeGate | None:
        if not isinstance(tool, ToolDefinitionModel):
            raise AgentToolRuntimeGovernanceError(
                "Trusted ToolDefinitionModel is required."
            )
        if not isinstance(registry_decision, Mapping):
            raise AgentToolRuntimeGovernanceError(
                "Tool Registry decision must be an object."
            )
        if not isinstance(input_data, dict):
            raise AgentToolRuntimeGovernanceError(
                "Tool input must be an object."
            )
        if (
            validated_input is not None
            and not isinstance(validated_input, dict)
        ):
            raise AgentToolRuntimeGovernanceError(
                "Validated tool input must be an object."
            )

        metadata = tool.metadata_json or {}
        if not isinstance(metadata, Mapping):
            raise AgentToolRuntimeGovernanceError(
                "Tool metadata must be an object."
            )

        declares_governance = any(
            key in metadata
            for key in _GOVERNED_METADATA_KEYS
        )

        if workspace_id is None:
            if declares_governance:
                raise AgentToolRuntimeGovernanceError(
                    "Governed tool execution requires a Workspace."
                )
            return None

        profile_service = AgentProfileService(session)
        try:
            selection = profile_service.get_active(
                workspace_id=workspace_id
            )
        except AgentProfileSelectionNotFoundError as exc:
            if declares_governance:
                raise AgentToolRuntimeGovernanceError(
                    "Governed ToolDefinition has no active "
                    "Workspace Agent Policy Profile."
                ) from exc
            return None
        except AgentProfileServiceError as exc:
            raise AgentToolRuntimeGovernanceError(
                "Agent Policy Profile resolution failed closed."
            ) from exc

        try:
            workspace_policy = self._workspace_policy_resolver(
                session,
                workspace_id,
            )
        except Exception as exc:
            raise AgentToolRuntimeGovernanceError(
                "Workspace Policy resolution failed closed."
            ) from exc

        if (
            not isinstance(
                workspace_policy,
                EffectiveWorkspacePolicy,
            )
            or workspace_policy.workspace_id != workspace_id
        ):
            raise AgentToolRuntimeGovernanceError(
                "Workspace Policy scope mismatch."
            )

        try:
            binding = self._adapter.bind_tool(tool)
        except AgentEnforcementAdapterError as exc:
            raise AgentToolRuntimeGovernanceError(
                "Trusted Agent tool binding failed closed."
            ) from exc

        invocation_facts: TrustedAgentInvocationFacts | None = None
        if (
            AgentCapability.EXTERNAL_NETWORK
            in binding.capabilities
        ):
            if validated_input is None:
                raise AgentToolRuntimeBoundaryUnavailableError(
                    "Trusted validated network input is required "
                    "before destination derivation."
                )
            try:
                invocation_facts = self._invocation_resolver.derive(
                    binding=binding,
                    validated_input=validated_input,
                )
            except AgentTrustedInvocationError as exc:
                raise AgentToolRuntimeGovernanceError(
                    "Trusted invocation derivation failed closed."
                ) from exc

        secret_bindings = metadata.get(
            "secret_bindings",
            {},
        )
        if secret_bindings is None:
            secret_bindings = {}
        if not isinstance(secret_bindings, Mapping):
            raise AgentToolRuntimeGovernanceError(
                "Tool secret bindings must be an object."
            )
        contains_secrets = bool(secret_bindings)

        # ToolExecutionRuntime currently has no isolated-container
        # execution boundary. Code-execution capabilities therefore
        # receive BLOCKED runtime trust and cannot execute here.
        runtime_trust = (
            RuntimeTrust.BLOCKED
            if AgentCapability.CODE_EXECUTION
            in binding.capabilities
            else None
        )

        try:
            request = self._adapter.build_request(
                tool=tool,
                workspace_policy=workspace_policy,
                external_domain=(
                    None
                    if invocation_facts is None
                    else invocation_facts.external_domain
                ),
            )
            registry_layer = (
                self._adapter.tool_registry_decision(
                    tool=tool,
                    registry_decision=registry_decision,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                )
            )
            workspace_layer = (
                self._adapter.workspace_policy_decision(
                    workspace_policy=workspace_policy,
                    request=request,
                )
            )
            runtime_layer = (
                self._adapter.runtime_policy_decision(
                    tool=tool,
                    workspace_policy=workspace_policy,
                    request=request,
                    runtime_trust=runtime_trust,
                    contains_secrets=contains_secrets,
                )
            )
            decision = self._enforcer.evaluate(
                profile=selection.profile,
                expected_profile_fingerprint=(
                    selection.profile.fingerprint
                ),
                request=request,
                layer_decisions=(
                    registry_layer,
                    workspace_layer,
                    runtime_layer,
                ),
            )
            input_fingerprint = (
                invocation_facts.input_fingerprint
                if invocation_facts is not None
                else fingerprint_agent_tool_input(input_data)
            )
        except (AgentEnforcementAdapterError, ValueError) as exc:
            raise AgentToolRuntimeGovernanceError(
                "Before Tool Execution evaluation failed closed."
            ) from exc

        if (
            invocation_facts is not None
            and decision.action != AgentEnforcementAction.DENY
        ):
            raise AgentToolRuntimeBoundaryUnavailableError(
                "Trusted network destination was derived, but "
                "network transport enforcement is not wired."
            )

        return AgentToolRuntimeGate(
            profile=selection.profile,
            profile_source=selection.source,
            binding=binding,
            request=request,
            registry_layer=registry_layer,
            workspace_layer=workspace_layer,
            runtime_layer=runtime_layer,
            decision=decision,
            input_fingerprint=input_fingerprint,
            invocation_facts=invocation_facts,
        )


    def finalize_with_consumed_approval(
        self,
        *,
        gate: AgentToolRuntimeGate,
        record: PolicyApprovalRecord,
        expected_scope: AgentToolApprovalScope,
    ) -> AgentToolRuntimeGate:
        'Re-compose enforcement using only a consumed exact approval.'

        if not isinstance(gate, AgentToolRuntimeGate):
            raise AgentToolRuntimeGovernanceError(
                "Trusted AgentToolRuntimeGate is required."
            )

        if gate.human_control_layer is not None:
            raise AgentToolRuntimeGovernanceError(
                "Human Control has already been applied to this gate."
            )

        if not isinstance(expected_scope, AgentToolApprovalScope):
            raise AgentToolRuntimeGovernanceError(
                "Trusted AgentToolApprovalScope is required."
            )

        try:
            rebuilt_scope = AgentHumanApprovalContract.build_scope(
                decision=gate.decision,
                binding=gate.binding,
                execution_id=expected_scope.execution_id,
                input_fingerprint=gate.input_fingerprint,
            )

            if rebuilt_scope.fingerprint != expected_scope.fingerprint:
                raise AgentHumanApprovalError(
                    "Approval scope no longer matches the current gate."
                )

            human_control = (
                AgentHumanApprovalContract.allow_decision(
                    record=record,
                    expected_scope=rebuilt_scope,
                )
            )

            final_decision = self._enforcer.evaluate(
                profile=gate.profile,
                expected_profile_fingerprint=(
                    gate.profile.fingerprint
                ),
                request=gate.request,
                layer_decisions=(
                    gate.registry_layer,
                    gate.workspace_layer,
                    gate.runtime_layer,
                    human_control,
                ),
            )
        except (AgentHumanApprovalError, ValueError) as exc:
            raise AgentToolRuntimeGovernanceError(
                "Human Control finalization failed closed."
            ) from exc

        return replace(
            gate,
            decision=final_decision,
            human_control_layer=human_control,
        )


__all__ = [
    "AGENT_TOOL_RUNTIME_GOVERNANCE_SCHEMA_VERSION",
    "AgentToolRuntimeBoundaryUnavailableError",
    "AgentToolRuntimeGate",
    "AgentToolRuntimeGovernance",
    "AgentToolRuntimeGovernanceError",
    "WorkspacePolicyResolver",
]
